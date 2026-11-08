"""证据出处账本：Provenance Gate 的判定核心（agent 侧能力，纯函数）。

无模型/设备/ground truth 依赖；research 实验与产线 agent 共用此模块。

问题：agent 即将提交的值 V，在本次请求的证据文本里有没有出处？
- 有出处（V 出现在某步控件树文本 / 摘要文本 / 应用提示文本）→ 不打扰；
- 无出处（V 是猜的、幻觉的、或来自已逐出像素的回忆）→ 闸门浮出可回看的候选步。

判定只用 agent 可见历史的派生账本（文本块 + 帧状态），不读评估侧字段：
promo 码/expected/故障词等 ground truth 一律不进本模块。

候选步排序规则（无文本出处时）：
1. 已蒸馏但摘要文本不含 V 的逐出帧（V 很可能被蒸馏丢掉）→ 新在前；
2. 未蒸馏的逐出帧（内容未知）→ 新在前；
3. 保留在请求里的帧不列入（像素已在上下文，无需回看）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

MIN_VALUE_CHARS = 2  # 单字符值多为噪声（按钮字、序号），不触发


@dataclass(frozen=True)
class TextBlock:
    kind: str            # "ui" | "summary" | "note" | "feedback"
    text: str
    step: int | None = None


@dataclass(frozen=True)
class StepEvidence:
    step: int
    has_image: bool
    retained: bool               # 本请求是否保留该帧像素
    distilled: bool = False      # 是否已蒸馏进文本记忆
    summary_text: str | None = None


@dataclass(frozen=True)
class ProvenanceFinding:
    value: str
    trigger: bool                            # 闸门是否应触发（值有效且无文本出处）
    matches: tuple[TextBlock, ...] = ()      # 文本出处命中块
    review_candidates: tuple[int, ...] = ()  # 建议回看的步号（新→旧/风险→低）
    reasons: tuple[str, ...] = field(default_factory=tuple)  # 候选排序理由（日志用）


def find_substantiation(value: str, blocks: list[TextBlock]) -> list[TextBlock]:
    """返回 V 的文本出处命中块；空/过短值不判定（不触发）。

    匹配 = 连续子串，ASCII 大小写不敏感（优惠码类值会大小写漂移）；
    中文按原样子串。不猜测同义、不归一化数字格式——出处必须是字面证据。
    """
    v = (value or "").strip()
    if len(v) < MIN_VALUE_CHARS:
        return []
    vl = v.lower()
    out = []
    for b in blocks:
        if not b.text:
            continue
        if vl in b.text.lower():
            out.append(b)
    return out


def rank_review_candidates(steps: list[StepEvidence]) -> list[tuple[int, str]]:
    """无文本出处时给出建议回看步：[(step, reason), ...]。

    只列有图像且未保留在本请求的帧；已蒸馏但缺值的排前（蒸馏丢失风险）。
    """
    dropped = [s for s in steps if s.has_image and not s.retained]
    lost_risk = sorted((s for s in dropped if s.distilled),
                       key=lambda s: s.step, reverse=True)
    unknown = sorted((s for s in dropped if not s.distilled),
                     key=lambda s: s.step, reverse=True)
    out: list[tuple[int, str]] = []
    out += [(s.step, "distilled_without_value") for s in lost_risk]
    out += [(s.step, "evicted_undistilled") for s in unknown]
    return out


def check_value(value: str, blocks: list[TextBlock],
                steps: list[StepEvidence]) -> ProvenanceFinding:
    """闸门主入口：值 V 是否有文本出处；无则给出回看候选。"""
    v = (value or "").strip()
    if len(v) < MIN_VALUE_CHARS:
        return ProvenanceFinding(value=v, trigger=False,
                                 reasons=("value_too_short",))
    matches = find_substantiation(v, blocks)
    if matches:
        return ProvenanceFinding(value=v, trigger=False,
                                 matches=tuple(matches),
                                 reasons=("substantiated_in_text",))
    cands = rank_review_candidates(steps)
    return ProvenanceFinding(
        value=v, trigger=True,
        review_candidates=tuple(s for s, _ in cands),
        reasons=tuple(r for _, r in cands),
    )


REASON_ZH = {
    "distilled_without_value": "已蒸馏但摘要缺值（蒸馏可能丢失）",
    "evicted_undistilled": "已逐出未蒸馏（内容未知）",
}


def format_gate_response(finding: ProvenanceFinding, max_steps: int = 5) -> str:
    """闸门触发时返回给 agent 的文本：浮出可回看候选步，不注入答案。

    与 confirm_and_tap 对偶（权限闸门 vs 证据闸门）：首次无出处提交被拦下并
    指路取证；agent 回看后重新提交同一值即视为显式确认（钩子侧记录 override），
    闸门本身永远不读 ground truth、不判定对错。候选步只列前 max_steps 条控长度。
    """
    lines = [f"[证据闸门] 待提交的「{finding.value}」在本次请求的证据文本中没有出处"
             f"（控件树、摘要与对话文本均未出现）。请用 review_screenshot 回看候选步"
             f"核实后重新提交；确信无误可再次提交同一值以确认。"]
    for step, reason in zip(finding.review_candidates[:max_steps],
                            finding.reasons[:max_steps]):
        lines.append(f"- 第 {step} 步：{REASON_ZH.get(reason, reason)}")
    omitted = len(finding.review_candidates) - max_steps
    if omitted > 0:
        lines.append(f"（另有 {omitted} 步候选未列出）")
    return "\n".join(lines)
