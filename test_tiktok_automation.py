import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from tiktok_automation import (
    ACCOUNT_PROFILE_MAIN_RU,
    AUTONOMOUS_RUSSIAN_SOURCE_URL,
    AutomationController,
    CANONICAL_TIKTOK_HASHTAGS,
    PostQueueManager,
    english_story_hashtags,
    english_story_lane,
    is_youtube_download_blocker,
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

    def test_story_lane_is_determined_by_category_not_transcript_keywords(self) -> None:
        self.assertEqual(english_story_lane("world economy story"), "economy")
        self.assertEqual(english_story_lane("cat animation"), "cat")
        self.assertEqual(english_story_lane("strange true history"), "history")

    def test_story_caption_repairs_stale_hashtags_and_ignores_unrelated_excerpt_words(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        controller.post_queue = type("Queue", (), {"default_hashtags": ["#storytime"]})()
        item = {
            "source_id": "source-economy",
            "clip_label": "story_01_vertical_captioned",
            "content_mode": "monetization",
            "account_profile": "future_en",
            "audience_language": "en",
            "story_category": "world economy story",
            "segment_excerpt": "A cat entered court and posted a confession before the market crashed.",
            "hashtags": english_story_hashtags("cat animation"),
        }

        caption = controller._caption_hint_for_item(item)

        self.assertIn("#economics", caption)
        self.assertIn("#moneyhistory", caption)
        self.assertNotIn("#catsoftiktok", caption)
        self.assertTrue(any(phrase in caption for phrase in ("price", "economy", "excitement")))

    def test_public_reconciliation_uses_repaired_story_hashtags(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        clip = {
            "account_profile": "future_en",
            "audience_language": "en",
            "story_category": "world economy story",
            "hashtags": english_story_hashtags("cat animation"),
        }

        expected = controller._expected_public_match_hashtags(clip)

        self.assertIn("#economics", expected)
        self.assertIn("#moneyhistory", expected)
        self.assertNotIn("#catsoftiktok", expected)

    def test_story_discovery_wait_does_not_increment_source_failures(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        calls: list[str] = []
        controller.sources = type(
            "Sources",
            (),
            {"mark_source_failure": lambda _self, *_args, **_kwargs: calls.append("failure")},
        )()
        controller.append_log = lambda _message: None
        controller.notify = lambda _message: None

        from tiktok_story_short import StoryDiscoveryUnavailable

        controller._record_source_generation_failure(
            {"id": "story-source", "title": "English stories", "source_url": "story://batch"},
            StoryDiscoveryUnavailable("fresh topic unavailable"),
        )

        self.assertEqual(calls, [])

    def test_youtube_reload_challenge_is_a_download_blocker(self) -> None:
        self.assertTrue(
            is_youtube_download_blocker(
                "ERROR: [youtube] reDWpYU3UZw: The page needs to be reloaded.",
                "https://youtu.be/reDWpYU3UZw",
            )
        )

    def test_youtube_reload_challenge_parks_source_without_consuming_retry(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        calls: list[tuple[str, str]] = []
        controller.sources = type(
            "Sources",
            (),
            {
                "defer_source_failure": lambda _self, source_id, error: calls.append((source_id, error)),
                "mark_source_failure": lambda _self, *_args, **_kwargs: self.fail(
                    "A YouTube access challenge must not consume a source retry."
                ),
            },
        )()
        controller.append_log = lambda _message: None
        controller.notify = lambda _message: None

        controller._record_source_generation_failure(
            {
                "id": "youtube-source",
                "title": "Queued YouTube source",
                "source_url": "https://youtu.be/reDWpYU3UZw",
            },
            RuntimeError("ERROR: [youtube] reDWpYU3UZw: The page needs to be reloaded."),
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "youtube-source")

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
        self.assertIn("AI-generated content label", message)

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


class StoryEventPersistenceTests(unittest.TestCase):
    def test_event_identity_survives_queue_reload_and_prevents_retitled_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "story.mp4"
            clip.write_bytes(b"test media")
            source = dict(id="source-a", source_url="story://english/a",
                          account_profile="future_en", audience_language="en", content_mode="monetization")
            segment = dict(story_title="A Court Case", story_slug="first-title",
                           story_event_id="us-mata-avianca-sanctions-2023", excerpt="A judge found invented precedents.")
            queue = PostQueueManager(root, [])
            queue.enqueue_clip_files(source, [clip], segments=[segment])
            reloaded = PostQueueManager(root, [])
            self.assertEqual(reloaded.list_items()[0].get("story_event_id"), segment["story_event_id"])
            self.assertIn("event:us-mata-avianca-sanctions-2023", story_item_identity_keys(reloaded.list_items()[0]))
            duplicate = dict(segment, story_title="The Judge Discovered The Truth", story_slug="new-slug",
                             excerpt="The citations did not exist.")
            self.assertEqual(reloaded.enqueue_clip_files(dict(source, id="source-b"), [clip], segments=[duplicate]), [])


class RussianOriginalStoryDistributionTests(unittest.TestCase):
    def test_russian_original_story_keeps_required_hashtags_and_follow_cta(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        controller.post_queue = type("Queue", (), {"default_hashtags": list(CANONICAL_TIKTOK_HASHTAGS)})()
        item = {
            "source_url": f"{AUTONOMOUS_RUSSIAN_SOURCE_URL}/test-r00",
            "source_id": "source-ru",
            "content_mode": "monetization",
            "account_profile": "main_ru",
            "audience_language": "ru",
            "story_category": "cinema history",
            "story_title": "Тайна старого кинотеатра",
            "segment_excerpt": "Эту пленку никто не должен был увидеть.",
        }

        caption = controller._caption_hint_for_item(item)

        self.assertIn("Подпишись", caption)
        self.assertEqual(caption.splitlines()[-1].split()[:5], CANONICAL_TIKTOK_HASHTAGS)

    def test_russian_story_delivery_uses_title_and_ai_label_note(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        controller.post_queue = type("Queue", (), {"default_hashtags": list(CANONICAL_TIKTOK_HASHTAGS)})()
        item = {
            "source_url": f"{AUTONOMOUS_RUSSIAN_SOURCE_URL}/test-r00",
            "source_title": "Original Russian Story Batch",
            "content_mode": "monetization",
            "account_profile": "main_ru",
            "audience_language": "ru",
            "story_category": "cinema history",
            "story_title": "Тайна старого кинотеатра",
            "segment_excerpt": "Эту пленку никто не должен был увидеть.",
        }

        message = controller._upload_delivery_message(item, "Monetization")

        self.assertIn("Тайна старого кинотеатра", message)
        self.assertIn("AI-generated content label", message)
        self.assertNotIn("0/3", message)

    def test_existing_russian_youtube_source_blocks_autonomous_batch_creation(self) -> None:
        controller = AutomationController.__new__(AutomationController)
        sources = [
            {
                "id": "youtube-source",
                "source_url": "https://youtu.be/example",
                "account_profile": ACCOUNT_PROFILE_MAIN_RU,
                "audience_language": "ru",
                "added_at": "2026-08-24T00:00:00Z",
            }
        ]
        controller.sources = type("Sources", (), {"list_sources": lambda _self: list(sources)})()
        controller._maybe_create_autonomous_source = lambda: self.fail("autonomous source must wait")

        ordered = controller._ordered_sources()

        self.assertEqual([item["id"] for item in ordered], ["youtube-source"])

    def test_russian_autonomous_batch_uses_three_videos_and_continuous_rotation(self) -> None:
        controller = AutomationController.__new__(AutomationController)

        class Sources:
            def __init__(self) -> None:
                self.items: list[dict[str, object]] = []

            def add_source(self, payload: dict[str, object]) -> None:
                self.items.append(dict(payload, id="ru-original-source", added_at="2026-08-24T00:00:00Z"))

            def list_sources(self) -> list[dict[str, object]]:
                return list(self.items)

        controller.sources = Sources()
        controller.post_queue = type("Queue", (), {"list_items": lambda _self: []})()
        controller.append_log = lambda _message: None
        controller.notify = lambda _message: None
        controller._active_account_profile = lambda: ACCOUNT_PROFILE_MAIN_RU

        with patch.dict(
            os.environ,
            {
                "TIKTOK_AUTONOMOUS_RUSSIAN_STORIES": "true",
                "TIKTOK_RU_ORIGINAL_BATCH_SIZE": "3",
            },
            clear=False,
        ):
            created = controller._maybe_create_autonomous_source()

        self.assertIsNotNone(created)
        self.assertEqual(created["planned_clips"], 3)
        self.assertTrue(str(created["source_url"]).startswith(AUTONOMOUS_RUSSIAN_SOURCE_URL))
        self.assertEqual(created["audience_language"], "ru")


if __name__ == "__main__":
    unittest.main()
