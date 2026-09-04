import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import tiktok_story_short as story


class StoryCaptionLayoutTests(unittest.TestCase):
    def test_beat_render_retries_atomically_with_ultrafast_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            background = root / "scene.png"
            output = root / "beat_02.mp4"
            background.write_bytes(b"image")
            commands: list[list[str]] = []

            def run(command: list[str], *, timeout: int):
                commands.append(command)
                self.assertNotEqual(Path(command[-1]), output)
                if len(commands) == 1:
                    raise RuntimeError(f"Command timed out after {timeout} seconds")
                Path(command[-1]).write_bytes(b"valid-video")

            with (
                patch.object(story, "_run", side_effect=run),
                patch.object(
                    story,
                    "_valid_video_file",
                    side_effect=lambda path, **_kwargs: path.exists() and path.stat().st_size > 0,
                ),
            ):
                story._render_beat_segment(
                    "ffmpeg",
                    {"short_title": "A TEST"},
                    {"narration": "The test narration."},
                    2,
                    8,
                    8.0,
                    output,
                    background,
                )
            rendered_bytes = output.read_bytes()

        self.assertEqual(len(commands), 2)
        self.assertIn("veryfast", commands[0])
        self.assertIn("ultrafast", commands[1])
        self.assertEqual(rendered_bytes, b"valid-video")

    def test_completed_beat_is_reused_without_running_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "beat_01.mp4"
            output.write_bytes(b"already-valid")
            messages: list[str] = []
            with (
                patch.object(story, "_valid_video_file", return_value=True),
                patch.object(story, "_render_beat_segment") as render,
            ):
                story._render_or_reuse_story_beat(
                    "ffmpeg",
                    {"short_title": "A TEST"},
                    {"narration": "The test narration."},
                    index=1,
                    total=8,
                    duration=8.0,
                    output_path=output,
                    background_path=root / "scene.png",
                    logger=messages.append,
                )

        render.assert_not_called()
        self.assertTrue(any("Reusing completed story beat 1/8" in message for message in messages))

    def test_final_merge_retries_atomically_instead_of_leaving_partial_mp4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            captions = root / "story_captions.ass"
            output = root / "final.mp4"
            captions.write_text("[Script Info]\n", encoding="utf-8")
            commands: list[list[str]] = []

            def run(command: list[str], *, timeout: int):
                commands.append(command)
                self.assertNotEqual(Path(command[-1]), output)
                if len(commands) == 1:
                    Path(command[-1]).write_bytes(b"partial")
                    raise RuntimeError(f"Command timed out after {timeout} seconds")
                Path(command[-1]).write_bytes(b"complete")

            with (
                patch.object(story, "_run", side_effect=run),
                patch.object(
                    story,
                    "_valid_video_file",
                    side_effect=lambda path, **_kwargs: path.exists() and path.read_bytes() == b"complete",
                ),
            ):
                story._merge_segments_with_audio(
                    "ffmpeg",
                    root / "concat.txt",
                    root / "voiceover.mp3",
                    output,
                    65.0,
                    captions_path=captions,
                )
            rendered_bytes = output.read_bytes()

        self.assertEqual(len(commands), 2)
        self.assertIn("veryfast", commands[0])
        self.assertIn("ultrafast", commands[1])
        self.assertEqual(rendered_bytes, b"complete")

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
            with (
                patch.object(
                    story,
                    "_run",
                    side_effect=lambda command, **_kwargs: Path(command[-1]).write_bytes(b"video"),
                ) as run,
                patch.object(story, "_valid_video_file", return_value=True),
            ):
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

    def test_story_rotation_prioritizes_proven_history_mystery_and_legal_lanes(self) -> None:
        lanes = [lane.lower() for lane in story.GENRE_ROTATION]
        history_or_mystery = sum(
            1
            for lane in lanes
            if any(
                token in lane
                for token in ("history", "historical", "mystery", "ancient", "biography", "lost", "folklore")
            )
        )
        legal = sum(1 for lane in lanes if "lawsuit" in lane or "court" in lane)
        experiments = sum(
            1
            for lane in lanes
            if any(token in lane for token in ("cat", "2d", "economy", "reddit"))
        )

        self.assertEqual(len(lanes), 24)
        self.assertEqual(len(story.FALLBACK_TOPIC_SLUGS), len(lanes))
        self.assertEqual(len(set(story.FALLBACK_TOPIC_SLUGS)), len(lanes))
        self.assertGreaterEqual(history_or_mystery, 12)
        self.assertGreaterEqual(legal, 5)
        self.assertLessEqual(experiments, 5)

    def test_ai_story_category_cannot_override_requested_rotation_lane(self) -> None:
        payload = {
            "slug": "the-price-that-broke-a-city",
            "title": "The Price That Broke A City",
            "short_title": "WHEN PRICES BROKE",
            "hook": "A price doubled until the whole city stopped.",
            "category": "cat animation",
            "beats": [
                {
                    "label": f"Beat {index}",
                    "narration": f"Economy narration for beat {index}.",
                    "onscreen_text": f"PRICE {index}",
                }
                for index in range(1, 9)
            ],
        }

        normalized = story._normalize_ai_story(
            payload,
            source_entry={"source_url": "story://autonomous-english-reels/test-r00"},
            sequence_index=1,
            genre="world economy story",
        )

        self.assertEqual(normalized["category"], "world economy story")

    def test_complete_fallback_cycle_stays_unique_and_in_requested_lanes(self) -> None:
        source = {"source_url": "story://autonomous-english-reels/fallback-r00"}
        seen: set[str] = set()
        selected: list[dict[str, object]] = []

        with patch.object(story, "_ai_story_discovery_enabled", return_value=False):
            for sequence_index in range(1, len(story.GENRE_ROTATION) + 1):
                candidate, selection_index = story._select_unseen_story(
                    source,
                    sequence_index=sequence_index,
                    excluded_story_keys=seen,
                )
                self.assertEqual(selection_index, sequence_index)
                self.assertEqual(candidate["category"], story.GENRE_ROTATION[sequence_index - 1])
                self.assertFalse(story.story_identity_keys(candidate).intersection(seen))
                seen.update(story.story_identity_keys(candidate))
                selected.append(candidate)

        self.assertEqual(len(selected), len(story.GENRE_ROTATION))

    def test_story_follow_cta_is_spoken_once(self) -> None:
        source_story = {
            "beats": [
                {"narration": "This is the opening."},
                {"narration": "This is the ending."},
            ]
        }

        enriched = story._with_opening_hook(source_story, sequence_index=1)
        enriched_again = story._with_opening_hook(enriched, sequence_index=1)
        narration = story.story_narration_text(enriched_again)

        self.assertEqual(narration.count(story.FOLLOW_CTA), 1)
        self.assertTrue(narration.endswith(story.FOLLOW_CTA))

    def test_russian_story_mode_only_accepts_autonomous_russian_sources(self) -> None:
        autonomous_source = {
            "source_url": "story://autonomous-russian-originals/test-r00",
            "content_mode": "monetization",
            "account_profile": "main_ru",
            "audience_language": "ru",
        }
        pasted_youtube_source = dict(
            autonomous_source,
            source_url="https://youtu.be/example",
        )

        with patch.dict(os.environ, {"TIKTOK_RU_STORY_MODE": "true"}, clear=False):
            self.assertTrue(story.original_story_mode_enabled(autonomous_source))
            self.assertFalse(story.original_story_mode_enabled(pasted_youtube_source))

    def test_russian_story_prompt_requires_original_natural_russian(self) -> None:
        prompt = story._ai_story_prompt(
            {
                "title": "Original Russian Story Batch",
                "source_url": "story://autonomous-russian-originals/test-r00",
                "account_profile": "main_ru",
                "audience_language": "ru",
            },
            sequence_index=1,
            genre="cinema history",
        )

        self.assertIn("natural Russian", prompt)
        self.assertIn("Do not summarize copyrighted films", prompt)
        self.assertIn("one fresh vertical short story", prompt)

    def test_russian_story_follow_cta_and_hook_are_spoken_once(self) -> None:
        source_story = {
            "audience_language": "ru",
            "beats": [
                {"narration": "Эта история началась в старом кинотеатре."},
                {"narration": "Правду нашли только много лет спустя."},
            ],
        }

        enriched = story._with_opening_hook(source_story, sequence_index=1)
        enriched_again = story._with_opening_hook(enriched, sequence_index=1)
        narration = story.story_narration_text(enriched_again)

        self.assertEqual(narration.count(story.RUSSIAN_FOLLOW_CTA), 1)
        self.assertTrue(narration.endswith(story.RUSSIAN_FOLLOW_CTA))
        self.assertTrue(story._starts_with_curiosity_hook(narration, language="ru"))

    def test_story_identity_preserves_cyrillic_titles(self) -> None:
        keys = story.story_identity_keys(
            {
                "slug": "old-cinema-secret",
                "title": "Тайна старого кинотеатра",
                "hook": "Эту пленку никто не должен был увидеть",
            }
        )

        self.assertIn("title:тайна старого кинотеатра", keys)
        self.assertIn("hook:эту пленку никто не должен был увидеть", keys)

    def test_story_follow_cta_recognizes_legacy_and_punctuation_variants(self) -> None:
        for existing_cta in (
            "Follow for tomorrow's true 60-second story.",
            "Follow for tomorrow's 60-second story!",
        ):
            source_story = {"beats": [{"narration": f"The ending. {existing_cta}"}]}
            enriched = story._with_opening_hook(source_story, sequence_index=1)
            narration = story.story_narration_text(enriched)

            self.assertEqual(narration.casefold().count("follow for tomorrow"), 1)

    def test_nonpolitical_library_topics_use_neutral_story_beats(self) -> None:
        topic = next(item for item in story.TOPIC_LIBRARY if item["slug"] == "antikythera-mechanism")
        narration = story.story_narration_text({"beats": story._beats_for_topic(topic)}).casefold()

        self.assertNotIn("supporters", narration)
        self.assertNotIn("one leader", narration)
        self.assertNotIn("the room around him", narration)
        self.assertIn("interlocking gears", narration)

    def test_exhausted_second_fallback_cycle_waits_instead_of_repeating(self) -> None:
        source = {"source_url": "story://autonomous-english-reels/fallback-r24"}
        seen = {
            identity
            for index in range(1, len(story.GENRE_ROTATION) + 1)
            for identity in story.story_identity_keys(
                story._build_library_story({}, sequence_index=index)
            )
        }

        with (
            patch.object(story, "_ai_story_discovery_enabled", return_value=False),
            self.assertRaises(story.StoryDiscoveryUnavailable),
        ):
            story._select_unseen_story(source, sequence_index=1, excluded_story_keys=seen)

    def test_story_batch_rotation_offset_continues_across_batches(self) -> None:
        first_batch = {
            "source_url": "story://autonomous-english-reels/20260716T120000Z-r00",
        }
        second_batch = {
            "source_url": "story://autonomous-english-reels/20260717T200000Z-r08",
        }

        first_story = story.build_story(first_batch, sequence_index=1)
        continued_story = story.build_story(second_batch, sequence_index=1)
        expected_story = story._build_library_story(second_batch, sequence_index=9)

        self.assertNotEqual(first_story["category"], continued_story["category"])
        self.assertEqual(continued_story["category"], expected_story["category"])
        self.assertEqual(story.story_rotation_size(), len(story.GENRE_ROTATION))

    def test_unseen_story_selection_skips_repeated_identity(self) -> None:
        repeated = {
            "slug": "dyatlov-pass-1959",
            "title": "Repeated",
            "hook": "Nine hikers entered the mountains.",
            "category": "survival mystery",
            "beats": [{"narration": "Repeated", "onscreen_text": "REPEATED"}],
        }
        unseen = {
            "slug": "flannan-isles-1900",
            "title": "Unseen",
            "hook": "Three lighthouse keepers vanished.",
            "category": "lost place mystery",
            "beats": [{"narration": "Unseen", "onscreen_text": "UNSEEN"}],
        }
        seen = story.story_identity_keys(repeated)

        fallback_topics = [
            {"slug": "dyatlov-pass-1959"},
            {"slug": "flannan-isles-1900"},
        ]
        with (
            patch.object(story, "_ai_story_discovery_enabled", return_value=False),
            patch.object(story, "_fallback_candidates", return_value=fallback_topics),
            patch.object(story, "_library_story_from_topic", side_effect=[repeated, unseen]) as mocked,
        ):
            selected, selection_index = story._select_unseen_story(
                {"source_url": "story://autonomous-english-reels/test-r00"},
                sequence_index=2,
                excluded_story_keys=seen,
            )

        self.assertEqual(selected["slug"], "flannan-isles-1900")
        self.assertEqual(selection_index, 2)
        self.assertEqual(mocked.call_count, 2)

    def test_ai_story_discovery_defaults_on_when_openai_is_configured(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-key"}, clear=False):
            os.environ.pop("TIKTOK_AI_STORY_DISCOVERY", None)
            self.assertTrue(story._ai_story_discovery_enabled())

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-openai-key",
                "TIKTOK_AI_STORY_DISCOVERY": "false",
            },
            clear=False,
        ):
            self.assertFalse(story._ai_story_discovery_enabled())

    def test_ai_story_prompt_includes_previously_generated_topics(self) -> None:
        prompt = story._ai_story_prompt(
            {"title": "Autonomous English Story Batch"},
            sequence_index=20,
            genre="historical mystery",
            excluded_story_keys={
                "slug:flannan-isles-1900",
                "title:the lighthouse keepers who vanished",
                "hook:three lighthouse keepers vanished without a trace",
            },
        )

        self.assertIn("Do not reuse or retell", prompt)
        self.assertIn("the lighthouse keepers who vanished", prompt.lower())
        self.assertIn("flannan-isles-1900", prompt.lower())

    def test_ai_story_selection_recovers_after_library_is_exhausted(self) -> None:
        seen = {
            identity
            for index in range(1, len(story.TOPIC_LIBRARY) + 1)
            for identity in story.story_identity_keys(
                story._build_library_story({}, sequence_index=index)
            )
        }
        fresh_story = {
            "slug": "fresh-original-story",
            "title": "A Fresh Original Story",
            "short_title": "FRESH STORY",
            "hook": "This story has never appeared in the queue.",
            "category": "original 2d animation story",
            "beats": [{"narration": "Fresh narration", "onscreen_text": "FRESH STORY"}],
        }

        with (
            patch.object(story, "_ai_story_discovery_enabled", return_value=True),
            patch.object(story, "_build_ai_story", return_value=fresh_story) as mocked,
        ):
            selected, _selection_index = story._select_unseen_story(
                {"source_url": "story://autonomous-english-reels/test-r00"},
                sequence_index=1,
                excluded_story_keys=seen,
            )

        self.assertEqual(selected["slug"], "fresh-original-story")
        self.assertEqual(mocked.call_args.kwargs["excluded_story_keys"], seen)

    def test_story_badges_match_new_niches(self) -> None:
        self.assertEqual(story._story_badge({"category": "lawsuit story"}), "LAWSUIT STORY")
        self.assertEqual(story._story_badge({"category": "cat animation"}), "CAT ANIMATION")
        self.assertEqual(story._story_badge({"category": "world economy story"}), "ECONOMY STORY")

    def test_story_brand_accepts_systemd_safe_underscores(self) -> None:
        with patch.dict(os.environ, {"TIKTOK_STORY_BRAND": "FILM_BOX_OFFICIAL"}, clear=False):
            self.assertEqual(story._story_brand(), "FILM BOX OFFICIAL")

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


class StoryEventIdentityTests(unittest.TestCase):
    EVENT_ID = "alpha-v-beta-san-jose-2023-sanctions"

    def candidate(self, **changes: Any) -> dict[str, Any]:
        return {
            "slug": "synthetic-first-case",
            "title": "A Synthetic Court Story",
            "short_title": "COURT STORY",
            "hook": "Alpha challenged the evidence.",
            "event_id": self.EVENT_ID,
            "beats": [
                {"label": str(i), "narration": f"Synthetic case beat {i}.", "onscreen_text": "THE CASE"}
                for i in range(8)
            ],
            **changes,
        }

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        return story._normalize_ai_story(payload, source_entry={}, sequence_index=4, genre="court case")

    def test_event_identity_normalizes_without_replacing_legacy_keys(self) -> None:
        candidate = self.candidate(event_id="  ALPHA v. BETA / San_Jose / 2023 / Sanctions  ")
        normalized = self.normalize(candidate)
        self.assertEqual(normalized["event_id"], self.EVENT_ID)
        keys = story.story_identity_keys(candidate)
        legacy = {key: value for key, value in candidate.items() if key != "event_id"}
        self.assertEqual(keys, story.story_identity_keys(legacy) | {f"event:{self.EVENT_ID}"})
        self.assertNotEqual(candidate["event_id"], normalized["event_id"])

    def test_event_identity_preserves_unicode_and_canonical_equivalence(self) -> None:
        pairs = [
            ("Affaire E\u0301lodie / Rene\u0301 / Paris / 2023", "affaire-\u00e9lodie-ren\u00e9-paris-2023"),
            ("\u041f\u043e\u0436\u0430\u0440 / \u041c\u043e\u0441\u043a\u0432\u0430 / 1812", "\u043f\u043e\u0436\u0430\u0440-\u043c\u043e\u0441\u043a\u0432\u0430-1812"),
        ]
        for raw, expected in pairs:
            with self.subTest(raw=raw):
                self.assertEqual(self.normalize(self.candidate(event_id=raw))["event_id"], expected)
                self.assertEqual(story.story_identity_keys({"event_id": raw}), {f"event:{expected}"})
        self.assertNotEqual(story.story_identity_keys({"event_id": pairs[0][1]}),
                            story.story_identity_keys({"event_id": "affaire-elodie-rene-paris-2023"}))

    def test_invalid_optional_event_ids_do_not_create_identities(self) -> None:
        for value in (None, False, 2023, [], {}, "", "   ", "---", "unknown", "N/A",
                      "court-case", "history-story-2023", "ai-court-case-2023", "event-123",
                      "2023-2024-2025", "a-b-2023", "placeholder-event-id", "x" * 161,
                      self.EVENT_ID + "-" + "x" * 161, "alpha\x00beta-san-jose-2023"):
            with self.subTest(value=value):
                self.assertEqual(story.story_identity_keys({"event_id": value}), set())
                self.assertFalse(self.normalize(self.candidate(event_id=value)).get("event_id"))
        legacy = {k: v for k, v in self.candidate().items() if k != "event_id"}
        self.assertFalse(self.normalize(legacy).get("event_id"))

    def test_event_id_length_limit_never_truncates_distinct_ids(self) -> None:
        boundary = "alpha-beta-" + "x" * (story.MAX_STORY_EVENT_ID_LENGTH - len("alpha-beta-"))
        self.assertEqual(story.story_identity_keys({"event_id": boundary}), {f"event:{boundary}"})
        for overlong in (boundary + "y", "alpha-beta-" + "\u00df" * 80):
            with self.subTest(overlong=overlong):
                self.assertEqual(story.story_identity_keys({"event_id": overlong}), set())

    def test_only_bare_generic_hooks_are_ignored(self) -> None:
        for opener in [*story.HOOK_OPENERS, *story.RUSSIAN_HOOK_OPENERS, "Did you know?",
                       "Have you heard this story?", "What if I told you?"]:
            with self.subTest(opener=opener):
                bare = "  " + opener.upper().replace("?", "!!!") + "  "
                self.assertEqual(story.story_identity_keys({"hook": bare}), set())
                specific = {"hook": opener + " Alpha challenged the evidence."}
                self.assertTrue(any(key.startswith("hook:") for key in story.story_identity_keys(specific)))
        self.assertEqual(story.story_identity_keys({"title": "Legacy Title", "slug": "legacy-slug", "hook": "Did you know?"}),
                         {"title:legacy title", "slug:legacy-slug"})

    def test_prompt_requests_specific_event_in_existing_discovery(self) -> None:
        prompt = story._ai_story_prompt({}, sequence_index=4, genre="court case").lower()
        for required in ("event_id", "canonical", "parties", "place", "year", "fiction", "empty string"):
            self.assertIn(required, prompt)

    def test_event_prompt_priority_and_generic_hook_filter(self) -> None:
        seen = {f"title:prior title {i:03}" for i in range(70)} | {f"event:{self.EVENT_ID}"}
        values = story._excluded_story_prompt_values(seen)
        self.assertEqual(values[0], self.EVENT_ID)
        self.assertEqual(len(values), 60)
        self.assertEqual(story._excluded_story_prompt_values({"hook:did you know this actually happened", "event:unknown"}), [])
        self.assertEqual(story._excluded_story_prompt_values(seen, limit=0), [])

    def test_exact_event_is_rejected_even_when_omitted_from_prompt(self) -> None:
        old = self.candidate(event_id="zulu-v-beta-san-jose-2023-sanctions")
        seen = story.story_identity_keys(old) | {f"event:alpha-case-{i:03}-london-2023" for i in range(70)}
        seen |= {f"title:prior title {i:03}" for i in range(70)}
        repeated = self.candidate(event_id=old["event_id"], slug="changed-slug", title="Changed Title", hook="A changed hook.")
        fresh = self.candidate(event_id="gamma-v-delta-london-2024", slug="fresh-case", title="Fresh Title", hook="A fresh hook.")
        self.assertNotIn(old["event_id"], story._excluded_story_prompt_values(seen))
        with (
            patch.object(story, "_ai_story_discovery_enabled", return_value=True),
            patch.object(story, "_build_ai_story", side_effect=[repeated, fresh]) as build,
        ):
            selected, index = story._select_unseen_story({}, sequence_index=4, excluded_story_keys=seen)
        self.assertEqual(selected["event_id"], fresh["event_id"])
        self.assertEqual((build.call_count, index), (2, 4))
        self.assertEqual(build.call_args.kwargs["excluded_story_keys"], seen)

    def test_distinct_events_and_title_only_suspicions_are_allowed(self) -> None:
        old = self.candidate(title="The Court Case That Shook AI Trust Forever", hook=story.HOOK_OPENERS[0])
        for event_id in ("gamma-v-delta-london-2024", "alpha-v-beta-san-jose-2024-appeal", ""):
            fresh = self.candidate(event_id=event_id, title="The Court Case That Upended AI Trust Forever",
                                   slug="another-case", hook=story.HOOK_OPENERS[0])
            with (
                self.subTest(event_id=event_id),
                patch.object(story, "_ai_story_discovery_enabled", return_value=True),
                patch.object(story, "_build_ai_story", return_value=fresh) as build,
            ):
                selected, _ = story._select_unseen_story({}, sequence_index=4, excluded_story_keys=story.story_identity_keys(old))
                self.assertEqual(selected["slug"], "another-case")
                self.assertEqual(build.call_count, 1)
        presidents = [topic for topic in story.TOPIC_LIBRARY if topic["slug"] in
                      {"sankara-1987-burkina-faso", "allende-1973-chile"}]
        self.assertEqual(len(presidents), 2)
        self.assertFalse(story.story_identity_keys(presidents[0]) & story.story_identity_keys(presidents[1]))

    def test_discovery_preserves_event_without_an_additional_request(self) -> None:
        with (
            patch.object(story, "_ai_story_discovery_enabled", return_value=True),
            patch.object(story, "_request_ai_story_payload", return_value=self.candidate()) as request,
        ):
            selected, _ = story._select_unseen_story({}, sequence_index=4)
        self.assertEqual(selected["event_id"], self.EVENT_ID)
        request.assert_called_once()

    def test_event_id_survives_story_and_segment_serialization(self) -> None:
        for event_id in (self.EVENT_ID, "unknown", None):
            with self.subTest(event_id=event_id):
                self.assert_serialized_event_id(event_id)

    def assert_serialized_event_id(self, event_id: Any) -> None:
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as mocks:
            candidate = self.normalize(self.candidate(event_id=event_id))
            for name, value in (
                ("_select_unseen_story", (candidate, 4)), ("_generate_story_voiceover", {}),
                ("_generate_story_word_alignment", {"words": [{"text": "Synthetic", "start": 0, "end": 1}]}),
                ("_write_story_caption_ass", None),
                ("_write_caption_manifest", None), ("render_story_video", None),
                ("validate_story_video_layout", Path(tmp) / "validation.json"), ("_media_duration", 70.0),
            ):
                mocks.enter_context(patch.object(story, name, return_value=value))
            result = story.generate_tiktok_story_clip(Path(tmp), {}, sequence_index=4)
            saved_story = json.loads(result.story_path.read_text(encoding="utf-8"))
            saved_segments = json.loads(result.segments_path.read_text(encoding="utf-8"))
        expected = self.EVENT_ID if event_id == self.EVENT_ID else ""
        self.assertEqual(saved_story["event_id"], expected)
        self.assertEqual(saved_segments[0]["story_event_id"], expected)


if __name__ == "__main__":
    unittest.main()
