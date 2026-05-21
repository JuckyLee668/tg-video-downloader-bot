from typing import List, Optional

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=200)

class JoinRequest(BaseModel):
    link: str = Field(min_length=1, max_length=300)

class SearchKeywordRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=120)
    limit: Optional[int] = Field(default=50, ge=1, le=1000)
    media_type: Optional[str] = None
    offset_id: Optional[int] = Field(default=0, ge=0)

class SearchTimeRequest(BaseModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    limit: Optional[int] = Field(default=100, ge=1, le=1000)
    media_type: Optional[str] = None
    offset_id: Optional[int] = Field(default=0, ge=0)

class SearchRecentRequest(BaseModel):
    limit: Optional[int] = Field(default=50, ge=1, le=1000)
    media_type: Optional[str] = None
    offset_id: Optional[int] = Field(default=0, ge=0)

class DownloadBatchRequest(BaseModel):
    message_ids: List[int] = Field(min_length=1, max_length=500)
    channel_id: Optional[str] = None
    formats: Optional[List[str]] = Field(default=None, max_length=50)

class LoginSendCodeRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=32, pattern=r"^\+?[0-9][0-9 ()-]*$")

class LoginSignInRequest(BaseModel):
    code: str = Field(min_length=2, max_length=32)

class ProxyConfigRequest(BaseModel):
    scheme: str = Field(default="http", pattern=r"^(http|socks5)$")
    hostname: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=1080, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    rdns: bool = True

class ConfigResponse(BaseModel):
    bot_token: str
    user_api_id: str
    user_api_hash: str
    proxy: Optional[ProxyConfigRequest]
    save_path: str
    max_download_task: int
    media_types: List[str]

class TaskIdRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=200)

class BatchDeleteRequest(BaseModel):
    task_ids: List[str] = Field(min_length=1, max_length=500)

class HistoryDeleteRequest(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=500)

class ForwardRequest(BaseModel):
    from_channel_id: str = Field(min_length=1, max_length=100)
    message_ids: List[int] = Field(min_length=1, max_length=500)
    to_chat_id: str = Field(min_length=1, max_length=100)
