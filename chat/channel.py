"""聊天频道 - 含5个bug"""
from dataclasses import dataclass, field

@dataclass
class Player:
    pid: str
    online: bool = True
    muted: bool = False  # bug3: 只检查是否在频道
    message_queue: list = field(default_factory=list)

class ChatChannel:
    def __init__(self):
        self.players: dict[str, Player] = {}
        self.offline_messages: dict[str, list] = {}  # bug2: 无上限

    def broadcast_message(self, message: str) -> int:
        # bug1: 一个玩家失败中断全部
        sent = 0
        for pid, player in self.players.items():
            if player.online:
                if len(player.message_queue) >= 100:
                    raise Exception("queue full")
                player.message_queue.append(message)
                sent += 1
        return sent

    def store_offline_message(self, pid: str, message: str) -> bool:
        # bug2: 无数量和大小上限
        if pid not in self.offline_messages:
            self.offline_messages[pid] = []
        self.offline_messages[pid].append(message)
        return True

    def can_speak(self, pid: str) -> bool:
        # bug3: 只检查是否在频道，不检查禁言
        return pid in self.players

    def filter_message(self, message: str) -> str:
        # bug4: 简单替换，可绕过
        return message.replace("***", "[filtered]")

    def get_offline_messages(self, pid: str) -> list:
        # bug2相关：一次性加载全部
        return self.offline_messages.get(pid, [])
