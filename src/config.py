"""Environment-based configuration for the Slack <-> Claude Code bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Config:
    slack_bot_token: str
    slack_app_token: str
    slack_channel_id: str
    allowed_user_ids: set[str]

    claude_bin: str
    claude_working_dir: Path
    claude_add_dirs: list[str]
    claude_permission_mode: str
    claude_timeout_seconds: int


def load_config() -> Config:
    allowed_users = _split_csv(os.environ.get("SLACK_ALLOWED_USERS", ""))
    if not allowed_users:
        raise RuntimeError(
            "SLACK_ALLOWED_USERS must list at least one Slack user ID "
            "(comma-separated) - this bot restricts who can trigger it."
        )

    working_dir = Path(
        os.environ.get("CLAUDE_WORKING_DIR", str(REPO_ROOT))
    ).expanduser().resolve()

    add_dirs = _split_csv(
        os.environ.get("CLAUDE_ADD_DIRS", str(Path.home() / "proj"))
    )

    return Config(
        slack_bot_token=_require("SLACK_BOT_TOKEN"),
        slack_app_token=_require("SLACK_APP_TOKEN"),
        slack_channel_id=_require("SLACK_CHANNEL_ID"),
        allowed_user_ids=set(allowed_users),
        claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
        claude_working_dir=working_dir,
        claude_add_dirs=add_dirs,
        claude_permission_mode=os.environ.get(
            "CLAUDE_PERMISSION_MODE", "bypassPermissions"
        ),
        claude_timeout_seconds=int(
            os.environ.get("CLAUDE_TIMEOUT_SECONDS", "1800")
        ),
    )
