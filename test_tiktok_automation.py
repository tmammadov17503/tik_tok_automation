import tempfile
import unittest
from pathlib import Path

from tiktok_automation import (
    AutomationController,
    PostQueueManager,
    english_story_hashtags,
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
                    }
                ],
            )

            self.assertEqual(items[0]["story_category"], "cat animation")
            self.assertIn("#catsoftiktok", items[0]["hashtags"])
            self.assertNotIn("#historytok", items[0]["hashtags"])

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
