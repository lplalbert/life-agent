"""Provenance Gate 判定核心测试：无出处值触发、有出处不打扰、候选排序、
短值不触发、ground truth 语义不进判定。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from life_agent.provenance import (MIN_VALUE_CHARS, SUBSTANTIATION_KINDS,
                                   ProvenanceFinding, StepEvidence,
                                   TextBlock, check_value, find_substantiation,
                                   format_gate_response, rank_review_candidates)
from dataclasses import FrozenInstanceError


def ui(step, text):
    return TextBlock("ui", text, step)


def summary(text):
    return TextBlock("summary", text, None)


def task(text):
    return TextBlock("task", text, None)


def note(text):
    return TextBlock("note", text, None)


def feedback(text):
    return TextBlock("feedback", text, None)


class Substantiation(unittest.TestCase):
    def test_value_in_summary_text_is_substantiated(self):
        blocks = [summary("第0步横幅：会员日优惠码 CS3J")]
        m = find_substantiation("CS3J", blocks)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].kind, "summary")

    def test_find_substantiation_is_channel_agnostic(self):
        # 通用匹配器对 ui 文本也字面命中——渠道过滤只在 check_value 做
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


class ChannelPolicy(unittest.TestCase):
    """渠道化出处：控件树文本（ui）不算提交值出处，四个记忆渠道各算。

    动机=实测失败通路「把输入框提示当答案」：占位提示「会员日优惠码」住在
    控件树文本里，字面子串规则会误判「有出处」放行（pg-preflight-1 gate miss）。
    """

    def test_ui_text_alone_does_not_substantiate(self):
        f = check_value("会员日优惠码",
                        [task("购买商品"), ui(8, "EditText hint=会员日优惠码")], [])
        self.assertTrue(f.trigger)
        self.assertEqual(f.matches, ())

    def test_ui_text_does_not_hide_true_substantiation(self):
        # ui 命中被剔除后，summary 渠道的出处仍正常放行
        f = check_value("W3JG", [ui(1, "提示 W3JG"), summary("横幅码 W3JG")], [])
        self.assertFalse(f.trigger)
        self.assertEqual([m.kind for m in f.matches], ["summary"])

    def test_each_memory_channel_substantiates(self):
        for block in (task("目标码 W3JG"), summary("横幅 W3JG"),
                      note("我抄录：W3JG"), feedback("人类回答：W3JG")):
            f = check_value("W3JG", [ui(0, "无关"), block], [])
            self.assertFalse(f.trigger, block.kind)
            self.assertEqual(f.reasons, ("substantiated_in_text",))

    def test_substantiation_kinds_exclude_ui(self):
        self.assertEqual(set(SUBSTANTIATION_KINDS),
                         {"task", "summary", "note", "feedback"})
        self.assertNotIn("ui", SUBSTANTIATION_KINDS)


class PlaceholderRule(unittest.TestCase):
    """占位提示规则（字段语义）：V 等于输入框占位提示/标签 = 问题文案当答案。

    无视一切转录出处（思考/摘要/工具回显都可能「洗白」屏幕文案）直接触发；
    转录命中记入 matches 供归因。pg-preflight-2 实测驱动。
    """

    HINT = "会员日优惠码"

    def _finding(self, blocks, questions=None):
        return check_value(self.HINT, blocks, [],
                           question_texts=questions or (self.HINT,))

    def test_placeholder_triggers_despite_summary_laundering(self):
        f = self._finding([task("购买商品"), summary(f"输入框提示：{self.HINT}")])
        self.assertTrue(f.trigger)
        self.assertEqual(f.trigger_reason, "field_placeholder")
        self.assertEqual([m.kind for m in f.matches], ["summary"])  # 归因：转录渠道

    def test_placeholder_triggers_despite_note_and_feedback(self):
        f = self._finding([note(f"提示是{self.HINT}，试试输入"),
                          feedback(f"已点击 「{self.HINT}」")])
        self.assertTrue(f.trigger)
        self.assertEqual(sorted(m.kind for m in f.matches), ["feedback", "note"])

    def test_placeholder_triggers_even_without_any_recital(self):
        f = self._finding([task("购买商品")])
        self.assertTrue(f.trigger)
        self.assertEqual(f.trigger_reason, "field_placeholder")

    def test_question_match_is_stripped_and_case_insensitive(self):
        f = check_value("W3jg ", [summary("无关")], [],
                        question_texts=("  w3JG ",))
        self.assertTrue(f.trigger)
        self.assertEqual(f.trigger_reason, "field_placeholder")

    def test_placeholder_fragment_also_triggers(self):
        # pg-preflight-3：模型打的是占位提示片段「会员日」——问题文案的片段同样
        # 不是答案（note 转录 ×6 曾放行）
        f = check_value("会员日", [note("横幅标题是会员日")], [],
                        question_texts=("会员日优惠码",))
        self.assertTrue(f.trigger)
        self.assertEqual(f.trigger_reason, "field_placeholder")

    def test_non_placeholder_value_unaffected(self):
        f = check_value("W3JG", [summary("横幅码 W3JG")], [],
                        question_texts=(self.HINT,))
        self.assertFalse(f.trigger)

    def test_no_provenance_reason_kept_for_other_triggers(self):
        f = check_value("WXYZ", [task("无关")], [])
        self.assertTrue(f.trigger)
        self.assertEqual(f.trigger_reason, "no_provenance")


class GateResponseText(unittest.TestCase):
    def test_names_value_and_review_path_without_answers(self):
        f = check_value("W3JG", [ui(0, "商品列表")],
                        [StepEvidence(0, True, retained=False, distilled=True,
                                      summary_text="列表页")])
        msg = format_gate_response(f)
        self.assertIn("W3JG", msg)                  # 提交值原样回显（agent 自己的输入）
        self.assertIn("review_screenshot", msg)     # 指路取证
        self.assertIn("第 0 步", msg)
        self.assertIn("已蒸馏但摘要缺值", msg)

    def test_candidate_list_is_capped_with_omission_note(self):
        steps = [StepEvidence(i, True, retained=False, distilled=False)
                 for i in range(8)]
        f = check_value("WXYZ", [], steps)
        msg = format_gate_response(f, max_steps=3)
        self.assertIn("第 7 步", msg)                # 新→旧取前 3
        self.assertNotIn("第 4 步", msg)
        self.assertIn("另有 5 步候选未列出", msg)

    def test_never_injects_ground_truth(self):
        f = check_value("WXYZ", [], [])
        msg = format_gate_response(f)
        for leak in ("promo", "优惠码", "expected", "fault"):
            self.assertNotIn(leak, msg)

    def test_placeholder_variant_names_question_not_answer(self):
        f = check_value("会员日优惠码", [], [StepEvidence(0, True, retained=False)],
                        question_texts=("会员日优惠码",))
        msg = format_gate_response(f)
        self.assertIn("占位提示", msg)               # 贴因：问题文案不是答案
        self.assertIn("会员日优惠码", msg)            # 提交值原样回显
        self.assertIn("review_screenshot", msg)
        self.assertIn("第 0 步", msg)


if __name__ == "__main__":
    unittest.main()
