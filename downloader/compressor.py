"""FFmpeg-based video compression for files exceeding Telegram's 2GB limit."""

import asyncio
import shutil
from pathlib import Path
from typing import Callable, Optional

from loguru import logger


class VideoCompressor:
    """Compress video files using ffmpeg to reduce size below a target threshold."""

    def is_available(self) -> bool:
        """Check if ffmpeg is available on the system."""
        return shutil.which("ffmpeg") is not None

    def has_ffprobe(self) -> bool:
        """Check if ffprobe is available for metadata extraction."""
        return shutil.which("ffprobe") is not None

    def get_video_info(self, file_path: str) -> dict:
        """Extract video metadata using ffprobe.

        Returns dict with: duration_sec, width, height, video_bitrate, audio_bitrate, codec.
        """
        import json
        import subprocess

        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning(f"ffprobe failed: {result.stderr.strip()}")
                return {}
            data = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"ffprobe error: {e}")
            return {}

        info: dict = {"duration_sec": 0.0, "width": 0, "height": 0,
                      "video_bitrate": 0, "audio_bitrate": 0, "codec": ""}

        fmt = data.get("format", {})
        if fmt.get("duration"):
            info["duration_sec"] = float(fmt["duration"])

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                info["width"] = stream.get("width", 0) or 0
                info["height"] = stream.get("height", 0) or 0
                info["codec"] = stream.get("codec_name", "")
                if stream.get("bit_rate"):
                    info["video_bitrate"] = int(stream["bit_rate"])
            elif stream.get("codec_type") == "audio":
                if stream.get("bit_rate"):
                    info["audio_bitrate"] = int(stream["bit_rate"])

        return info

    def estimate_target_bitrate(self, file_path: str, target_size_bytes: int) -> int:
        """Calculate the target video bitrate needed to fit within target_size_bytes.

        Returns bitrate in bits/sec. Accounts for audio at 128k.
        """
        info = self.get_video_info(file_path)
        duration = info.get("duration_sec", 0)
        if duration <= 0:
            # Fallback: use file size as rough estimate
            logger.warning("Cannot determine video duration; using conservative bitrate estimate")
            return 2_000_000  # 2 Mbps conservative default

        # Reserve 10% for container overhead
        available_bytes = int(target_size_bytes * 0.9)
        # Audio: 128 kbps
        audio_bytes = int(128_000 / 8 * duration)
        video_bytes = max(1, available_bytes - audio_bytes)
        video_bitrate = int(video_bytes * 8 / duration)
        # Clamp to reasonable range
        video_bitrate = max(500_000, min(video_bitrate, 50_000_000))
        logger.info(
            f"Estimated target video bitrate: {video_bitrate // 1000} kbps "
            f"(duration={duration:.1f}s, target_size={target_size_bytes / 1024 / 1024:.0f}MB)"
        )
        return video_bitrate

    async def compress_video(
        self,
        file_path: str,
        target_size_bytes: int,
        progress_callback: Optional[Callable] = None,
        crf: int = 23,
        preset: str = "fast",
        max_bitrate: str = "",
    ) -> Path:
        """Compress a video file to fit within target_size_bytes.

        Uses CRF-based encoding by default. If max_bitrate is provided, uses
        two-pass bitrate-limited encoding instead.

        Returns the path to the compressed file (replaces original).
        """
        if not self.is_available():
            raise RuntimeError("ffmpeg is not installed; cannot compress video")

        original = Path(file_path)
        if not original.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        original_size = original.stat().st_size
        if original_size <= target_size_bytes:
            logger.info(f"File is already under target size ({original_size} bytes), no compression needed")
            return original

        temp_output = original.parent / f"{original.stem}_compressed{original.suffix or '.mp4'}"
        # Use .mp4 container for output compatibility
        if not temp_output.suffix or temp_output.suffix.lower() not in (".mp4", ".mkv", ".mov", ".avi", ".webm"):
            temp_output = temp_output.with_suffix(".mp4")

        loop = asyncio.get_event_loop()

        if max_bitrate:
            await self._compress_with_bitrate(
                str(original), str(temp_output), max_bitrate, preset, progress_callback, loop
            )
        else:
            await self._compress_with_crf(
                str(original), str(temp_output), crf, target_size_bytes, preset, progress_callback, loop
            )

        # Verify output
        if not temp_output.exists() or temp_output.stat().st_size == 0:
            raise RuntimeError(f"Compression produced empty or missing file: {temp_output}")

        compressed_size = temp_output.stat().st_size
        reduction = (1 - compressed_size / original_size) * 100
        logger.info(
            f"Compression complete: {original_size / 1024 / 1024:.0f}MB -> "
            f"{compressed_size / 1024 / 1024:.0f}MB ({reduction:.1f}% reduction)"
        )

        # Replace original with compressed
        original.unlink()
        # Use os.rename for atomic cross-filesystem moves
        import os
        final_path = original.parent / original.name
        os.rename(str(temp_output), str(final_path))

        # Fire final progress
        if progress_callback:
            await progress_callback(compressed_size, compressed_size)

        return final_path

    async def _compress_with_crf(
        self,
        input_path: str,
        output_path: str,
        crf: int,
        target_size_bytes: int,
        preset: str,
        progress_callback: Optional[Callable],
        loop,
    ):
        """Compress using CRF encoding. Retries with higher CRF if still too large (up to 3 attempts)."""
        current_crf = crf
        for attempt in range(3):
            logger.info(f"CRF compression attempt {attempt + 1} (CRF={current_crf}, preset={preset})")
            await self._run_ffmpeg_crf(input_path, output_path, current_crf, preset, progress_callback, loop)

            out_path = Path(output_path)
            if not out_path.exists() or out_path.stat().st_size == 0:
                raise RuntimeError("CRF compression produced empty output")

            if out_path.stat().st_size <= target_size_bytes:
                return  # Success

            # Still too large; try higher CRF
            current_crf = min(51, current_crf + 5)
            logger.warning(
                f"Compressed size ({out_path.stat().st_size / 1024 / 1024:.0f}MB) "
                f"still over target ({target_size_bytes / 1024 / 1024:.0f}MB), "
                f"retrying with CRF={current_crf}"
            )
            # Remove the too-large output before retry
            out_path.unlink(missing_ok=True)

        # Last attempt already tried; if we get here the last attempt was still too large
        logger.warning("Compressed file still exceeds target after all CRF attempts")

    async def _compress_with_bitrate(
        self,
        input_path: str,
        output_path: str,
        max_bitrate: str,
        preset: str,
        progress_callback: Optional[Callable],
        loop,
    ):
        """Compress using max bitrate + buffer size constraint."""
        args = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-c:v", "libx264",
            "-maxrate", max_bitrate,
            "-bufsize", max_bitrate,
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-preset", preset,
            "-progress", "pipe:1",
            "-nostats",
            output_path,
        ]
        await self._run_ffmpeg(args, progress_callback, loop)

    async def _run_ffmpeg_crf(
        self,
        input_path: str,
        output_path: str,
        crf: int,
        preset: str,
        progress_callback: Optional[Callable],
        loop,
    ):
        """Run ffmpeg with CRF encoding."""
        args = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-c:v", "libx264",
            "-crf", str(crf),
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-preset", preset,
            "-progress", "pipe:1",
            "-nostats",
            output_path,
        ]
        await self._run_ffmpeg(args, progress_callback, loop)

    async def _run_ffmpeg(
        self,
        args: list,
        progress_callback: Optional[Callable],
        loop,
    ):
        """Execute ffmpeg with progress parsing.

        Parses ffmpeg's `-progress pipe:1` output for `out_time_us` values,
        and uses the video duration to calculate percentage progress.
        """
        import os

        total_duration_us = 0
        # Try to get duration from ffprobe first
        input_path = None
        for i, arg in enumerate(args):
            if arg == "-i" and i + 1 < len(args):
                input_path = args[i + 1]
                break
        if input_path:
            info = self.get_video_info(input_path)
            total_duration_us = int(info.get("duration_sec", 0) * 1_000_000)

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        # Read progress from stdout, errors from stderr
        last_progress = -1
        last_update_time = 0.0
        import time

        async def read_stderr():
            """Read stderr in the background to prevent buffer deadlock."""
            try:
                while True:
                    line = await process.stderr.readline()
                    if not line:
                        break
            except Exception:
                pass

        stderr_task = asyncio.ensure_future(read_stderr())

        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break

                line_str = line.decode("utf-8", errors="replace").strip()
                if line_str.startswith("out_time_us="):
                    try:
                        out_time_us = int(line_str.split("=")[1])
                        if total_duration_us > 0 and progress_callback:
                            progress = int(out_time_us / total_duration_us * 100)
                            current_time = time.time()
                            if progress != last_progress and (progress > last_progress or (current_time - last_update_time) > 2):
                                last_progress = progress
                                last_update_time = current_time
                                await progress_callback(
                                    min(out_time_us, total_duration_us),
                                    total_duration_us,
                                )
                    except (ValueError, IndexError):
                        pass

            # Wait for process to complete
            await process.wait()
        finally:
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg exited with code {process.returncode}")


compressor = VideoCompressor()
