"""Tests for telegram/handlers/cmd_aliyun.py — cross-platform detection."""

import os
import platform

import pytest

from telegram.handlers.cmd_aliyun import _detect_os_arch, _install_path


class TestDetectOsArch:
    """_detect_os_arch returns (os_key, arch_key, ext, bin_name)."""

    def test_linux_amd64(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        os_key, arch, ext, bin_name = _detect_os_arch()
        assert os_key == "linux"
        assert arch == "amd64"
        assert ext == ".zip"
        assert bin_name == "aliyunpan"

    def test_linux_arm64(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform, "machine", lambda: "aarch64")
        os_key, arch, ext, bin_name = _detect_os_arch()
        assert os_key == "linux"
        assert arch == "arm64"

    def test_windows_amd64(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(platform, "machine", lambda: "AMD64")
        os_key, arch, ext, bin_name = _detect_os_arch()
        assert os_key == "windows"
        assert arch == "x64"
        assert ext == ".zip"
        assert bin_name == "aliyunpan.exe"

    def test_windows_x86(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(platform, "machine", lambda: "x86")
        os_key, arch, ext, bin_name = _detect_os_arch()
        assert arch == "x86"

    def test_windows_arm64(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(platform, "machine", lambda: "arm64")
        os_key, arch, ext, bin_name = _detect_os_arch()
        assert arch == "arm64"

    def test_macos_amd64(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        os_key, arch, ext, bin_name = _detect_os_arch()
        assert os_key == "darwin"
        assert arch == "amd64"
        assert bin_name == "aliyunpan"

    def test_macos_arm64(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform, "machine", lambda: "arm64")
        os_key, arch, ext, bin_name = _detect_os_arch()
        assert arch == "arm64"


class TestInstallPath:
    def test_linux_returns_usr_local_bin(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        path = _install_path()
        assert str(path) == "/usr/local/bin/aliyunpan"

    def test_windows_returns_writable_path(self, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        with monkeypatch.context() as m:
            m.setattr(os, "environ", {"PATH": "C:\\Windows\\system32;C:\\Windows"})
            path = _install_path()
            assert "aliyunpan.exe" in str(path)
