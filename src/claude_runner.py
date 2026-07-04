"""Runs the local Claude Code CLI non-interactively and parses its result."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass

from .config import Config

logger = logging.getLogger(__name__)


class ClaudeRunError(Exception):
    pass


@dataclass
class ClaudeResult:
    text: str
    session_id: str
    is_error: bool


def run_claude(config: Config, prompt: str, session_id: str | None) -> ClaudeResult:
    """Invoke `claude -p` once, optionally resuming an existing session.

    Blocking call - the caller is expected to run this off the Slack event loop
    thread (e.g. via a background thread or executor).
    """
    args = [
        config.claude_bin,
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        config.claude_permission_mode,
    ]
    for add_dir in config.claude_add_dirs:
        args += ["--add-dir", add_dir]
    if session_id:
        args += ["--resume", session_id]
    # `--` stops option parsing so the prompt isn't swallowed by the
    # variadic --add-dir argument list.
    args += ["--", prompt]

    try:
        completed = subprocess.run(
            args,
            cwd=config.claude_working_dir,
            capture_output=True,
            text=True,
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
    except json.JSONDecodeError as exc:
        raise ClaudeRunError(
            f"Could not parse claude output as JSON: {completed.stdout[:500]!r}"
        ) from exc

    return ClaudeResult(
        text=payload.get("result", ""),
        session_id=payload["session_id"],
        is_error=bool(payload.get("is_error", False)),
    )
