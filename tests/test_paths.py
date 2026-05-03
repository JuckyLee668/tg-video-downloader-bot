from pathlib import Path

from core.paths import safe_join_download_path, sanitize_filename


def test_sanitize_filename_removes_path_segments():
    assert sanitize_filename("../unsafe/name.mp4") == "name.mp4"


def test_sanitize_filename_handles_windows_reserved_names():
    assert sanitize_filename("CON.txt") == "_CON.txt"


def test_safe_join_stays_inside_root(tmp_path: Path):
    target = safe_join_download_path(tmp_path, "../video.mp4")
    assert target == tmp_path.resolve() / "video.mp4"
