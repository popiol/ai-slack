"""Background bridge: Slack channel messages -> Claude Code -> Slack replies.

Listens on one Slack channel via Socket Mode, runs each message through the
local `claude` CLI (resuming a session per Slack thread), and posts the
result back as a threaded reply.
"""

from __future__ import annotations

import logging
import threading

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .claude_runner import ClaudeRunError, run_claude
from .config import Config, load_config
from .slack_format import chunk_text, to_slack_mrkdwn

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("src")

# thread_ts -> claude session_id. In-memory only: restarting the process
# starts fresh Claude sessions for any thread it hasn't seen since restart.
_sessions: dict[str, str] = {}
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
    if event.get("channel") != config.slack_channel_id:
        return False
    if event.get("bot_id"):
        return False
    if event.get("subtype") in IGNORED_SUBTYPES:
        return False
    if event.get("user") not in config.allowed_user_ids:
        return False
    if not event.get("text"):
        return False
    return True


def _process(event: dict, client, config: Config) -> None:
    channel = event["channel"]
    ts = event["ts"]
    thread_ts = event.get("thread_ts", ts)

    try:
        client.reactions_add(channel=channel, timestamp=ts, name="eyes")
    except Exception:
        logger.exception("Failed to add :eyes: reaction")

    with _sessions_lock:
        session_id = _sessions.get(thread_ts)

    try:
        result = run_claude(config, event["text"], session_id)
    except ClaudeRunError as exc:
        logger.exception("Claude run failed for thread %s", thread_ts)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f":x: Claude Code failed: {exc}",
        )
        _swap_reaction(client, channel, ts, "eyes", "x")
        return

    with _sessions_lock:
        _sessions[thread_ts] = result.session_id

    reply = to_slack_mrkdwn(result.text) or "(no output)"
    for chunk in chunk_text(reply):
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunk)

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
    except Exception:
        logger.exception("Failed to add :%s: reaction", new)


def main() -> None:
    config = load_config()
    app = App(token=config.slack_bot_token)

    @app.event("message")
    def handle_message(event, client, ack) -> None:
        ack()
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
