from unittest.mock import patch

import pytest

from downloader.external import ExternalDownloader


class TestExternalDownloader:
    def testis_supported_twitter(self):
        assert ExternalDownloader.is_supported(
            "https://twitter.com/user/status/123456789"
        )
        assert ExternalDownloader.is_supported(
            "https://x.com/user/status/123456789"
        )
        assert ExternalDownloader.is_supported(
            "https://mobile.twitter.com/user/status/123456789/video/1"
        )

    def testis_supported_other(self):
        assert not ExternalDownloader.is_supported(
            "https://youtube.com/watch?v=abc"
        )
        assert not ExternalDownloader.is_supported(
            "https://t.me/c/123/456"
        )

    @pytest.mark.asyncio
    async def test_extract_info_mocked(self):
        """Test extract_info with mocked yt-dlp."""
        mock_info = {
            "title": "Test Video",
            "duration": 30.0,
            "width": 1920,
            "height": 1080,
            "ext": "mp4",
            "filesize_approx": 5000000,
            "uploader": "testuser",
            "description": "A test video",
            "formats": [
                {"vcodec": "avc1", "width": 1920, "height": 1080},
                {"vcodec": "none", "width": 0, "height": 0},
            ],
        }

        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.return_value = (
                mock_info
            )

            downloader = ExternalDownloader()
            result = await downloader.extract_info(
                "https://x.com/user/status/123"
            )

        assert result["title"] == "Test Video"
        assert result["duration"] == 30.0
        assert result["resolution"] == "1920x1080"
        assert result["filesize"] == 5000000
        assert result["uploader"] == "testuser"
        assert result["ext"] == "mp4"

    @pytest.mark.asyncio
    async def test_extract_info_playlist(self):
        """Test extract_info when yt-dlp returns a playlist."""
        mock_info = {
            "entries": [
                {
                    "title": "Video in playlist",
                    "duration": 10.0,
                    "ext": "mp4",
                    "formats": [],
                }
            ]
        }

        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.return_value = (
                mock_info
            )

            downloader = ExternalDownloader()
            result = await downloader.extract_info(
                "https://x.com/user/status/123"
            )

        assert result["title"] == "Video in playlist"
