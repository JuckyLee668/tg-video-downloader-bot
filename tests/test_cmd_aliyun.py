"""Tests for telegram/handlers/cmd_aliyun.py — /aliyun command."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram.handlers.cmd_aliyun import (
    _find_aliyunpan,
    _run_aliyunpan,
    aliyun_handler,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_event():
    """Minimal Telethon event mock with respond()."""
    ev = MagicMock()
    ev.respond = AsyncMock()
    ev.chat_id = 12345
    return ev


# ── _find_aliyunpan ──────────────────────────────────────────────────────────


class TestFindAliyunpan:
    def test_returns_none_when_cli_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _: None)
        # Also mock the hardcoded paths to not exist
        monkeypatch.setattr("pathlib.Path.is_file", lambda _: False)
        monkeypatch.setattr("pathlib.Path.stat", lambda _: None)
        assert _find_aliyunpan() is None

    def test_returns_path_when_cli_exists(self, monkeypatch, tmp_path):
        bin_path = str(tmp_path / "aliyunpan")
        tmp_path.joinpath("aliyunpan").write_text("#!/bin/sh\necho ok")
        tmp_path.joinpath("aliyunpan").chmod(0o755)

        monkeypatch.setattr("shutil.which", lambda _: bin_path)
        assert _find_aliyunpan() == bin_path


# ── _run_aliyunpan ───────────────────────────────────────────────────────────


class TestRunAliyunpan:
    async def test_returns_error_when_cli_missing(self, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: None,
        )
        code, text = await _run_aliyunpan("who")
        assert code == 1
        assert "未安装" in text

    async def test_runs_command_successfully(self, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/fake/aliyunpan",
        )

        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(
            return_value=(b"nickname: TestUser", b"")
        )

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        )

        code, text = await _run_aliyunpan("who")
        assert code == 0
        assert "TestUser" in text


# ── aliyun_handler (main entry) ──────────────────────────────────────────────


class TestAliyunHandlerEntry:
    """Verify the routing logic in aliyun_handler() itself."""

    @pytest.fixture(autouse=True)
    def patch_deps(self, monkeypatch):
        """Always provide a working aliyunpan + minimal config."""
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        # Prevent _run_aliyunpan from actually shelling out
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(
            return_value=(b"nickname: TestUser\n", b"")
        )
        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        )
        # Provide a real-ish config object
        from core.config import AliyunDriveUploadConfig

        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = AliyunDriveUploadConfig(enabled=True)
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

    async def test_empty_arg_shows_status(self, mock_event):
        """Regression: empty arg caused 'list index out of range'."""
        await aliyun_handler(mock_event, "")
        mock_event.respond.assert_awaited_once()
        text = mock_event.respond.call_args[0][0]
        assert "阿里云盘" in text

    async def test_none_arg_shows_status(self, mock_event):
        """None arg (no arg passed) should also work."""
        await aliyun_handler(mock_event, None)
        mock_event.respond.assert_awaited_once()
        text = mock_event.respond.call_args[0][0]
        assert "阿里云盘" in text

    async def test_unknown_subcommand_returns_help(self, mock_event):
        await aliyun_handler(mock_event, "foobar")
        mock_event.respond.assert_awaited_once()
        text = mock_event.respond.call_args[0][0]
        assert "未知子命令" in text

    async def test_whitespace_only_arg(self, mock_event):
        """Whitespace should be treated like empty arg."""
        await aliyun_handler(mock_event, "   ")
        mock_event.respond.assert_awaited_once()
        text = mock_event.respond.call_args[0][0]
        assert "阿里云盘" in text

    async def test_login_subcommand(self, mock_event):
        # fixture patch_deps already mocks create_subprocess_exec
        await aliyun_handler(mock_event, "login")
        # _cmd_login calls respond twice: "正在生成" + result
        text = mock_event.respond.call_args_list[-1][0][0]
        assert "扫码" in text or "登录" in text

    async def test_logout_subcommand(self, mock_event):
        # fixture patch_deps already mocks create_subprocess_exec
        await aliyun_handler(mock_event, "logout")
        text = mock_event.respond.call_args_list[-1][0][0]
        assert "退出" in text or "logout" in text.lower()


# ── _show_status ──────────────────────────────────────────────────────────────


class TestShowStatus:
    async def test_shows_login_and_quota_when_authenticated(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )

        # who -> authenticated
        def _fake_run(*args, **kwargs):
            if "who" in args:
                return (0, "昵称: TestUser")
            if "quota" in args:
                return (0, "2.79TB / used 170.18GB")
            return (0, "")

        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(side_effect=_fake_run),
        )

        from core.config import AliyunDriveUploadConfig

        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = AliyunDriveUploadConfig(
            enabled=True, remote_path="/video"
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "")
        mock_event.respond.assert_awaited_once()
        text = mock_event.respond.call_args[0][0]
        assert "logged in" in text or "已登录" in text
        assert "enabled" in text or "已启用" in text
        assert "/video" in text
        assert "2.79TB" in text

    async def test_shows_not_logged_in_when_unauthenticated(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )

        def _fake_run(*args, **kwargs):
            if "who" in args:
                return (0, "please login first")
            if "quota" in args:
                return (0, "")
            return (0, "")

        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(side_effect=_fake_run),
        )

        from core.config import AliyunDriveUploadConfig

        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = AliyunDriveUploadConfig(
            enabled=True, remote_path="/video"
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "")
        text = mock_event.respond.call_args[0][0]
        assert "not logged in" in text or "未登录" in text

    async def test_shows_disabled_when_auto_upload_off(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )

        def _fake_run(*args, **kwargs):
            if "who" in args:
                return (0, "nickname: TestUser")
            if "quota" in args:
                return (0, "1TB / used 500GB")
            return (0, "")

        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(side_effect=_fake_run),
        )

        from core.config import AliyunDriveUploadConfig

        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = AliyunDriveUploadConfig(
            enabled=False, remote_path="/video"
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "")
        text = mock_event.respond.call_args[0][0]
        assert "disabled" in text or "已禁用" in text

    async def test_cli_not_installed(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._ensure_aliyunpan",
            AsyncMock(return_value=(False, "aliyunpan CLI 未安装")),
        )

        await aliyun_handler(mock_event, "")
        text = mock_event.respond.call_args[0][0]
        assert "未安装" in text

    async def test_auto_install_success(self, mock_event, monkeypatch):
        """When auto-install succeeds, the success msg is sent first."""
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._ensure_aliyunpan",
            AsyncMock(return_value=(True, "✅ 已自动安装 aliyunpan CLI (v0.3.9)")),
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(0, "nickname: TestUser")),
        )

        from core.config import AliyunDriveUploadConfig
        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = AliyunDriveUploadConfig(enabled=True)
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "")
        # First respond call should be the install success message
        first_text = mock_event.respond.call_args_list[0][0][0]
        assert "自动安装" in first_text


# ── /aliyun ls ────────────────────────────────────────────────────────────────


class TestAliyunLs:
    async def test_ls_without_path(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(0, "drwxr-xr-x  video\n-rw-r--r--  test.mp4")),
        )

        await aliyun_handler(mock_event, "ls")
        text = mock_event.respond.call_args[0][0]
        assert "阿里云盘文件" in text
        assert "video" in text
        assert "test.mp4" in text

    async def test_ls_with_path(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(0, "drwxr-xr-x  subdir")),
        )

        await aliyun_handler(mock_event, "ls /video/foo")
        text = mock_event.respond.call_args[0][0]
        assert "subdir" in text

    async def test_ls_failure(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(1, "permission denied")),
        )

        await aliyun_handler(mock_event, "ls")
        text = mock_event.respond.call_args[0][0]
        assert "失败" in text


# ── /aliyun tree ──────────────────────────────────────────────────────────────


class TestAliyunTree:
    async def test_tree_default(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(0, "/\n└── video\n    └── test.mp4")),
        )

        await aliyun_handler(mock_event, "tree")
        text = mock_event.respond.call_args[0][0]
        assert "目录树" in text
        assert "test.mp4" in text

    async def test_tree_failure(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(1, "not found")),
        )

        await aliyun_handler(mock_event, "tree /nonexistent")
        text = mock_event.respond.call_args[0][0]
        assert "失败" in text


# ── /aliyun on/off ────────────────────────────────────────────────────────────


class TestAliyunToggle:
    async def test_enable(self, mock_event, monkeypatch):
        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = MagicMock()
        cfg_mock.aliyundrive_upload.enabled = False
        cfg_mock.aliyundrive_upload.remote_path = "/video"
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "on")
        assert cfg_mock.aliyundrive_upload.enabled is True
        cfg_mock.save.assert_called_once()
        text = mock_event.respond.call_args[0][0]
        assert "已启用" in text

    async def test_disable(self, mock_event, monkeypatch):
        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = MagicMock()
        cfg_mock.aliyundrive_upload.enabled = True
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "off")
        assert cfg_mock.aliyundrive_upload.enabled is False
        cfg_mock.save.assert_called_once()
        text = mock_event.respond.call_args[0][0]
        assert "已禁用" in text


# ── /aliyun path ──────────────────────────────────────────────────────────────


class TestAliyunSetPath:
    async def test_set_path(self, mock_event, monkeypatch):
        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = MagicMock()
        cfg_mock.aliyundrive_upload.enabled = False
        cfg_mock.aliyundrive_upload.remote_path = ""
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "path /backup")
        assert cfg_mock.aliyundrive_upload.remote_path == "/backup"
        # Should auto-enable
        assert cfg_mock.aliyundrive_upload.enabled is True
        cfg_mock.save.assert_called_once()
        text = mock_event.respond.call_args[0][0]
        assert "/backup" in text

    async def test_set_path_no_arg_shows_error(self, mock_event, monkeypatch):
        await aliyun_handler(mock_event, "path")
        text = mock_event.respond.call_args[0][0]
        assert "用法" in text

    async def test_path_alias_dir(self, mock_event, monkeypatch):
        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = MagicMock()
        cfg_mock.aliyundrive_upload.enabled = False
        cfg_mock.aliyundrive_upload.remote_path = ""
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "dir /movies")
        assert cfg_mock.aliyundrive_upload.remote_path == "/movies"

    async def test_path_alias_set(self, mock_event, monkeypatch):
        cfg_mock = MagicMock()
        cfg_mock.aliyundrive_upload = MagicMock()
        cfg_mock.aliyundrive_upload.enabled = True
        cfg_mock.aliyundrive_upload.remote_path = ""
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun.config", cfg_mock
        )

        await aliyun_handler(mock_event, "set /data")
        assert cfg_mock.aliyundrive_upload.remote_path == "/data"


# ── ls / tree with long output truncation ────────────────────────────────────


class TestOutputTruncation:
    async def test_ls_truncates_long_output(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        lines = [f"file_{i}.mp4" for i in range(100)]
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(0, "\n".join(lines))),
        )

        await aliyun_handler(mock_event, "ls")
        text = mock_event.respond.call_args[0][0]
        # First 40 lines shown, rest hinted
        assert "file_0" in text
        assert "file_39" in text
        assert "file_50" not in text
        assert "还有 60 行" in text

    async def test_tree_truncates_long_output(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )
        lines = [f"subdir_{i}" for i in range(50)]
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._run_aliyunpan",
            AsyncMock(return_value=(0, "\n".join(lines))),
        )

        await aliyun_handler(mock_event, "tree")
        text = mock_event.respond.call_args[0][0]
        assert "还有 10 行" in text


# ── Login subcommand edge cases ──────────────────────────────────────────────


class TestLoginEdgeCases:
    async def test_login_timeout(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )

        fake_proc = AsyncMock()
        fake_proc.communicate = AsyncMock(side_effect=TimeoutError)

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        )

        await aliyun_handler(mock_event, "login")

        text = mock_event.respond.call_args_list[-1][0][0]
        assert "超时" in text

    async def test_login_no_qr_url_found(self, mock_event, monkeypatch):
        monkeypatch.setattr(
            "telegram.handlers.cmd_aliyun._find_aliyunpan",
            lambda: "/usr/local/bin/aliyunpan",
        )

        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(
            return_value=(b"login initiated\nsome random text", b"")
        )

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec",
            AsyncMock(return_value=fake_proc),
        )

        await aliyun_handler(mock_event, "login")

        text = mock_event.respond.call_args_list[-1][0][0]
        # Should still show instructions even without QR URL
        assert "扫码" in text or "登录" in text
