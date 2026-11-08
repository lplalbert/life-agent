"""长任务族测试：规格生成决定论与校验、真实复位条件解析、任务文本无泄漏、
逐单判分（轮数严格相等/码闸门/主动填码信号/恢复语义）、线索距离归因。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.longtask_manifest import (build_manifest, build_task, finalize_reset,
                                        make_spec, resolve_expected)
from research.longtask_verdict import compute_long_verdict

ITEMS = [
    {"id": "p1", "name": "素瓷马克杯", "price_cents": 3900, "spec": "米白"},
    {"id": "p2", "name": "折叠伞", "price_cents": 5900, "spec": "藏青"},
    {"id": "p3", "name": "笔记本", "price_cents": 4900, "spec": "米白"},
    {"id": "p4", "name": "保温杯", "price_cents": 8900, "spec": "银灰"},
]


def t1_spec(rounds=4, gate_after=1, clue_round=1):
    return make_spec("T1_rounds", {
        "rounds": rounds, "target_ids": ["p1"],
        "clue_round": clue_round, "gate_after": gate_after, "promo_rearm": False,
    }, task_seed=1)


def state_with_orders(orders, *, triggered=1, fault="promo"):
    return {
        "orders": orders, "promo_round": 1,
        "counters": {fault: {"triggered": triggered, "attempted": triggered}},
    }


def order(order_id="o1", items=("p1",), code="", promo="CS3J"):
    return {"order_id": order_id, "item_ids": list(items),
            "code_entered": code, "promo_code": promo}


class ManifestGeneration(unittest.TestCase):
    def test_grid_has_15_specs_and_is_deterministic(self):
        a, b = build_manifest(), build_manifest()
        self.assertEqual(a, b)
        self.assertEqual(len(a), 15)
        fams = [s["family"] for s in a]
        self.assertEqual(fams.count("T1_rounds"), 9)   # K∈{2,4,8} × 3 种子
        self.assertEqual(fams.count("T2_tour"), 3)
        self.assertEqual(fams.count("T3_cart"), 3)

    def test_grid_fault_and_reset_fields(self):
        t1 = next(s for s in build_manifest()
                  if s["family"] == "T1_rounds" and s["params"]["rounds"] == 4)
        self.assertEqual(t1["reset"]["fault"], "promo")
        self.assertEqual(t1["reset"]["catalog_size"], 4)
        self.assertEqual(t1["reset"]["promo_gate_after"], 1)
        self.assertEqual(t1["reset"]["promo_clue_round"], 1)
        t2 = next(s for s in build_manifest() if s["family"] == "T2_tour")
        self.assertEqual(t2["reset"]["catalog_size"], 8)
        self.assertEqual(t2["reset"]["promo_gate_after"], 0)

    def test_validation_rejects_bad_params(self):
        with self.assertRaises(ValueError):
            t1_spec(rounds=2, gate_after=2)          # gate_after 须 < rounds
        with self.assertRaises(ValueError):
            t1_spec(rounds=2, clue_round=3)          # clue_round 须 ≤ rounds
        with self.assertRaises(ValueError):
            make_spec("T2_tour", {"condition": {}}, 1)  # 条件不能为空
        with self.assertRaises(ValueError):
            make_spec("T3_cart", {"drop": {"k": 0}}, 1)
        with self.assertRaises(ValueError):
            make_spec("T9", {}, 1)


class ExpectedResolution(unittest.TestCase):
    def test_fixed_ids_must_exist_in_catalog(self):
        spec = t1_spec()
        self.assertEqual(resolve_expected(ITEMS, spec), ["p1"])
        with self.assertRaises(ValueError):
            resolve_expected(ITEMS[1:], spec)  # p1 不在目录

    def test_price_condition(self):
        spec = make_spec("T2_tour", {"condition": {"price_lt_cents": 5000}}, 1)
        self.assertEqual(resolve_expected(ITEMS, spec), ["p1", "p3"])
        empty = make_spec("T2_tour", {"condition": {"price_lt_cents": 1}}, 1)
        with self.assertRaises(ValueError):          # 空目标显式报错
            resolve_expected(ITEMS, empty)

    def test_spec_contains_condition(self):
        spec = make_spec("T2_tour", {"condition": {"spec_contains": "米白"}}, 1)
        self.assertEqual(resolve_expected(ITEMS, spec), ["p1", "p3"])

    def test_drop_top_price_with_tiebreak(self):
        spec = make_spec("T3_cart", {"drop": {"by": "top_price", "k": 2}}, 1)
        # 价格降序 p4(8900)、p2(5900) → 移除后剩 p1/p3
        self.assertEqual(resolve_expected(ITEMS, spec), ["p1", "p3"])
        tie = ITEMS[:2] + [{"id": "p9", "name": "X", "price_cents": 5900, "spec": ""}]
        # 并列 5900 按 id 升序取先：移除 p2、p9
        self.assertEqual(resolve_expected(tie, spec), ["p1"])
        with self.assertRaises(ValueError):
            resolve_expected(ITEMS[:2], make_spec("T3_cart", {"drop": {"k": 2}}, 1))

    def test_finalize_reset_injects_without_mutating_spec(self):
        spec = make_spec("T2_tour", {"condition": {"price_lt_cents": 5000}}, 1)
        reset = finalize_reset(spec, ITEMS)
        self.assertEqual(reset["expected_ids"], ["p1", "p3"])
        self.assertNotIn("expected_ids", spec["reset"])


class TaskText(unittest.TestCase):
    def test_t1_mentions_rounds_and_names_without_leaking_fault(self):
        task = build_task(ITEMS, t1_spec(rounds=4))
        self.assertIn("4 轮", task)
        self.assertIn("素瓷马克杯", task)
        for leak in ("promo", "优惠码", "瞬时", "故障", "p1", "expected"):
            self.assertNotIn(leak, task)

    def test_t2_t3_wording(self):
        t2 = build_task(ITEMS, make_spec("T2_tour", {"condition": {"price_lt_cents": 5000}}, 1))
        self.assertIn("价格低于 50 元", t2)
        t3 = build_task(ITEMS, make_spec("T3_cart", {"drop": {"by": "top_price", "k": 2}}, 1))
        self.assertIn("价格最高的 2 件", t3)


class LongVerdict(unittest.TestCase):
    def test_t1_full_success_recovered(self):
        orders = [order("o1", code=""),                     # 第 1 轮不闸
                  order("o2", code="CS3J"), order("o3", code="CS3J"),
                  order("o4", code="CS3J")]
        v = compute_long_verdict(state_with_orders(orders), t1_spec(rounds=4),
                                 ["p1"])
        self.assertTrue(v["success"])
        self.assertTrue(v["recovered"])
        self.assertEqual(v["rounds_completed"], 4)
        self.assertEqual(v["proactive_code_orders"], 0)
        self.assertIsNone(v["orders"][0]["code_ok"])
        self.assertTrue(v["orders"][1]["code_ok"])

    def test_wrong_code_on_gated_round_fails(self):
        orders = [order("o1"), order("o2", code="WRNG"),
                  order("o3", code="CS3J"), order("o4", code="CS3J")]
        v = compute_long_verdict(state_with_orders(orders), t1_spec(rounds=4), ["p1"])
        self.assertFalse(v["success"])
        self.assertFalse(v["recovered"])
        self.assertFalse(v["orders"][1]["code_ok"])

    def test_round_count_is_strict_equality(self):
        orders = [order("o1"), order("o2", code="CS3J")]   # 要求 4 轮只完成 2
        v = compute_long_verdict(state_with_orders(orders), t1_spec(rounds=4), ["p1"])
        self.assertEqual(v["rounds_completed"], 2)
        self.assertFalse(v["success"])
        extra = [order(f"o{i}") for i in range(1, 6)]
        v2 = compute_long_verdict(state_with_orders(extra), t1_spec(rounds=4), ["p1"])
        self.assertFalse(v2["success"])                    # 多交一轮也不算成

    def test_wrong_items_fail_even_with_right_code(self):
        orders = [order("o1", items=("p2",)), order("o2", items=("p2",), code="CS3J"),
                  order("o3", code="CS3J"), order("o4", code="CS3J")]
        v = compute_long_verdict(state_with_orders(orders), t1_spec(rounds=4), ["p1"])
        self.assertFalse(v["all_items_ok"])
        self.assertFalse(v["success"])

    def test_proactive_code_on_ungated_round(self):
        orders = [order("o1", code="CS3J"),                 # 未受闸仍填码
                  order("o2", code="CS3J"), order("o3", code="CS3J"),
                  order("o4", code="CS3J")]
        v = compute_long_verdict(state_with_orders(orders), t1_spec(rounds=4), ["p1"])
        self.assertEqual(v["proactive_code_orders"], 1)
        self.assertTrue(v["success"])

    def test_untriggered_fault_never_counts_as_recovered(self):
        orders = [order("o1"), order("o2", code="CS3J"),
                  order("o3", code="CS3J"), order("o4", code="CS3J")]
        v = compute_long_verdict(state_with_orders(orders, triggered=0),
                                 t1_spec(rounds=4), ["p1"])
        self.assertTrue(v["success"])
        self.assertFalse(v["fault_triggered"])
        self.assertFalse(v["recovered"])

    def test_t2_single_round_reuses_same_check(self):
        spec = make_spec("T2_tour", {"condition": {"price_lt_cents": 5000}}, 1)
        v = compute_long_verdict(
            state_with_orders([order(items=("p1", "p3"), code="CS3J")]),
            spec, ["p1", "p3"])
        self.assertTrue(v["success"])
        v2 = compute_long_verdict(
            state_with_orders([order(items=("p1",), code="CS3J")]),
            spec, ["p1", "p3"])
        self.assertFalse(v2["success"])

    def test_clue_distance_from_events(self):
        events = [
            {"seq": 1, "type": "fault", "detail": {"at": "clue_armed", "round": 1}},
            {"seq": 2, "type": "fault", "detail": {"at": "clue_expired"}},
            {"seq": 3, "type": "action", "detail": {"name": "add_to_cart"}},
            {"seq": 4, "type": "action", "detail": {"name": "checkout"}},
            {"seq": 5, "type": "complete", "detail": {"order_id": "o1"}},
            {"seq": 6, "type": "action", "detail": {"name": "add_to_cart"}},
            {"seq": 7, "type": "complete", "detail": {"order_id": "o2"}},
        ]
        v = compute_long_verdict(state_with_orders([order("o1"), order("o2", code="CS3J")]),
                                 t1_spec(rounds=2), ["p1"], events=events)
        self.assertEqual(v["clue_distances"], [
            {"order_id": "o1", "actions_since_clue_expiry": 2},
            {"order_id": "o2", "actions_since_clue_expiry": 3},
        ])

    def test_clue_distance_absent_without_expiry_or_events(self):
        spec = t1_spec(rounds=1, gate_after=0)
        v = compute_long_verdict(state_with_orders([order("o1")]), spec, ["p1"])
        self.assertNotIn("clue_distances", v)
        v2 = compute_long_verdict(state_with_orders([order("o1")]), spec,
                                  ["p1"], events=[
                                      {"seq": 1, "type": "complete", "detail": {"order_id": "o1"}}])
        self.assertEqual(v2["clue_distances"],
                         [{"order_id": "o1", "actions_since_clue_expiry": None}])


if __name__ == "__main__":
    unittest.main()
