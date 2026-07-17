"""External video downloader using yt-dlp (Twitter/X, etc.)."""

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yt_dlp
from loguru import logger


class ExternalDownloader:
    """Download videos from external platforms via yt-dlp."""

    def __init__(self):
        self.cookies_file: Optional[str] = None
        # Auto-detect existing cookies file
        default_cookies = Path(__file__).resolve().parent.parent / "data" / "twitter_cookies.txt"
        if default_cookies.exists():
            self.cookies_file = str(default_cookies)
            logger.info(f"Found Twitter cookies file: {default_cookies}")

    @staticmethod
    def _has_ffmpeg() -> bool:
        return shutil.which("ffmpeg") is not None

    def _video_format(self) -> str:
        """Choose best format; fall back to single stream when ffmpeg is absent."""
        if self._has_ffmpeg():
            return "bestvideo+bestaudio/best"
        logger.warning("ffmpeg not found — falling back to single-stream 'best' format")
        return "best"

    def _yt_opts(self, extra: dict = None) -> dict:
        """Build yt-dlp options, including cookies if available."""
        opts: dict = {
            "quiet": True,
            "no_warnings": True,
            **(extra or {}),
        }
        if self.cookies_file and Path(self.cookies_file).exists():
            opts["cookiefile"] = self.cookies_file
        return opts

    @staticmethod
    def is_supported(url: str) -> bool:
        """Check if the URL is from a supported platform."""
        return bool(
            re.search(r"(?:twitter\.com|x\.com)/\w+/status/\d+", url)
        )

    async def extract_info(self, url: str) -> Dict[str, Any]:
        """Extract video metadata without downloading.

        Returns a dict with: title, duration, resolution, filesize, thumbnail.
        """
        loop = asyncio.get_event_loop()

        def _extract():
            opts = self._yt_opts({
                "extract_flat": False,
                "format": self._video_format(),
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await loop.run_in_executor(None, _extract)
        except Exception as e:
            raise RuntimeError(f"无法提取视频信息: {e}") from e

        if not info:
            raise RuntimeError("无法获取视频信息，请检查链接是否有效。")

        # yt-dlp may return a playlist — pick the first entry if so
        if "entries" in info:
            entries = info["entries"]
            if not entries:
                raise RuntimeError("链接中未找到视频。")
            info = entries[0]

        formats = info.get("formats") or []
        best_video = None
        for fmt in formats:
            if fmt.get("vcodec") and fmt.get("vcodec") != "none":
                best_video = fmt
                break

        width = (best_video or {}).get("width", 0) or 0
        height = (best_video or {}).get("height", 0) or 0
        resolution_str = f"{width}x{height}" if width and height else "未知"

        duration = float(info.get("duration") or 0)

        return {
            "title": info.get("title") or "未知标题",
            "duration": duration,
            "resolution": resolution_str,
            "width": width,
            "height": height,
            "filesize": info.get("filesize_approx") or info.get("filesize") or 0,
            "thumbnail": info.get("thumbnail") or "",
            "ext": info.get("ext") or "mp4",
            "uploader": info.get("uploader") or "",
            "description": (info.get("description") or "")[:200],
        }

    async def download(
        self,
        url: str,
        save_path: str,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        """Download a video from URL to save_path.

        Returns the actual file path (yt-dlp may add extension).
        """
        save_path = str(save_path)
        outtmpl = save_path

        loop = asyncio.get_event_loop()

        last_downloaded: int = 0
        last_total: int = 0

        def _progress_hook(d: dict):
            nonlocal last_downloaded, last_total
            if d.get("status") == "downloading":
                last_downloaded = d.get("downloaded_bytes", 0) or 0
                last_total = (d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            elif d.get("status") == "finished":
                last_downloaded = last_total = d.get("total_bytes", 0) or 0

        async def _fire_progress():
            if progress_callback:
                total = last_total if last_total > 0 else last_downloaded * 2
                await progress_callback(last_downloaded, max(total, 1))

        def _download():
            opts = self._yt_opts({
                "outtmpl": outtmpl,
                "format": self._video_format(),
                "progress_hooks": [_progress_hook],
                "merge_output_format": "mp4",
                "noplaylist": True,
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        # Run download in executor; poll progress from the main thread
        download_task = loop.run_in_executor(None, _download)

        while not download_task.done():
            await asyncio.sleep(0.5)
            await _fire_progress()

        # Ensure any exception is raised
        await download_task
        # Final progress update
        await _fire_progress()

        # yt-dlp may still be merging/renaming the temp file; wait, check periodically
        for _ in range(10):
            actual_path = self._find_downloaded_file(save_path)
            if actual_path and actual_path.stat().st_size > 0:
                break
            await asyncio.sleep(1)

        # yt-dlp may have added an extension; find the actual file
        actual_path = self._find_downloaded_file(save_path)
        if actual_path is None:
            raise RuntimeError(f"下载完成但找不到文件: {save_path}")

        file_size = actual_path.stat().st_size
        if file_size == 0:
            actual_path.unlink(missing_ok=True)
            raise RuntimeError("下载的文件为空。可能原因：链接需要登录、cookies 过期、或视频已被删除。")

        logger.info(
            f"External download complete: {actual_path.name} ({file_size} bytes)"
        )
        return str(actual_path)

    @staticmethod
    def _find_downloaded_file(save_path: str) -> Optional[Path]:
        """Find the downloaded file; yt-dlp may have changed the name or extension."""
        target = Path(save_path)
        if target.exists() and target.stat().st_size > 0:
            return target

        # yt-dlp may save to a different name; search by stem prefix
        parent = target.parent
        stem = target.stem
        best = None
        for f in sorted(parent.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if not f.is_file():
                continue
            if f.stem == stem and f.stat().st_size > 0:
                return f
            # Also match files that contain the original stem (yt-dlp suffixes)
            if stem in f.stem and f.stat().st_size > 0:
                best = best or f
        return best


external_downloader = ExternalDownloader()
