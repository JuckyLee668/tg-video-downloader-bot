from pydantic import BaseModel
from typing import List, Optional

class ConnectRequest(BaseModel):
    identifier: str

class JoinRequest(BaseModel):
    link: str

class SearchKeywordRequest(BaseModel):
    keyword: str
    limit: Optional[int] = 50
    media_type: Optional[str] = None

class SearchTimeRequest(BaseModel):
    start_date: str # YYYY-MM-DD
    end_date: str # YYYY-MM-DD
    limit: Optional[int] = 100
    media_type: Optional[str] = None

class SearchRecentRequest(BaseModel):
    limit: Optional[int] = 50
    media_type: Optional[str] = None

class DownloadBatchRequest(BaseModel):
    message_ids: List[int]
    channel_id: Optional[str] = None
    formats: Optional[List[str]] = None

class LoginSendCodeRequest(BaseModel):
    phone: str

class LoginSignInRequest(BaseModel):
    code: str

class ProxyConfigRequest(BaseModel):
    scheme: str = "http"
    hostname: str = "127.0.0.1"
    port: int = 1080
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
    task_id: str

class BatchDeleteRequest(BaseModel):
    task_ids: List[str]

class ForwardRequest(BaseModel):
    from_channel_id: str
    message_ids: List[int]
    to_chat_id: str
