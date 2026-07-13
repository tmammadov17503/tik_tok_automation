import os
import tempfile
import unittest
from pathlib import Path

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

    def test_elevenlabs_budget_guard_limits_monthly_characters(self) -> None:
        original_limit = os.environ.get("ELEVENLABS_MONTHLY_CHARACTER_LIMIT")
        original_max_story = os.environ.get("ELEVENLABS_MAX_STORY_CHARS")
        original_usage = os.environ.get("ELEVENLABS_USAGE_PATH")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                usage_path = Path(tmp) / "usage.json"
                os.environ["ELEVENLABS_MONTHLY_CHARACTER_LIMIT"] = "10"
                os.environ["ELEVENLABS_MAX_STORY_CHARS"] = "200"
                os.environ["ELEVENLABS_USAGE_PATH"] = str(usage_path)

                self.assertTrue(story._elevenlabs_budget_allows("12345", Path(tmp) / "voice.mp3"))
                self.assertFalse(story._elevenlabs_budget_allows("12345678901", Path(tmp) / "voice.mp3"))
        finally:
            _restore_env("ELEVENLABS_MONTHLY_CHARACTER_LIMIT", original_limit)
            _restore_env("ELEVENLABS_MAX_STORY_CHARS", original_max_story)
            _restore_env("ELEVENLABS_USAGE_PATH", original_usage)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
