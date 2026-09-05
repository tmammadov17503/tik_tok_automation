import threading
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from telegram_bot import TelegramBotService
from tiktok_automation import AutomationController


class QueueHealthTests(unittest.TestCase):
    def make_controller(self, sources, items):
        controller = AutomationController.__new__(AutomationController)
        controller.sources = SimpleNamespace(list_sources=lambda: sources)
        controller.post_queue = SimpleNamespace(
            list_items=lambda: items,
            items_for_source=lambda sid: [i for i in items if i.get("source_id") == sid],
            allocated_count_for_source=lambda sid: sum(
                i.get("source_id") == sid and i.get("status") in
                {"pending", "uploading", "processing", "sent_to_inbox"} for i in items
            ),
            remote_pending_count=lambda: sum(i.get("status") in
                {"uploading", "processing", "sent_to_inbox"} for i in items),
        )
        controller.max_pending_shares = 5
        controller._active_account_profile = lambda: "main_ru"
        controller._maybe_retire_weak_source = lambda source: False
        controller._maybe_create_autonomous_source = lambda: None
        controller._maybe_finalize_source = Mock()
        controller.append_log = Mock()
        controller.notify = Mock()
        controller._lock = threading.Lock()
        controller._read_state = lambda: {}
        controller._write_state = Mock()
        return controller

    def source(self, sid="old", posted=7):
        return dict(id=sid, planned_clips=8, posted_clips=posted, title=sid,
                    account_profile="main_ru", status="active", added_at=sid)

    def test_fully_delivered_batch_does_not_block_next_source(self):
        old, new = self.source(), self.source("z-new", 0)
        items = [dict(id="last", source_id="old", status="sent_to_inbox")]
        controller = self.make_controller([old, new], items)
        self.assertEqual(controller._pick_source_for_generation()["id"], "z-new")
        self.assertEqual(old["posted_clips"], 7)
        self.assertEqual(items[0]["status"], "sent_to_inbox")
        controller._maybe_finalize_source.assert_not_called()

    def test_unfinished_delivery_preserves_source_order(self):
        for status in ("pending", "uploading", "processing", "failed"):
            with self.subTest(status=status):
                old, new = self.source(), self.source("z-new", 0)
                controller = self.make_controller([old, new], [
                    dict(id="last", source_id="old", status=status)])
                self.assertEqual(controller._current_sequence_source()["id"], "old")

    def test_all_eight_inbox_items_complete_delivery_but_not_publication(self):
        old = self.source(posted=0)
        items = [dict(id=str(n), source_id="old", status="sent_to_inbox") for n in range(8)]
        controller = self.make_controller([old], items)
        self.assertTrue(controller._source_delivery_complete(old))
        self.assertFalse(controller._source_is_finished(old))

    def test_unknown_zero_plan_is_not_delivery_complete(self):
        source = dict(self.source(), planned_clips=0, posted_clips=0)
        controller = self.make_controller([source], [])
        self.assertFalse(controller._source_delivery_complete(source))

    def test_duplicate_item_ids_do_not_complete_delivery(self):
        source = self.source(posted=6)
        item = dict(id="one", source_id="old", status="sent_to_inbox")
        controller = self.make_controller([source], [item, dict(item)])
        self.assertFalse(controller._source_delivery_complete(source))

    def test_full_inbox_blocks_costly_generation_for_both_modes(self):
        source = self.source("new", 0)
        items = [dict(id=str(n), source_id="old", status="sent_to_inbox") for n in range(5)]
        controller = self.make_controller([source], items)
        controller._pick_source_for_generation = Mock(side_effect=AssertionError("No generation at inbox cap"))
        controller._generate_from_next_source()
        controller._pick_source_for_generation.assert_not_called()

    def test_wait_reason_names_full_inbox_without_claiming_crash(self):
        controller = self.make_controller([], [dict(status="sent_to_inbox") for _ in range(5)])
        message = controller.generation_wait_reason()
        self.assertIn("5/5", message)
        self.assertIn("/inbox", message)

    def test_delivery_complete_has_clear_wait_reason_if_no_next_source(self):
        controller = self.make_controller([self.source()], [
            dict(id="last", source_id="old", status="sent_to_inbox")])
        self.assertIn("delivered", controller.generation_wait_reason())

    def test_next_queued_source_is_not_reported_blocked_by_old_inbox(self):
        controller = self.make_controller([self.source(), self.source("z-new", 0)], [
            dict(id="last", source_id="old", status="sent_to_inbox")])
        self.assertEqual(controller.generation_wait_reason(), "")

    def test_parked_source_reason_is_visible(self):
        source = dict(self.source(), status="parked", last_error="YouTube sign-in challenge")
        controller = self.make_controller([source], [])
        self.assertIn("source access", controller.generation_wait_reason().lower())

    def test_inbox_list_uses_story_names_and_delivery_times(self):
        controller = self.make_controller([], [dict(
            id="a", status="sent_to_inbox", story_title="The Hidden Temple",
            inbox_delivered_at="2026-09-01T10:00:00Z")])
        text = controller.inbox_summary_text()
        self.assertIn("The Hidden Temple", text)
        self.assertIn("2026-09-01", text)
        self.assertNotIn("money_", text)

    def test_inbox_command_routes_read_only(self):
        bot = TelegramBotService.__new__(TelegramBotService)
        bot.automation = SimpleNamespace(inbox_summary_text=lambda: "Inbox summary")
        bot._send_message = Mock()
        bot._handle_text("token", "chat", "/inbox")
        bot._send_message.assert_called_once_with("token", "chat", "Inbox summary")

    def test_wait_notification_is_sent_once_a_day_not_every_cycle(self):
        controller = self.make_controller([], [dict(status="sent_to_inbox") for _ in range(5)])
        state = {}
        controller._read_state = lambda: dict(state)
        controller._write_state = lambda value: state.update(value)
        controller._notify_generation_wait()
        controller._notify_generation_wait()
        controller.notify.assert_called_once()
        state["last_wait_notice_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        controller._notify_generation_wait()
        self.assertEqual(controller.notify.call_count, 2)

    def test_cooldown_does_not_start_generation_even_with_new_queued_source(self):
        controller = self.make_controller([self.source("new", 0)], [])
        controller._running = threading.Lock()
        controller._read_state = lambda: dict(enabled=True, next_run_at=(
            datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
        controller._refresh_remote_statuses = Mock(return_value=0)
        controller._generate_from_next_source = Mock()
        controller._sync_public_video_metrics = Mock()
        controller._run_cycle(forced=False)
        controller._refresh_remote_statuses.assert_not_called()
        controller._generate_from_next_source.assert_not_called()
        controller._sync_public_video_metrics.assert_not_called()
        self.assertFalse(controller._running.locked())

    def test_processing_wait_and_empty_inbox_are_clear(self):
        controller = self.make_controller([self.source()], [dict(source_id="old", status="processing")])
        self.assertIn("processing", controller.generation_wait_reason())
        self.assertIn("No videos", controller.inbox_summary_text())

    def test_telegram_status_includes_waiting_reason(self):
        bot = TelegramBotService.__new__(TelegramBotService)
        bot.automation = SimpleNamespace(status=lambda: dict(waiting_reason="Inbox full", enabled=True))
        self.assertIn("Waiting: Inbox full", bot._status_text())


if __name__ == "__main__":
    unittest.main()
