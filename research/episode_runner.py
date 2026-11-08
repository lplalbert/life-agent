#!/usr/bin/env python3
"""research-env episode 控制器（验收 C/D）：复位 → 派任务 → 收集 ground truth → 评分。

权限分离（android-research-platform.md）：
- 控制器/评估器读应用隐藏状态（state.json / events.jsonl）与故障参数；
- agent 只见任务文本、截图与控件树；故障标签、expected_ids、success 判定
  不进入任务文本与 agent 可见事件。
- 复位探针失败标 invalid 并停止该 episode，不静默重跑。

episode 目录（runs/episodes/<id>/）：
- task.txt / stepNN.png / stepNN_ui.txt / run.jsonl：agent 可见或产生的证据；
- ground_truth_state.json / ground_truth_events.jsonl / episode.json：控制器产物，
  只在 agent 运行结束后写出，agent 代码不读取。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.env_control import (  # noqa: E402
    DEFAULT_SERIAL, broadcast_reset, open_app, read_events, read_state,
)
from life_agent.agent import MODEL_DEFAULT, Agent  # noqa: E402
from life_agent.runlog import RunLogger  # noqa: E402


def build_task(items: list, expected_ids: list) -> str:
    """任务文本：只给业务目标，不含故障标签、路径提示或隐藏参数。"""
    want = set(expected_ids or [])
    names = [it["name"] for it in items if it.get("id") in want]
    if names:
        targets = "、".join(f"「{n}」" for n in names)
        return f"在研测商城购买 {targets}，完成下单。"
    return "在研测商城自选商品购买一件，完成下单。"


def compute_verdict(state: dict, fault: str) -> dict:
    """独立成功检查：只看应用内部业务状态 + 目标参数，不看模型 finish 文本。"""
    success = bool(state.get("success"))
    counters = state.get("counters", {})
    c = counters.get(fault, {}) or {}
    triggered = int(c.get("triggered", 0) or 0)
    return {
        "success": success,
        "fault": fault,
        "fault_triggered": triggered > 0,
        "triggered_count": triggered,
        "recovered": bool(triggered > 0 and success),
        "order_status": state.get("order_status"),
        "submitted_ids": state.get("submitted_ids"),
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_episode(cfg: dict, *, serial: str, model: str, policy: str, max_images: int,
                experience_text: str, out_root: Path, max_steps: int,
                dry_reset: bool, retrieval: bool = False,
                summarize_every: int = 0, retrieval_nudge: bool = False,
                provenance_gate: bool = False) -> dict:
    episode_id = cfg["episode_id"]
    ep_dir = Path(out_root) / episode_id
    ep_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. 复位 + 独立探针 ----
    broadcast_reset(serial, **cfg["reset"])
    state0 = read_state(serial)
    if not state0.get("post_reset_clean"):
        record = {
            "episode_id": episode_id, "status": "invalid_reset",
            "config": cfg, "reset_probe": state0.get("post_reset_clean"),
        }
        (ep_dir / "episode.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    task = build_task(state0.get("items", []), cfg["reset"].get("expected_ids") or [])
    (ep_dir / "task.txt").write_text(task, encoding="utf-8")

    if dry_reset:
        record = {"episode_id": episode_id, "status": "dry_reset_ok", "task": task,
                  "config": cfg}
        (ep_dir / "episode.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    # ---- 2. agent 运行（只见 task + 感知；不读取本节目录的 ground truth） ----
    import anthropic
    from life_agent.phone import Phone

    # 重试/续跑前归档未完成尝试的证据：run.jsonl 为追加写，截图按步号命名，
    # 不归档会混写两次运行。保留原始（不清理、不覆盖）到 attempt-<ts>/。
    stale = sorted(ep_dir.glob("run*.jsonl")) + sorted(ep_dir.glob("step*"))
    if stale:
        attempt_dir = ep_dir / f"attempt-{int(time.time())}"
        attempt_dir.mkdir()
        for p in stale:
            p.rename(attempt_dir / p.name)

    open_app(serial)
    phone = Phone(serial=serial)
    client = anthropic.Anthropic()
    run_logger = RunLogger(
        ep_dir,
        model=model,
        endpoint=os.environ.get("ANTHROPIC_BASE_URL", ""),
        context_policy=policy,
        max_images=max_images,
        experience_sha256=_sha256(experience_text) if experience_text else None,
        experience_chars=len(experience_text),
        task_sha256=_sha256(task),
        episode_id=episode_id,
        env_version=state0.get("env_version"),
        retrieval=retrieval,
        summarize_every=summarize_every,
        retrieval_nudge=retrieval_nudge,
        provenance_gate=provenance_gate,
    )
    agent = Agent(
        phone, client, model=model, run_dir=ep_dir, max_steps=max_steps,
        context_policy=policy, max_images=max_images,
        frozen_experience=experience_text, run_logger=run_logger,
        retrieval=retrieval, summarize_every=summarize_every,
        retrieval_nudge=retrieval_nudge, provenance_gate=provenance_gate,
    )
    t0 = time.time()
    usage = agent.run(task)
    elapsed = time.time() - t0

    # ---- 3. ground truth 收集（仅控制器；agent 运行已结束） ----
    state1 = read_state(serial)
    events = read_events(serial)
    (ep_dir / "ground_truth_state.json").write_text(
        json.dumps(state1, ensure_ascii=False, indent=2), encoding="utf-8")
    with (ep_dir / "ground_truth_events.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # ---- 4. 评分与记录 ----
    verdict = compute_verdict(state1, cfg["reset"]["fault"])
    record = {
        "episode_id": episode_id, "status": "completed", "config": cfg,
        "task": task, "verdict": verdict,
        "usage": {
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "cache_read": usage.cache_read, "cache_creation": usage.cache_creation,
        },
        "elapsed_s": round(elapsed, 2), "max_steps": max_steps,
        "model": model, "context_policy": policy, "max_images": max_images,
        "retrieval": retrieval, "summarize_every": summarize_every,
        "retrieval_nudge": retrieval_nudge, "provenance_gate": provenance_gate,
    }
    (ep_dir / "episode.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def _csv(value: str | None) -> list:
    return [v for v in (value or "").split(",") if v] if value else []


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", required=True, help="episode 唯一 ID")
    parser.add_argument("--task-seed", type=int, default=1)
    parser.add_argument("--fault-seed", type=int, default=1)
    parser.add_argument("--fault", default="none",
                        choices=["none", "delay", "noop", "popup", "confusion", "promo"])
    parser.add_argument("--delay-ms", type=int)
    parser.add_argument("--noop-n", type=int)
    parser.add_argument("--popup-at")
    parser.add_argument("--confusion-at")
    parser.add_argument("--expected", help="目标商品 id，逗号分隔，如 p1,p3")
    parser.add_argument("--initial-cart", help="初始清单商品 id，逗号分隔")
    parser.add_argument("--policy", default="full",
                        choices=["full", "recent", "evidence", "evidence_nofresh", "keyframe"])
    parser.add_argument("--max-images", type=int, default=2)
    parser.add_argument("--retrieval", action="store_true",
                        help="注入历史取回工具 review_screenshot（方法组件）")
    parser.add_argument("--retrieval-nudge", action="store_true",
                        help="system 追加取回触发提示（需 --retrieval）")
    parser.add_argument("--provenance-gate", action="store_true",
                        help="证据闸门：type_text 无出处值拦下取证（需 --retrieval）")
    parser.add_argument("--summarize-every", type=int, default=0,
                        help="摘要蒸馏基线：每 M 回合一次额外模型调用（0=关）")
    parser.add_argument("--experience", type=Path, help="冻结经验文件（研究期间不变）")
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL") or MODEL_DEFAULT)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--serial", default=DEFAULT_SERIAL)
    parser.add_argument("--out", type=Path, default=ROOT / "runs" / "episodes")
    parser.add_argument("--dry-reset", action="store_true",
                        help="只验证复位/任务文本生成，不调用模型（不产生费用）")
    args = parser.parse_args(argv)

    from life_agent.cli import _load_env
    _load_env()

    experience_text = ""
    if args.experience:
        experience_text = args.experience.read_text(encoding="utf-8")

    cfg = {
        "episode_id": args.episode,
        "reset": {
            "episode": args.episode,
            "task_seed": args.task_seed,
            "fault_seed": args.fault_seed,
            "fault": args.fault,
            "delay_ms": args.delay_ms,
            "noop_n": args.noop_n,
            "popup_at": args.popup_at,
            "confusion_at": args.confusion_at,
            "expected_ids": _csv(args.expected),
            "initial_cart": _csv(args.initial_cart),
        },
    }
    record = run_episode(
        cfg, serial=args.serial, model=args.model, policy=args.policy,
        max_images=args.max_images, experience_text=experience_text,
        out_root=args.out, max_steps=args.max_steps, dry_reset=args.dry_reset,
        retrieval=args.retrieval, summarize_every=args.summarize_every,
        retrieval_nudge=args.retrieval_nudge,
        provenance_gate=args.provenance_gate,
    )
    print(json.dumps({k: record[k] for k in record if k != "reset_probe"},
                     ensure_ascii=False, indent=2))
    return 0 if record["status"] in ("completed", "dry_reset_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
