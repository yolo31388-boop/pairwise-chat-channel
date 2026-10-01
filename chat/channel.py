"""聊天频道"""
import re
from dataclasses import dataclass, field

MAX_QUEUE_SIZE = 100
MAX_OFFLINE_MESSAGES = 1000
MAX_MESSAGE_BYTES = 1024

@dataclass
class Player:
    pid: str
    online: bool = True
    muted: bool = False  # 禁言状态存服务端
    message_queue: list = field(default_factory=list)

class ChatChannel:
    def __init__(self):
        self.players: dict[str, Player] = {}
        self.offline_messages: dict[str, list] = {}
        self.dead_letter: list = []  # 分发失败的消息

    def broadcast_message(self, message: str) -> int:
        sent = 0
        for pid, player in self.players.items():
            if player.online:
                try:
                    if len(player.message_queue) >= MAX_QUEUE_SIZE:
                        raise Exception("queue full")
                    player.message_queue.append(message)
                    sent += 1
                except Exception:
                    # 单个玩家失败不影响其他人，消息进死信队列
                    self.dead_letter.append({"pid": pid, "message": message})
        return sent

    def store_offline_message(self, pid: str, message: str) -> bool:
        # 大小上限：超限直接拒绝
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            return False
        if pid not in self.offline_messages:
            self.offline_messages[pid] = []
        # 数量上限：满了丢弃最旧的
        if len(self.offline_messages[pid]) >= MAX_OFFLINE_MESSAGES:
            self.offline_messages[pid].pop(0)
        self.offline_messages[pid].append(message)
        return True

    def can_speak(self, pid: str) -> bool:
        # 服务端校验禁言状态，不依赖客户端
        player = self.players.get(pid)
        return player is not None and not player.muted

    def filter_message(self, message: str) -> str:
        # 正则匹配连续3个及以上星号，无法通过多加星号绕过
        return re.sub(r"\*{3,}", "[filtered]", message)

    def get_offline_messages(self, pid: str) -> list:
        return self.offline_messages.get(pid, [])
