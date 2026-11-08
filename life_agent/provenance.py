"""证据出处账本：Provenance Gate 的判定核心（agent 侧能力，纯函数）。

无模型/设备/ground truth 依赖；research 实验与产线 agent 共用此模块。

问题：agent 即将提交的值 V，有没有可作出处的证据？
- 有出处（V 出现在任务 / 视觉记忆摘要 / 对话与思考 / 工具反馈）→ 不打扰；
- 无出处（V 是猜的、幻觉的、来自已逐出像素的回忆、或抄了屏幕文案）→
  闸门浮出可回看的候选步。

**渠道规则**：控件树文本（kind="ui"）不算提交值的出处——占位提示、按钮字、
需求文案就住在那里，把字段提示当答案是实测失败通路（批次 5 nudge 失利、
批次 6 i03/i12、PG 预检 pg-preflight-1）。回答必须来自记忆渠道，屏幕文案
是问题本身不是答案。find_substantiation 是通用字面匹配器（不带渠道策略），
渠道过滤在 check_value。

**占位提示规则（字段语义）**：V 属于当前屏输入框的占位提示/标签文案（相等
或片段）时，无论它在哪个渠道被转录（思考、摘要、工具回显都可能「洗白」
屏幕文案）都不构成出处——问题文案不能当答案（pg-preflight-2：转录通路放行
占位提示，matched_kinds=note；pg-preflight-3：片段「会员日」同样被转录放行）。
输入框标签由调用方从当前屏节点收集传入（question_texts），判定核心不依赖
控件树解析。限制：占位提示若内嵌建议词（如「搜索陶瓷马克杯」），该建议词
的合法输入会被误拦一次，由 override 通路放行——research-env 无此类提示。

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

# 提交值的合法出处渠道：任务、视觉记忆摘要、对话/思考（自发抄录）、工具反馈
# （含 ask_human 的人类回答与动作回显）。"ui"（控件树文本）刻意不在其中。
SUBSTANTIATION_KINDS = ("task", "summary", "note", "feedback")


@dataclass(frozen=True)
class TextBlock:
    kind: str            # "task" | "ui" | "summary" | "note" | "feedback"
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
    matches: tuple[TextBlock, ...] = ()      # 文本出处命中块（占位提示触发时=转录命中，供归因）
    review_candidates: tuple[int, ...] = ()  # 建议回看的步号（新→旧/风险→低）
    reasons: tuple[str, ...] = field(default_factory=tuple)  # 候选排序理由（日志用）
    trigger_reason: str = ""                 # 触发原因：""|no_provenance|field_placeholder


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
                steps: list[StepEvidence],
                question_texts: tuple[str, ...] | list[str] = ()) -> ProvenanceFinding:
    """闸门主入口：值 V 在合法渠道（SUBSTANTIATION_KINDS）里有无出处。

    控件树文本不构成提交值的出处（渠道规则见模块 docstring）；无出处则给出
    回看候选。find_substantiation 保持通用匹配语义，渠道过滤只在本入口做。
    question_texts = 当前屏输入框的占位提示/标签：V 属于其文案（相等或片段）=
    问题文案当答案，无视一切转录出处直接触发（trigger_reason="field_placeholder"）。
    """
    v = (value or "").strip()
    if len(v) < MIN_VALUE_CHARS:
        return ProvenanceFinding(value=v, trigger=False,
                                 reasons=("value_too_short",))
    questions = [t.strip().lower() for t in question_texts if t and t.strip()]
    if any(v.lower() in q for q in questions):
        cands = rank_review_candidates(steps)
        # 转录命中仍记入 matches（供归因：哪个渠道「洗白」了屏幕文案）
        recital = find_substantiation(
            v, [b for b in blocks if b.kind in SUBSTANTIATION_KINDS])
        return ProvenanceFinding(
            value=v, trigger=True,
            matches=tuple(recital),
            review_candidates=tuple(s for s, _ in cands),
            reasons=tuple(r for _, r in cands),
            trigger_reason="field_placeholder",
        )
    matches = find_substantiation(
        v, [b for b in blocks if b.kind in SUBSTANTIATION_KINDS])
    if matches:
        return ProvenanceFinding(value=v, trigger=False,
                                 matches=tuple(matches),
                                 reasons=("substantiated_in_text",))
    cands = rank_review_candidates(steps)
    return ProvenanceFinding(
        value=v, trigger=True,
        review_candidates=tuple(s for s, _ in cands),
        reasons=tuple(r for _, r in cands),
        trigger_reason="no_provenance",
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
    占位提示触发（field_placeholder）用更贴因的文案：问题文案不能当答案。
    """
    if finding.trigger_reason == "field_placeholder":
        head = (f"[证据闸门] 待提交的「{finding.value}」属于输入框占位提示/标签"
                f"文案（问题文案），不是答案。请用 review_screenshot 回看候选步"
                f"核实真正的值后重新提交；确信无误可再次提交同一值以确认。")
    else:
        head = (f"[证据闸门] 待提交的「{finding.value}」缺少证据出处：任务、视觉记忆"
                f"摘要与你的记录里都没有它（屏幕上的提示语/按钮文案不算出处，不能当作"
                f"答案）。请用 review_screenshot 回看候选步核实真正的值后重新提交；"
                f"确信无误可再次提交同一值以确认。")
    lines = [head]
    for step, reason in zip(finding.review_candidates[:max_steps],
                            finding.reasons[:max_steps]):
        lines.append(f"- 第 {step} 步：{REASON_ZH.get(reason, reason)}")
    omitted = len(finding.review_candidates) - max_steps
    if omitted > 0:
        lines.append(f"（另有 {omitted} 步候选未列出）")
    return "\n".join(lines)
