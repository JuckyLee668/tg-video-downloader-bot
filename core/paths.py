import re
from pathlib import Path

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(file_name: str, fallback: str = "download.bin", max_length: int = 180) -> str:
    raw_name = Path(str(file_name or fallback)).name.strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")
    if not cleaned:
        cleaned = fallback

    stem = Path(cleaned).stem
    suffix = Path(cleaned).suffix
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"

    cleaned = f"{stem}{suffix}"
    if len(cleaned) > max_length:
        suffix = Path(cleaned).suffix
        stem = Path(cleaned).stem[: max_length - len(suffix)]
        cleaned = f"{stem}{suffix}"
    return cleaned


def safe_join_download_path(root: str | Path, file_name: str) -> Path:
    root_path = Path(root).expanduser().resolve()
    target = (root_path / sanitize_filename(file_name)).resolve()
    if target != root_path and root_path not in target.parents:
        raise ValueError("download path escapes configured save directory")
    return target
