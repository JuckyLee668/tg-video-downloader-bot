"""Tests for action_prompt.py — the unified media action flow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.handlers.action_prompt import (
    _enqueue_task,
    _format_duration,
    _format_size,
    _truncate_filename,
    handle_action_choice,
    handle_action_target,
    handle_media_action,
)

# ── shared constants ───────────────────────────────────────────────────

_DM_PATH = "telegram.handlers.action_prompt.download_manager"
_SM_PATH = "telegram.handlers.action_prompt.state_manager"
_CFG_PATH = "telegram.handlers.action_prompt.config"


# ── _truncate_filename ────────────────────────────────────────────────

class TestTruncateFilename:
    def test_short_name_unchanged(self):
        assert _truncate_filename("hello", max_bytes=200) == "hello"

    def test_ascii_exact_boundary(self):
        name = "a" * 200
        result = _truncate_filename(name, max_bytes=200)
        assert len(result.encode("utf-8")) <= 200

    def test_ascii_over_limit(self):
        name = "a" * 300
        result = _truncate_filename(name, max_bytes=200)
        assert len(result.encode("utf-8")) <= 200
        assert len(result) == 200

    def test_chinese_multibyte_within_limit(self):
        name = "测试视频标题"
        result = _truncate_filename(name, max_bytes=200)
        assert result == name

    def test_chinese_multibyte_over_limit(self):
        name = "涩" * 100  # ~300 bytes
        result = _truncate_filename(name, max_bytes=200)
        encoded = result.encode("utf-8")
        assert len(encoded) <= 200
        # must be valid UTF-8 (no broken characters)
        result.encode("utf-8")

    def test_real_tweet_title(self):
        """The exact tweet title that caused Errno 36."""
        title = (
            "涩涩A哥 - 纪念母狗第一次喷水全过程！！！ "
            "小黄文看的太湿想学女主用屁屁，家里没合适的棒棒，"
            "就找了好久以前的秒潮想双管齐下，拿都拿出来了试一下吧，啊啊..."
        )
        assert len(title.encode("utf-8")) > 200

        result = _truncate_filename(title, max_bytes=200)
        result_bytes = len(result.encode("utf-8"))
        assert result_bytes <= 200
        assert result_bytes + 4 < 255  # + ".mp4"

    def test_mixed_ascii_and_multibyte(self):
        name = "Video_2024_" + "视" * 100
        result = _truncate_filename(name, max_bytes=200)
        assert len(result.encode("utf-8")) <= 200

    def test_custom_max_bytes(self):
        name = "a" * 100
        result = _truncate_filename(name, max_bytes=50)
        assert len(result.encode("utf-8")) <= 50

    def test_fallback_for_all_multibyte(self):
        name = "视" * 100
        result = _truncate_filename(name, max_bytes=200)
        assert len(result.encode("utf-8")) <= 200


# ── _format_duration / _format_size ───────────────────────────────────

class TestFormatDuration:
    def test_zero_seconds(self):
        assert _format_duration(0) == "未知"

    def test_seconds_only(self):
        assert _format_duration(45) == "0:45"

    def test_minutes_and_seconds(self):
        assert _format_duration(125) == "2:05"

    def test_hours(self):
        assert _format_duration(3661) == "1:01:01"


class TestFormatSize:
    def test_zero(self):
        assert _format_size(0) == "未知"

    def test_bytes(self):
        assert _format_size(500) == "500 B"

    def test_kilobytes(self):
        assert _format_size(2048) == "2 KB"

    def test_megabytes(self):
        assert _format_size(5_000_000) == "5 MB"

    def test_gigabytes(self):
        assert _format_size(2_000_000_000) == "2 GB"


# ── _enqueue_task: Telegram source ────────────────────────────────────

class TestEnqueueTaskTelegram:
    @pytest.mark.asyncio
    async def test_telegram_download(self):
        """Telegram single-file download task."""
        event = _make_event(chat_id=12345, message_id=1000)

        with patch(_DM_PATH) as mock_dm:
            mock_dm.add_task = AsyncMock(return_value="task-1")
            await _enqueue_task(
                event,
                info={
                    "title": "My Video",
                    "ext": "mp4",
                    "duration": 30,
                    "resolution": "1920x1080",
                    "filesize": 5000000,
                    "uploader": "TestChannel",
                    "message_id": 1000,
                },
                source_type="telegram",
                source_data={"channel_id": "-100123", "access_hash": 999},
                action="download",
            )

        mock_dm.add_task.assert_awaited_once()
        task = mock_dm.add_task.call_args[0][0]
        assert task["chat_id"] == "12345"
        assert task["media_type"] == "video"
        assert task["file_name"] == "My Video.mp4"
        assert task["task_data"]["action"] == "download"
        assert task["task_data"]["requester_chat_id"] == 12345

    @pytest.mark.asyncio
    async def test_telegram_forward(self):
        """Telegram task with forward target."""
        event = _make_event(chat_id=12345, message_id=1001)

        with patch(_DM_PATH) as mock_dm:
            mock_dm.add_task = AsyncMock(return_value="task-2")
            await _enqueue_task(
                event,
                info={
                    "title": "Video 2",
                    "ext": "mp4",
                    "duration": 60,
                    "resolution": "1280x720",
                    "filesize": 10000000,
                    "uploader": "SomeChannel",
                    "message_id": 1001,
                },
                source_type="telegram",
                source_data={"channel_id": "-100456"},
                action="forward",
                forward_target="@mychannel",
            )

        task = mock_dm.add_task.call_args[0][0]
        assert task["task_data"]["action"] == "forward"
        assert task["task_data"]["forward_target"] == "@mychannel"
        assert task["task_data"]["delete_after_forward"] is True


# ── _enqueue_task: Twitter/X source (the path that crashed) ───────────

class TestEnqueueTaskTwitter:
    @pytest.mark.asyncio
    async def test_twitter_download_no_source_data(self):
        """The exact scenario that crashed: source_data=None."""
        event = _make_event(chat_id=777)

        with patch(_DM_PATH) as mock_dm:
            mock_dm.add_task = AsyncMock(return_value="task-tw-1")
            # source_data=None is the crash trigger — must not raise
            await _enqueue_task(
                event,
                info={
                    "title": "涩涩A哥 - 纪念母狗第一次喷水全过程！！！...",
                    "ext": "mp4",
                    "duration": 338.6,
                    "resolution": "1280x720",
                    "filesize": 0,
                    "uploader": "XUser",
                    "source_url": "https://x.com/i/status/2077422862970085503",
                },
                source_type="twitter",
                source_data=None,  # ← this used to crash with AttributeError
                action="download",
            )

        mock_dm.add_task.assert_awaited_once()
        task = mock_dm.add_task.call_args[0][0]
        assert task["task_data"]["source_type"] == "external"
        assert task["task_data"]["source_url"] == "https://x.com/i/status/2077422862970085503"
        assert task["task_data"]["action"] == "download"
        assert task["chat_id"] == "777"
        # channel_id falls back to chat_id when source_data is None
        assert task["channel_id"] == "777"
        # message_id is an MD5 hex hash, not a Telegram message ID
        assert task["message_id"] != "0"
        assert len(task["message_id"]) == 12  # MD5[:12]

    @pytest.mark.asyncio
    async def test_twitter_filename_truncated(self):
        """Long tweet titles are truncated to avoid Errno 36."""
        event = _make_event(chat_id=777)
        long_title = "视" * 80  # 80 Chinese chars = ~240 bytes

        with patch(_DM_PATH) as mock_dm:
            mock_dm.add_task = AsyncMock()
            await _enqueue_task(
                event,
                info={
                    "title": long_title,
                    "ext": "mp4",
                    "duration": 60,
                    "resolution": "1920x1080",
                    "filesize": 5000000,
                    "uploader": "XUser",
                    "source_url": "https://x.com/user/status/123",
                },
                source_type="twitter",
                source_data={},
                action="download",
            )

        task = mock_dm.add_task.call_args[0][0]
        fname = task["file_name"]
        assert len(fname.encode("utf-8")) < 255
        assert fname.endswith(".mp4")

    @pytest.mark.asyncio
    async def test_twitter_forward_with_target(self):
        """Twitter download + forward with explicit target."""
        event = _make_event(chat_id=888)

        with patch(_DM_PATH) as mock_dm:
            mock_dm.add_task = AsyncMock()
            await _enqueue_task(
                event,
                info={
                    "title": "Test Tweet Video",
                    "ext": "mp4",
                    "duration": 30,
                    "resolution": "1280x720",
                    "filesize": 2000000,
                    "uploader": "XUser",
                    "source_url": "https://x.com/user/status/456",
                },
                source_type="twitter",
                source_data=None,
                action="forward",
                forward_target="@mychannel",
            )

        task = mock_dm.add_task.call_args[0][0]
        assert task["task_data"]["action"] == "forward"
        assert task["task_data"]["forward_target"] == "@mychannel"
        assert task["task_data"]["source_type"] == "external"

    @pytest.mark.asyncio
    async def test_twitter_cloud_action(self):
        """Twitter → cloud upload action."""
        event = _make_event(chat_id=999)

        with patch(_DM_PATH) as mock_dm:
            mock_dm.add_task = AsyncMock()
            await _enqueue_task(
                event,
                info={
                    "title": "Cloud Video",
                    "ext": "mp4",
                    "duration": 120,
                    "resolution": "1920x1080",
                    "filesize": 8000000,
                    "uploader": "XUser",
                    "source_url": "https://x.com/user/status/789",
                },
                source_type="twitter",
                source_data=None,
                action="cloud",
            )

        task = mock_dm.add_task.call_args[0][0]
        assert task["task_data"]["action"] == "cloud"


# ── handle_media_action ───────────────────────────────────────────────

class TestHandleMediaAction:
    @pytest.mark.asyncio
    async def test_auto_mode_enabled(self):
        """When default_action is enabled, skip preview and enqueue directly."""
        event = _make_event(chat_id=111)

        mock_da = MagicMock()
        mock_da.enabled = True
        mock_da.action = "download"
        mock_da.target_chat = ""

        info = {
            "title": "Auto Video",
            "ext": "mp4",
            "duration": 30,
            "resolution": "1280x720",
            "filesize": 1000000,
            "uploader": "Test",
            "source_url": "https://x.com/user/status/1",
        }

        with (
            patch(_CFG_PATH) as mock_config,
            patch(_DM_PATH) as mock_dm,
        ):
            mock_config.default_action = mock_da
            mock_dm.add_task = AsyncMock()

            await handle_media_action(
                event, info, source_type="twitter", source_data=None
            )

        event.respond.assert_called()
        calls = [c[0][0] for c in event.respond.call_args_list]
        combined = " ".join(str(c) for c in calls)
        assert "默认" in combined or "已加入队列" in combined

    @pytest.mark.asyncio
    async def test_interactive_mode_preview(self):
        """When default_action is disabled, show preview and set FSM state."""
        event = _make_event(chat_id=222)

        mock_da = MagicMock()
        mock_da.enabled = False
        mock_da.action = ""

        info = {
            "title": "Interactive Video",
            "ext": "mp4",
            "duration": 45,
            "resolution": "1920x1080",
            "filesize": 3000000,
            "uploader": "Channel1",
        }

        with (
            patch(_CFG_PATH) as mock_config,
            patch(_SM_PATH) as mock_sm,
        ):
            mock_config.default_action = mock_da
            mock_sm.set = AsyncMock()
            mock_sm.clear = AsyncMock()

            await handle_media_action(
                event, info, source_type="telegram", source_data={"channel_id": "-100"}
            )

        event.respond.assert_called()
        preview_text = event.respond.call_args_list[0][0][0]
        assert "1️⃣" in preview_text
        assert "2️⃣" in preview_text
        assert "3️⃣" in preview_text
        assert "4️⃣" in preview_text

        mock_sm.set.assert_awaited_once()
        state = mock_sm.set.call_args[0][1]
        assert state["command"] == "action"
        assert state["step"] == "choose"
        assert state["source_type"] == "telegram"


# ── handle_action_choice (FSM) ────────────────────────────────────────

class TestHandleActionChoice:
    @pytest.mark.asyncio
    async def test_choice_1_download(self):
        """User picks option 1: download only."""
        event = _make_event(chat_id=333, text="1")
        state = {
            "info": {
                "title": "PickMe",
                "ext": "mp4",
                "duration": 10,
                "resolution": "640x480",
                "filesize": 500000,
                "uploader": "X",
                "message_id": 99,
            },
            "source_type": "telegram",
            "source_data": {"channel_id": "-100"},
        }

        with (
            patch(_SM_PATH) as mock_sm,
            patch(_DM_PATH) as mock_dm,
        ):
            mock_sm.clear = AsyncMock()
            mock_sm.update = AsyncMock()
            mock_dm.add_task = AsyncMock()

            await handle_action_choice(event, state)

        mock_sm.clear.assert_awaited_once()
        mock_dm.add_task.assert_awaited_once()
        task = mock_dm.add_task.call_args[0][0]
        assert task["task_data"]["action"] == "download"
        assert task["task_data"]["requester_chat_id"] == 333

    @pytest.mark.asyncio
    async def test_choice_2_forward_asks_target(self):
        """User picks option 2: forward — should ask for target."""
        event = _make_event(chat_id=444, text="2")
        state = {
            "info": {
                "title": "Fwd",
                "ext": "mp4",
                "duration": 20,
                "resolution": "1280x720",
                "filesize": 2000000,
                "uploader": "X",
                "message_id": 50,
            },
            "source_type": "telegram",
            "source_data": {},
        }

        with (
            patch(_SM_PATH) as mock_sm,
            patch(_DM_PATH) as mock_dm,
        ):
            mock_sm.clear = AsyncMock()
            mock_sm.update = AsyncMock()
            mock_dm.add_task = AsyncMock()

            await handle_action_choice(event, state)

        # Should NOT clear state — updates to step="forward_target"
        mock_sm.clear.assert_not_called()
        mock_sm.update.assert_awaited_once()
        # update(chat_id, step=..., action=...) → call_args = ((chat_id,), {kw: val, ...})
        pos_args, kw_args = mock_sm.update.call_args
        assert pos_args[0] == event.chat_id
        assert kw_args["step"] == "forward_target"
        assert kw_args["action"] == "forward"
        # Should ask user for target
        event.respond.assert_called()
        assert "目标" in event.respond.call_args[0][0]

    @pytest.mark.asyncio
    async def test_choice_invalid(self):
        """User sends garbage — should prompt again."""
        event = _make_event(chat_id=555, text="xyz")
        state = {
            "info": {"title": "T", "ext": "mp4", "duration": 0, "resolution": "", "filesize": 0, "uploader": "", "message_id": 1},
            "source_type": "telegram",
            "source_data": {},
        }

        with (
            patch(_SM_PATH) as mock_sm,
            patch(_DM_PATH) as mock_dm,
        ):
            mock_sm.clear = AsyncMock()
            mock_dm.add_task = AsyncMock()

            await handle_action_choice(event, state)

        mock_sm.clear.assert_not_called()
        mock_dm.add_task.assert_not_called()
        event.respond.assert_called()
        assert "1-4" in event.respond.call_args[0][0]


# ── handle_action_target (FSM) ────────────────────────────────────────

class TestHandleActionTarget:
    @pytest.mark.asyncio
    async def test_target_forward(self):
        """User enters forward target → enqueue with target."""
        event = _make_event(chat_id=666, text="@mytarget")
        state = {
            "info": {
                "title": "TargetTest",
                "ext": "mp4",
                "duration": 90,
                "resolution": "1920x1080",
                "filesize": 7000000,
                "uploader": "Me",
                "source_url": "https://x.com/user/status/999",
            },
            "source_type": "twitter",
            "source_data": {},
            "action": "forward",
        }

        with (
            patch(_SM_PATH) as mock_sm,
            patch(_DM_PATH) as mock_dm,
        ):
            mock_sm.clear = AsyncMock()
            mock_dm.add_task = AsyncMock()

            await handle_action_target(event, state)

        mock_sm.clear.assert_awaited_once()
        mock_dm.add_task.assert_awaited_once()
        task = mock_dm.add_task.call_args[0][0]
        assert task["task_data"]["forward_target"] == "@mytarget"
        assert task["task_data"]["action"] == "forward"

    @pytest.mark.asyncio
    async def test_target_all_action(self):
        """Forward target for 'all' action."""
        event = _make_event(chat_id=777, text="-100123456")
        state = {
            "info": {
                "title": "AllTest",
                "ext": "mp4",
                "duration": 60,
                "resolution": "1280x720",
                "filesize": 5000000,
                "uploader": "Bot",
                "message_id": 200,
            },
            "source_type": "telegram",
            "source_data": {"channel_id": "-100xyz"},
            "action": "all",
        }

        with (
            patch(_SM_PATH) as mock_sm,
            patch(_DM_PATH) as mock_dm,
        ):
            mock_sm.clear = AsyncMock()
            mock_dm.add_task = AsyncMock()

            await handle_action_target(event, state)

        task = mock_dm.add_task.call_args[0][0]
        assert task["task_data"]["action"] == "all"
        assert task["task_data"]["forward_target"] == "-100123456"


# ── helpers ───────────────────────────────────────────────────────────

def _make_event(chat_id=123, text="", message_id=1):
    """Create a minimal mock Telethon event."""
    event = MagicMock()
    event.chat_id = chat_id
    event.text = text
    event.respond = AsyncMock()
    event.message = MagicMock()
    event.message.id = message_id
    event.message.file = None
    event.message.media = None
    return event
