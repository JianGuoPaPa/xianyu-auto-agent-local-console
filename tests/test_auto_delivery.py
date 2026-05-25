import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from auto_delivery import build_delivery_key, extract_pending_delivery_event
from context_manager import ChatContextManager
from main import XianyuLive


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class AutoDeliveryTests(unittest.TestCase):
    def test_extracts_paid_system_message_from_chat_payload(self):
        message = {
            "1": {
                "2": "chat123@goofish",
                "10": {
                    "senderUserId": "buyer456",
                    "reminderContent": "[我已付款，等待你发货]",
                    "reminderUrl": "https://www.goofish.com/item?id=abc&itemId=item789",
                },
            },
            "3": {"needPush": "false"},
        }

        event = extract_pending_delivery_event(message)

        self.assertTrue(event["triggered"])
        self.assertEqual(event["chat_id"], "chat123")
        self.assertEqual(event["buyer_id"], "buyer456")
        self.assertEqual(event["item_id"], "item789")

    def test_extracts_pending_ship_red_reminder_from_order_payload(self):
        event = extract_pending_delivery_event(
            {"1": "chat123@goofish", "3": {"redReminder": "等待卖家发货"}}
        )

        self.assertTrue(event["triggered"])
        self.assertEqual(event["chat_id"], "chat123")
        self.assertEqual(event["red_reminder"], "等待卖家发货")

    def test_delivery_key_prefers_order_id_and_falls_back_to_chat_item_buyer(self):
        self.assertEqual(
            build_delivery_key("order123", "chat1", "item1", "buyer1"),
            "order:order123",
        )
        self.assertEqual(
            build_delivery_key(None, "chat1", "item1", "buyer1"),
            "chat:chat1:item:item1:buyer:buyer1",
        )

    def test_context_manager_tracks_latest_chat_target_and_blocks_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chat_history.db"
            manager = ChatContextManager(db_path=str(db_path))
            manager.add_message_by_chat("chat1", "buyer1", "item1", "user", "怎么发货")

            target = manager.get_latest_chat_delivery_target("chat1")

            self.assertEqual(target["user_id"], "buyer1")
            self.assertEqual(target["item_id"], "item1")
            self.assertTrue(
                manager.reserve_auto_delivery(
                    "chat:chat1:item:item1:buyer:buyer1",
                    chat_id="chat1",
                    item_id="item1",
                    buyer_id="buyer1",
                    delivery_text="网盘链接",
                )
            )
            manager.mark_auto_delivery_result(
                "chat:chat1:item:item1:buyer:buyer1",
                "success",
            )
            self.assertFalse(
                manager.reserve_auto_delivery(
                    "chat:chat1:item:item1:buyer:buyer1",
                    chat_id="chat1",
                    item_id="item1",
                    buyer_id="buyer1",
                    delivery_text="网盘链接",
                )
            )
            self.assertEqual(
                manager.get_auto_delivery_record("chat:chat1:item:item1:buyer:buyer1")["status"],
                "success",
            )

    def test_xianyu_live_sends_configured_delivery_text_on_pending_ship_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chat_history.db"
            manager = ChatContextManager(db_path=str(db_path))
            manager.add_message_by_chat("chat1", "buyer1", "item1", "user", "怎么发货")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                INSERT INTO item_reply_profiles (item_id, enabled, delivery_text, custom_prompt, updated_at)
                VALUES ('item1', 1, '百度网盘链接：https://pan.baidu.com/s/demo', '', '2026-05-20T10:00:00')
                """
            )
            conn.commit()
            conn.close()

            live = object.__new__(XianyuLive)
            live.context_manager = manager
            live.myid = "seller1"
            ws = FakeWebSocket()

            handled = asyncio.run(
                live.handle_auto_delivery_event(
                    {"1": "chat1@goofish", "3": {"redReminder": "等待卖家发货"}},
                    ws,
                )
            )

            self.assertTrue(handled)
            self.assertEqual(len(ws.sent), 1)
            self.assertEqual(ws.sent[0]["body"][0]["cid"], "chat1@goofish")
            self.assertEqual(ws.sent[0]["body"][1]["actualReceivers"][0], "buyer1@goofish")
            record = manager.get_auto_delivery_record("chat:chat1:item:item1:buyer:buyer1")
            self.assertEqual(record["status"], "success")


if __name__ == "__main__":
    unittest.main()
