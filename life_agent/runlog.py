"""运行级 JSONL 记录（研究用）：因果事件流 + 模型调用原始记录 + 请求协议校验。

run.jsonl 每行一个 JSON 记录：
- run_header：模型、端点、上下文策略、冻结经验 SHA-256、任务文本摘要等固定配置；
- call：调用序号、起止时间/延迟、stop_reason、usage 原始字段、本次请求实际保留的图像步；
- summary_call：摘要基线的额外蒸馏调用（独立记录，不占 call 序号，保持 step↔seq 映射）；
- action：工具调用与执行结果、期望效果、敏感闸门判定；
- observation / verification：因果事件（严格字段 + 附加字段）。
  仅含 kind/step/image_ref/check_id/source_steps/state 的投影可直接喂给
  research.context_policy.select()；call/action 等记录不属于该接口，调用前须过滤。
- protocol_check：请求构造边界校验（tool_use/tool_result 配对、thinking 原文与用户文本保真）。

本模块只记录与校验，不做决策；不读取控制器 ground truth，不修改证据文件。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

CAUSAL_KINDS = ("observation", "verification")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bfield(block, key: str):
    """兼容 dict 内容块与 SDK pydantic 内容块的字段读取。"""
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _block_identity(block) -> dict:
    """内容块的保真指纹：文本/thinking 记哈希，图像槽与图像只记类型（允许策略替换）。"""
    btype = _bfield(block, "type")
    if btype == "text":
        return {"kind": "text", "sha256": _sha256_text(str(_bfield(block, "text") or ""))}
    if btype == "thinking":
        return {"kind": "thinking", "sha256": _sha256_text(str(_bfield(block, "thinking") or ""))}
    if btype in ("image", "_image_slot"):
        return {"kind": "image"}
    return {"kind": str(btype)}


def content_identity(content) -> list[dict]:
    if isinstance(content, str):
        return [{"kind": "text", "sha256": _sha256_text(content)}]
    return [_block_identity(b) for b in content]


def _text_identity(content) -> list[dict]:
    """文本/thinking 指纹序列；图像按策略可缺席，不参与比较。"""
    return [x for x in content_identity(content) if x.get("kind") != "image"]


def validate_request(messages: list, contract: list) -> list[str]:
    """校验将要发送的请求：返回问题列表（空 = 通过）。

    contract 是内容产生时记录的契约（见 RunLogger.note_message）。
    图像按策略丢弃是唯一允许的差异；文本、thinking、tool 配对必须保持不变。
    """
    problems = []
    if len(messages) != len(contract):
        problems.append(f"message count {len(messages)} != contract {len(contract)}")
        return problems

    seen_tool_use: dict[str, int] = {}
    seen_tool_result: dict[str, int] = {}
    for i, (msg, spec) in enumerate(zip(messages, contract)):
        role = msg.get("role")
        if role != spec.get("role"):
            problems.append(f"msg[{i}] role {role} != contract {spec.get('role')}")
        content = msg.get("content")
        blocks = [content] if isinstance(content, str) else content
        if spec.get("kind") == "tool_results":
            result_ids = [_bfield(b, "tool_use_id") for b in blocks
                          if _bfield(b, "type") == "tool_result"]
            if result_ids != spec.get("tool_result_ids"):
                problems.append(f"msg[{i}] tool_result ids mismatch: {result_ids} != {spec.get('tool_result_ids')}")
            inner_texts = []
            for b in blocks:
                if _bfield(b, "type") == "tool_result":
                    inner_texts.extend(_text_identity(_bfield(b, "content") or []))
            spec_texts = [x for x in spec.get("content_identity", []) if x.get("kind") != "image"]
            if inner_texts != spec_texts:
                problems.append(f"msg[{i}] tool_result content changed (texts/thinking must be verbatim)")
            for rid in result_ids:
                seen_tool_result[str(rid)] = i
        else:
            if _text_identity(content) != [x for x in spec.get("content_identity", []) if x.get("kind") != "image"]:
                problems.append(f"msg[{i}] text/thinking content changed")
            for b in (blocks if isinstance(blocks, list) else []):
                if _bfield(b, "type") == "tool_use":
                    seen_tool_use[str(_bfield(b, "id"))] = i

    for tid, mi in seen_tool_use.items():
        if tid not in seen_tool_result:
            problems.append(f"tool_use {tid} (msg[{mi}]) has no tool_result")
    for tid, mi in seen_tool_result.items():
        if tid not in seen_tool_use:
            problems.append(f"tool_result {tid} (msg[{mi}]) has no tool_use")
    return problems


class RunLogger:
    """run.jsonl 写入器。写入即落盘（append），保留原始证据，不覆盖历史。"""

    def __init__(self, run_dir: Path, **header):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "run.jsonl"
        self.events: list[dict] = []  # 因果事件（含严格字段），供 context_policy 投影
        self.contract: list[dict] = []  # 各消息的保真契约
        self._call_seq = 0
        self._write({"kind": "run_header", "ts": time.time(), **header})

    # ---- 基础写入 ----

    def _write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def note_message(self, role: str, content, kind: str = "message",
                     tool_result_ids: list | None = None) -> None:
        """记录一条已构造消息的保真契约（文本/thinking 哈希、tool_result 配对）。"""
        if kind == "tool_results":
            inner: list = []
            for b in content or []:
                inner.extend(_bfield(b, "content") or [])
            identity = content_identity(inner)
        else:
            identity = content_identity(content)
        self.contract.append({
            "role": role,
            "kind": kind,
            "tool_result_ids": tool_result_ids or [],
            "content_identity": identity,
        })

    # ---- 记录接口 ----

    def log_call(self, ts_start: float, ts_end: float, stop_reason: str,
                 usage: dict, retained_image_steps: list) -> int:
        self._call_seq += 1
        self._write({
            "type": "call", "seq": self._call_seq, "ts_start": ts_start, "ts_end": ts_end,
            "latency_ms": round((ts_end - ts_start) * 1000), "stop_reason": stop_reason,
            "usage": usage, "retained_image_steps": retained_image_steps,
        })
        return self._call_seq

    def log_action(self, step: int, tool_use_id: str, name: str, args: dict,
                   result: str, is_error: bool, expectation: str, gate: dict | None = None):
        self._write({
            "type": "action", "step": step, "tool_use_id": tool_use_id, "name": name,
            "args": args, "result": result, "is_error": is_error,
            "expectation": expectation, "gate": gate or {},
        })

    def log_protocol_check(self, call_seq: int, problems: list) -> None:
        self._write({"type": "protocol_check", "call_seq": call_seq,
                     "ok": not problems, "problems": problems})

    def log_observation(self, step: int, image_ref: str | None, ui_ref: str | None,
                        screen: str, image_ok: bool, ui_ok: bool,
                        image_attempts: int, ui_attempts: int,
                        image_error: str | None, ui_error: str | None,
                        screen_fingerprint: str | None = None):
        event = {
            "kind": "observation", "step": step, "image_ref": image_ref,
            "screen": screen, "image_ok": image_ok, "ui_ok": ui_ok,
            "image_attempts": image_attempts, "ui_attempts": ui_attempts,
            "image_error": image_error, "ui_error": ui_error, "ui_ref": ui_ref,
            "screen_fingerprint": screen_fingerprint,
        }
        self.events.append(event)
        self._write(event)

    def log_summary_call(self, ts_start: float, ts_end: float, usage: dict,
                         source_steps: list, summary_chars: int,
                         error: str | None = None, summary_text: str | None = None,
                         stop_reason: str | None = None) -> None:
        """摘要基线的额外蒸馏调用：独立记录，不占 call 序号（step↔seq 映射不受影响）。

        summary_text 留存蒸馏原文（≤200 字视觉事实，来自截图的世界数据），
        供后验归因「摘要是否抄到关键细节」；不做改写。stop_reason 留存截断
        等终止原因（空文本时判读是否被 max_tokens 截断）。
        """
        self._write({
            "type": "summary_call", "ts_start": ts_start, "ts_end": ts_end,
            "latency_ms": round((ts_end - ts_start) * 1000), "usage": usage,
            "source_steps": source_steps, "summary_chars": summary_chars,
            "summary_text": summary_text, "error": error, "stop_reason": stop_reason,
        })

    def log_verification(self, step: int, check_id: str, source_steps: list,
                         state: str, basis: str):
        if state not in ("pending", "failed", "verified"):
            raise ValueError(f"invalid verification state: {state}")
        event = {
            "kind": "verification", "step": step, "check_id": check_id,
            "source_steps": source_steps, "state": state, "basis": basis,
        }
        self.events.append(event)
        self._write(event)

    def log_gate(self, step: int, value: str, trigger: bool, action: str,
                 candidates: list | None = None, reasons: list | None = None,
                 trigger_reason: str = "", matched_kinds: list | None = None) -> None:
        """证据闸门（Provenance Gate）判定记录：value 为 agent 自己的提交值。

        action: "blocked"（首次无出处，拦下取证）| "override"（重提放行）|
        "pass"（有出处/过短，不触发）。trigger_reason: ""|no_provenance|
        field_placeholder（占位提示当答案）。matched_kinds: 出处命中块的渠道
        （占位提示触发时=「洗白」屏幕文案的转录渠道，供归因）。不进 causal_events。
        """
        if action not in ("blocked", "override", "pass"):
            raise ValueError(f"invalid gate action: {action}")
        self._write({
            "type": "gate", "step": step, "value": value, "trigger": trigger,
            "action": action, "candidates": candidates or [],
            "reasons": reasons or [], "trigger_reason": trigger_reason,
            "matched_kinds": matched_kinds or [],
        })

    # ---- 供 context_policy 使用的投影 ----

    def causal_events(self) -> list[dict]:
        """投影出 research/README 事件接口可接受的因果事件（保留附加字段）。"""
        return [dict(e) for e in self.events]
