#!/usr/bin/env python3
"""episode 矩阵统计：配对符号检验 + 聚类 bootstrap 置信区间（docs §3.3 预声明口径）。

只读 episode.json 的业务判分（verdict.recovered/success），不重判、不剔除：
invalid_reset / 无记录目录单列统计桶（环境故障留档），完成 episode 全部计入分母。

- 配对：同配对键（pilot = instance+condition；longtask = spec_id）两臂对比
  → 胜/负/平 + 精确符号检验（平局剔除后二项检验，单侧）。
- 聚类 bootstrap：任务族为聚类单位（pilot F1–F4 / longtask T1–T3；--cluster instance
  可换实例级），重采样聚类给恢复率与配对差的百分位 95% CI（种子固定可复现）。

用法：
    python research/stats_cluster.py --arm recent1retrpg --vs recent1retrnudgeb
    python research/stats_cluster.py --arm recent1retrpg --vs recent1retrnudgeb \
        --cluster instance --boot 10000
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.pilot_runner import INSTANCES, POLICY_VARIANTS  # noqa: E402

_POLICIES_BY_LEN = sorted(POLICY_VARIANTS, key=len, reverse=True)
_FAMILY_BY_IID = {iid: fam for iid, _s, fam, _e, _i in INSTANCES}
_LONGTASK_FAMILY = {"t1": "T1", "t2": "T2", "t3": "T3"}


def _split_episode_id(ep_id: str) -> tuple[str, str] | None:
    """episode_id → (配对键, 任务族)；不认识返回 None。"""
    for pol in _POLICIES_BY_LEN:
        suffix = f"-{pol}"
        if ep_id.endswith(suffix):
            head = ep_id[: -len(suffix)]
            break
    else:
        return None
    m = re.match(r"^(i\d{2})-(c\d)$", head)
    if m:
        iid, cid = m.groups()
        return f"{iid}-{cid}", _FAMILY_BY_IID.get(iid, "unknown")
    m = re.match(r"^(t\d[a-z0-9]*-\w+)$", head)  # longtask: t1r2s1g1-promo
    if m:
        spec = m.group(1)
        return spec, _LONGTASK_FAMILY.get(spec[:2], "unknown")
    return None


def load_arm(episodes_dir: Path, arm: str, id_prefix: str = "") -> tuple[dict[str, dict], list[dict]]:
    """配对键 → episode 摘要（只收带业务判分 verdict 的记录）；同键取字典序最后的目录。

    无 verdict 的记录（dry-run、invalid_reset、未跑完）进 skipped 桶单列留档，
    不进恢复率分母也不静默丢弃。id_prefix 过滤 episode 配对键（t=长任务、i=pilot），
    防不同实验的同名臂混入同一配对表。
    """
    out: dict[str, dict] = {}
    skipped: list[dict] = []
    for d in sorted(episodes_dir.glob(f"*-{arm}")):
        ep_json = d / "episode.json"
        if not ep_json.exists():
            continue
        rec = json.loads(ep_json.read_text(encoding="utf-8"))
        parsed = _split_episode_id(d.name)
        if parsed is None:
            continue
        key, family = parsed
        if id_prefix and not key.startswith(id_prefix):
            continue
        if "verdict" not in rec:
            skipped.append({"dir": d.name, "status": rec.get("status")})
            continue
        v = rec["verdict"]
        out[key] = {
            "dir": d.name, "family": family,
            "status": rec.get("status"),
            "recovered": bool(v.get("recovered")),
            "success": bool(v.get("success")),
            "triggered": int(v.get("triggered_count") or 0),
            "usage": rec.get("usage") or {},
        }
    return out, skipped


def sign_test(wins: int, losses: int) -> float:
    """精确符号检验单侧 p（平局剔除；H0：胜负各半）。"""
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def _rate(records: list[dict]) -> float:
    return sum(r["recovered"] for r in records) / len(records) if records else float("nan")


def cluster_bootstrap(a: dict[str, dict], b: dict[str, dict], *,
                      cluster: str, boot: int, seed: int) -> dict:
    keys = sorted(set(a) & set(b))
    if cluster == "instance":
        cl_of = {k: k for k in keys}
    else:
        cl_of = {k: a[k]["family"] for k in keys}
    clusters = sorted(set(cl_of.values()))
    by_cluster = {c: [k for k in keys if cl_of[k] == c] for c in clusters}

    rng = random.Random(seed)
    deltas, rates_a, rates_b = [], [], []
    for _ in range(boot):
        picked = [rng.choice(clusters) for _ in clusters]
        ks = [k for c in picked for k in by_cluster[c]]
        ra = _rate([a[k] for k in ks])
        rb = _rate([b[k] for k in ks])
        rates_a.append(ra)
        rates_b.append(rb)
        deltas.append(ra - rb)

    def pct(xs: list[float], q: float) -> float:
        xs = sorted(x for x in xs if not math.isnan(x))
        return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")

    return {
        "clusters": clusters,
        "rate_a_ci95": [pct(rates_a, 0.025), pct(rates_a, 0.975)],
        "rate_b_ci95": [pct(rates_b, 0.025), pct(rates_b, 0.975)],
        "delta_ci95": [pct(deltas, 0.025), pct(deltas, 0.975)],
        "boot": boot, "seed": seed, "cluster_unit": cluster,
    }


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episodes", type=Path, default=ROOT / "runs" / "episodes")
    ap.add_argument("--arm", required=True, help="实验臂 episode 后缀（如 recent1retrpg）")
    ap.add_argument("--vs", required=True, help="对照臂 episode 后缀")
    ap.add_argument("--cluster", choices=("family", "instance"), default="family")
    ap.add_argument("--id-prefix", default="",
                    help="episode 配对键前缀过滤（t=长任务、i=pilot），防跨实验同名臂混入")
    ap.add_argument("--boot", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=20261107)
    ap.add_argument("--out", type=Path, default=None,
                    help="结果 JSON 路径（默认 research/results/stats-<arm>-vs-<vs>.json）")
    args = ap.parse_args(argv)

    a, skipped_a = load_arm(args.episodes, args.arm, args.id_prefix)
    b, skipped_b = load_arm(args.episodes, args.vs, args.id_prefix)
    keys = sorted(set(a) & set(b))
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))

    wins = sum(1 for k in keys if a[k]["recovered"] and not b[k]["recovered"])
    losses = sum(1 for k in keys if not a[k]["recovered"] and b[k]["recovered"])
    ties = len(keys) - wins - losses

    noncompleted = [dict(r, side=args.arm) for r in skipped_a] + \
                   [dict(r, side=args.vs) for r in skipped_b]

    result = {
        "arm": args.arm, "vs": args.vs,
        "pairs": len(keys),
        "only_in_arm": only_a, "only_in_vs": only_b,
        "paired": {"wins": wins, "losses": losses, "ties": ties,
                   "sign_test_one_sided_p": sign_test(wins, losses)},
        "arms": {
            args.arm: {
                "n": len(a), "recovered": sum(r["recovered"] for r in a.values()),
                "success": sum(r["success"] for r in a.values()),
                "fault_triggered": sum(r["triggered"] > 0 for r in a.values()),
                "usage_total": {
                    "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in a.values()),
                    "output_tokens": sum(r["usage"].get("output_tokens", 0) for r in a.values()),
                    "cache_read": sum(r["usage"].get("cache_read", 0) for r in a.values()),
                },
            },
            args.vs: {
                "n": len(b), "recovered": sum(r["recovered"] for r in b.values()),
                "success": sum(r["success"] for r in b.values()),
                "fault_triggered": sum(r["triggered"] > 0 for r in b.values()),
                "usage_total": {
                    "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in b.values()),
                    "output_tokens": sum(r["usage"].get("output_tokens", 0) for r in b.values()),
                    "cache_read": sum(r["usage"].get("cache_read", 0) for r in b.values()),
                },
            },
        },
        "unjudged_records": noncompleted,
        "bootstrap": cluster_bootstrap(a, b, cluster=args.cluster,
                                       boot=args.boot, seed=args.seed),
    }

    out = args.out or (ROOT / "research" / "results" /
                       f"stats-{args.arm}-vs-{args.vs}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({k: v for k, v in result.items()
                      if k != "unjudged_records"}, ensure_ascii=False, indent=2))
    print(f"[无判分留档] {len(noncompleted)} 条（dry-run/invalid_reset/未跑完，不入分母不丢弃；见 {out}）")
    print(f"[written] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
