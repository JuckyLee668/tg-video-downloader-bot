import os
from pathlib import Path
import yaml
from typing import List, Optional, Dict
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from loguru import logger
from utils.runtime_paths import app_path

load_dotenv(dotenv_path=app_path(".env"), override=True)

class ProxyConfig(BaseModel):
    scheme: str = "http"
    hostname: str = "127.0.0.1"
    port: int = 1080
    username: Optional[str] = None
    password: Optional[str] = None
    rdns: bool = True  # whether to resolve DNS on proxy side

class ChatConfig(BaseModel):
    chat_id: str
    last_read_message_id: int = 0
    download_filter: Optional[str] = None

class UserApiConfig(BaseModel):
    api_id: Optional[str] = None
    api_hash: Optional[str] = None
    proxy: Optional[ProxyConfig] = None

def _normalize_runtime_path(path_str: str) -> str:
    path = Path(path_str)
    if path.is_absolute():
        return str(path)
    return str(app_path(path_str))


class Config(BaseModel):
    bot_token: str = Field(default="")
    proxy: Optional[ProxyConfig] = None  # global proxy (default off)
    chat: List[ChatConfig] = Field(default_factory=list)
    enable_private_chat: bool = Field(default=True)
    media_types: List[str] = Field(default_factory=lambda: ["audio", "document", "photo", "video", "voice", "animation"])
    file_formats: Dict[str, List[str]] = Field(default_factory=dict)
    save_path: str = Field(default="./downloads")
    group_same_channel_files: bool = Field(default=True)
    file_path_prefix: List[str] = Field(default_factory=lambda: ["chat_title"])
    file_name_prefix: List[str] = Field(default_factory=lambda: ["message_id", "file_name"])
    file_name_prefix_split: str = Field(default=" - ")
    max_download_task: int = Field(default=3)
    always_fresh_download: bool = Field(default=False)
    batch_size: int = Field(default=20)
    batch_interval: int = Field(default=500)
    adaptive_concurrency: bool = Field(default=True)
    allowed_user_ids: List[str] = Field(default_factory=list)
    user_api: UserApiConfig = Field(default_factory=UserApiConfig)
    max_connected_channels: int = Field(default=10)
    date_format: str = Field(default="%Y_%m")

    def save(self, config_path: str = "config.yaml"):
        local_config_path = app_path("config.local.yaml")
        if local_config_path.exists():
            config_path = str(local_config_path)
        else:
            config_path = str(app_path(config_path))

        data = self.model_dump(mode='json')
        
        # Don't save things that should stay in environment
        # But for simplicity in this project, we might just save everything if provided via API
        
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        logger.info(f"配置已保存到 {config_path}")

def load_config(config_path: str = "config.yaml") -> Config:
    local_config_path = app_path("config.local.yaml")
    default_config_path = app_path(config_path)
    resolved_config_path = local_config_path if local_config_path.exists() else default_config_path

    if resolved_config_path.exists():
        with open(resolved_config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    
    # Override with environment variables
    if os.getenv("BOT_TOKEN"):
        data["bot_token"] = os.getenv("BOT_TOKEN")
    
    if "user_api" not in data:
        data["user_api"] = {}

    if os.getenv("USER_API_ID"):
        data["user_api"]["api_id"] = os.getenv("USER_API_ID")

    if os.getenv("USER_API_HASH"):
        data["user_api"]["api_hash"] = os.getenv("USER_API_HASH")

    config = Config(**data)
    config.save_path = _normalize_runtime_path(config.save_path)
    return config

# Singleton instance
config = load_config()
