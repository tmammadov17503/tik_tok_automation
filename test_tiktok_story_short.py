import unittest

import tiktok_story_short as story


class StoryCaptionLayoutTests(unittest.TestCase):
    def test_caption_groups_keep_short_phrases_separated(self) -> None:
        groups = story._caption_word_groups("LIGHT WAS THERE.", max_words=story.CAPTION_MAX_WORDS)

        self.assertEqual(groups, [["LIGHT", "WAS"], ["THERE."]])
        self.assertTrue(all(len(group) <= 2 for group in groups))

    def test_caption_word_slots_leave_visible_gap(self) -> None:
        slots = story._caption_line_slots(["WAS", "THERE."], 64)

        first_end = slots[0][0] + slots[0][1]
        second_start = slots[1][0]
        self.assertGreaterEqual(second_start - first_end, story.CAPTION_MIN_WORD_GAP)

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


if __name__ == "__main__":
    unittest.main()
