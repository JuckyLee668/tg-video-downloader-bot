"""Tests for web/api_models.py — Pydantic request model validation."""

import pytest
from pydantic import ValidationError

from web.api_models import (
    BatchDeleteRequest,
    ConnectRequest,
    DownloadBatchRequest,
    ForwardRequest,
    HistoryDeleteRequest,
    JoinRequest,
    LoginSendCodeRequest,
    LoginSignInRequest,
    ProxyConfigRequest,
    SearchKeywordRequest,
    SearchRecentRequest,
    SearchTimeRequest,
    TaskIdRequest,
)


class TestSearchRecentRequest:
    def test_default_limit(self):
        req = SearchRecentRequest()
        assert req.limit == 50

    def test_custom_limit(self):
        req = SearchRecentRequest(limit=20)
        assert req.limit == 20

    def test_limit_max_1000(self):
        req = SearchRecentRequest(limit=1000)
        assert req.limit == 1000

    def test_limit_exceeds_1000_raises(self):
        with pytest.raises(ValidationError):
            SearchRecentRequest(limit=1001)

    def test_limit_min_1(self):
        with pytest.raises(ValidationError):
            SearchRecentRequest(limit=0)

    def test_media_type_default_none(self):
        req = SearchRecentRequest()
        assert req.media_type is None

    def test_offset_id_default_0(self):
        req = SearchRecentRequest()
        assert req.offset_id == 0


class TestSearchKeywordRequest:
    def test_valid_keyword(self):
        req = SearchKeywordRequest(keyword="test")
        assert req.keyword == "test"

    def test_empty_keyword_raises(self):
        with pytest.raises(ValidationError):
            SearchKeywordRequest(keyword="")

    def test_whitespace_only_passes(self):
        # Pydantic's min_length does not strip whitespace by default
        req = SearchKeywordRequest(keyword="   ")
        assert req.keyword == "   "

    def test_keyword_too_long_raises(self):
        with pytest.raises(ValidationError):
            SearchKeywordRequest(keyword="x" * 121)

    def test_with_limit(self):
        req = SearchKeywordRequest(keyword="test", limit=20)
        assert req.limit == 20

    def test_media_type(self):
        req = SearchKeywordRequest(keyword="test", media_type="video")
        assert req.media_type == "video"


class TestSearchTimeRequest:
    def test_valid_dates(self):
        req = SearchTimeRequest(start_date="2024-01-01", end_date="2024-01-31")
        assert req.start_date == "2024-01-01"
        assert req.end_date == "2024-01-31"

    def test_invalid_date_format_raises(self):
        with pytest.raises(ValidationError):
            SearchTimeRequest(start_date="01-01-2024", end_date="2024-01-31")

    def test_default_limit_100(self):
        req = SearchTimeRequest(start_date="2024-01-01", end_date="2024-01-31")
        assert req.limit == 100


class TestDownloadBatchRequest:
    def test_valid_message_ids(self):
        req = DownloadBatchRequest(message_ids=[1, 2, 3])
        assert req.message_ids == [1, 2, 3]

    def test_empty_message_ids_raises(self):
        with pytest.raises(ValidationError):
            DownloadBatchRequest(message_ids=[])

    def test_too_many_ids_raises(self):
        with pytest.raises(ValidationError):
            DownloadBatchRequest(message_ids=list(range(501)))

    def test_channel_id_optional(self):
        req = DownloadBatchRequest(message_ids=[1])
        assert req.channel_id is None

    def test_formats_filter(self):
        req = DownloadBatchRequest(message_ids=[1], formats=["mp4", "mkv"])
        assert req.formats == ["mp4", "mkv"]


class TestForwardRequest:
    def test_valid(self):
        req = ForwardRequest(
            from_channel_id="-100123",
            message_ids=[1, 2],
            to_chat_id="@target",
        )
        assert req.from_channel_id == "-100123"
        assert req.message_ids == [1, 2]
        assert req.to_chat_id == "@target"

    def test_empty_from_channel_raises(self):
        with pytest.raises(ValidationError):
            ForwardRequest(from_channel_id="", message_ids=[1], to_chat_id="@x")

    def test_empty_message_ids_raises(self):
        with pytest.raises(ValidationError):
            ForwardRequest(from_channel_id="-100123", message_ids=[], to_chat_id="@x")

    def test_empty_to_chat_raises(self):
        with pytest.raises(ValidationError):
            ForwardRequest(from_channel_id="-100123", message_ids=[1], to_chat_id="")


class TestProxyConfigRequest:
    def test_valid_socks5(self):
        req = ProxyConfigRequest(scheme="socks5", hostname="127.0.0.1", port=10808)
        assert req.scheme == "socks5"
        assert req.port == 10808

    def test_defaults(self):
        req = ProxyConfigRequest(hostname="proxy.example.com", port=8080)
        assert req.scheme == "http"
        assert req.rdns is True

    def test_invalid_scheme_raises(self):
        with pytest.raises(ValidationError):
            ProxyConfigRequest(scheme="https", hostname="x", port=80)

    def test_port_range(self):
        with pytest.raises(ValidationError):
            ProxyConfigRequest(scheme="http", hostname="x", port=0)


class TestConnectRequest:
    def test_valid(self):
        req = ConnectRequest(identifier="@channel")
        assert req.identifier == "@channel"

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            ConnectRequest(identifier="")


class TestJoinRequest:
    def test_valid(self):
        req = JoinRequest(link="https://t.me/+abc123")
        assert req.link == "https://t.me/+abc123"

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            JoinRequest(link="")


class TestBatchDeleteRequest:
    def test_task_ids_as_strings(self):
        req = BatchDeleteRequest(task_ids=["1", "2", "3"])
        assert req.task_ids == ["1", "2", "3"]

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            BatchDeleteRequest(task_ids=[])


class TestHistoryDeleteRequest:
    def test_history_ids(self):
        req = HistoryDeleteRequest(ids=[1, 2])
        assert req.ids == [1, 2]

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            HistoryDeleteRequest(ids=[])


class TestTaskIdRequest:
    def test_valid(self):
        req = TaskIdRequest(task_id="42")
        assert req.task_id == "42"


class TestLoginSendCodeRequest:
    def test_valid_phone(self):
        req = LoginSendCodeRequest(phone="+8613800138000")
        assert req.phone == "+8613800138000"

    def test_invalid_phone_raises(self):
        with pytest.raises(ValidationError):
            LoginSendCodeRequest(phone="abc")


class TestLoginSignInRequest:
    def test_valid(self):
        req = LoginSignInRequest(code="12345")
        assert req.code == "12345"
