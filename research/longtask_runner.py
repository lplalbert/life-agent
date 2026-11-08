#!/usr/bin/env python3
"""长任务族 episode 运行器（T1–T3 价值域实验）。

与 pilot_runner 分工：这边任务是多轮下单/全目录巡览/长清单（历史 12→96 步），
规格来自 longtask_manifest，expected_ids 在真实复位后条件解析（Kotlin RNG 不
复刻），判分用 longtask_verdict 逐单核对。断点续跑/预算帽/顺序随机化与
pilot_runner 同口径（同名 episode 目录会被跳过/覆盖——重跑旧臂用新标签）。

真实复位枚举：同 (task_seed, catalog_size) 的目录决定论，先做一次探针复位读
items，缓存后注入 expected_ids 再交 run_episode（其内部会再复位，两次复位同
配置 = 同初始快照）。

用法：
    python research/longtask_runner.py --dry-reset           # 零模型验证规格/复位/任务
    python research/longtask_runner.py --families T1_rounds --policies recent1sum3 --limit 2
    python research/longtask_runner.py                       # 全网格（断点续跑）
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.env_control import DEFAULT_SERIAL, broadcast_reset, read_state  # noqa: E402
from research.longtask_manifest import build_manifest, build_task, finalize_reset  # noqa: E402
from research.longtask_verdict import compute_long_verdict  # noqa: E402
from research.pilot_runner import POLICY_VARIANTS  # noqa: E402
from research.episode_runner import run_episode  # noqa: E402


def _probe_items(serial: str, spec: dict, cache: dict) -> list[dict]:
    """真实复位枚举目录（同 seed 同 catalog 决定论；结果按键缓存）。"""
    key = (spec["task_seed"], spec["reset"]["catalog_size"])
    if key not in cache:
        broadcast_reset(
            serial,
            episode=f"probe-{key[0]}-{key[1]}",
            task_seed=spec["task_seed"], fault_seed=spec["fault_seed"],
            fault="none", expected_ids=None, initial_cart=None,
            delay_ms=None, noop_n=None, popup_at=None, confusion_at=None,
            catalog_size=spec["reset"]["catalog_size"],
        )
        cache[key] = read_state(serial).get("items", [])
    return cache[key]


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-reset", action="store_true",
                        help="只验证规格/真实复位枚举/任务文本，不调用模型（零费用）")
    parser.add_argument("--families", help="任务族（逗号分隔，如 T1_rounds,T2_tour）")
    parser.add_argument("--seeds", default="1,2,3", help="任务种子（逗号分隔）")
    parser.add_argument("--fault", default="promo")
    parser.add_argument("--policies", default="recent1sum3",
                        help="策略变体（逗号分隔）；可用: " + ",".join(sorted(POLICY_VARIANTS)))
    parser.add_argument("--limit", type=int, help="只跑前 N 个（preflight）")
    parser.add_argument("--match", help="只跑 episode_id 含任一逗号分隔子串的")
    parser.add_argument("--order-seed", type=int, default=20261107)
    parser.add_argument("--budget-input", type=int, default=4_000_000,
                        help="累计未缓存输入 token 硬帽（默认 4M）")
    parser.add_argument("--budget-output", type=int, default=400_000)
    parser.add_argument("--max-steps", type=int, default=120,
                        help="长任务步数上限（T1 K=8 轮需 ≥48，默认 120）")
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "episodes")
    parser.add_argument("--no-resume", action="store_true", help="不跳过已完成 episode")
    parser.add_argument("--retry-invalid", action="store_true",
                        help="重试 invalid_reset 的 episode（默认留档不重跑）")
    args = parser.parse_args(argv)

    fams = tuple(s for s in (args.families or "").split(",") if s) or None
    seeds = tuple(int(s) for s in args.seeds.split(",") if s)
    pols = tuple(s for s in args.policies.split(",") if s)
    for p in pols:
        if p not in POLICY_VARIANTS:
            raise ValueError(f"未知策略变体 {p!r}；可用: {sorted(POLICY_VARIANTS)}")

    manifest = build_manifest(families=fams, seeds=seeds, fault=args.fault)
    eps = [{"spec": s, "policy": p, "episode_id": f"{s['spec_id']}-{p}"}
           for s in manifest for p in pols]

    from life_agent.cli import _load_env
    _load_env()
    import os
    model = args.model or os.environ.get("ANTHROPIC_MODEL") or "mimo-v2.6-pro"

    random.Random(args.order_seed).shuffle(eps)
    if args.match:
        pats = [s for s in args.match.split(",") if s]
        eps = [e for e in eps if any(p in e["episode_id"] for p in pats)]
    if args.limit:
        eps = eps[: args.limit]

    item_cache: dict = {}
    cum_input = cum_output = cum_cache_read = 0
    ran, skipped, failed = [], [], []
    t_all = time.time()

    # 续跑口径与 pilot_runner 一致：live 不把 dry_reset_ok 当完成（同名目录
    # dry-run 后可直接上真跑）；invalid_reset 默认留档不静默重跑。
    done_status = ("completed", "dry_reset_ok") if args.dry_reset else ("completed",)
    for idx, ep in enumerate(eps, 1):
        spec, policy = ep["spec"], ep["policy"]
        ep_dir = Path(args.out) / ep["episode_id"]
        prev = ep_dir / "episode.json"
        if prev.exists() and not args.no_resume:
            status = json.loads(prev.read_text(encoding="utf-8")).get("status")
            if status in done_status or (status == "invalid_reset" and not args.retry_invalid):
                skipped.append((ep["episode_id"], status))
                continue

        print(f"\n=== [{idx}/{len(eps)}] {ep['episode_id']} "
              f"({spec['family']}/{policy}) ===")
        try:
            items = _probe_items(args.serial, spec, item_cache)
            reset = finalize_reset(spec, items)
            expected = list(reset["expected_ids"])
            reset = {
                **reset, "episode": ep["episode_id"],
                "task_seed": spec["task_seed"], "fault_seed": spec["fault_seed"],
            }
            cfg = {"episode_id": ep["episode_id"], "reset": reset}
            task_preview = build_task(items, spec)
            print(f"    expected={expected} task={task_preview}")
            if args.dry_reset:
                ep_dir.mkdir(parents=True, exist_ok=True)
                record = {"episode_id": ep["episode_id"], "status": "dry_reset_ok",
                          "task": task_preview, "config": cfg}
                (ep_dir / "episode.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                ran.append(ep["episode_id"])
                continue

            ctx_policy, max_images, extras = POLICY_VARIANTS[policy]
            record = run_episode(
                cfg, serial=args.serial, model=model, policy=ctx_policy,
                max_images=max_images, experience_text="", out_root=args.out,
                max_steps=args.max_steps, dry_reset=False,
                task_builder=lambda its, _s=spec: build_task(its, _s),
                verdict_fn=lambda state, _s=spec, _e=expected:
                    compute_long_verdict(state, _s, _e),
                **extras,
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
                  f"recovered={v['recovered']} rounds={v.get('rounds_completed')}"
                  f"/{v.get('rounds_expected')} | in={u['input_tokens']} "
                  f"out={u['output_tokens']} cache_r={u['cache_read']} | "
                  f"{record['elapsed_s']}s")
            ran.append(ep["episode_id"])
            if cum_input >= args.budget_input or cum_output >= args.budget_output:
                print(f"\n[预算帽] 累计 in={cum_input} out={cum_output} 达帽，停止。"
                      f" 已跑 {len(ran)}，余 {len(eps) - idx}。续跑直接重跑本命令。")
                break
        else:
            failed.append((ep["episode_id"], status))
            print(f"    [{status}] 留档不重试")

    summary = {
        "dry_reset": args.dry_reset, "ran": len(ran), "skipped": len(skipped),
        "failed": failed,
        "usage": {"input_tokens": cum_input, "output_tokens": cum_output,
                  "cache_read": cum_cache_read},
        "budget": {"input": args.budget_input, "output": args.budget_output},
        "elapsed_s": round(time.time() - t_all, 1), "order_seed": args.order_seed,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"longtask_summary-{'dry' if args.dry_reset else 'live'}-{int(time.time())}.json"
     ).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n== 汇总 ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
