import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from elevenlabs_budget import (
    commit_reservation,
    load_weekly_usage,
    release_reservation,
    reserve_credits,
)


NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class ElevenLabsBudgetTest(unittest.TestCase):
    def test_shared_cap_counts_usage_from_both_pipelines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "shared.sqlite3"
            tiktok = reserve_credits(
                ledger,
                pipeline="tiktok_english",
                input_characters=1_000,
                model="eleven_flash_v2_5",
                shared_weekly_credit_budget=600,
                pipeline_weekly_credit_budget=500,
                credits_per_character=0.5,
                now=NOW,
            )
            self.assertTrue(tiktok.allowed)
            commit_reservation(ledger, tiktok.reservation_id, actual_credits=500)

            youtube = reserve_credits(
                ledger,
                pipeline="youtube_english",
                input_characters=300,
                model="eleven_flash_v2_5",
                shared_weekly_credit_budget=600,
                pipeline_weekly_credit_budget=200,
                credits_per_character=0.5,
                now=NOW,
            )

            self.assertFalse(youtube.allowed)
            self.assertEqual(youtube.reason, "shared_weekly_budget")

    def test_pipeline_reservation_protects_other_agent_share(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "shared.sqlite3"
            decision = reserve_credits(
                ledger,
                pipeline="youtube_english",
                input_characters=500,
                model="eleven_flash_v2_5",
                shared_weekly_credit_budget=1_000,
                pipeline_weekly_credit_budget=200,
                credits_per_character=0.5,
                now=NOW,
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "pipeline_weekly_budget")

    def test_released_reservation_does_not_consume_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "shared.sqlite3"
            decision = reserve_credits(
                ledger,
                pipeline="tiktok_english",
                input_characters=800,
                model="eleven_flash_v2_5",
                shared_weekly_credit_budget=600,
                pipeline_weekly_credit_budget=500,
                credits_per_character=0.5,
                now=NOW,
            )
            self.assertTrue(decision.allowed)
            release_reservation(ledger, decision.reservation_id)

            usage = load_weekly_usage(
                ledger,
                pipeline="tiktok_english",
                shared_weekly_credit_budget=600,
                pipeline_weekly_credit_budget=500,
                now=NOW,
            )
            self.assertEqual(usage.shared_used_credits, 0)
            self.assertEqual(usage.pipeline_used_credits, 0)
            self.assertEqual(usage.generation_count, 0)


if __name__ == "__main__":
    unittest.main()
