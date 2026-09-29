"""聊天与频道系统"""
import html
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

PERM_MUTE = 1 << 0          # 禁言他人
PERM_KICK = 1 << 1          # 踢人
PERM_BAN = 1 << 2           # 封禁
PERM_PIN = 1 << 3           # 置顶消息
PERM_MANAGE_ADMIN = 1 << 4  # 管理权限
PERM_ALL = PERM_MUTE | PERM_KICK | PERM_BAN | PERM_PIN | PERM_MANAGE_ADMIN

MAX_PAGE_SIZE = 50
SIMILARITY_WINDOW = 30.0    # 内容相似度检查窗口（秒）
SIMILARITY_THRESHOLD = 0.8  # 相似度阈值

# 形近字 / 异体写法 -> 规范字（含繁简、形似字）
_LOOKALIKES = {
    "侧": "测", "測": "测", "冊": "册",
    "試": "试",
    "壞": "坏",
    "騙": "骗",
    "賭": "赌",
    "掛": "挂",
    "黃": "黄",
}

# 谐音字组：同一组内的字（第一个）互相等价
_HOMOPHONE_GROUPS = [
    "测册侧策厕",
    "试是事市式示士世视势饰",
    "坏",
    "邪斜鞋协携",
    "骗片遍便变",
    "赌堵杜度肚",
    "挂刮瓜寡卦",
    "黄皇荒慌凰",
    "毒独读渡度",
]


def _build_homophone_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for group in _HOMOPHONE_GROUPS:
        canonical = group[0]
        for ch in group:
            mapping.setdefault(ch, canonical)
    return mapping


_HOMOPHONES = _build_homophone_map()

# leet / 拉丁字母形近写法 -> 规范小写字母
_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "7": "t", "@": "a", "$": "s", "|": "l", "!": "i",
})

_MENTION_RE = re.compile(r"@([A-Za-z0-9_\u4e00-\u9fa5]+)")
_PUNCT_RE = re.compile(r"[\s_]+")


def normalize_text(text: str) -> tuple[str, list[int]]:
    """把文本归一化用于模糊匹配。

    处理：NFKC（全角转半角）、转小写、去空白/下划线、
    leet/形近字替换、谐音字归并。
    返回 (归一化文本, 索引表)，索引表第 i 项为归一化字符在原文中的位置。
    """
    normalized_chars: list[str] = []
    index_map: list[int] = []
    # 逐字符归一化，展开出的多个字符都映射回同一原始位置
    for idx, original in enumerate(text):
        for ch in unicodedata.normalize("NFKC", original).casefold():
            if ch.isspace():
                continue
            ch = ch.translate(_LEET)
            if ch in _LOOKALIKES:
                ch = _LOOKALIKES[ch]
            elif ch in _HOMOPHONES:
                ch = _HOMOPHONES[ch]
            if ch.isalnum():
                normalized_chars.append(ch)
                index_map.append(idx)
    return "".join(normalized_chars), index_map


def compact_for_compare(text: str) -> str:
    """内容相似度比较用：小写并去掉空白与下划线。"""
    return _PUNCT_RE.sub("", unicodedata.normalize("NFKC", text).casefold())


@dataclass
class Message:
    sender: str
    content: str
    timestamp: float
    channel: str = "world"
    mentions: list[str] = field(default_factory=list)


@dataclass
class Mute:
    player: str
    channel: str
    expires: float | None  # None 表示永久禁言；否则为到期时间戳


@dataclass
class NotificationEvent:
    target: str
    sender: str
    channel: str
    message: Message
    timestamp: float


class ChatSystem:
    def __init__(self):
        self.messages: dict[str, list[Message]] = {"world": []}
        self.muted: list[Mute] = []
        self.sensitive_words: list[str] = ["bad", "evil"]
        self.admins: dict[str, int] = {}  # player -> 权限位掩码
        self.mention_notifications: list[str] = []  # 被@的玩家列表
        self.notification_events: list[NotificationEvent] = []
        self.last_message: dict[str, float] = {}  # player -> 最近发言时间戳
        self.last_content: dict[str, str] = {}    # player -> 最近发言内容

    def filter_sensitive(self, content: str) -> str:
        """模糊敏感词过滤：忽略空格、谐音、形近字等变形，命中处替换为 ***。"""
        normalized, index_map = normalize_text(content)
        if not normalized:
            return content
        spans: list[tuple[int, int]] = []
        for word in self.sensitive_words:
            word_norm, _ = normalize_text(word)
            if not word_norm:
                continue
            start = 0
            while True:
                pos = normalized.find(word_norm, start)
                if pos == -1:
                    break
                spans.append((index_map[pos], index_map[pos + len(word_norm) - 1] + 1))
                start = pos + len(word_norm)
        if not spans:
            return content
        # 合并重叠/相邻区间
        spans.sort()
        merged: list[list[int]] = []
        for lo, hi in spans:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        chars = list(content)
        for lo, hi in reversed(merged):
            chars[lo:hi] = list("***")
        return "".join(chars)

    def mute_player(self, player: str, channel: str, duration: float):
        """禁言。duration<=0 表示永久，否则为持续秒数；按到期时间戳记录。"""
        # 同一玩家同一频道只保留一条禁言记录（新的覆盖旧的）
        self.muted = [m for m in self.muted
                      if not (m.player == player and m.channel == channel)]
        if duration and duration > 0:
            self.muted.append(Mute(player, channel, time.time() + duration))
        else:
            self.muted.append(Mute(player, channel, None))

    def unmute_player(self, player: str, channel: str) -> bool:
        before = len(self.muted)
        self.muted = [m for m in self.muted
                      if not (m.player == player and m.channel == channel)]
        return len(self.muted) < before

    def is_muted(self, player: str, channel: str) -> bool:
        """是否处于禁言状态；到期的禁言记录会被自动清除。"""
        now = time.time()
        active = False
        remaining: list[Mute] = []
        for m in self.muted:
            if m.player != player or m.channel != channel:
                remaining.append(m)
                continue
            if m.expires is None:
                active = True
                remaining.append(m)
            elif now < m.expires:
                active = True
                remaining.append(m)
            # 已到期：丢弃，自动解除
        if len(remaining) != len(self.muted):
            self.muted = remaining
        return active

    def get_history(self, channel: str, page: int = 0,
                    page_size: int = MAX_PAGE_SIZE) -> list[Message]:
        """分页拉取历史，按时间倒序：page=0 为最新一页，每页最多 50 条。"""
        if page < 0:
            return []
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))
        history = self.messages.get(channel, [])
        end = len(history) - page * page_size
        if end <= 0:
            return []
        start = max(0, end - page_size)
        return list(reversed(history[start:end]))

    def _parse_mentions(self, content: str) -> list[str]:
        seen: set[str] = set()
        mentions: list[str] = []
        for name in _MENTION_RE.findall(content):
            if name not in seen:
                seen.add(name)
                mentions.append(name)
        return mentions

    def _notify_mentions(self, msg: Message):
        now = msg.timestamp
        for target in msg.mentions:
            self.mention_notifications.append(target)
            self.notification_events.append(
                NotificationEvent(target, msg.sender, msg.channel, msg, now))

    def send_message(self, player: str, content: str,
                     channel: str = "world") -> bool:
        if self.is_muted(player, channel):
            return False
        mentions = self._parse_mentions(content)
        now = time.time()
        msg = Message(player, self.filter_sensitive(content), now,
                      channel, mentions)
        self.messages.setdefault(channel, []).append(msg)
        self.last_message[player] = now
        self.last_content[player] = content
        self._notify_mentions(msg)
        return True

    def grant_permission(self, player: str, perm: int):
        self.admins[player] = self.admins.get(player, 0) | perm

    def revoke_permission(self, player: str, perm: int):
        if player in self.admins:
            self.admins[player] &= ~perm

    def is_admin(self, player: str, perm: int = PERM_ALL) -> bool:
        """检查玩家是否拥有指定权限位（位掩码全部命中才算通过）。"""
        bits = self.admins.get(player, 0)
        return bool(bits & perm) and (bits & perm) == perm

    def render_message(self, msg: Message) -> str:
        """先做 HTML 转义，再把 @提及 包裹成高亮元素。"""
        sender = html.escape(msg.sender)
        content = html.escape(msg.content)
        for name in dict.fromkeys(msg.mentions):
            escaped = html.escape(f"@{name}")
            content = content.replace(
                escaped, f'<span class="mention">{escaped}</span>')
        return f"<div>{sender}: {content}</div>"

    def can_send(self, player: str, content: str,
                 cooldown: float = 3.0) -> bool:
        """发言冷却：时间间隔不足拦截；间隔较长但内容高度相似同样拦截。"""
        now = time.time()
        last_ts = self.last_message.get(player)
        if last_ts is not None and now - last_ts < cooldown:
            return False
        last_text = self.last_content.get(player)
        if last_text is not None and now - last_ts < SIMILARITY_WINDOW:
            a = compact_for_compare(last_text)
            b = compact_for_compare(content)
            if a and b:
                ratio = SequenceMatcher(None, a, b).ratio()
                if ratio >= SIMILARITY_THRESHOLD:
                    return False
        return True
