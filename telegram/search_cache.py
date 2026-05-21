import time
from typing import Any, Dict, List


class SearchCache:
    """
    缓存用户的最后一次搜索结果
    防止内存无限增长，支持 TTL 和结果上限
    """
    def __init__(self, ttl: int = 7200, max_users: int = 500):
        self.cache: Dict[int, Dict[str, Any]] = {}
        self.ttl = ttl
        self.max_users = max_users

    def set(self, chat_id: int, results: List[Any]):
        # 如果用户太多，清理最旧的
        if len(self.cache) >= self.max_users:
            oldest_user = min(self.cache.keys(), key=lambda k: self.cache[k]["timestamp"])
            del self.cache[oldest_user]

        self.cache[chat_id] = {
            "results": results,
            "timestamp": time.time()
        }

    def get(self, chat_id: int) -> List[Any]:
        item = self.cache.get(chat_id)
        if not item:
            return []

        # 检查 TTL
        if time.time() - item["timestamp"] > self.ttl:
            del self.cache[chat_id]
            return []

        return item["results"]

    def clear(self, chat_id: int):
        self.cache.pop(chat_id, None)

search_cache = SearchCache()
