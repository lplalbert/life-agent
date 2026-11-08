#!/usr/bin/env python3
"""批次 3 preflight：以真实端点验证摘要蒸馏调用的最小请求形状。

直接调用生产代码路径 life_agent.agent.Agent._distill_evicted（非手工复刻）：
- client.beta.messages.stream(model, max_tokens=SUMMARY_MAX_TOKENS, system=纯字符串,
  messages=单 user)——无 thinking、无 tools、无 betas/fallbacks，这是与主调用形状的
  全部差异点，也是唯一未在真实端点验证过的形状（主调用形状已在 180-pilot 验证）。
装配镜像 run() 主循环（_perceive 返回图像块由调用方存 _image_blocks、
_log_observation 落因果事件）；帧取真实 episode i01-c5-recent1 的 step00–03
（recent1sum3 首次触发的精确形状：3 个候选帧含 promo 横幅）。顺带单样本观察
蒸馏能否读出像素细节（仅记录，不替代臂的系统回答；ground truth 仅后验比对）。

首跑发现 MiMo 默认先输出 thinking，300 上限被 thinking 吃光、text 截断为零
→ SUMMARY_MAX_TOKENS 提到 800 并加空文本重试语义；本脚本兼作该修复的复验。

需要与 episode_runner 相同的环境（ANTHROPIC_BASE_URL 与认证变量）：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe research/preflight_summary_shape.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from life_agent.agent import Agent, MODEL_DEFAULT  # noqa: E402
from life_agent.runlog import RunLogger  # noqa: E402

EP_DIR = ROOT / "runs" / "episodes" / "i01-c5-recent1"
N_FRAMES = 4  # 感知 step 0..3 → recent1 下候选 [0,1,2]（首次触发形状）


class StubPhone:
    """按步喂真实帧序列；preflight 不连设备。"""

    def __init__(self, pngs: list[bytes]):
        self._pngs = pngs
        self._i = 0

    def screenshot_png(self) -> bytes:
        png = self._pngs[min(self._i, len(self._pngs) - 1)]
        self._i += 1
        return png

    def dump_ui(self) -> list:
        return []

    def current_activity(self) -> str:
        return "com.lifeagent.researchenv/.MainActivity"


class StreamSpy:
    """包一层捕获最终响应的块结构（不改生产代码）。

    anthropic 的 stream() 先返回 manager，__enter__ 后才产出真正流；
    get_final_message 在进入后的流上。
    """

    def __init__(self, manager, sink: list):
        self._manager = manager
        self._sink = sink
        self._stream = None

    def __enter__(self):
        self._stream = self._manager.__enter__()
        return self

    def __exit__(self, *a):
        return self._manager.__exit__(*a)

    def get_final_message(self):
        resp = self._stream.get_final_message()
        blocks = []
        for b in resp.content:
            t = getattr(b, "type", "?")
            if t == "text":
                blocks.append({"type": t, "chars": len(b.text or ""),
                               "head": (b.text or "")[:200]})
            elif t == "thinking":
                th = getattr(b, "thinking", "") or ""
                blocks.append({"type": t, "chars": len(th), "head": th[:200]})
            else:
                blocks.append({"type": t})
        usage = resp.usage
        self._sink.append({
            "stop_reason": resp.stop_reason,
            "blocks": blocks,
            "usage": {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_read": getattr(usage, "cache_read_input_tokens", None),
            },
        })
        return resp


def main() -> int:
    pngs = []
    for s in range(N_FRAMES):
        p = EP_DIR / f"step{s:02d}.png"
        if not p.exists():
            print(f"缺少真实帧 {p}，无法 preflight")
            return 1
        pngs.append(p.read_bytes())
    code = ""
    gt = EP_DIR / "ground_truth_state.json"
    if gt.exists():
        code = str(json.loads(gt.read_text(encoding="utf-8")).get("promo_code", ""))

    import anthropic

    model = os.environ.get("ANTHROPIC_MODEL") or MODEL_DEFAULT
    client = anthropic.Anthropic()

    # 记录真实发出的请求形状（kwargs 快照，图像数据不打印）
    sent: list[dict] = []
    captured: list[dict] = []
    real_stream = client.beta.messages.stream

    def spy(**kwargs):
        sent.append({
            "model": kwargs.get("model"), "max_tokens": kwargs.get("max_tokens"),
            "system_type": type(kwargs.get("system")).__name__,
            "roles": [m.get("role") for m in kwargs.get("messages", [])],
            "block_types": [
                b.get("type") if isinstance(b, dict) else getattr(b, "type", "?")
                for m in kwargs.get("messages", [])
                for b in (m.get("content") if isinstance(m.get("content"), list) else [])
            ],
            "has_tools": "tools" in kwargs,
            "has_thinking": "thinking" in kwargs,
            "has_betas": "betas" in kwargs,
        })
        return StreamSpy(real_stream(**kwargs), captured)

    client.beta.messages.stream = spy

    tmp = Path(tempfile.mkdtemp(prefix="preflight-summary-"))
    run_log = RunLogger(tmp, model=model, context_policy="recent", max_images=1,
                        preflight="summary_shape")
    agent = Agent(
        StubPhone(pngs), client, model=model, run_dir=tmp, max_steps=5,
        context_policy="recent", max_images=1,
        run_logger=run_log, summarize_every=3,
    )
    # 镜像 run() 主循环的装配：存图像块 + 落因果事件
    #（recent1 下首次触发候选 [0,1,2]，最新帧 3 恒保留）
    for s in range(N_FRAMES):
        image, _text, meta = agent._perceive(s)
        if image is not None:
            agent._image_blocks[s] = image
        agent._log_observation(s, meta)
    result = agent._distill_evicted()
    again = agent._distill_evicted()  # 成功则不再发请求；失败重试（预期内）

    print("== 请求形状 ==")
    print(json.dumps(sent, ensure_ascii=False, indent=2))
    print("== 响应结构 ==")
    print(json.dumps(captured, ensure_ascii=False, indent=2))
    print("== 蒸馏结果 ==")
    if result is None:
        print("返回 None（无候选、空文本或调用失败）")
    else:
        text, candidates = result
        print(f"source_steps={candidates}")
        print(f"summary_text（单样本观察，不作系统结论）：\n{text}")
        if code:
            print(f"单样本比对：summary {'含' if code in text else '不含'} promo 码 {code}")
    print(f"usage 入账：input={agent.usage.input_tokens} output={agent.usage.output_tokens} "
          f"cache_read={agent.usage.cache_read} cache_creation={agent.usage.cache_creation}")
    sc = [json.loads(l) for l in (tmp / "run.jsonl").read_text(encoding="utf-8").splitlines()
          if json.loads(l).get("type") == "summary_call"]
    for r in sc:
        print(f"summary_call: source_steps={r['source_steps']} chars={r['summary_chars']} "
              f"summary_text={'有' if r.get('summary_text') else '无'} error={r['error']}")
    print(f"二次调用：{'返回 None' if again is None else '非 None'}，"
          f"共发请求 {len(captured)} 次（成功后应为 1）")
    ok_shape = (sent and not sent[0]["has_tools"] and not sent[0]["has_thinking"]
                and not sent[0]["has_betas"])
    return 0 if (ok_shape and result is not None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
