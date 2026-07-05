"""Tests for main module logic (pure functions only, no Slack I/O)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.main import _should_handle


def _make_config(
    channel_id: str = "C123",
    allowed_user_ids: set[str] | None = None,
) -> MagicMock:
    config = MagicMock()
    config.slack_channel_id = channel_id
    config.allowed_user_ids = (
        allowed_user_ids if allowed_user_ids is not None else {"U1"}
    )
    return config


def _base_event(**overrides) -> dict:
    event = {
        "channel": "C123",
        "ts": "1234.5678",
        "user": "U1",
        "text": "hello",
    }
    event.update(overrides)
    return event


class TestShouldHandle:
    def test_valid_event_accepted(self):
        assert _should_handle(_base_event(), _make_config()) is True

    def test_wrong_channel_rejected(self):
        assert _should_handle(_base_event(channel="COTHER"), _make_config()) is False

    def test_bot_message_rejected(self):
        assert _should_handle(_base_event(bot_id="BXXX"), _make_config()) is False

    def test_ignored_subtype_rejected(self):
        assert (
            _should_handle(_base_event(subtype="bot_message"), _make_config()) is False
        )

    def test_disallowed_user_rejected(self):
        assert _should_handle(_base_event(user="USTRANGER"), _make_config()) is False

    def test_no_text_rejected(self):
        assert _should_handle(_base_event(text=""), _make_config()) is False

    def test_all_ignored_subtypes_rejected(self):
        from src.main import IGNORED_SUBTYPES

        for subtype in IGNORED_SUBTYPES:
            assert _should_handle(_base_event(subtype=subtype), _make_config()) is False
