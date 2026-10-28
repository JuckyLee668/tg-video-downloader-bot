"""Link detection for Twitter/X posts.

Users paste links in every shape the apps produce, often wrapped in words, so
detection must accept the real variants and hand yt-dlp a bare URL.
"""

import pytest

from downloader.external import ExternalDownloader as E


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/user/status/1234567890",
        "https://twitter.com/user/status/1234567890?s=20",
        "https://x.com/i/status/1234567890",
        "https://x.com/i/web/status/1234567890",  # shape the X app copies
        "x.com/user/status/1234567890",  # no scheme
        "https://mobile.twitter.com/user/status/1234567890",
        "https://vxtwitter.com/user/status/1234567890",
        "https://x.com/user/status/1234567890/photo/1",
        "https://x.com/user/statuses/1234567890",  # legacy path
    ],
)
def test_supported_links(url):
    assert E.is_supported(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://t.me/c/123/456",
        "https://youtube.com/watch?v=abc",
        "https://netflix.com/watch/123",  # must not match a bare "x.com" substring
        "https://x.com/someuser",  # profile, not a post
        "https://example.com/user/status/123",
    ],
)
def test_unsupported_links(url):
    assert not E.is_supported(url)


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "帮我下这个 https://x.com/user/status/1234567890 谢谢",
            "https://x.com/user/status/1234567890",
        ),
        ("看看https://x.com/i/web/status/99", "https://x.com/i/web/status/99"),
        (
            "https://example.com/a https://x.com/b/status/7 tail",
            "https://x.com/b/status/7",
        ),
        ("x.com/user/status/777", "x.com/user/status/777"),
        # Trailing punctuation from a sentence must not end up in the URL.
        ("https://x.com/user/status/1234567890.", "https://x.com/user/status/1234567890"),
        ("没有链接的一段话", None),
        ("https://youtube.com/watch?v=abc", None),
        ("", None),
    ],
)
def test_extract_supported_url(text, expected):
    assert E.extract_supported_url(text) == expected
