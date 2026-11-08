"""长任务族（T1–T3）episode 规格生成：价值域实验的任务轴。

与 pilot_runner 的短 episode 矩阵互补：这边把历史推到 12→96 步并拉开「线索出现」
与「收银台要用」的距离，让 full 历史撞预算墙，检验压缩臂在该域是否保恢复。

- T1_rounds 多轮下单：K 轮「加购→结算」，瞬时线索在早期轮首屏，码闸门从第
  gate_after+1 轮起生效（收银台在后续轮次）——证据必须跨轮存活。
- T2_tour 全目录巡览：catalog_size=8，浏览全部详情后按参数条件下单——
  线索在复位首屏、闸门在唯一收银台，距离=整段巡览。
- T3_cart 长清单：全部加入后按约束（如移除最高价 k 件）结算。

规格决定论生成（family × params × seeds）；但 expected_ids 的条件解析必须在真实
复位后做——商品目录由 Kotlin RNG 生成，Python 不复刻（同 pilot 实例的「真实复位
枚举」约定）。resolve_expected / finalize_reset 在拿到 state0.items 后调用。

界面与任务文本不含故障标签、预期答案或路径提示；任务只给业务目标。
"""
from __future__ import annotations

FAMILIES = ("T1_rounds", "T2_tour", "T3_cart")


def _validate(spec: dict) -> dict:
    fam = spec["family"]
    p = spec["params"]
    if fam == "T1_rounds":
        rounds = int(p.get("rounds", 0))
        if rounds < 1:
            raise ValueError(f"T1 rounds 须 ≥1: {spec['spec_id']}")
        gate_after = int(p.get("gate_after", 0))
        if gate_after >= rounds:
            raise ValueError(f"T1 gate_after 须 < rounds（至少一轮受闸）: {spec['spec_id']}")
        clue_round = int(p.get("clue_round", 1))
        if not (1 <= clue_round <= rounds):
            raise ValueError(f"T1 clue_round 须在 1..rounds: {spec['spec_id']}")
        if not p.get("target_ids"):
            raise ValueError(f"T1 target_ids 不能为空: {spec['spec_id']}")
    elif fam == "T2_tour":
        cond = p.get("condition") or {}
        if not ({"price_lt_cents", "spec_contains"} & set(cond)):
            raise ValueError(f"T2 condition 须含 price_lt_cents 或 spec_contains: {spec['spec_id']}")
    elif fam == "T3_cart":
        k = int((p.get("drop") or {}).get("k", 0))
        if k < 1:
            raise ValueError(f"T3 drop.k 须 ≥1: {spec['spec_id']}")
    else:
        raise ValueError(f"未知任务族 {fam!r}；可用: {FAMILIES}")
    return spec


def _reset_for(spec: dict, fault: str) -> dict:
    fam = spec["family"]
    p = spec["params"]
    if fam == "T1_rounds":
        return {
            "fault": fault,
            "catalog_size": 4,
            "promo_rearm": False,
            "promo_clue_round": int(p.get("clue_round", 1)),
            "promo_gate_after": int(p.get("gate_after", 0)),
            "delay_ms": None, "noop_n": None, "popup_at": None, "confusion_at": None,
        }
    # T2/T3：catalog 全量 8，线索复位首屏、闸门在唯一收银台（既有语义，距离=任务全长）
    return {
        "fault": fault,
        "catalog_size": 8,
        "promo_rearm": False, "promo_clue_round": 1, "promo_gate_after": 0,
        "delay_ms": None, "noop_n": None, "popup_at": None, "confusion_at": None,
    }


def make_spec(family: str, params: dict, task_seed: int, *, fault: str = "promo") -> dict:
    """构造单条规格（决定论；同参数同规格逐字段一致）。"""
    fault_seed = task_seed
    if family == "T1_rounds":
        tag = f"t1r{params['rounds']}s{task_seed}g{params.get('gate_after', 0)}"
        expected_spec = {"kind": "fixed", "ids": list(params["target_ids"])}
    elif family == "T2_tour":
        tag = f"t2s{task_seed}"
        expected_spec = {"kind": "condition", **params["condition"]}
    elif family == "T3_cart":
        tag = f"t3k{params['drop']['k']}s{task_seed}"
        expected_spec = {"kind": "drop_top_price", "k": int(params["drop"]["k"])}
    else:
        raise ValueError(f"未知任务族 {family!r}；可用: {FAMILIES}")
    spec = {
        "spec_id": f"{tag}-{fault}",
        "family": family,
        "params": dict(params),
        "task_seed": task_seed,
        "fault_seed": fault_seed,
        "expected_spec": expected_spec,
        "reset": _reset_for({"family": family, "params": params, "spec_id": tag}, fault),
    }
    return _validate(spec)


def build_manifest(families: tuple | None = None, seeds: tuple = (1, 2, 3),
                   *, fault: str = "promo") -> list[dict]:
    """默认网格：T1 × K∈{2,4,8} × 种子 + T2/T3 × 种子（各 3 种子 = 15 规格/故障）。"""
    fams = tuple(families) if families else FAMILIES
    eps: list[dict] = []
    for seed in seeds:
        if "T1_rounds" in fams:
            for k in (2, 4, 8):
                eps.append(make_spec("T1_rounds", {
                    "rounds": k, "target_ids": ["p1"],
                    "clue_round": 1, "gate_after": 1,
                    "promo_rearm": False,
                }, seed, fault=fault))
        if "T2_tour" in fams:
            eps.append(make_spec("T2_tour", {
                "condition": {"price_lt_cents": 5000},
            }, seed, fault=fault))
        if "T3_cart" in fams:
            eps.append(make_spec("T3_cart", {
                "drop": {"by": "top_price", "k": 2},
            }, seed, fault=fault))
    return eps


def resolve_expected(items: list[dict], spec: dict) -> list[str]:
    """运行时从真实复位目录解析 expected_ids（Python 不复刻 Kotlin 目录生成）。

    - fixed：直接取（校验存在于目录）；
    - condition：price_lt_cents / spec_contains 逐件判定；
    - drop_top_price：全部减去价格最高 k 件（价格并列按 id 升序取先）。
    解析为空视为规格/目录不匹配，显式报错（不静默当无目标）。
    """
    by_id = {it["id"]: it for it in items}
    es = spec["expected_spec"]
    kind = es["kind"]
    if kind == "fixed":
        out = [i for i in es["ids"] if i in by_id]
        if len(out) != len(es["ids"]):
            raise ValueError(f"固定目标不在目录中: {es['ids']} vs {sorted(by_id)}")
    elif kind == "condition":
        if "price_lt_cents" in es:
            out = [it["id"] for it in items if it["price_cents"] < int(es["price_lt_cents"])]
        else:
            needle = es["spec_contains"]
            out = [it["id"] for it in items if needle in (it.get("spec") or "")]
    elif kind == "drop_top_price":
        k = int(es["k"])
        if k >= len(items):
            raise ValueError(f"drop.k={k} 须 < 目录件数 {len(items)}")
        dropped = {it["id"] for it in
                   sorted(items, key=lambda it: (-it["price_cents"], it["id"]))[:k]}
        out = [it["id"] for it in items if it["id"] not in dropped]
    else:
        raise ValueError(f"未知 expected_spec kind {kind!r}")
    if not out:
        raise ValueError(f"条件解析出空目标（规格与目录不匹配）: {spec['spec_id']}")
    return out


def finalize_reset(spec: dict, items: list[dict]) -> dict:
    """真实复位枚举后注入 expected_ids，得到可直接 broadcast_reset 的 kwargs。"""
    reset = dict(spec["reset"])
    reset["expected_ids"] = resolve_expected(items, spec)
    return reset


def build_task(items: list[dict], spec: dict) -> str:
    """任务文本：只给业务目标，不含故障标签、路径提示或隐藏参数（码不提）。"""
    by_id = {it["id"]: it["name"] for it in items}
    fam = spec["family"]
    p = spec["params"]
    if fam == "T1_rounds":
        targets = "、".join(f"「{by_id[i]}」" for i in p["target_ids"] if i in by_id)
        return f"在研测商城连续完成 {p['rounds']} 轮下单：每轮把 {targets} 加入清单并完成下单。"
    if fam == "T2_tour":
        cond = p["condition"]
        if "price_lt_cents" in cond:
            wording = f"价格低于 {int(cond['price_lt_cents']) / 100:g} 元"
        else:
            wording = f"规格含「{cond['spec_contains']}」"
        return f"在研测商城浏览每一件商品的详情，然后购买其中{wording}的商品，完成下单。"
    k = int(p["drop"]["k"])
    return (f"在研测商城把全部商品加入清单，然后移除其中价格最高的 {k} 件，"
            f"对清单里剩下的商品完成下单。")
