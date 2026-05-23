"""Tests for telegram/auto_watch.py — WatchManager._message_file_info.

Uses types.SimpleNamespace instead of Telethon's Message to avoid
read-only property issues (msg.file is a property with no setter).
"""

from types import SimpleNamespace

from telegram.auto_watch import WatchManager


def _make_msg(
    msg_id: int = 1,
    video=False,
    photo=False,
    audio=False,
    voice=False,
    gif=False,
    document=False,
    file_name: str | None = None,
    file_size: int = 0,
):
    """Build a minimal Message-like namespace for testing static methods."""
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
    msg.message = ""
    # For chat attributes used by _message_file_info indirectly
    msg.chat_id = -100123
    msg.chat = SimpleNamespace(title="Test Channel", access_hash=12345)
    return msg


class TestWatchManagerMessageFileInfo:
    def test_video_with_name(self):
        msg = _make_msg(video=True, file_name="clip.mp4")
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "clip.mp4"
        assert mtype == "video"

    def test_video_without_name(self):
        msg = _make_msg(video=True, msg_id=42)
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "video_42.mp4"
        assert mtype == "video"

    def test_photo(self):
        msg = _make_msg(photo=True, msg_id=7)
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "photo_7.jpg"
        assert mtype == "photo"

    def test_audio_with_name(self):
        msg = _make_msg(audio=True, file_name="song.mp3")
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "song.mp3"
        assert mtype == "audio"

    def test_audio_without_name(self):
        msg = _make_msg(audio=True, msg_id=3)
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "audio_3.mp3"
        assert mtype == "audio"

    def test_voice(self):
        msg = _make_msg(voice=True, msg_id=9)
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "voice_9.ogg"
        assert mtype == "voice"

    def test_gif(self):
        msg = _make_msg(gif=True, file_name="animation.gif")
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "animation.gif"
        assert mtype == "animation"

    def test_document_with_name(self):
        msg = _make_msg(document=True, file_name="doc.pdf")
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "doc.pdf"
        assert mtype == "document"

    def test_document_without_name(self):
        msg = _make_msg(document=True, msg_id=5)
        name, mtype = WatchManager._message_file_info(msg)
        assert name == "doc_5"
        assert mtype == "document"

    def test_unknown_media(self):
        msg = _make_msg(msg_id=1)
        msg.media = True  # has media but no specific type
        msg.document = None
        msg.video = msg.photo = msg.audio = msg.voice = msg.gif = None
        name, mtype = WatchManager._message_file_info(msg)
        assert mtype == "unknown"
        assert "media" in name

    def test_empty_name_with_extension_resolves(self):
        """file.name with extension only (like '.mp4') — splitext('') case."""
        msg = _make_msg(video=True, file_name=".mp4")
        name, mtype = WatchManager._message_file_info(msg)
        # os.path.splitext('.mp4') == ('.mp4', '') — the stem is '.mp4' which is truthy
        assert mtype == "video"
