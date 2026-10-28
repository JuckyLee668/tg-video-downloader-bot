"""Regression tests for the fixes applied during the server deployment."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.database import DatabaseManager
from telegram.handlers.download import _msg_to_info


def _fake_message(**kwargs):
    msg = SimpleNamespace(
        id=kwargs.pop("id", 42),
        file=kwargs.pop("file", None),
        document=kwargs.pop("document", None),
        video=kwargs.pop("video", None),
        photo=kwargs.pop("photo", None),
        audio=kwargs.pop("audio", None),
        voice=kwargs.pop("voice", None),
        gif=kwargs.pop("gif", None),
        sticker=kwargs.pop("sticker", None),
        chat=kwargs.pop("chat", None),
    )
    for key, value in kwargs.items():
        setattr(msg, key, value)
    return msg


def test_msg_to_info_does_not_double_extension():
    msg = _fake_message(
        file=SimpleNamespace(name="clip.mp4", size=123, mime_type="video/mp4")
    )
    info = _msg_to_info(msg)
    assert info["title"] == "clip"
    assert info["ext"] == "mp4"
    assert info["media_type"] == "video"


def test_msg_to_info_photo_without_filename_uses_jpg():
    msg = _fake_message(photo=SimpleNamespace(), file=SimpleNamespace(name="", size=1))
    info = _msg_to_info(msg)
    assert info["ext"] == "jpg"
    assert info["media_type"] == "photo"
    # Caller appends ".<ext>", so the title itself must not carry an extension.
    assert info["title"] == "media_42"


async def test_watch_state_allows_two_owners_on_same_channel(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "db.sqlite"))
    await db.init()
    try:
        await db.update_watch_state("owner-1", "-100123", 10)
        await db.update_watch_state("owner-2", "-100123", 20)

        cursor = await db._db.execute(
            "SELECT owner_chat_id, last_read_id FROM watch_state WHERE channel_id = ?",
            ("-100123",),
        )
        rows = await cursor.fetchall()
        assert {row["owner_chat_id"] for row in rows} == {"owner-1", "owner-2"}
    finally:
        await db.close()


async def test_requeue_with_delay_is_not_claimed_immediately(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "db.sqlite"))
    await db.init()
    try:
        task_id = await db.add_download_task(
            {"chat_id": "1", "message_id": "2", "file_name": "v.mp4"}
        )
        task = await db.claim_next_task()
        assert task["task_id"] == task_id

        await db.requeue_task(task_id, "Flood wait", delay_seconds=60)
        assert await db.claim_next_task() is None

        # Make the delay expire and verify the task becomes claimable again.
        await db._db.execute(
            "UPDATE download_queue SET next_retry_at = datetime('now', '-1 second')"
        )
        await db._db.commit()
        again = await db.claim_next_task()
        assert again["task_id"] == task_id
    finally:
        await db.close()


async def test_concurrent_writes_do_not_break_single_connection(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "db.sqlite"))
    await db.init()
    try:
        async def add(i: int):
            return await db.add_download_task(
                {"chat_id": str(i), "message_id": str(i), "file_name": f"{i}.mp4"}
            )

        ids = await asyncio.gather(*(add(i) for i in range(20)))
        assert len(set(ids)) == 20

        claimed = await asyncio.gather(*(db.claim_next_task() for _ in range(20)))
        assert len([task for task in claimed if task]) == 20
    finally:
        await db.close()


async def test_history_dedup_uses_source_channel(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "db.sqlite"))
    await db.init()
    try:
        await db.add_download_history(
            {
                "chat_id": "user-private-chat",
                "channel_id": "-100999",
                "message_id": "7",
                "file_name": "v.mp4",
                "file_size": 1,
            }
        )
        assert await db.is_already_downloaded("-100999", "7")
        assert not await db.is_already_downloaded("user-private-chat", "7")
    finally:
        await db.close()


# ── account switching guards ────────────────────────────────────────────

class _FakeEvent:
    def __init__(self):
        self.replies: list[str] = []

    async def respond(self, text, *args, **kwargs):
        self.replies.append(text)


class _FakeUserClient:
    def __init__(self, authorized=True):
        self._authorized = authorized
        self.logged_out = False
        self.code_requests = 0

    async def is_user_authorized(self):
        return self._authorized

    async def get_me(self):
        if not self._authorized:
            return None
        return SimpleNamespace(
            id=7145393228, first_name="Stars", last_name="", username="stars"
        )

    async def log_out(self):
        self.logged_out = True
        self._authorized = False

    async def connect(self):
        pass

    def is_connected(self):
        return True

    async def send_code_request(self, *args, **kwargs):
        self.code_requests += 1
        raise AssertionError("must not request a code while logged in")


async def test_send_code_refused_when_already_authorized(monkeypatch):
    """auth.sendCode from a live key drops the login (AUTH_RESTART) — never do it."""
    import pytest

    from telegram import client as client_module

    wrapper = client_module.TelegramClientWrapper()
    fake = _FakeUserClient(authorized=True)

    async def fake_ensure():
        wrapper.user_client = fake
        return fake

    monkeypatch.setattr(wrapper, "_ensure_user_client", fake_ensure)

    with pytest.raises(RuntimeError) as exc:
        await wrapper.send_code("+447561450377")

    assert "已登录" in str(exc.value)
    assert fake.code_requests == 0


async def test_login_with_phone_does_not_send_code_when_logged_in(monkeypatch):
    from telegram.handlers import auth

    fake = _FakeUserClient(authorized=True)
    monkeypatch.setattr(auth.tg_clients, "user_client", fake)

    sent: list[str] = []

    async def fake_send_code(event, phone):
        sent.append(phone)

    monkeypatch.setattr(auth, "_send_code", fake_send_code)

    event = _FakeEvent()
    await auth.login_handler(event, "+447561450377")

    assert sent == []
    assert "已登录" in event.replies[0]
    assert "stars" in event.replies[0]


async def test_logout_removes_session_file(monkeypatch, tmp_path: Path):
    from telegram.handlers import auth

    fake = _FakeUserClient(authorized=True)
    monkeypatch.setattr(auth.tg_clients, "user_client", fake)

    session = tmp_path / "session.txt"
    session.write_text("stale")
    monkeypatch.setattr(auth.tg_clients, "session_file", str(session))

    event = _FakeEvent()
    await auth.logout_handler(event)

    assert fake.logged_out is True
    assert not session.exists()
    assert "已退出登录" in event.replies[0]


class _ResolveClient:
    """Minimal stand-in for a Telethon client used by resolve_input_entity."""

    def __init__(self, known_ints=(), dialogs=()):
        self.known_ints = set(known_ints)
        self.dialogs = list(dialogs)
        self.requested = []

    async def get_input_entity(self, peer):
        self.requested.append(peer)
        if isinstance(peer, int):
            if peer in self.known_ints:
                return f"peer:{peer}"
            raise ValueError(f"Cannot find any entity corresponding to {peer!r}")
        return f"input:{peer}"

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog


async def test_resolve_input_entity_coerces_numeric_id():
    """A numeric channel id must not be looked up as a username."""
    from telegram.search import resolve_input_entity

    client = _ResolveClient(known_ints={-1002175222990})

    assert await resolve_input_entity(client, " -1002175222990 ") == "peer:-1002175222990"
    assert client.requested == [-1002175222990]


async def test_resolve_input_entity_accepts_bare_channel_id():
    from telegram.search import resolve_input_entity

    client = _ResolveClient(known_ints={-1002175222990})

    assert await resolve_input_entity(client, "2175222990") == "peer:-1002175222990"
    assert -1002175222990 in client.requested


async def test_resolve_input_entity_falls_back_to_dialog_scan():
    from telegram.search import resolve_input_entity

    dialog = SimpleNamespace(id=-100999, entity="dialog-entity")
    client = _ResolveClient(dialogs=[dialog])

    assert await resolve_input_entity(client, "-100999") == "input:dialog-entity"


async def test_resolve_input_entity_keeps_usernames_and_links():
    from telegram.search import resolve_input_entity

    client = _ResolveClient()

    assert await resolve_input_entity(client, "@some_channel") == "input:some_channel"
    assert await resolve_input_entity(client, "https://t.me/some_channel") == "input:some_channel"


async def test_forwarding_status_clears_stale_error(tmp_path: Path):
    """The queue must show "forwarding" (and drop the previous error) while uploading."""
    db = DatabaseManager(str(tmp_path / "db.sqlite"))
    await db.init()
    try:
        task_id = await db.add_download_task(
            {"chat_id": "1", "message_id": "2", "file_name": "v.mp4"}
        )
        await db.update_task_status(task_id, "failed", "User client is required for forwarding")
        await db.update_task_status(task_id, "forwarding")

        cursor = await db._db.execute(
            "SELECT status, error_message FROM download_queue WHERE task_id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "forwarding"
        assert not row["error_message"]
    finally:
        await db.close()


async def test_client_logout_clears_state(monkeypatch, tmp_path: Path):
    from telegram import client as client_module

    wrapper = client_module.TelegramClientWrapper()
    fake = _FakeUserClient(authorized=True)
    wrapper.user_client = fake
    session = tmp_path / "session.txt"
    session.write_text("stale")
    wrapper.session_file = str(session)
    wrapper.qr_status = "authorized"

    assert await wrapper.logout() is True
    assert fake.logged_out is True
    assert not session.exists()
    assert wrapper.qr_status == "idle"
