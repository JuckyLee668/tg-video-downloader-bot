from pathlib import Path

from core.database import DatabaseManager


async def test_claim_next_task_is_atomic(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "telegram_downloader.db"))
    await db.init()
    task_id = await db.add_download_task({
        "chat_id": "1",
        "message_id": "2",
        "file_name": "video.mp4",
        "file_size": 10,
    })

    first = await db.claim_next_task()
    second = await db.claim_next_task()

    assert first["task_id"] == task_id
    assert first["status"] == "downloading"
    assert second is None


async def test_complete_download_task_archives_and_deletes_queue_item(tmp_path: Path):
    db = DatabaseManager(str(tmp_path / "telegram_downloader.db"))
    await db.init()
    task_id = await db.add_download_task({
        "chat_id": "1",
        "message_id": "2",
        "file_name": "video.mp4",
        "file_size": 10,
    })
    task = await db.claim_next_task()

    await db.complete_download_task(task, {"download_path": str(tmp_path / "video.mp4")})

    assert await db.get_task_by_id(task_id) is None
    history = await db.get_history_list()
    assert history["total"] == 1
    assert history["items"][0]["file_name"] == "video.mp4"
