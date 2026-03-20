from pathlib import Path
import sys


def get_bundle_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_path(*parts: str) -> Path:
    return get_bundle_root().joinpath(*parts)


def app_path(*parts: str) -> Path:
    return get_app_root().joinpath(*parts)
