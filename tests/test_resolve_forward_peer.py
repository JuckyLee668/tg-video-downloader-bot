from unittest.mock import AsyncMock, MagicMock

import pytest

from downloader.manager import DownloadManager


class TestResolveForwardPeer:
    @pytest.mark.asyncio
    async def test_resolve_private_channel_link(self):
        manager = DownloadManager()
        client = MagicMock()
        client.get_input_entity = AsyncMock(return_value="mock_peer")
        client.get_entity = AsyncMock(return_value="mock_entity")

        # Test private link with message ID
        peer, reply_to = await manager._resolve_forward_peer(client, "https://t.me/c/3958174041/635")
        assert peer == "mock_peer"
        assert reply_to == 635
        client.get_input_entity.assert_any_call(-1003958174041)

    @pytest.mark.asyncio
    async def test_resolve_public_channel_link(self):
        manager = DownloadManager()
        client = MagicMock()
        client.get_input_entity = AsyncMock(return_value="mock_peer")
        client.get_entity = AsyncMock(return_value="mock_entity")

        # Test public link with message ID
        peer, reply_to = await manager._resolve_forward_peer(client, "https://t.me/username/635")
        assert peer == "mock_peer"
        assert reply_to == 635
        client.get_input_entity.assert_any_call("@username")

    @pytest.mark.asyncio
    async def test_resolve_public_preview_link(self):
        manager = DownloadManager()
        client = MagicMock()
        client.get_input_entity = AsyncMock(return_value="mock_peer")
        client.get_entity = AsyncMock(return_value="mock_entity")

        # Test preview link
        peer, reply_to = await manager._resolve_forward_peer(client, "https://t.me/s/previewchannel")
        assert peer == "mock_peer"
        assert reply_to is None
        client.get_input_entity.assert_any_call("@previewchannel")

    @pytest.mark.asyncio
    async def test_resolve_plain_numeric_id(self):
        manager = DownloadManager()
        client = MagicMock()
        client.get_input_entity = AsyncMock(return_value="mock_peer")
        client.get_entity = AsyncMock(return_value="mock_entity")

        peer, reply_to = await manager._resolve_forward_peer(client, "-100123456789")
        assert peer == "mock_peer"
        assert reply_to is None
        client.get_input_entity.assert_any_call(-100123456789)

    @pytest.mark.asyncio
    async def test_resolve_plain_username(self):
        manager = DownloadManager()
        client = MagicMock()
        client.get_input_entity = AsyncMock(return_value="mock_peer")
        client.get_entity = AsyncMock(return_value="mock_entity")

        peer, reply_to = await manager._resolve_forward_peer(client, "@my_channel")
        assert peer == "mock_peer"
        assert reply_to is None
        client.get_input_entity.assert_any_call("@my_channel")
