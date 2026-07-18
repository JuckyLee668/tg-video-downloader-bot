import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, Field

load_dotenv(override=True)

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

class AliyunDriveUploadConfig(BaseModel):
    """阿里云盘自动上传配置"""
    enabled: bool = False
    remote_path: str = "/video"
    delete_after_upload: bool = False


class DefaultActionConfig(BaseModel):
    """默认操作：收到任何视频时自动执行，跳过交互询问"""
    enabled: bool = False
    action: str = "download"  # download / forward / cloud / all
    target_chat: str = ""     # 转发目标 (forward/all 时使用)
    delete_after_forward: bool = False


class LocalForwardConfig(BaseModel):
    """下载后自动转发到指定聊天（兼容旧配置）"""
    enabled: bool = False
    target_chat: str = ""
    delete_after_forward: bool = False


class FileRenameConfig(BaseModel):
    """下载后智能重命名"""
    enabled: bool = False
    # 可用变量: {channel_title} {channel_username} {date} {time} {original_name} {ext}
    pattern: str = "{channel_title}/{date}_{original_name}"


class FileDedupConfig(BaseModel):
    """文件去重"""
    enabled: bool = True
    by_message_id: bool = True   # 按 chat_id + message_id 去重
    by_file_id: bool = False     # 按 Telegram file_id 去重（更严格）

class LargeFileConfig(BaseModel):
    """大文件处理：超过阈值时使用 ffmpeg 压缩"""
    enabled: bool = True                    # 是否启用大文件处理
    threshold_mb: int = 2000               # 阈值 (MB)
    action: str = "ask"                    # "compress" | "skip" | "ask"
    crf: int = 23                          # ffmpeg CRF 值 (0-51, 越低质量越好)
    preset: str = "fast"                   # ffmpeg preset: ultrafast/fast/medium/slow 等
    max_bitrate: str = ""                  # 可选最大码率限制 (如 "4000k")

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
    web_host: str = Field(default="127.0.0.1")
    web_port: int = Field(default=8000)
    web_cors_origins: List[str] = Field(default_factory=lambda: ["http://127.0.0.1:8000"])
    environment: str = Field(default="local")
    aliyundrive_upload: AliyunDriveUploadConfig = Field(default_factory=AliyunDriveUploadConfig)
    default_action: DefaultActionConfig = Field(default_factory=DefaultActionConfig)
    local_forward: LocalForwardConfig = Field(default_factory=LocalForwardConfig)
    file_rename: FileRenameConfig = Field(default_factory=FileRenameConfig)
    file_dedup: FileDedupConfig = Field(default_factory=FileDedupConfig)
    large_file: LargeFileConfig = Field(default_factory=LargeFileConfig)
    progress_notification: bool = Field(default=True)

    def save(self, config_path: str = "config.yaml"):
        # We need to be careful with .local override, but usually we save to the original if possible
        if os.path.exists("config.local.yaml"):
            config_path = "config.local.yaml"

        data = self.model_dump(mode='json')

        # Don't save things that should stay in environment
        # But for simplicity in this project, we might just save everything if provided via API

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        logger.info(f"配置已保存到 {config_path}")

def load_config(config_path: str = "config.yaml") -> Config:
    if os.path.exists("config.local.yaml"):
        config_path = "config.local.yaml"

    if Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
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

    if os.getenv("APP_ENV"):
        data["environment"] = os.getenv("APP_ENV")
    if os.getenv("WEB_HOST"):
        data["web_host"] = os.getenv("WEB_HOST")
    if os.getenv("WEB_PORT"):
        data["web_port"] = int(os.getenv("WEB_PORT"))
    if os.getenv("WEB_CORS_ORIGINS"):
        data["web_cors_origins"] = [
            item.strip() for item in os.getenv("WEB_CORS_ORIGINS", "").split(",") if item.strip()
        ]

    return Config(**data)

# Singleton instance
config = load_config()
