import os
import re
import secrets
from pathlib import Path

from loguru import logger

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


PART_MARKER = ".__part__"


def part_path_for(target: Path) -> Path:
    """Return a unique sibling path to write into before the file is final.

    Two workers may legitimately want the same file name (the same
    ``video_123.mp4`` exists in many channels). Writing straight to the target
    would interleave both transfers into one corrupt file, so each download
    gets its own token. Stem and suffix are preserved because Telethon/yt-dlp
    derive the extension from them.
    """
    target = Path(target)
    token = secrets.token_hex(4)
    return target.with_name(f"{target.stem}{PART_MARKER}{token}{target.suffix}")


def part_token(path) -> str | None:
    """Token embedded by :func:`part_path_for`, or None for a normal path."""
    name = Path(path).name
    if PART_MARKER not in name:
        return None
    return name.split(PART_MARKER, 1)[1].split(".", 1)[0]


def is_part_file(path) -> bool:
    return PART_MARKER in Path(path).name


def strip_part_marker(path: Path) -> Path:
    """Undo :func:`part_path_for`, keeping any extension a downloader appended."""
    path = Path(path)
    name = path.name
    if PART_MARKER not in name:
        return path
    head, _, rest = name.partition(PART_MARKER)
    _, dot, ext = rest.partition(".")
    return path.parent / (f"{head}.{ext}" if dot else head)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return -1


def place_file_atomic(
    src: Path,
    desired: Path,
    *,
    known_size: int | None = None,
    reuse_same_size: bool = False,
    max_suffix: int = 999,
) -> Path:
    """Move ``src`` to ``desired`` without ever overwriting another file.

    ``os.link`` is atomic and raises ``FileExistsError`` when the name is taken,
    unlike ``Path.rename``/``os.replace`` which silently destroy the existing
    file. The loser of a race therefore keeps its bytes as ``name_1.ext`` instead
    of clobbering the winner.

    With ``reuse_same_size`` a pre-existing file of exactly the same size is
    treated as the same download and reused (the temp copy is dropped).
    """
    src = Path(src)
    desired = Path(desired)
    size = known_size if known_size is not None else _file_size(src)

    for index in range(max_suffix + 1):
        candidate = (
            desired
            if index == 0
            else desired.with_name(f"{desired.stem}_{index}{desired.suffix}")
        )
        try:
            os.link(src, candidate)
        except FileExistsError:
            if reuse_same_size and index == 0 and size > 0 and _file_size(candidate) == size:
                logger.info(f"Reusing identical file already on disk: {candidate.name}")
                src.unlink(missing_ok=True)
                return candidate
            continue
        except OSError as exc:
            # No hard-link support here (or cross-device): fall back to a move,
            # still refusing to touch an existing target.
            if candidate.exists():
                continue
            logger.debug(f"Hard link unavailable for {candidate.name} ({exc}); using replace")
            os.replace(src, candidate)
            return candidate
        src.unlink(missing_ok=True)
        return candidate

    raise RuntimeError(f"No free file name available for {desired}")


def safe_join_download_path(root: str | Path, file_name: str) -> Path:
    root_path = Path(root).expanduser().resolve()
    if not root or str(root).strip() in ("", ".", "./"):
        raise ValueError(
            "save_path is empty or resolves to the current directory. "
            "Set a dedicated download directory in config.yaml, e.g. save_path: ./downloads"
        )
    target = (root_path / sanitize_filename(file_name)).resolve()
    if target != root_path and root_path not in target.parents:
        raise ValueError("download path escapes configured save directory")
    return target
