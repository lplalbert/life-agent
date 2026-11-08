"""Provenance Gate 判定核心测试：无出处值触发、有出处不打扰、候选排序、
短值不触发、ground truth 语义不进判定。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from life_agent.provenance import (MIN_VALUE_CHARS, ProvenanceFinding, StepEvidence,
                                   TextBlock, check_value, find_substantiation,
                                   rank_review_candidates)
from dataclasses import FrozenInstanceError


def ui(step, text):
    return TextBlock("ui", text, step)


def summary(text):
    return TextBlock("summary", text, None)


class Substantiation(unittest.TestCase):
    def test_value_in_summary_text_is_substantiated(self):
        blocks = [summary("第0步横幅：会员日优惠码 CS3J")]
        m = find_substantiation("CS3J", blocks)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].kind, "summary")

    def test_value_in_old_ui_text_is_substantiated(self):
        blocks = [ui(3, "券码 W3JG 已填入输入框")]
        self.assertEqual(len(find_substantiation("W3JG", blocks)), 1)

    def test_ascii_case_insensitive(self):
        blocks = [ui(1, "code: cs3j")]
        self.assertEqual(len(find_substantiation("CS3J", blocks)), 1)

    def test_missing_value_not_substantiated(self):
        blocks = [ui(0, "商品列表页"), summary("第0步：列表页")]
        self.assertEqual(find_substantiation("CS3J", blocks), [])

    def test_short_or_empty_value_never_judged(self):
        self.assertEqual(find_substantiation("", [ui(0, "x")]), [])
        self.assertEqual(find_substantiation("A", [ui(0, "A")]), [])
        self.assertGreaterEqual(MIN_VALUE_CHARS, 2)

    def test_literal_substring_only(self):
        # 出处 = 字面子串命中（含片段，宁少触发不误报）；改写/插字符变体不命中
        blocks = [ui(0, "优惠码 W3JG 已填入")]
        self.assertEqual(find_substantiation("W3JG", blocks), [blocks[0]])
        self.assertEqual(find_substantiation("W3 JG", blocks), [])
        self.assertEqual(find_substantiation("W3JGX", blocks), [])
        self.assertEqual(find_substantiation("优惠码有效", blocks), [])


class ReviewCandidates(unittest.TestCase):
    def test_retained_frames_not_suggested(self):
        steps = [StepEvidence(0, True, retained=True),
                 StepEvidence(1, True, retained=True)]
        self.assertEqual(rank_review_candidates(steps), [])

    def test_distilled_without_value_ranks_before_undistilled(self):
        steps = [
            StepEvidence(0, True, retained=False, distilled=True, summary_text="列表页"),
            StepEvidence(1, True, retained=False, distilled=False),
            StepEvidence(2, True, retained=False, distilled=True, summary_text="详情页"),
            StepEvidence(3, True, retained=False, distilled=False),
            StepEvidence(4, True, retained=True, distilled=False),
        ]
        ranked = rank_review_candidates(steps)
        self.assertEqual([s for s, _ in ranked], [2, 0, 3, 1])
        self.assertEqual([r for _, r in ranked],
                         ["distilled_without_value"] * 2 + ["evicted_undistilled"] * 2)

    def test_imageless_steps_excluded(self):
        steps = [StepEvidence(0, False, retained=False, distilled=True)]
        self.assertEqual(rank_review_candidates(steps), [])


class GateDecision(unittest.TestCase):
    def test_trigger_without_text_provenance(self):
        f = check_value("CS3J", [ui(0, "商品列表")],
                        [StepEvidence(0, True, retained=False, distilled=True,
                                      summary_text="列表页")])
        self.assertTrue(f.trigger)
        self.assertEqual(f.review_candidates, (0,))
        self.assertIn("distilled_without_value", f.reasons)

    def test_no_trigger_with_text_provenance(self):
        f = check_value("CS3J", [summary("横幅优惠码 CS3J")], [])
        self.assertFalse(f.trigger)
        self.assertEqual(f.review_candidates, ())
        self.assertIn("substantiated_in_text", f.reasons)

    def test_short_value_never_triggers(self):
        f = check_value("A", [], [StepEvidence(0, True, retained=False)])
        self.assertFalse(f.trigger)
        self.assertIn("value_too_short", f.reasons)

    def test_finding_is_immutable_record(self):
        f = check_value("CS3J", [], [])
        self.assertIsInstance(f, ProvenanceFinding)
        with self.assertRaises(FrozenInstanceError):
            f.trigger = False


if __name__ == "__main__":
    unittest.main()
