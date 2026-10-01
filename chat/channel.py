"""聊天频道"""
import re
from dataclasses import dataclass, field

MAX_QUEUE_SIZE = 100
MAX_OFFLINE_MESSAGES = 1000
MAX_MESSAGE_BYTES = 4096

_FILTER_PATTERN = re.compile(r"\*{3,}")

@dataclass
class Player:
    pid: str
    online: bool = True
    muted: bool = False
    message_queue: list = field(default_factory=list)

class ChatChannel:
    def __init__(self):
        self.players: dict[str, Player] = {}
        self.offline_messages: dict[str, list] = {}
        self.dead_letter: list = []

    def broadcast_message(self, message: str) -> int:
        sent = 0
        for pid, player in self.players.items():
            if not player.online:
                continue
            try:
                if len(player.message_queue) >= MAX_QUEUE_SIZE:
                    raise Exception("queue full")
                player.message_queue.append(message)
                sent += 1
            except Exception as exc:
                self.dead_letter.append({"pid": pid, "message": message, "reason": str(exc)})
        return sent

    def store_offline_message(self, pid: str, message: str) -> bool:
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            return False
        box = self.offline_messages.setdefault(pid, [])
        if len(box) >= MAX_OFFLINE_MESSAGES:
            box.pop(0)
        box.append(message)
        return True

    def can_speak(self, pid: str) -> bool:
        player = self.players.get(pid)
        return player is not None and not player.muted

    def filter_message(self, message: str) -> str:
        return _FILTER_PATTERN.sub("[filtered]", message)

    def get_offline_messages(self, pid: str) -> list:
        return self.offline_messages.get(pid, [])
