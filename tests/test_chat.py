"""聊天与频道系统 - 红态测试"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat.channel import ChatSystem, Message
import time

class TestSensitiveFilter:
    def test_filters_spaced_words(self):
        cs = ChatSystem()
        result = cs.filter_sensitive("b a d")  # 加空格
        assert "***" in result or "b a d" not in result  # bug1: 没过滤

    def test_filters_homophones(self):
        cs = ChatSystem()
        cs.sensitive_words = ["测试"]
        result = cs.filter_sensitive("测 试")  # 谐音/拆字
        assert "测 试" not in result  # bug1: 没过滤

class TestMuteDuration:
    def test_permanent_mute_distinct_from_temporary(self):
        cs = ChatSystem()
        cs.mute_player("p1", "world", 0)  # 永久
        cs.mute_player("p2", "world", 0.1)  # 临时
        assert cs.is_muted("p1", "world") == True
        time.sleep(0.15)
        assert cs.is_muted("p2", "world") == False  # bug2: 可能还在禁言
        assert cs.is_muted("p1", "world") == True  # 永久仍生效

class TestHistoryPagination:
    def test_history_has_pagination(self):
        cs = ChatSystem()
        for i in range(100):
            cs.messages["world"].append(Message(f"p{i}", f"msg{i}", i))
        page1 = cs.get_history("world", 0, 10)
        assert len(page1) == 10  # bug3: 返回100条

class TestMentionNotification:
    def test_mention_triggers_notification(self):
        cs = ChatSystem()
        cs.send_message("p1", "@p2 hello", "world")
        assert "p2" in cs.mention_notifications  # bug4: 空列表

class TestAdminPermission:
    def test_admin_uses_permission_bits(self):
        cs = ChatSystem()
        cs.admins["p1"] = 0b001  # 只有权限1
        assert cs.is_admin("p1", 0b001) == True
        assert cs.is_admin("p1", 0b010) == False  # bug5: 返回True

class TestHtmlEscape:
    def test_message_escapes_html(self):
        cs = ChatSystem()
        msg = Message("p1", "<script>alert(1)</script>", 0)
        rendered = cs.render_message(msg)
        assert "<script>" not in rendered  # bug6: 没转义

class TestSpamFilter:
    def test_spam_filter_checks_content_similarity(self):
        cs = ChatSystem()
        cs.last_message["p1"] = time.time() - 10  # 超过冷却
        cs.last_content["p1"] = "hello world"
        # 换个说法但内容相似，应该被拦
        assert cs.can_send("p1", "hello  world", 3.0) == False  # bug7: 返回True
