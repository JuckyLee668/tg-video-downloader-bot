"""Tests for telegram/handlers/utils.py — index parsing, formatting, file naming."""

import pytest

from telegram.handlers.utils import (
    format_size,
    format_time,
    message_file_name,
    parse_indices,
)


class _MockFile:
    """Minimal mock for msg.file with optional name and mime_type."""

    def __init__(self, name: str | None = None, mime_type: str = ""):
        self.name = name
        self.mime_type = mime_type


class _MockMsg:
    """Minimal mock for a Telegram Message with basic attributes."""

    def __init__(self, msg_id: int, file: _MockFile | None = None):
        self.id = msg_id
        self.file = file


# ── parse_indices ──────────────────────────────────────────────────────────


class TestParseIndices:
    def test_empty_string_returns_empty_set(self):
        assert parse_indices("") == set()

    def test_all_keyword_returns_empty_set(self):
        assert parse_indices("all") == set()
        assert parse_indices("ALL") == set()
        assert parse_indices("  all  ") == set()

    def test_single_index(self):
        assert parse_indices("3") == {3}

    def test_comma_separated(self):
        assert parse_indices("1, 3, 5") == {1, 3, 5}

    def test_range(self):
        assert parse_indices("1-3") == {1, 2, 3}

    def test_mixed_range_and_singles(self):
        assert parse_indices("1-3, 5, 7-8") == {1, 2, 3, 5, 7, 8}

    def test_chinese_comma(self):
        assert parse_indices("1，3") == {1, 3}

    def test_whitespace_tolerant(self):
        assert parse_indices("  1 - 3 , 5 ") == {1, 2, 3, 5}

    def test_single_range(self):
        assert parse_indices("5-5") == {5}

    def test_large_range(self):
        assert parse_indices("1-100") == set(range(1, 101))

    def test_invalid_range_raises(self):
        with pytest.raises(ValueError, match="Invalid range"):
            parse_indices("abc-def")

    def test_invalid_index_raises(self):
        with pytest.raises(ValueError, match="Invalid index"):
            parse_indices("abc")

    def test_none_input_returns_empty(self):
        assert parse_indices(None) == set()  # type: ignore[arg-type]


# ── format_size ────────────────────────────────────────────────────────────


class TestFormatSize:
    def test_zero_bytes(self):
        assert format_size(0) == "0.00 B"

    def test_bytes(self):
        assert format_size(512) == "512.00 B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.00 KB"

    def test_megabytes(self):
        assert format_size(1_048_576) == "1.00 MB"

    def test_gigabytes(self):
        assert format_size(1_073_741_824) == "1.00 GB"

    def test_terabytes(self):
        assert format_size(1_099_511_627_776) == "1.00 TB"

    def test_petabytes(self):
        assert format_size(1_125_899_906_842_624) == "1.00 PB"

    def test_large_float(self):
        # 2.5 GB
        assert "2.50 GB" in format_size(int(2.5 * 1024**3))

    def test_non_power_of_two(self):
        # 1500 MB
        assert format_size(1_500 * 1024**2) == "1.46 GB"


# ── format_time ────────────────────────────────────────────────────────────


class TestFormatTime:
    def test_returns_mm_dd_hh_mm(self, monkeypatch):
        # Spy on the actual implementation to understand what it does
        # format_time uses datetime.fromtimestamp() which is timezone-aware
        # in the local TZ (CST=UTC+8). We'll check the structure instead.
        result = format_time(1710508200.0)
        # Should be MM-DD HH:MM format
        parts = result.split(" ")
        assert len(parts) == 2
        date_part, time_part = parts
        assert len(date_part.split("-")) == 2  # MM-DD
        assert len(time_part.split(":")) == 2  # HH:MM

    def test_unix_epoch_returns_local_time(self):
        result = format_time(0)
        # datetime.fromtimestamp(0) 依赖本地时区 (CI=UTC, 本地=CST)
        # 只验证格式正确即可
        parts = result.split(" ")
        assert len(parts) == 2
        assert len(parts[0].split("-")) == 2  # MM-DD
        assert len(parts[1].split(":")) == 2  # HH:MM


# ── message_file_name ──────────────────────────────────────────────────────


class TestMessageFileName:
    def test_uses_file_name_when_available(self):
        msg = _MockMsg(42, _MockFile(name="hello.mp4"))
        assert message_file_name(msg) == "hello.mp4"

    def test_video_mime(self):
        msg = _MockMsg(42, _MockFile(mime_type="video/mp4"))
        assert message_file_name(msg) == "media_42.mp4"

    def test_audio_mime(self):
        msg = _MockMsg(5, _MockFile(mime_type="audio/mpeg"))
        assert message_file_name(msg) == "media_5.mp3"

    def test_image_mime(self):
        msg = _MockMsg(7, _MockFile(mime_type="image/png"))
        assert message_file_name(msg) == "media_7.jpg"

    def test_unknown_mime_no_ext(self):
        msg = _MockMsg(3, _MockFile(mime_type="application/octet-stream"))
        assert message_file_name(msg) == "media_3"

    def test_no_file_attr_falls_back_to_media_id(self):
        msg = _MockMsg(10)
        assert message_file_name(msg) == "media_10"
