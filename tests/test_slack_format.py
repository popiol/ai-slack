"""Tests for slack_format module."""

from src.slack_format import chunk_text, to_slack_mrkdwn


class TestToSlackMrkdwn:
    def test_bold_converted(self):
        assert to_slack_mrkdwn("**hello**") == "*hello*"

    def test_multiple_bold(self):
        assert to_slack_mrkdwn("**a** and **b**") == "*a* and *b*"

    def test_heading_converted(self):
        assert to_slack_mrkdwn("# Title") == "*Title*"

    def test_h2_converted(self):
        assert to_slack_mrkdwn("## Section") == "*Section*"

    def test_heading_in_middle_of_text(self):
        result = to_slack_mrkdwn("intro\n## Section\ntext")
        assert "*Section*" in result

    def test_code_fence_left_alone(self):
        text = "```python\n**not bold**\n```"
        assert to_slack_mrkdwn(text) == text

    def test_bold_outside_code_fence_converted(self):
        text = "**bold** before ```code\n**not**\n``` after **also bold**"
        result = to_slack_mrkdwn(text)
        assert result.startswith("*bold*")
        assert "```code\n**not**\n```" in result
        assert result.endswith("*also bold*")

    def test_plain_text_unchanged(self):
        assert to_slack_mrkdwn("plain text") == "plain text"

    def test_empty_string(self):
        assert to_slack_mrkdwn("") == ""


class TestChunkText:
    def test_short_text_single_chunk(self):
        assert chunk_text("hello") == ["hello"]

    def test_text_at_limit_single_chunk(self):
        text = "x" * 3900
        assert chunk_text(text) == [text]

    def test_text_over_limit_split(self):
        text = "x" * 7800
        chunks = chunk_text(text)
        assert len(chunks) == 2
        assert all(len(c) <= 3900 for c in chunks)
        assert "".join(chunks) == text

    def test_custom_limit(self):
        chunks = chunk_text("abcdef", limit=3)
        assert chunks == ["abc", "def"]

    def test_empty_string(self):
        assert chunk_text("") == [""]
