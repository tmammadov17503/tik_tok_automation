import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tiktok_story_short as story


class StoryCaptionLayoutTests(unittest.TestCase):
    def test_caption_groups_allow_three_words_when_they_fit(self) -> None:
        groups = story._caption_word_groups("LIGHT WAS THERE.", max_words=story.CAPTION_MAX_WORDS)

        self.assertEqual(groups, [["LIGHT", "WAS", "THERE."]])
        self.assertTrue(all(len(group) <= 3 for group in groups))

    def test_caption_word_slots_keep_compact_visible_gap(self) -> None:
        slots = story._caption_line_slots(["WAS", "THERE."], 64)

        first_end = slots[0][0] + slots[0][1]
        second_start = slots[1][0]
        self.assertGreaterEqual(second_start - first_end, story.CAPTION_MIN_WORD_GAP)
        self.assertLessEqual(second_start - first_end, 24)

    def test_caption_prefers_short_onscreen_phrase_over_full_narration(self) -> None:
        beat = {
            "narration": "This is a long narration line that should stay in the voiceover and not become a heavy caption filter.",
            "onscreen_text": "THE CASE BROKE",
        }

        self.assertEqual(story._caption_text_for_beat(beat), "THE CASE BROKE")

    def test_caption_layout_accepts_screenshot_regression_phrase(self) -> None:
        test_story = {
            "beats": [
                {
                    "label": "The Reveal",
                    "narration": "The light was there. The men were not.",
                    "onscreen_text": "LIGHT WAS THERE",
                }
            ]
        }

        self.assertEqual(story._caption_layout_issues(test_story), [])

    def test_story_lane_rotation_covers_requested_niches(self) -> None:
        source = {
            "id": "test",
            "source_url": "autonomous://english-stories/test",
            "content_mode": "monetization",
            "account_profile": "future_en",
            "audience_language": "en",
        }

        categories = [
            story._build_library_story(source, sequence_index=index)["category"].lower()
            for index in range(1, len(story.GENRE_ROTATION) + 1)
        ]

        self.assertTrue(any("history" in category for category in categories))
        self.assertTrue(any("mystery" in category for category in categories))
        self.assertTrue(any("lawsuit" in category for category in categories))
        self.assertTrue(any("court" in category for category in categories))
        self.assertTrue(any("storytime" in category or "reddit" in category for category in categories))
        self.assertTrue(any("cat" in category for category in categories))
        self.assertTrue(any("economy" in category for category in categories))
        self.assertTrue(any("2d" in category or "animation" in category for category in categories))

    def test_story_badges_match_new_niches(self) -> None:
        self.assertEqual(story._story_badge({"category": "lawsuit story"}), "LAWSUIT STORY")
        self.assertEqual(story._story_badge({"category": "cat animation"}), "CAT ANIMATION")
        self.assertEqual(story._story_badge({"category": "world economy story"}), "ECONOMY STORY")

    def test_elevenlabs_budget_uses_shared_weekly_credit_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "shared.sqlite3"
            with patch.dict(
                os.environ,
                {
                    "ELEVENLABS_SHARED_LEDGER_PATH": str(ledger_path),
                    "ELEVENLABS_SHARED_WEEKLY_CREDIT_BUDGET": "2",
                    "ELEVENLABS_PIPELINE_WEEKLY_CREDIT_BUDGET": "2",
                    "ELEVENLABS_CREDITS_PER_CHARACTER": "0.5",
                },
                clear=False,
            ):
                decision = story._reserve_elevenlabs_credits("12345", Path(tmp) / "voice.mp3")

            self.assertFalse(decision.allowed)
            self.assertIn(decision.reason, {"pipeline_weekly_budget", "shared_weekly_budget"})
if __name__ == "__main__":
    unittest.main()
