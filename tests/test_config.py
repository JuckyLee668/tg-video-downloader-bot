"""Tests for core/config.py — Config loading and environment overrides."""

import os
from pathlib import Path

import yaml

from core.config import load_config


def _write_yaml(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


class TestConfigDefaults:
    def test_empty_config_uses_defaults(self, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {})
        cfg = load_config(cfg_path)
        assert cfg.save_path == "./downloads"
        assert cfg.max_download_task == 3
        assert cfg.environment == "local"
        assert cfg.web_port == 8000
        assert cfg.progress_notification is True
        assert cfg.file_dedup.enabled is True
        assert cfg.file_rename.enabled is False
        assert cfg.local_forward.enabled is False
        assert cfg.aliyundrive_upload.enabled is False

    def test_config_overrides_from_yaml(self, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {
            "save_path": "/data/downloads",
            "max_download_task": 5,
            "progress_notification": False,
            "file_dedup": {"enabled": False, "by_message_id": False},
            "file_rename": {"enabled": True, "pattern": "{channel_title}/{original_name}"},
            "local_forward": {"enabled": True, "target_chat": "@myself"},
        })
        cfg = load_config(cfg_path)
        assert cfg.save_path == "/data/downloads"
        assert cfg.max_download_task == 5
        assert cfg.progress_notification is False
        assert cfg.file_dedup.enabled is False
        assert cfg.file_rename.enabled is True
        assert cfg.file_rename.pattern == "{channel_title}/{original_name}"
        assert cfg.local_forward.enabled is True
        assert cfg.local_forward.target_chat == "@myself"

    def test_local_yaml_takes_precedence(self, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        local_path = str(tmp_path / "config.local.yaml")
        _write_yaml(cfg_path, {"save_path": "./downloads", "web_port": 8000})
        _write_yaml(local_path, {"save_path": "/custom/path"})

        orig_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            cfg = load_config("config.yaml")
            assert cfg.save_path == "/custom/path"
            assert cfg.web_port == 8000  # merged from default config
        finally:
            os.chdir(orig_cwd)

    def test_env_vars_override_yaml(self, monkeypatch, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {"bot_token": "yaml_token", "environment": "local"})
        monkeypatch.setenv("BOT_TOKEN", "env_token")

        cfg = load_config(cfg_path)
        assert cfg.bot_token == "env_token"

    def test_env_vars_for_user_api(self, monkeypatch, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {})
        monkeypatch.setenv("USER_API_ID", "12345")
        monkeypatch.setenv("USER_API_HASH", "abcde")

        cfg = load_config(cfg_path)
        assert cfg.user_api.api_id == "12345"
        assert cfg.user_api.api_hash == "abcde"

    def test_env_vars_for_web(self, monkeypatch, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {})
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("WEB_HOST", "0.0.0.0")
        monkeypatch.setenv("WEB_PORT", "9000")

        cfg = load_config(cfg_path)
        assert cfg.environment == "production"
        assert cfg.web_host == "0.0.0.0"
        assert cfg.web_port == 9000

    def test_cors_origins_from_env(self, monkeypatch, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {})
        monkeypatch.setenv("WEB_CORS_ORIGINS", "http://localhost:3000, https://example.com")

        cfg = load_config(cfg_path)
        assert len(cfg.web_cors_origins) == 2
        assert "http://localhost:3000" in cfg.web_cors_origins
        assert "https://example.com" in cfg.web_cors_origins

    def test_proxy_config_defaults_to_none(self, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {})
        cfg = load_config(cfg_path)
        assert cfg.proxy is None
        assert cfg.user_api.proxy is None

    def test_allowed_user_ids_default_empty(self, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {})
        cfg = load_config(cfg_path)
        assert cfg.allowed_user_ids == []


class TestConfigSave:
    def test_save_writes_to_local_if_exists(self, tmp_path: Path):
        orig_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            cfg_path = "config.yaml"
            _write_yaml(cfg_path, {"save_path": "./downloads"})
            local_path = "config.local.yaml"
            _write_yaml(local_path, {})

            cfg = load_config(cfg_path)
            cfg.save_path = "/new/path"
            cfg.save()

            with open(local_path) as f:
                data = yaml.safe_load(f)
            assert data["save_path"] == "/new/path"
        finally:
            os.chdir(orig_cwd)

    def test_save_creates_valid_yaml(self, tmp_path: Path):
        cfg_path = str(tmp_path / "config.yaml")
        _write_yaml(cfg_path, {})
        cfg = load_config(cfg_path)
        cfg.web_port = 9999
        cfg.save(cfg_path)

        with open(cfg_path) as f:
            data = yaml.safe_load(f)
        assert data["web_port"] == 9999
