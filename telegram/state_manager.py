import time
from typing import Any, Dict, Optional


class StateManager:
    """
    管理用户的交互状态 (FSM)
    """
    def __init__(self, ttl: int = 3600):
        self.states: Dict[int, Dict[str, Any]] = {}
        self.ttl = ttl

    def set(self, chat_id: int, state: Dict[str, Any]):
        self.states[chat_id] = {
            "data": state,
            "timestamp": time.time()
        }

    def get(self, chat_id: int) -> Optional[Dict[str, Any]]:
        item = self.states.get(chat_id)
        if not item:
            return None
        
        # 检查 TTL
        if time.time() - item["timestamp"] > self.ttl:
            self.clear(chat_id)
            return None
            
        return item["data"]

    def update(self, chat_id: int, **kwargs):
        item = self.states.get(chat_id)
        if item:
            item["data"].update(kwargs)
            item["timestamp"] = time.time()

    def clear(self, chat_id: int):
        self.states.pop(chat_id, None)

state_manager = StateManager()
