"""聊天与频道系统。

功能:
- 敏感词模糊过滤(忽略空格/零宽字符/大小写/全角, 支持谐音与形近字归一化)
- 禁言区分临时(到期时间戳, 到期自动解除)与永久
- 聊天记录分页加载(单页最多 50 条)
- @提及触发通知事件, 渲染时高亮
- 管理员权限使用可配置的位掩码
- 消息渲染前进行 HTML 转义
- 发言冷却同时检查时间间隔与内容相似度
"""
import html
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Optional

# 管理员权限位
PERM_MUTE = 1 << 0
PERM_KICK = 1 << 1
PERM_BAN = 1 << 2
PERM_PIN = 1 << 3
PERM_MANAGE = 1 << 4

MAX_PAGE_SIZE = 50
DEFAULT_COOLDOWN = 3.0
SIMILARITY_WINDOW = 60.0   # 内容相似度检查窗口(秒)
SIMILARITY_THRESHOLD = 0.8


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
    expires: Optional[float] = None  # None=永久禁言; 否则为到期时间戳


# ---------------------------------------------------------------------------
# 文本归一化: 去空格、大小写/全角折叠、谐音与形近字归一
# ---------------------------------------------------------------------------
_CHAR_VARIANTS: dict[str, str] = {}


def _variant_group(canonical: str, *variants: str) -> None:
    """把若干谐音/形近/异体字符登记到同一规范字下。"""
    for variant in variants:
        _CHAR_VARIANTS[variant] = canonical


# leet / 符号 / 同形西里尔字母异体
_variant_group("a", "@", "4", "а")
_variant_group("b", "8", "в")
_variant_group("e", "3", "е", "ё")
_variant_group("g", "9")
_variant_group("i", "1", "!", "|", "l")
_variant_group("o", "0", "о")
_variant_group("p", "р")
_variant_group("c", "с")
_variant_group("s", "5", "$")
_variant_group("t", "7", "+")
_variant_group("x", "×")
_variant_group("y", "у")
_variant_group("z", "2")

# 常见中文谐音 / 拆字 / 形近字(可在此继续扩充)
_variant_group("测", "侧", "厕")
_variant_group("试", "式", "室", "视")
_variant_group("赌", "堵")
_variant_group("毒", "读")
_variant_group("色", "瑟")
_variant_group("杀", "刹", "砂")
_variant_group("死", "四", "寺")
_variant_group("微", "威", "徽")
_variant_group("信", "鑫")
_variant_group("钱", "前")
_variant_group("加", "家", "佳")
_variant_group("公", "工")
_variant_group("王", "汪")
_variant_group("己", "已", "巳")
_variant_group("未", "末")
_variant_group("土", "士")
_variant_group("日", "曰")


def _analyze(content: str) -> tuple[str, list[int]]:
    """归一化文本, 同时返回每个归一化字符对应的原文下标。

    处理步骤: NFKC(全角/兼容字符折叠) -> 小写 -> 异体字映射,
    并丢弃空白、零宽等格式字符与标点(只保留字母/数字/汉字)。
    """
    normalized: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(content):
        folded = unicodedata.normalize("NFKC", char).casefold()
        for part in folded:
            if part.isspace() or unicodedata.category(part) == "Cf" or not part.isalnum():
                continue
            normalized.append(_CHAR_VARIANTS.get(part, part))
            positions.append(index)
    return "".join(normalized), positions


def _normalize(content: str) -> str:
    return _analyze(content)[0]


def _fingerprint(content: str) -> str:
    """内容相似度用指纹(已在归一化时剔除空白与标点)。"""
    return _normalize(content)


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    intervals.sort()
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


_MENTION_RE = re.compile(r"@([\w][\w\-]{0,31})", re.UNICODE)
_MENTION_HTML_RE = r'<span class="mention">@\1</span>'


class ChatSystem:
    def __init__(self):
        self.messages: dict[str, list[Message]] = {"world": []}
        self.muted: list[Mute] = []
        self.sensitive_words: list[str] = ["bad", "evil"]
        self.char_variants: dict[str, str] = _CHAR_VARIANTS
        self.admins: dict[str, int] = {}  # player -> 权限位掩码
        self.mention_notifications: list[str] = []
        self.mention_events: list[dict] = []
        self.on_mention: Optional[Callable[[dict], None]] = None
        self.last_message: dict[str, float] = {}
        self.last_content: dict[str, str] = {}

    # -- bug1: 敏感词模糊匹配 -------------------------------------------
    def filter_sensitive(self, content: str) -> str:
        normalized, positions = _analyze(content)
        if not normalized:
            return content
        intervals: list[tuple[int, int]] = []
        for word in self.sensitive_words:
            target = _normalize(word)
            if not target:
                continue
            search_from = 0
            while True:
                found = normalized.find(target, search_from)
                if found < 0:
                    break
                end = found + len(target)
                # 覆盖原文中整段(含中间插入的空格/符号), 全部打码
                intervals.append((positions[found], positions[end - 1] + 1))
                search_from = found + 1
        if not intervals:
            return content
        masked = list(content)
        for start, end in _merge_intervals(intervals):
            for index in range(start, end):
                masked[index] = "*"
        return "".join(masked)

    # -- bug2: 禁言时长(到期时间戳, 到期自动解除) ------------------------
    def mute_player(self, player: str, channel: str, duration: float = 0.0) -> None:
        """duration <= 0 表示永久禁言; 否则为持续秒数。"""
        expires = None if duration is None or duration <= 0 else time.time() + duration
        self.muted.append(Mute(player, channel, expires))

    def unmute_player(self, player: str, channel: str) -> None:
        self.muted = [
            mute for mute in self.muted
            if not (mute.player == player and mute.channel == channel)
        ]

    def is_muted(self, player: str, channel: str) -> bool:
        now = time.time()
        muted = False
        active: list[Mute] = []
        for mute in self.muted:
            if mute.player == player and mute.channel == channel:
                if mute.expires is None or mute.expires > now:
                    muted = True
                    active.append(mute)
                # 已到期的记录直接丢弃, 自动解除
            else:
                active.append(mute)
        self.muted = active
        return muted

    # -- bug3: 历史记录分页(每页最多 50 条) ------------------------------
    def get_history(self, channel: str, page: int = 0, page_size: int = 50) -> list[Message]:
        messages = self.messages.get(channel, [])
        page = max(0, page)
        if page_size <= 0:
            page_size = MAX_PAGE_SIZE
        page_size = min(page_size, MAX_PAGE_SIZE)
        start = page * page_size
        return messages[start:start + page_size]

    # -- bug4: @提及通知 -------------------------------------------------
    def _extract_mentions(self, content: str) -> list[str]:
        mentions: list[str] = []
        for name in _MENTION_RE.findall(content):
            if name not in mentions:
                mentions.append(name)
        return mentions

    def _notify_mentions(self, message: Message, mentions: list[str]) -> None:
        for name in mentions:
            self.mention_notifications.append(name)
            event = {
                "type": "mention",
                "mentioned": name,
                "sender": message.sender,
                "channel": message.channel,
                "content": message.content,
                "timestamp": message.timestamp,
            }
            self.mention_events.append(event)
            if self.on_mention is not None:
                self.on_mention(event)

    def send_message(self, player: str, content: str, channel: str = "world") -> bool:
        if self.is_muted(player, channel):
            return False
        if not self.can_send(player, content):
            return False
        message = Message(player, self.filter_sensitive(content), time.time(), channel)
        self.messages.setdefault(channel, []).append(message)
        self.last_message[player] = message.timestamp
        self.last_content[player] = content
        self._notify_mentions(message, self._extract_mentions(content))
        return True

    # -- bug5: 管理员权限位掩码 ------------------------------------------
    def grant_permission(self, player: str, perm: int) -> None:
        self.admins[player] = self.admins.get(player, 0) | perm

    def revoke_permission(self, player: str, perm: int) -> None:
        self.admins[player] = self.admins.get(player, 0) & ~perm

    def is_admin(self, player: str, perm: int) -> bool:
        return bool(self.admins.get(player, 0) & perm)

    # -- bug6: HTML 转义后渲染(@提及高亮) --------------------------------
    def render_message(self, message: Message) -> str:
        sender = html.escape(message.sender, quote=True)
        content = html.escape(message.content, quote=True)
        content = _MENTION_RE.sub(_MENTION_HTML_RE, content)
        return (
            f'<div class="chat-message">'
            f'<span class="sender">{sender}</span>: '
            f'<span class="content">{content}</span>'
            f"</div>"
        )

    # -- bug7: 冷却同时检查时间间隔与内容相似度 ---------------------------
    def _is_similar(self, left: str, right: str, threshold: float) -> bool:
        if not left or not right:
            return False
        if left == right or left in right or right in left:
            return True
        return SequenceMatcher(None, left, right).ratio() >= threshold

    def can_send(
        self,
        player: str,
        content: str,
        cooldown: float = DEFAULT_COOLDOWN,
        similarity_window: float = SIMILARITY_WINDOW,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
    ) -> bool:
        last_time = self.last_message.get(player)
        if last_time is None:
            return True
        elapsed = time.time() - last_time
        if elapsed < cooldown:
            return False
        if elapsed < similarity_window:
            current = _fingerprint(content)
            previous = _fingerprint(self.last_content.get(player, ""))
            if self._is_similar(current, previous, similarity_threshold):
                return False
        return True
