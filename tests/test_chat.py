"""聊天频道 - 红态测试"""
import pytest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat.channel import ChatChannel, Player

class TestBroadcastFailureIsolation:
    def test_one_player_failure_does_not_block_others(self):
        cc = ChatChannel()
        cc.players["a"] = Player("a", message_queue=["x"]*100)
        cc.players["b"] = Player("b")
        cc.players["c"] = Player("c")
        # a的队列满了，但b和c应该能收到
        sent = cc.broadcast_message("hello")
        assert sent >= 2

class TestOfflineMessageLimit:
    def test_offline_messages_have_limit(self):
        cc = ChatChannel()
        for i in range(10000):
            cc.store_offline_message("p1", f"msg{i}")
        assert len(cc.offline_messages["p1"]) <= 1000

class TestMutedPlayerCannotSpeak:
    def test_muted_player_cannot_speak(self):
        cc = ChatChannel()
        cc.players["p1"] = Player("p1", muted=True)
        assert cc.can_speak("p1") == False

class TestMessageFilter:
    def test_filter_catches_variations(self):
        cc = ChatChannel()
        result = cc.filter_message("****")  # 4个星
        assert "[filtered]" in result or result != "****"

class TestDeadLetterQueue:
    def test_failed_messages_go_to_dead_letter(self):
        cc = ChatChannel()
        cc.players["a"] = Player("a", message_queue=["x"]*100)
        cc.broadcast_message("test")
        # 失败的消息应该进死信队列
        assert hasattr(cc, "dead_letter")
