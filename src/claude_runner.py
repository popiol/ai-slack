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
    "Local Claude Code session transcripts live under "
    "~/.claude/projects/<encoded-project-path>/<session-id>.jsonl (one file per "
    "past session), each line a JSON event; the 'cwd' field on user/assistant "
    "events records the project directory that session ran in. If the user's "
    "message asks you to switch, resume, or continue a different past session "
    "(by id, project, or description), explore that directory (ls/grep/cat) to "
    "find the matching session file, then set action.type='switch_session' with "
    "action.params={\"session_id\": <id>, \"cwd\": <that session's cwd>}. "
    "Otherwise set action.type='none' with params={}. Always fill 'reply' with "
    "a user-facing message - either your normal answer, or (when switching) a "
    "short confirmation of what you switched to and why."
)


class ClaudeRunError(Exception):
    pass


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
        logger.error("claude exited %s: %s", completed.returncode, completed.stderr)
        raise ClaudeRunError(
            f"claude exited with status {completed.returncode}: "
            f"{completed.stderr.strip() or '(no stderr)'}"
        )

    try:
        payload = json.loads(completed.stdout)
        structured = payload["structured_output"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise ClaudeRunError(
            f"Could not parse claude structured output: {completed.stdout[:500]!r}"
        ) from exc

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
