#!/usr/bin/env python3
"""episode 矩阵运行器（12 实例 × 条件 × 策略变体 × 1 种子）。

manifest 决定论生成（research/pilot_manifest.json 可 --emit-manifest 落盘核对）：
- 12 个开发实例覆盖 4 个任务家族；任务 ID/商品名全部来自真实复位枚举（seed 1–6 目录），
  不编造；每实例带 1 个干扰项初值（confusion 机制需要可翻转目标）。
- 6 条件 = 正常 + 5 机制故障（delay/noop/popup/confusion/promo 瞬时线索）。
- 策略变体 = full / recent1/2/4 / evidence2（label → (context_policy, max_images, extras)）；
  默认标签 full/recent/evidence 为 180-pilot 的 ID 兼容命名（recent/evidence = 2 帧）；
  批次 3：keyframe2/4（关键帧基线）、recent1retr/recent2retr（历史取回工具）、
  recent1sum3（摘要蒸馏基线）。
- 各方法同 reset 配置 = 同初始快照（应用确定性复位）。
- 执行顺序固定种子随机化；断点续跑跳过已完成 episode；invalid_reset 留档不静默重跑。
- 硬预算帽：累计（未缓存输入+输出）token 超帽即停并出摘要；缓存读单独统计。

用法：
    python research/pilot_runner.py --dry-reset          # 零模型，验证复位/任务/目录
    python research/pilot_runner.py --limit 5            # 先跑 5 条作 preflight
    python research/pilot_runner.py                      # 全量（断点续跑）
    python research/pilot_runner.py --emit-manifest      # 只写 manifest
    # 瞬时线索实验：c5 全策略 + 近期策略对照条件
    python research/pilot_runner.py --conditions c5 --policies full,recent1,recent2,evidence2
    python research/pilot_runner.py --conditions c0,c3,c4 --policies recent1
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.episode_runner import run_episode  # noqa: E402

# ---- 12 开发实例（真实目录枚举 2026-11-07：每 seed 4 件，p1..p4）----
# 家族：F1 单件·首件 / F2 多件·相邻 / F3 单件·后件 / F4 多件·跨件。
# 每实例一个干扰项 init（confusion 翻转目标；正常条件下也须不误提交）。
INSTANCES = [
    # id      seed  family  expected      init
    ("i01",   1,    "F1",   ["p1"],       ["p2"]),
    ("i02",   2,    "F1",   ["p1"],       ["p3"]),
    ("i03",   4,    "F1",   ["p1"],       ["p2"]),
    ("i04",   1,    "F2",   ["p1", "p2"], ["p3"]),
    ("i05",   3,    "F2",   ["p1", "p2"], ["p4"]),
    ("i06",   5,    "F2",   ["p1", "p2"], ["p3"]),
    ("i07",   2,    "F3",   ["p3"],       ["p1"]),
    ("i08",   4,    "F3",   ["p4"],       ["p1"]),
    ("i09",   6,    "F3",   ["p3"],       ["p1"]),
    ("i10",   3,    "F4",   ["p2", "p4"], ["p1"]),
    ("i11",   5,    "F4",   ["p2", "p4"], ["p1"]),
    ("i12",   6,    "F4",   ["p2", "p4"], ["p3"]),
    # ---- 种子集 2（2026-11-08 真实目录枚举 seed 7–12，家族×种子布局镜像 i01–i12）----
    ("i13",   7,    "F1",   ["p1"],       ["p2"]),
    ("i14",   8,    "F1",   ["p1"],       ["p3"]),
    ("i15",   10,   "F1",   ["p1"],       ["p2"]),
    ("i16",   7,    "F2",   ["p1", "p2"], ["p3"]),
    ("i17",   9,    "F2",   ["p1", "p2"], ["p4"]),
    ("i18",   11,   "F2",   ["p1", "p2"], ["p3"]),
    ("i19",   8,    "F3",   ["p3"],       ["p1"]),
    ("i20",   10,   "F3",   ["p4"],       ["p1"]),
    ("i21",   12,   "F3",   ["p3"],       ["p1"]),
    ("i22",   9,    "F4",   ["p2", "p4"], ["p1"]),
    ("i23",   11,   "F4",   ["p2", "p4"], ["p1"]),
    ("i24",   12,   "F4",   ["p2", "p4"], ["p3"]),
]

# 6 条件：正常 + 5 机制故障（delay/noop/popup/confusion 触发点固定 enter_cart；promo 复位首屏闪现）
CONDITIONS = [
    ("c0", {"fault": "none"}),
    ("c1", {"fault": "delay", "delay_ms": 3000}),
    ("c2", {"fault": "noop", "noop_n": 2}),
    ("c3", {"fault": "popup", "popup_at": "enter_cart"}),
    ("c4", {"fault": "confusion", "confusion_at": "enter_cart"}),
    ("c5", {"fault": "promo"}),
]

# 策略变体：episode 标签 → (context_policy, max_images, extras)。
# recent/evidence 标签 = 180-pilot ID 兼容（均为 2 帧）。
# 批次 3：keyframe = 通用关键帧基线；retr = 历史取回工具（方法组件，消融即移除）；
# sumM = 摘要蒸馏基线（每 M 回合一次额外模型调用，计费计入）。
# 批次 4 消融（方法 M = ev2sum3retr，计划 §4 全栈）：
#   rec2sum3retr   = 去验证状态（锚定关，纯近窗填充）
#   ev2nfsum3retr  = 去通道新鲜度（最新帧不恒留）
#   ev2sum3        = 去历史取回
#   仅重试          = recent1（既有 0/12；预算匹配对照 recent2 既有 1/12）
POLICY_VARIANTS = {
    "full": ("full", 2, {}),
    "recent": ("recent", 2, {}),
    "recent1": ("recent", 1, {}),
    "recent2": ("recent", 2, {}),
    "recent4": ("recent", 4, {}),
    "evidence": ("evidence", 2, {}),
    "evidence2": ("evidence", 2, {}),
    "keyframe2": ("keyframe", 2, {}),
    "keyframe4": ("keyframe", 4, {}),
    "recent1retr": ("recent", 1, {"retrieval": True}),
    "recent2retr": ("recent", 2, {"retrieval": True}),
    "recent1sum3": ("recent", 1, {"summarize_every": 3}),
    "ev2sum3retr": ("evidence", 2, {"summarize_every": 3, "retrieval": True}),
    "rec2sum3retr": ("recent", 2, {"summarize_every": 3, "retrieval": True}),
    "ev2nfsum3retr": ("evidence_nofresh", 2, {"summarize_every": 3, "retrieval": True}),
    "ev2sum3": ("evidence", 2, {"summarize_every": 3}),
    # 批次 5：M 扫参（recent1 基座）+ 取回触发 A/B。
    # recent1sum3fx = M=3 在截断修复语义（上限 1200、截断=失败重试）下的重跑点——
    # 批 3 的 recent1sum3 跑在修复前语义，扫参曲线不能混用；新标签保证不覆盖批 3 目录。
    # 取回 A/B：recent1retrnudge 对照批 3 recent1retr（2/12；取回路径代码未变，
    # 复用先例 = 批 3 复用批 1 基线）。
    "recent1sum2": ("recent", 1, {"summarize_every": 2}),
    "recent1sum3fx": ("recent", 1, {"summarize_every": 3}),
    "recent1sum6": ("recent", 1, {"summarize_every": 6}),
    "recent1retrnudge": ("recent", 1, {"retrieval": True, "retrieval_nudge": True}),
    # 批次 6（效度复核）：recent1sum6b = M=6 在批量感知输出预算下重跑
    # （max_tokens 随候选帧数放宽 ≥400/帧，≤3 帧仍 1200）。批次 5 的 recent1sum6
    # 失利含「固定 1200 上限 × 6 帧批量」截断伪影，此臂分离「频率不足」与「截断」两解。
    "recent1sum6b": ("recent", 1, {"summarize_every": 6}),
    # Provenance Gate（PG）臂：type_text 出处检查（默认关，标签区分 A/B）。
    # recent1retrpg 对照 recent1retrnudge（软提示 10/12）：硬触发 vs 弱话术；
    # ev2sum3retrpg = 全方法 M + PG（价值域「蒸馏+Gate」臂）。
    "recent1retrpg": ("recent", 1, {"retrieval": True, "provenance_gate": True}),
    "ev2sum3retrpg": ("evidence", 2, {"summarize_every": 3, "retrieval": True,
                                      "provenance_gate": True}),
}

POLICIES = ("full", "recent", "evidence")


def build_manifest(conditions: set | None = None,
                   policies: tuple | None = None) -> list[dict]:
    conds = [c for c in CONDITIONS if not conditions or c[0] in conditions]
    pols = tuple(policies) if policies else POLICIES
    for p in pols:
        if p not in POLICY_VARIANTS:
            raise ValueError(f"未知策略变体 {p!r}；可用: {sorted(POLICY_VARIANTS)}")
    eps = []
    for iid, seed, family, expected, init in INSTANCES:
        for cid, fault_cfg in conds:
            for policy in pols:
                reset = {
                    "episode": f"{iid}-{cid}-{policy}",   # 应用侧事件分区键
                    "task_seed": seed,
                    "fault_seed": seed,
                    "expected_ids": list(expected),
                    "initial_cart": list(init),
                    "delay_ms": None, "noop_n": None,
                    "popup_at": None, "confusion_at": None,
                    **fault_cfg,
                }
                eps.append({
                    "episode_id": f"{iid}-{cid}-{policy}",
                    "instance": iid, "family": family,
                    "condition": cid, "policy": policy,
                    "reset": reset,
                })
    return eps


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-reset", action="store_true",
                        help="只验证复位/任务文本/目录，不调用模型（零费用）")
    parser.add_argument("--emit-manifest", action="store_true",
                        help="写 research/pilot_manifest.json 后退出")
    parser.add_argument("--limit", type=int, help="只跑前 N 个（preflight）")
    parser.add_argument("--match", help="只跑 episode_id 含任一逗号分隔子串的（如 i01 或 c4）")
    parser.add_argument("--conditions", help="只包含这些条件 id（逗号分隔，如 c5 或 c0,c3,c4）")
    parser.add_argument("--policies", default=",".join(POLICIES),
                        help="策略变体（逗号分隔，如 full,recent1,recent2,evidence2）；"
                             "可用: " + ",".join(sorted(POLICY_VARIANTS)))
    parser.add_argument("--order-seed", type=int, default=20261107)
    parser.add_argument("--budget-input", type=int, default=4_000_000,
                        help="累计未缓存输入 token 硬帽（默认 4M）")
    parser.add_argument("--budget-output", type=int, default=250_000)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL") or "mimo-v2.6-pro")
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "episodes")
    parser.add_argument("--retry-invalid", action="store_true",
                        help="重跑 invalid_reset 的 episode（默认留档跳过）")
    parser.add_argument("--no-resume", action="store_true", help="不跳过已完成 episode")
    args = parser.parse_args(argv)

    conds = {s for s in args.conditions.split(",") if s} if args.conditions else None
    pols = tuple(s for s in args.policies.split(",") if s)
    manifest = build_manifest(conditions=conds, policies=pols)
    manifest_path = ROOT / "research" / "pilot_manifest.json"
    if args.emit_manifest:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"manifest 写入 {manifest_path}（{len(manifest)} episodes）")
        return 0

    from life_agent.cli import _load_env
    _load_env()

    order = list(manifest)
    random.Random(args.order_seed).shuffle(order)  # 固定种子随机化执行顺序
    if args.match:
        pats = [s for s in args.match.split(",") if s]
        order = [e for e in order if any(p in e["episode_id"] for p in pats)]
    if args.limit:
        order = order[: args.limit]

    done_status = ("completed", "dry_reset_ok") if args.dry_reset else ("completed",)
    cum_input = cum_output = cum_cache_read = 0
    ran, skipped, failed = [], [], []
    t_all = time.time()

    for idx, ep in enumerate(order, 1):
        ep_dir = Path(args.out) / ep["episode_id"]
        prev = ep_dir / "episode.json"
        if prev.exists() and not args.no_resume:
            status = json.loads(prev.read_text(encoding="utf-8")).get("status")
            if status in done_status or (status == "invalid_reset" and not args.retry_invalid):
                skipped.append((ep["episode_id"], status))
                continue

        print(f"\n=== [{idx}/{len(order)}] {ep['episode_id']} "
              f"({ep['instance']}/{ep['condition']}/{ep['policy']}) ===")
        try:
            ctx_policy, max_images, extras = POLICY_VARIANTS[ep["policy"]]
            record = run_episode(
                ep, serial=args.serial, model=args.model, policy=ctx_policy,
                max_images=max_images, experience_text="", out_root=args.out,
                max_steps=args.max_steps, dry_reset=args.dry_reset, **extras,
            )
        except Exception as exc:  # noqa: BLE001 — 长跑不因单条 API/设备毛刺终止
            failed.append((ep["episode_id"], f"{type(exc).__name__}: {exc}"))
            print(f"    [EXCEPTION] {type(exc).__name__}: {exc}（留档；续跑自动重试）")
            continue
        status = record["status"]
        if status == "completed":
            u = record["usage"]
            cum_input += u["input_tokens"]
            cum_output += u["output_tokens"]
            cum_cache_read += u["cache_read"]
            v = record["verdict"]
            print(f"    success={v['success']} triggered={v['triggered_count']} "
                  f"recovered={v['recovered']} | in={u['input_tokens']} out={u['output_tokens']} "
                  f"cache_r={u['cache_read']} | {record['elapsed_s']}s")
            ran.append(ep["episode_id"])
            if cum_input >= args.budget_input or cum_output >= args.budget_output:
                print(f"\n[预算帽] 累计 in={cum_input} out={cum_output} 达帽，停止。"
                      f" 已跑 {len(ran)}，余 {len(order) - idx}。续跑直接重跑本命令。")
                break
        elif status == "dry_reset_ok":
            ran.append(ep["episode_id"])
        else:
            failed.append((ep["episode_id"], status))
            print(f"    [{status}] 留档不重试")

    summary = {
        "dry_reset": args.dry_reset,
        "ran": len(ran), "skipped": len(skipped), "failed": failed,
        "usage": {"input_tokens": cum_input, "output_tokens": cum_output,
                  "cache_read": cum_cache_read},
        "budget": {"input": args.budget_input, "output": args.budget_output},
        "elapsed_s": round(time.time() - t_all, 1),
        "order_seed": args.order_seed,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"pilot_summary-{'dry' if args.dry_reset else 'live'}-{int(time.time())}.json"
     ).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 汇总 ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
