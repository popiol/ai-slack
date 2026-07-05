"""Background bridge: Slack channel messages -> Claude Code -> Slack replies.

Listens on one Slack channel via Socket Mode, runs each message through the
local `claude` CLI (resuming a session per Slack thread), and posts the
result back as a threaded reply.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .claude_runner import ClaudeRunError, find_session_cwd, run_claude
from .config import Config, load_config
from .slack_format import chunk_text, to_slack_mrkdwn

logger = logging.getLogger("src")

# thread_ts -> (claude session_id, cwd it was run from). In-memory only:
# restarting the process starts fresh Claude sessions for any thread it
# hasn't seen since restart. cwd is tracked per-thread (not just a global
# default) because resuming a session requires running from the same
# directory it originally started in - relevant once a thread switches to a
# session that belongs to a different project.
_sessions: dict[str, tuple[str, Path]] = {}
_sessions_lock = threading.Lock()

IGNORED_SUBTYPES = {
    "bot_message",
    "message_changed",
    "message_deleted",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
}


def _should_handle(event: dict, config: Config) -> bool:
    ts = event.get("ts")
    if event.get("channel") != config.slack_channel_id:
        logger.debug("Skip %s: wrong channel %r", ts, event.get("channel"))
        return False
    if event.get("bot_id"):
        logger.debug("Skip %s: from a bot (bot_id=%r)", ts, event.get("bot_id"))
        return False
    if event.get("subtype") in IGNORED_SUBTYPES:
        logger.debug("Skip %s: ignored subtype %r", ts, event.get("subtype"))
        return False
    if event.get("user") not in config.allowed_user_ids:
        logger.debug("Skip %s: user %r not in allow-list", ts, event.get("user"))
        return False
    if not event.get("text"):
        logger.debug("Skip %s: no text", ts)
        return False
    return True


def _run_claude_safe(config: Config, text: str, session_id, cwd, client, channel, thread_ts, ts):
    """Run claude and report any failure to Slack. Returns None on failure."""
    try:
        return run_claude(config, text, session_id, cwd)
    except ClaudeRunError as exc:
        logger.exception("Claude run failed for thread %s", thread_ts)
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=f":x: Claude Code failed: {exc}"
        )
    except Exception as exc:
        logger.exception("Unexpected error processing thread %s", thread_ts)
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=f":x: Unexpected error: {exc}"
        )
    _swap_reaction(client, channel, ts, "eyes", "x")
    return None


def _apply_action(thread_ts: str, result, cwd: Path) -> str | None:
    """Update the thread's (session_id, cwd) mapping per the action Claude
    returned. Returns a warning to post to Slack if a requested switch
    couldn't be applied, otherwise None."""
    if result.action.type != "switch_session" or not result.action.params.get("session_id"):
        with _sessions_lock:
            _sessions[thread_ts] = (result.session_id, cwd)
        return None

    new_session_id = result.action.params["session_id"]
    # Look the cwd up directly from the target session's own transcript file
    # rather than trusting Claude's regenerated copy of the path in
    # action.params - the model can subtly mistype it (e.g. '-' vs '_') even
    # when it read the correct value. Fall back to Claude's reported value
    # only if the transcript can't be found.
    actual_cwd = find_session_cwd(new_session_id)
    if actual_cwd is None:
        logger.warning(
            "Could not find transcript for session %s to verify cwd; falling "
            "back to claude-reported value",
            new_session_id,
        )
    new_cwd = actual_cwd or Path(result.action.params.get("cwd") or cwd)
    if not new_cwd.is_dir():
        logger.warning(
            "Switch target for thread %s has stale cwd %s; keeping current session %s",
            thread_ts, new_cwd, result.session_id,
        )
        with _sessions_lock:
            _sessions[thread_ts] = (result.session_id, cwd)
        return (
            f":warning: Can't switch to session {new_session_id}: "
            f"its recorded directory {new_cwd} no longer exists "
            f"(likely renamed/moved/deleted). Staying on the current session."
        )

    logger.info(
        "Switching thread %s to session %s (cwd=%s)", thread_ts, new_session_id, new_cwd
    )
    with _sessions_lock:
        _sessions[thread_ts] = (new_session_id, new_cwd)
    return None


def _process(event: dict, client, config: Config) -> None:
    channel = event["channel"]
    ts = event["ts"]
    user = event["user"]
    thread_ts = event.get("thread_ts", ts)
    text = event["text"]

    logger.debug(
        "Processing message ts=%s thread=%s user=%s text=%r",
        ts, thread_ts, user, text,
    )

    try:
        client.reactions_add(channel=channel, timestamp=ts, name="eyes")
        logger.debug("Added :eyes: reaction to %s", ts)
    except Exception:
        logger.exception("Failed to add :eyes: reaction to %s", ts)

    with _sessions_lock:
        existing = _sessions.get(thread_ts)
    session_id, cwd = existing if existing else (None, config.claude_working_dir)
    logger.debug(
        "%s claude session for thread %s (cwd=%s)",
        "Resuming" if session_id else "Starting new", thread_ts, cwd,
    )

    result = _run_claude_safe(config, text, session_id, cwd, client, channel, thread_ts, ts)
    if result is None:
        return

    logger.debug(
        "Claude run done for thread %s: session=%s is_error=%s action=%s reply_len=%d",
        thread_ts, result.session_id, result.is_error, result.action.type, len(result.reply),
    )

    switch_failure = _apply_action(thread_ts, result, cwd)
    if switch_failure:
        # Claude's own `reply` assumed the switch succeeded (it has no idea we
        # rejected it for a stale cwd), so posting it alongside this warning
        # would contradict it. The warning alone fully explains what happened.
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=switch_failure)
    else:
        reply = to_slack_mrkdwn(result.reply) or "(no output)"
        chunks = chunk_text(reply)
        for i, chunk in enumerate(chunks, start=1):
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunk)
            logger.debug("Posted reply chunk %d/%d to thread %s", i, len(chunks), thread_ts)

    _swap_reaction(
        client, channel, ts, "eyes", "x" if result.is_error else "white_check_mark"
    )


def _swap_reaction(client, channel: str, ts: str, old: str, new: str) -> None:
    try:
        client.reactions_remove(channel=channel, timestamp=ts, name=old)
    except Exception:
        pass
    try:
        client.reactions_add(channel=channel, timestamp=ts, name=new)
        logger.debug("Set :%s: reaction on %s", new, ts)
    except Exception:
        logger.exception("Failed to add :%s: reaction", new)


def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = App(token=config.slack_bot_token)

    @app.event("message")
    def handle_message(event, client, ack) -> None:
        ack()
        logger.debug(
            "Received event ts=%s channel=%s user=%s subtype=%s",
            event.get("ts"), event.get("channel"), event.get("user"), event.get("subtype"),
        )
        if not _should_handle(event, config):
            return
        threading.Thread(
            target=_process, args=(event, client, config), daemon=True
        ).start()

    logger.info(
        "ai-slack listening on channel %s, working dir %s",
        config.slack_channel_id,
        config.claude_working_dir,
    )
    SocketModeHandler(app, config.slack_app_token).start()


if __name__ == "__main__":
    main()
