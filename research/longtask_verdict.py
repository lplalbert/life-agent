"""长任务族判分（T1–T3）：逐单核对应用内部业务状态，与模型 finish 文本无关。

短 episode 的 compute_verdict 只看最后一单（state.success = 末单 submitted_ids ==
expected_ids）；多轮任务必须逐单核对：每轮 items_ok、码闸门轮次 code_ok、
轮数严格相等。未触发故障不得计恢复成功（recovered = triggered && success）。

- code_required(i)：该单受码闸门约束 = 该单 promo_code 非空 且 i >= gate_after
  （码未武装时闸门不生效，见 EnvState.trySubmit）。
- proactive_codes：未受闸轮次仍主动填了码的单数——模型自发使用瞬时线索的行为信号
  （自发抄录通路的落地痕迹；通路归因仍由 flash_analysis 从文本/截图证据做）。
- clue_distances（可选，events 提供时）：各单完成距最近一次线索过期的业务动作数。
  只数 ground-truth 业务动作事件（导航/输入不入事件流），是距离下界而非轮次步数。
"""
from __future__ import annotations


def compute_long_verdict(state: dict, spec: dict, expected_ids: list,
                         events: list | None = None) -> dict:
    fault = spec["reset"]["fault"]
    counters = state.get("counters", {}) or {}
    c = counters.get(fault, {}) or {}
    triggered = int(c.get("triggered", 0) or 0)

    gate_after = int(spec["params"].get("gate_after", 0))
    rounds_expected = int(spec["params"].get("rounds", 1))
    expected = sorted(expected_ids)

    orders_raw = state.get("orders", []) or []
    details = []
    for i, o in enumerate(orders_raw):
        promo_code = (o.get("promo_code") or "")
        code_entered = (o.get("code_entered") or "")
        code_required = bool(promo_code) and i >= gate_after
        if code_required:
            code_ok = code_entered.strip() == promo_code.strip()
        else:
            code_ok = None
        details.append({
            "order_id": o.get("order_id"),
            "items_ok": sorted(o.get("item_ids") or []) == expected,
            "code_required": code_required,
            "code_ok": code_ok,
            "code_entered": code_entered,
            "promo_code": promo_code,
            "proactive_code": bool(code_entered.strip()) and not code_required,
        })

    rounds_completed = len(details)
    all_items_ok = bool(details) and all(d["items_ok"] for d in details)
    all_code_ok = all(d["code_ok"] is not False for d in details)
    success = (rounds_completed == rounds_expected
               and all_items_ok and all_code_ok)

    out = {
        "spec_id": spec["spec_id"],
        "success": success,
        "fault": fault,
        "fault_triggered": triggered > 0,
        "triggered_count": triggered,
        "recovered": bool(triggered > 0 and success),
        "rounds_expected": rounds_expected,
        "rounds_completed": rounds_completed,
        "all_items_ok": all_items_ok,
        "all_code_ok": all_code_ok,
        "proactive_code_orders": sum(1 for d in details if d["proactive_code"]),
        "orders": details,
    }
    if events is not None:
        out["clue_distances"] = _clue_distances(events)
    return out


def _clue_distances(events: list) -> list:
    """线索过期 → 各 complete 的业务动作距离（按完成先后）。

    同一 episode 多次武装（promo_rearm）时按最近一次过期起算；无过期事件的
    complete 记 None（线索未武装或首动作前即完成，距离 0 语义不明不做猜测）。
    """
    seqs = sorted(events, key=lambda e: e.get("seq") or 0)
    since = None
    out = []
    for e in seqs:
        etype, detail = e.get("type"), e.get("detail") or {}
        if etype == "fault" and detail.get("at") == "clue_expired":
            since = 0
        elif etype == "action" and since is not None:
            since += 1
        elif etype == "complete":
            out.append({"order_id": detail.get("order_id"),
                        "actions_since_clue_expiry": since})
    return out
