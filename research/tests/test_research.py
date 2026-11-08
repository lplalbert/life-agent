import tempfile
import unittest
from pathlib import Path

from research.audit_runs import audit
from research.context_policy import select


def observation(step, image=True):
    return {"kind": "observation", "step": step,
            "image_ref": f"step{step}.jpg" if image else None}


def check(step, state):
    return {"kind": "verification", "step": step, "check_id": "navigation",
            "source_steps": [0, 1], "state": state}


class EvidenceSelection(unittest.TestCase):
    def test_old_unresolved_evidence_survives_same_image_budget(self):
        events = [observation(0), observation(1), check(1, "pending"),
                  observation(2), observation(3)]
        self.assertEqual(select(events, "recent")["retained_steps"], [2, 3])
        result = select(events, "evidence")
        self.assertEqual(result["retained_steps"], [0, 3])
        self.assertEqual(result["unresolved_sources_without_image"], [1])

    def test_verification_only_changes_later_prefix(self):
        prefix = [observation(0), observation(1), check(1, "pending"), observation(2)]
        self.assertEqual(select(prefix, "evidence")["retained_steps"], [0, 2])
        self.assertEqual(select(prefix + [check(2, "verified")], "evidence")
                         ["retained_steps"], [1, 2])

    def test_future_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            select([observation(0), check(0, "pending")], "evidence")

    def test_missing_current_image_does_not_invent_one(self):
        result = select([observation(0), observation(1, False)], "evidence", 1)
        self.assertEqual(result["retained_steps"], [0])

    def test_full_is_unbudgeted_reference(self):
        self.assertEqual(select([observation(i) for i in range(4)], "full", 1)
                         ["retained_steps"], [0, 1, 2, 3])

    def test_duplicate_observation_and_invalid_budget_are_rejected(self):
        with self.assertRaises(ValueError):
            select([observation(0), observation(0)], "recent")
        with self.assertRaises(ValueError):
            select([], "recent", 0)


class NoFreshAblation(unittest.TestCase):
    """evidence_nofresh = 消融「通道新鲜度」：最新帧不恒留。"""

    def test_unresolved_evidence_displaces_latest_without_freshness(self):
        events = [observation(0), observation(1), check(1, "pending"),
                  observation(2), observation(3)]
        # evidence 恒留最新帧 → [0, 3]；nofresh 预算全按优先级 → 证据源 [0,1] 占满
        self.assertEqual(select(events, "evidence")["retained_steps"], [0, 3])
        self.assertEqual(select(events, "evidence_nofresh")["retained_steps"], [0, 1])

    def test_without_checks_nofresh_matches_recent(self):
        events = [observation(i) for i in range(4)]
        self.assertEqual(select(events, "evidence_nofresh")["retained_steps"],
                         select(events, "recent")["retained_steps"])

    def test_budget_enough_for_evidence_and_latest_nofresh_agrees(self):
        events = [observation(0), observation(1), check(1, "pending"),
                  observation(2), observation(3)]
        self.assertEqual(select(events, "evidence_nofresh", 3)["retained_steps"],
                         select(events, "evidence", 3)["retained_steps"])

    def test_unknown_policy_still_rejected(self):
        with self.assertRaises(ValueError):
            select([observation(0)], "evidence-no-fresh")


class Inventory(unittest.TestCase):
    def test_file_evidence_is_not_promoted_to_success_or_token_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "example"
            run.mkdir()
            for step in range(3):
                (run / f"step{step:02}.jpg").write_bytes(b"not-a-real-image")
                (run / f"step{step:02}_ui.txt").write_text("same", encoding="utf-8")
            result = audit(root)["runs"][0]
            self.assertEqual(result["observed_steps"], 3)
            self.assertEqual(result["hypothetical_replay"]["full_image_file_occurrences"], 6)
            self.assertEqual(result["hypothetical_replay"]["recent2_image_file_occurrences"], 5)
            self.assertIsNone(result["task_success"])
            self.assertIsNone(result["provider_usage"])
            self.assertEqual(len(result["frames"][0]["images"][0]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
