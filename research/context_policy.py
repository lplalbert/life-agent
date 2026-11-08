"""Offline image-reference selector. No model, device, or claimed token savings.

Events are causal, ordered records. Verification status must come from the
runtime verifier, never the fault injector or benchmark evaluator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(events: list[dict]) -> None:
    observations: set[int] = set()
    previous = -1
    for event in events:
        step = event.get("step")
        if type(step) is not int or step < previous or step < 0:
            raise ValueError("steps must be nonnegative integers in causal order")
        previous = step
        if event.get("kind") == "observation":
            if step in observations:
                raise ValueError("duplicate observation step")
            observations.add(step)
            if not isinstance(event.get("image_ref"), (str, type(None))):
                raise ValueError("image_ref must be a string or null")
        elif event.get("kind") == "verification":
            if not isinstance(event.get("check_id"), str) or not event["check_id"]:
                raise ValueError("verification requires check_id")
            if event.get("state") not in {"pending", "failed", "verified"}:
                raise ValueError("invalid verification state")
            sources = event.get("source_steps")
            if not isinstance(sources, list) or not sources:
                raise ValueError("verification requires source_steps")
            if any(type(s) is not int or s not in observations for s in sources):
                raise ValueError("source_steps must reference already observed steps")
        else:
            raise ValueError("unknown event kind")


def _keyframes(available: list[int], observations: dict[int, dict]) -> list[int]:
    """关键帧 = 每段「屏幕状态游程」的最后一帧。

    状态由 observation 的 screen_fingerprint（生产边界计算：图像字节+控件树）
    判定；缺指纹的步各视为独立状态（保守多留，不并入邻步）。
    """
    keyframes: list[int] = []
    prev_fp: object = None
    run_last: int | None = None
    for s in available:
        fp = observations[s].get("screen_fingerprint") or f"missing:{s}"
        if run_last is not None and fp != prev_fp:
            keyframes.append(run_last)
        prev_fp = fp
        run_last = s
    if run_last is not None:
        keyframes.append(run_last)
    return keyframes


def select(events: list[dict], policy: str, max_images: int = 2) -> dict:
    validate(events)
    if type(max_images) is not int or max_images < 1:
        raise ValueError("max_images must be a positive integer")
    if policy not in {"full", "recent", "evidence", "evidence_nofresh", "keyframe"}:
        raise ValueError("unknown policy")
    observations = {e["step"]: e for e in events if e["kind"] == "observation"}
    checks = {}
    for event in events:
        if event["kind"] == "verification":
            checks[event["check_id"]] = event
    available = [s for s, e in observations.items() if e.get("image_ref")]
    selected: set[int] = set()
    if policy == "full":
        selected.update(available)
    elif available:
        # evidence_nofresh = 消融「通道新鲜度」：最新帧不恒留，预算完全按
        # 优先级（未解决检查证据优先、其后近序）分配；其余分支与 evidence 相同。
        if policy != "evidence_nofresh":
            selected.add(available[-1])
        priorities = []
        if policy in ("evidence", "evidence_nofresh"):
            for check in reversed(list(checks.values())):
                if check["state"] != "verified":
                    priorities.extend(check["source_steps"])
        if policy == "keyframe":
            # 严格关键帧：只留状态游程末帧，不用近期帧填满预算——
            # 重复状态不发（发帧数可能少于 max_images，成本差异如实记账）。
            priorities = list(reversed(_keyframes(available, observations)))
        else:
            priorities.extend(reversed(available))
        for step in priorities:
            if len(selected) >= max_images:
                break
            if step in available:
                selected.add(step)
    retained = sorted(selected)
    omitted = sorted(set(available) - selected)
    unresolved = [c for c in checks.values() if c["state"] != "verified"]
    return {
        "policy": policy,
        "budget_unit": "image_references_not_tokens",
        "max_images": None if policy == "full" else max_images,
        "retained_steps": retained,
        "image_refs": [observations[s]["image_ref"] for s in retained],
        "omitted_image_steps": omitted,
        "unresolved_check_ids": [c["check_id"] for c in unresolved],
        "unresolved_sources_without_image": sorted({
            s for c in unresolved for s in c["source_steps"] if s not in selected
        }),
        "limitations": [
            "Reference selection only; not integrated into Android Messages requests.",
            "Text, summaries, image tokenization, retrieval and cache cost are not budgeted.",
            "No task success or fault recovery is inferred from these selections.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("--policy",
                        choices=["full", "recent", "evidence", "evidence_nofresh", "keyframe"],
                        required=True)
    parser.add_argument("--max-images", type=int, default=2)
    args = parser.parse_args()
    events = json.loads(args.events.read_text(encoding="utf-8"))
    print(json.dumps(select(events, args.policy, args.max_images), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
