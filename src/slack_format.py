"""Best-effort conversion of Claude's Markdown output to Slack mrkdwn."""

from __future__ import annotations

import re

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)

SLACK_TEXT_LIMIT = 3900


def to_slack_mrkdwn(text: str) -> str:
    """Convert **bold** and #headings to Slack mrkdwn, leaving code fences alone."""
    pieces = _CODE_FENCE.split(text)
    fences = _CODE_FENCE.findall(text)

    converted = []
    for piece in pieces:
        piece = _HEADING.sub(r"*\1*", piece)
        piece = _BOLD.sub(r"*\1*", piece)
        converted.append(piece)

    result = converted[0]
    for fence, piece in zip(fences, converted[1:]):
        result += fence + piece
    return result


def chunk_text(text: str, limit: int = SLACK_TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]
