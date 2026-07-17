import tempfile
import unittest
from pathlib import Path

from tiktok_automation import (
    AutomationController,
    PostQueueManager,
    english_story_hashtags,
    story_item_identity_keys,
)


class EnglishStoryDistributionTests(unittest.TestCase):
    def test_story_hashtags_are_niche_specific(self) -> None:
        cat_tags = english_story_hashtags("cat animation")
        court_tags = english_story_hashtags("court case")

        self.assertIn("#catsoftiktok", cat_tags)
        self.assertIn("#animation", cat_tags)
        self.assertNotIn("#historytok", cat_tags)
        self.assertIn("#lawtok", court_tags)
        self.assertIn("#courtcase", court_tags)
        self.assertNotEqual(cat_tags, court_tags)

    def test_story_category_and_hashtags_survive_queueing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "story_01_captioned.mp4"
            clip.write_bytes(b"video")
            queue = PostQueueManager(root, ["#storytime"])
            source = {
                "id": "source-1",
                "source_url": "story://autonomous-english-reels/test-r00",
                "title": "English stories",
                "content_mode": "monetization",
                "account_profile": "future_en",
                "audience_language": "en",
            }

            items = queue.enqueue_clip_files(
                source,
                [clip],
                segments=[
                    {
                        "start_seconds": 0,
                        "end_seconds": 70,
                        "excerpt": "An animated cat saved the harbor.",
                        "story_category": "cat animation",
                        "story_title": "The Cat Who Saved The Harbor",
                        "story_short_title": "THE HARBOR CAT",
                        "story_slug": "cat-harbor-rescue",
                    }
                ],
            )

            self.assertEqual(items[0]["story_category"], "cat animation")
            self.assertEqual(items[0]["story_title"], "The Cat Who Saved The Harbor")
            self.assertEqual(items[0]["story_slug"], "cat-harbor-rescue")
            self.assertIn("#catsoftiktok", items[0]["hashtags"])
            self.assertNotIn("#historytok", items[0]["hashtags"])

    def test_duplicate_story_is_rejected_across_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_clip = root / "story_01_captioned.mp4"
            second_clip = root / "story_02_captioned.mp4"
            first_clip.write_bytes(b"first")
            second_clip.write_bytes(b"second")
            queue = PostQueueManager(root, ["#storytime"])
            first_source = {
                "id": "source-1",
                "source_url": "story://autonomous-english-reels/first-r00",
                "content_mode": "monetization",
                "account_profile": "future_en",
                "audience_language": "en",
            }
            second_source = dict(first_source, id="source-2", source_url="story://autonomous-english-reels/second-r08")
            segment = {
                "excerpt": "Nine hikers entered the mountains.",
                "story_category": "survival mystery",
                "story_title": "The Hikers Who Fled Their Own Tent",
                "story_slug": "dyatlov-pass-1959",
            }

            first_items = queue.enqueue_clip_files(first_source, [first_clip], segments=[segment])
            duplicate_items = queue.enqueue_clip_files(second_source, [second_clip], segments=[segment])

            self.assertEqual(len(first_items), 1)
            self.assertEqual(duplicate_items, [])
            self.assertEqual(len(queue.list_items()), 1)

    def test_story_identity_uses_slug_and_hook_for_legacy_items(self) -> None:
        modern = story_item_identity_keys(
            {"story_slug": "dyatlov-pass-1959", "segment_excerpt": "Nine hikers entered the mountains."}
        )
        legacy = story_item_identity_keys({"segment_excerpt": "  Nine HIKERS entered the mountains!  "})

        self.assertIn("slug:dyatlov-pass-1959", modern)
        self.assertTrue(modern.intersection(legacy))

    def test_unique_story_count_deduplicates_repeated_legacy_hooks(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        items = [
            {
                "source_url": "story://autonomous-english-reels/first",
                "account_profile": "future_en",
                "audience_language": "en",
                "content_mode": "monetization",
                "segment_excerpt": "Nine hikers entered the mountains.",
            },
            {
                "source_url": "story://autonomous-english-reels/second",
                "account_profile": "future_en",
                "audience_language": "en",
                "content_mode": "monetization",
                "segment_excerpt": "Nine hikers entered the mountains!",
            },
            {
                "source_url": "story://autonomous-english-reels/second",
                "account_profile": "future_en",
                "audience_language": "en",
                "content_mode": "monetization",
                "segment_excerpt": "Three lighthouse keepers vanished.",
            },
        ]
        controller.post_queue = type("Queue", (), {"list_items": lambda _self: items})()

        self.assertEqual(controller._unique_english_story_count(), 2)

    def test_english_notifications_use_story_title_not_internal_counter(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        item = {
            "source_url": "story://autonomous-english-reels/test-r08",
            "source_title": "Autonomous English Story Batch",
            "content_mode": "monetization",
            "account_profile": "future_en",
            "audience_language": "en",
            "clip_label": "money_02_vertical_captioned",
            "story_title": "The Lighthouse Keepers Who Vanished",
            "segment_excerpt": "Three keepers vanished from an island.",
            "hashtags": ["#storytime"],
        }

        message = controller._upload_delivery_message(item, "Monetization")

        self.assertIn("The Lighthouse Keepers Who Vanished", message)
        self.assertNotIn("money_02", message)

    def test_remote_inbox_refresh_preserves_first_delivery_time(self) -> None:
        original_time = "2026-07-11T22:55:03Z"
        item = {
            "id": "clip-1",
            "status": "sent_to_inbox",
            "publish_id": "publish-1",
            "inbox_delivered_at": original_time,
            "clip_label": "money_07_vertical_captioned",
        }
        updates: list[dict[str, object]] = []
        controller = AutomationController.__new__(AutomationController)
        controller.post_queue = type(
            "Queue",
            (),
            {
                "active_items": lambda _self: [item],
                "update_item": lambda _self, _item_id, **changes: updates.append(changes),
            },
        )()
        controller.publisher = type(
            "Publisher",
            (),
            {"fetch_status": lambda _self, _publish_id: {"status": "SEND_TO_USER_INBOX"}},
        )()
        controller.append_log = lambda _message: None
        controller.notify = lambda _message: None

        changed = controller._refresh_remote_statuses()

        self.assertEqual(changed, 0)
        self.assertEqual(updates[0]["inbox_delivered_at"], original_time)

    def test_caption_uses_queued_niche_hashtags(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        controller.post_queue = type("Queue", (), {"default_hashtags": ["#storytime"]})()
        item = {
            "source_id": "source-1",
            "clip_label": "money_01_vertical_captioned",
            "content_mode": "monetization",
            "account_profile": "future_en",
            "audience_language": "en",
            "story_category": "world economy story",
            "segment_excerpt": "A flower became more valuable than a house.",
            "hashtags": english_story_hashtags("world economy story"),
        }

        caption = controller._caption_hint_for_item(item)

        self.assertIn("#economics", caption)
        self.assertIn("#moneyhistory", caption)
        self.assertNotIn("#mysterytok", caption)


if __name__ == "__main__":
    unittest.main()
