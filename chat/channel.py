"""聊天与频道系统 - 含7个bug"""
import re
import time
from dataclasses import dataclass, field

@dataclass
class Message:
    sender: str
    content: str
    timestamp: float
    channel: str = "world"

@dataclass
class Mute:
    player: str
    channel: str
    expires: float  # bug2: 不区分时长，0=永久

class ChatSystem:
    def __init__(self):
        self.messages: dict[str, list[Message]] = {"world": []}  # channel -> messages
        self.muted: list[Mute] = []
        self.sensitive_words: list[str] = ["bad", "evil"]
        self.admins: dict[str, int] = {}  # player -> permission bits, bug5:硬编码
        self.mention_notifications: list[str] = []
        self.last_message: dict[str, float] = {}  # player -> last timestamp
        self.last_content: dict[str, str] = {}  # bug7: 不检查内容相似度

    def filter_sensitive(self, content: str) -> str:
        # bug1: 只匹配完整词，加空格谐音绕过
        for word in self.sensitive_words:
            content = content.replace(word, "***")
        return content

    def mute_player(self, player: str, channel: str, duration: float):
        # bug2: 不区分时长，永久和临时用同一字段
        self.muted.append(Mute(player, channel, time.time() + duration))

    def is_muted(self, player: str, channel: str) -> bool:
        now = time.time()
        for m in self.muted:
            if m.player == player and m.channel == channel:
                if m.expires == 0:  # 永久
                    return True
                if now < m.expires:
                    return True
        return False

    def get_history(self, channel: str, page: int = 0, page_size: int = 50) -> list[Message]:
        # bug3: 无分页，一次返回全部
        return self.messages.get(channel, [])

    def send_message(self, player: str, content: str, channel: str = "world") -> bool:
        if self.is_muted(player, channel):
            return False
        # bug4: @提及不触发通知
        msg = Message(player, self.filter_sensitive(content), time.time(), channel)
        self.messages.setdefault(channel, []).append(msg)
        self.last_message[player] = time.time()
        self.last_content[player] = content
        return True

    def is_admin(self, player: str, perm: int) -> bool:
        # bug5: 管理员权限硬编码
        return player in self.admins

    def render_message(self, msg: Message) -> str:
        # bug6: 不转义HTML
        return f"<div>{msg.sender}: {msg.content}</div>"

    def can_send(self, player: str, content: str, cooldown: float = 3.0) -> bool:
        # bug7: 只检查时间不检查内容相似度
        now = time.time()
        if player in self.last_message:
            if now - self.last_message[player] < cooldown:
                return False
        return True
