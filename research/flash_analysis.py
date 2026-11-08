#!/usr/bin/env python3
"""promo 瞬时线索实验后验分析：按（条件, 策略变体）聚合恢复率与取回路径。

归因证据（全部来自 run.jsonl + ground_truth，不依赖模型自报）：
- type_text 动作的 args.text 与 ground truth promo_code 比对 → 正确码在第几步被输入；
- 该动作对应调用的 retained_image_steps 是否含 step 0（横幅帧）：
    含   → 取证时图像证据可用（历史取回 / 始终在窗内 / 已钉住）；
    不含 → 码不可能来自当次请求，只能是观察时即时蒸馏进文本记忆或猜测
           （4 位 32 字符表，精确猜中概率 1/1M，可忽略）；
- 分路径细分：typing 前有 review_screenshot(0) 动作（步号严格更早）= 显式历史取回；
  summary_call 的 summary_text 含码 = 摘要蒸馏抄录（帧已逐出仍可经文本送达）；
- 未输入正确码的 episode：错码尝试/零尝试分别计。

用法：
    python research/flash_analysis.py                  # 全量聚合
    python research/flash_analysis.py --conditions c5
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "runs" / "episodes"

CODE_RE = re.compile(r"^[A-Z2-9]{4}$")


def load_episode(ep_dir: Path) -> dict | None:
    ej = ep_dir / "episode.json"
    if not ej.exists():
        return None
    rec = json.loads(ej.read_text(encoding="utf-8"))
    if rec.get("status") != "completed":
        return None
    out = {
        "episode_id": rec["episode_id"],
        "verdict": rec.get("verdict", {}),
        "usage": rec.get("usage", {}),
        "elapsed_s": rec.get("elapsed_s"),
    }
    gt = ep_dir / "ground_truth_state.json"
    if gt.exists():
        s = json.loads(gt.read_text(encoding="utf-8"))
        out["promo_code"] = s.get("promo_code", "")
        out["code_entered"] = s.get("code_entered", "")
        out["success_state"] = s.get("success")
        out["counters"] = s.get("counters", {})
    actions, calls, summaries = [], [], []
    rj = ep_dir / "run.jsonl"
    if rj.exists():
        for line in rj.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("type") == "call":
                calls.append(e)
            elif e.get("type") == "action":
                actions.append(e)
            elif e.get("type") == "summary_call":
                summaries.append(e)
    out["actions"] = actions
    out["calls"] = calls
    out["summaries"] = summaries
    return out


def classify(rec: dict) -> dict:
    """对 promo episode 归因：正确码输入步、取证时横幅帧是否在上下文、取回/摘要路径。"""
    code = rec.get("promo_code", "")
    res = {
        "typed_correct": False, "typed_step": None,
        "banner_frame_at_typing": None,   # True=可用 / False=已逐出 / None=未输入
        "banner_retrieved_before_typing": False,  # 显式 review_screenshot(0)，步号严格更早
        "code_in_summary": False,                 # 摘要蒸馏文本含码
        "wrong_attempts": 0, "typed_any_code": False,
    }
    if not code:
        return res
    code_u = code.upper()
    for s in rec.get("summaries", []):
        if code_u in str(s.get("summary_text") or "").upper():
            res["code_in_summary"] = True
    call_by_seq = {c["seq"]: c for c in rec.get("calls", [])}
    for a in rec.get("actions", []):
        if a.get("name") != "type_text":
            continue
        text = str((a.get("args") or {}).get("text", "")).strip()
        if not CODE_RE.match(text.upper()):
            continue
        res["typed_any_code"] = True
        if text.upper() == code_u:
            res["typed_correct"] = True
            res["typed_step"] = a.get("step")
            call = call_by_seq.get(a.get("step"))
            if call is not None:
                res["banner_frame_at_typing"] = 0 in (call.get("retained_image_steps") or [])
            res["banner_retrieved_before_typing"] = any(
                x.get("name") == "review_screenshot"
                and (x.get("args") or {}).get("step") == 0
                and (x.get("step") is not None and res["typed_step"] is not None)
                and x["step"] < res["typed_step"]
                for x in rec.get("actions", [])
            )
        else:
            res["wrong_attempts"] += 1
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conditions", help="只看这些条件（逗号分隔）")
    ap.add_argument("--match", help="只看 episode_id 含任一逗号分隔子串的（如 i13,i14）")
    ap.add_argument("--out", type=Path, help="写 JSON 汇总到该文件")
    args = ap.parse_args()
    conds = {s for s in (args.conditions or "").split(",") if s} or None
    pats = [s for s in (args.match or "").split(",") if s]

    groups = defaultdict(list)
    for ep_dir in sorted(EPISODES.iterdir() if EPISODES.exists() else []):
        if not ep_dir.is_dir():
            continue
        rec = load_episode(ep_dir)
        if rec is None:
            continue
        parts = rec["episode_id"].split("-")
        if len(parts) < 3:
            continue
        cond, label = parts[1], parts[-1]
        if conds and cond not in conds:
            continue
        if pats and not any(p in rec["episode_id"] for p in pats):
            continue
        rec["attribution"] = classify(rec)
        groups[(cond, label)].append(rec)

    rows = []
    for (cond, label), recs in sorted(groups.items()):
        n = len(recs)
        succ = sum(1 for r in recs if r["verdict"].get("success"))
        trig = sum(1 for r in recs if r["verdict"].get("fault_triggered"))
        recd = sum(1 for r in recs if r["verdict"].get("recovered"))
        att = [r["attribution"] for r in recs if r.get("promo_code")]
        typed = [a for a in att if a["typed_correct"]]
        with_img = sum(1 for a in typed if a["banner_frame_at_typing"] is True)
        no_img = sum(1 for a in typed if a["banner_frame_at_typing"] is False)
        via_retr = sum(1 for a in typed if a["banner_retrieved_before_typing"])
        via_sum = sum(1 for a in typed
                      if a["code_in_summary"] and not a["banner_frame_at_typing"])
        wrong = sum(1 for a in att if not a["typed_correct"] and a["typed_any_code"])
        never = sum(1 for a in att if not a["typed_any_code"])
        u_in = sum(r["usage"].get("input_tokens", 0) for r in recs)
        u_out = sum(r["usage"].get("output_tokens", 0) for r in recs)
        u_cr = sum(r["usage"].get("cache_read", 0) for r in recs)
        rows.append({
            "condition": cond, "policy": label, "n": n,
            "success": succ, "triggered": trig, "recovered": recd,
            "promo_episodes": len(att),
            "typed_correct": len(typed),
            "typed_banner_frame_retained": with_img,
            "typed_banner_frame_evicted": no_img,
            "typed_via_retrieval": via_retr,
            "typed_via_summary_distill": via_sum,
            "typed_wrong_only": wrong, "never_typed": never,
            "usage": {"input": u_in, "output": u_out, "cache_read": u_cr},
        })
        print(f"{cond}×{label:<8} n={n} success={succ} recovered={recd}/{trig} "
              f"typed_ok={len(typed)}(img@typing:{with_img}/evicted:{no_img}"
              f"/retr:{via_retr}/sum:{via_sum}) "
              f"wrong_only={wrong} never={never} in={u_in} out={u_out} cache_r={u_cr}")

    if args.out:
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n汇总写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
