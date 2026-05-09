"""
阿里云盘上传模块
下载完成后自动上传文件到阿里云盘指定目录
"""

import asyncio
import os
import shutil
from pathlib import Path

from loguru import logger


class AliyunDriveUploader:
    """Upload downloaded files to Aliyun Drive via aliyunpan CLI."""

    def __init__(self, enabled: bool = False, remote_path: str = "/video", delete_after_upload: bool = False):
        self.enabled = enabled
        self.remote_path = remote_path.rstrip("/")
        self.delete_after_upload = delete_after_upload
        self._aliyunpan_path: str | None = None

    async def _find_aliyunpan(self) -> str | None:
        """Locate the aliyunpan binary."""
        if self._aliyunpan_path:
            return self._aliyunpan_path

        candidates = [
            shutil.which("aliyunpan"),
            "/usr/local/bin/aliyunpan",
            "/usr/bin/aliyunpan",
        ]
        for path in candidates:
            if path and os.path.isfile(path) and os.access(path, os.X_OK):
                self._aliyunpan_path = path
                return path
        return None

    async def upload(self, local_path: str | Path) -> bool:
        """
        Upload a local file to Aliyun Drive remote_path.

        Returns True if upload succeeded, False otherwise.
        """
        if not self.enabled:
            logger.debug("AliyunDrive upload is disabled, skipping")
            return False

        local_path = Path(local_path)
        if not local_path.exists() or not local_path.is_file():
            logger.warning(f"AliyunDrive upload skipped: file not found: {local_path}")
            return False

        aliyunpan = await self._find_aliyunpan()
        if not aliyunpan:
            logger.error("AliyunDrive upload failed: aliyunpan CLI not found")
            return False

        remote_dir = f"{self.remote_path}/"
        logger.info(f"Uploading {local_path.name} to Aliyun Drive {remote_dir}...")

        try:
            proc = await asyncio.create_subprocess_exec(
                aliyunpan,
                "upload",
                str(local_path),
                remote_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

            output = stdout.decode("utf-8", errors="replace")
            if proc.returncode == 0 and "成功" in output:
                logger.info(f"AliyunDrive upload succeeded: {local_path.name} -> {remote_dir}")
                if self.delete_after_upload:
                    try:
                        local_path.unlink(missing_ok=True)
                        logger.info(f"Deleted local file after upload: {local_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete local file {local_path}: {e}")
                return True
            else:
                stderr_text = stderr.decode("utf-8", errors="replace")
                logger.error(
                    f"AliyunDrive upload failed (exit={proc.returncode}): "
                    f"{stderr_text or output}"
                )
                return False

        except asyncio.TimeoutError:
            logger.error(f"AliyunDrive upload timed out after 600s: {local_path.name}")
            return False
        except Exception as e:
            logger.error(f"AliyunDrive upload error: {e}")
            return False


aliyundrive_uploader = AliyunDriveUploader()
