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

    def test_caption_uses_every_narration_word_instead_of_static_onscreen_phrase(self) -> None:
        beat = {
            "narration": "Every spoken narration word must appear on screen.",
            "onscreen_text": "THE CASE BROKE",
        }

        self.assertEqual(
            story._caption_text_for_beat(beat),
            "Every spoken narration word must appear on screen.",
        )

    def test_caption_cues_cover_every_word_once_without_overlapping_windows(self) -> None:
        narration = "Every spoken word stays centered and perfectly timed."
        raw_words = [
            {"text": word, "start": index * 0.4, "end": (index + 1) * 0.4}
            for index, word in enumerate(narration.split())
        ]

        words = story._normalized_alignment_words(narration, raw_words, duration=3.2)
        cues = story._caption_cues(words, max_words=3)

        self.assertEqual([word.text for word in words], narration.split())
        self.assertEqual(len(cues), len(words))
        self.assertTrue(all(len(cue.group_words) <= 3 for cue in cues))
        self.assertTrue(all(cue.end > cue.start for cue in cues))
        self.assertTrue(all(left.end <= right.start for left, right in zip(cues, cues[1:])))
        self.assertEqual(
            [cue.group_words[cue.active_index] for cue in cues],
            narration.split(),
        )

    def test_ass_captions_are_centered_and_only_one_word_is_red_per_cue(self) -> None:
        narration = "Every word moves in time."
        raw_words = [
            {"text": word, "start": index * 0.5, "end": (index + 1) * 0.5}
            for index, word in enumerate(narration.split())
        ]
        words = story._normalized_alignment_words(narration, raw_words, duration=2.5)
        cues = story._caption_cues(words, max_words=3)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "story_captions.ass"
            story._write_story_caption_ass(cues, path)
            content = path.read_text(encoding="utf-8")

        self.assertIn("Alignment=2", content)
        self.assertIn("MarginL=120", content)
        self.assertIn("MarginR=120", content)
        dialogue_lines = [line for line in content.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(dialogue_lines), len(words))
        self.assertTrue(all(line.count("&H5E4DFF&") == 1 for line in dialogue_lines))

    def test_final_merge_burns_the_global_ass_track_into_the_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captions = root / "story_captions.ass"
            captions.write_text("[Script Info]\n", encoding="utf-8")
            with patch.object(story, "_run") as run:
                story._merge_segments_with_audio(
                    "ffmpeg",
                    root / "concat.txt",
                    root / "voiceover.mp3",
                    root / "final.mp4",
                    65.0,
                    captions_path=captions,
                )

        command = run.call_args.args[0]
        self.assertTrue(any("ass=" in part for part in command))
        self.assertIn("libx264", command)

    def test_alignment_falls_back_to_openai_and_still_requires_every_word(self) -> None:
        narration = "Every spoken word remains visible."
        complete_words = [
            {"text": word, "start": index * 0.5, "end": (index + 1) * 0.5}
            for index, word in enumerate(narration.split())
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            voiceover = root / "voiceover.mp3"
            voiceover.write_bytes(b"audio")
            alignment = root / "voiceover_alignment.json"
            with (
                patch.dict(
                    os.environ,
                    {"ELEVENLABS_API_KEY": "el-test", "OPENAI_API_KEY": "oa-test"},
                    clear=False,
                ),
                patch.object(story, "_media_duration", return_value=3.0),
                patch.object(
                    story,
                    "_elevenlabs_word_alignment",
                    return_value=complete_words[:-1],
                ),
                patch.object(story, "_openai_word_alignment", return_value=complete_words),
            ):
                payload = story._generate_story_word_alignment(
                    narration,
                    voiceover,
                    alignment,
                    logger=lambda _message: None,
                )

        self.assertEqual(payload["provider"], "openai_whisper_words")
        self.assertEqual(payload["expected_word_count"], len(narration.split()))
        self.assertEqual(payload["aligned_word_count"], len(narration.split()))
        self.assertEqual(payload["coverage"], 1.0)

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
