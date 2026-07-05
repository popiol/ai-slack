"""Runs the local Claude Code CLI non-interactively and parses its result."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config

logger = logging.getLogger(__name__)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "action": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["none", "switch_session"]},
                "params": {"type": "object"},
            },
            "required": ["type", "params"],
        },
    },
    "required": ["reply", "action"],
}

SYSTEM_PROMPT_ADDENDUM = (
    "You are being driven through Slack, not an interactive terminal. The user "
    "can ONLY see the text you put in the 'reply' field of your structured "
    "response - they cannot see any tool calls, command output, file contents, "
    "diffs, or anything else you produce while working. Never refer to "
    "something as 'shown above', 'listed above', or similar, since nothing is "
    "shown to the user except 'reply' itself - always inline the actual, "
    "complete content (file contents, command output, lists, etc.) directly "
    "in 'reply'.\n\n"
    "Local Claude Code session transcripts live under "
    "~/.claude/projects/<encoded-project-path>/<session-id>.jsonl (one file per "
    "past session), each line a JSON event; the 'cwd' field on user/assistant "
    "events records the project directory that session ran in. If the user's "
    "message asks you to switch, resume, or continue a different past session "
    "(by id, project, or description), explore that directory (ls/grep/cat) to "
    "find the matching session file, then set action.type='switch_session' with "
    "action.params={\"session_id\": <id>, \"cwd\": <that session's recorded "
    "cwd, exactly as found in the transcript>}. "
    "Otherwise set action.type='none' with params={}. Always fill 'reply' with "
    "a complete, self-contained user-facing message - either your normal "
    "answer, or (when switching) a short confirmation of what you switched to "
    "and why."
)


class ClaudeRunError(Exception):
    pass


def find_session_cwd(session_id: str) -> Path | None:
    """Look up a session's actual recorded cwd directly from its transcript
    file, rather than trusting a model-regenerated copy of the path. The
    model can retype a path incorrectly (e.g. swapping '-' for '_') even
    when it read the correct value - reading the transcript ourselves is
    exact, since it's just a string copy in code, not a regeneration."""
    matches = list(CLAUDE_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    if not matches:
        return None
    try:
        with matches[0].open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = event.get("cwd")
                if cwd:
                    return Path(cwd)
    except OSError:
        return None
    return None


@dataclass
class ClaudeAction:
    type: str
    params: dict


@dataclass
class ClaudeResult:
    reply: str
    session_id: str
    is_error: bool
    action: ClaudeAction


def run_claude(
    config: Config, prompt: str, session_id: str | None, cwd: Path
) -> ClaudeResult:
    """Invoke `claude -p` once, optionally resuming an existing session.

    Blocking call - the caller is expected to run this off the Slack event loop
    thread (e.g. via a background thread or executor).
    """
    if not cwd.is_dir():
        raise ClaudeRunError(f"cwd {cwd} does not exist or is not a directory")

    args = [
        config.claude_bin,
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        config.claude_permission_mode,
        "--json-schema",
        json.dumps(RESPONSE_SCHEMA),
        "--append-system-prompt",
        SYSTEM_PROMPT_ADDENDUM,
    ]
    for add_dir in [*config.claude_add_dirs, str(CLAUDE_PROJECTS_DIR)]:
        args += ["--add-dir", add_dir]
    if session_id:
        args += ["--resume", session_id]
    # `--` stops option parsing so the prompt isn't swallowed by the
    # variadic --add-dir argument list.
    args += ["--", prompt]

    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=config.claude_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeRunError(
            f"Claude Code timed out after {config.claude_timeout_seconds}s"
        ) from exc

    if completed.returncode != 0:
        logger.error(
            "claude exited %s: stderr=%r stdout=%r",
            completed.returncode, completed.stderr, completed.stdout,
        )
        detail = completed.stderr.strip() or completed.stdout.strip() or "(no output)"
        raise ClaudeRunError(
            f"claude exited with status {completed.returncode}: {detail}"
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeRunError(
            f"Could not parse claude output as JSON: {completed.stdout[:500]!r}"
        ) from exc

    # `structured_output` can be absent even on a successful run - notably when
    # resuming a session that predates --json-schema being passed, claude may
    # just answer in plain `result` text and skip the schema entirely. Degrade
    # to a plain reply with no action rather than failing the whole run.
    structured = payload.get("structured_output")
    if structured is None:
        logger.warning(
            "claude returned no structured_output (session=%s); falling back to "
            "plain result text",
            payload.get("session_id"),
        )
        structured = {"reply": payload.get("result", ""), "action": None}

    action_payload = structured.get("action") or {"type": "none", "params": {}}

    return ClaudeResult(
        reply=structured.get("reply", ""),
        session_id=payload["session_id"],
        is_error=bool(payload.get("is_error", False)),
        action=ClaudeAction(
            type=action_payload.get("type", "none"),
            params=action_payload.get("params") or {},
        ),
    )
