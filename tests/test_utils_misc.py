"""Tests for telegram/handlers/utils.py — message_file_info edge cases."""

from types import SimpleNamespace

from telegram.handlers.utils import message_file_info


def _make_msg(
    msg_id: int = 1,
    video=False, photo=False, audio=False, voice=False, gif=False, document=False,
    file_name=None, file_size=0,
):
    msg = SimpleNamespace()
    msg.id = msg_id
    msg.file = SimpleNamespace(name=file_name, size=file_size) if file_name or file_size else None
    msg.video = SimpleNamespace() if video else None
    msg.photo = SimpleNamespace() if photo else None
    msg.audio = SimpleNamespace() if audio else None
    msg.voice = SimpleNamespace() if voice else None
    msg.gif = SimpleNamespace() if gif else None
    msg.document = SimpleNamespace() if document else None
    msg.media = bool(video or photo or audio or voice or gif or document)
    msg.chat_id = -100123
    msg.chat = SimpleNamespace(title="Test")
    return msg


class TestMessageFileInfoMisc:
    def test_no_media_returns_unknown(self):
        msg = _make_msg(msg_id=99)
        name, mtype = message_file_info(msg)
        assert mtype == "unknown"
        assert "media" in name

    def test_file_without_name_uses_fallback(self):
        msg = _make_msg(video=True, msg_id=5)
        name, mtype = message_file_info(msg)
        assert name == "video_5.mp4"

    def test_photo_overrides_video(self):
        """video check comes first, so video wins when both are set."""
        msg = _make_msg(video=True, photo=True, msg_id=3)
        name, mtype = message_file_info(msg)
        assert mtype == "video"

    def test_document_with_name_keeps_name(self):
        msg = _make_msg(document=True, file_name="report.pdf", msg_id=10)
        name, mtype = message_file_info(msg)
        assert name == "report.pdf"
        assert mtype == "document"

    def test_video_with_dot_only_name(self):
        """file_name like '.mp4' — os.path.splitext('.mp4') == ('.mp4', '')."""
        msg = _make_msg(video=True, file_name=".mp4")
        name, mtype = message_file_info(msg)
        # stem is '.mp4' (truthy), so it's treated as a valid name
        assert name == ".mp4"
        assert mtype == "video"

    def test_video_with_empty_string_name(self):
        msg = _make_msg(video=True, file_name="", msg_id=7)
        name, mtype = message_file_info(msg)
        assert name == "video_7.mp4"
        assert mtype == "video"

    def test_animation_gif(self):
        msg = _make_msg(gif=True, file_name="funny.gif", msg_id=12)
        name, mtype = message_file_info(msg)
        assert name == "funny.gif"
        assert mtype == "animation"

    def test_voice_no_name(self):
        msg = _make_msg(voice=True, msg_id=8)
        name, mtype = message_file_info(msg)
        assert name == "voice_8.ogg"
        assert mtype == "voice"
