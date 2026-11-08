"""验收 C 确定性测试：运行级 JSONL、冻结经验、full/recent/evidence 策略、
请求协议保真、敏感闸门边界、perceive 成功后不重试、效果校验语义。

全部使用假 Phone / 假 Client，不调用模型、不需要设备。
"""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

from life_agent.agent import RETRIEVAL_NUDGE, SUMMARY_MAX_TOKENS, Agent, _summary_max_tokens
from life_agent.phone import PhoneError, UINode
from life_agent.runlog import RunLogger, validate_request
from research.context_policy import select
from research.episode_runner import build_task, compute_verdict


def make_png(color=(200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, "PNG")
    return buf.getvalue()


def node(label, bounds=(0, 0, 100, 100)):
    return UINode(text=label, desc="", rid="", cls="android.widget.Button",
                  clickable=True, bounds=bounds)


class FakePhone:
    """截图/控件树各自独立脚本；元素耗尽后重复最后一个（重试看到同一屏）。"""

    def __init__(self, shots, uis, pkg="com.lifeagent.researchenv/.MainActivity"):
        self.shots = list(shots)
        self.uis = list(uis)
        self.pkg = pkg
        self.screenshot_calls = 0
        self.dump_calls = 0
        self.taps = []

    @staticmethod
    def _take(script, used):
        val = script[min(used, len(script) - 1)]
        return val

    def screenshot_png(self):
        val = self._take(self.shots, self.screenshot_calls)
        self.screenshot_calls += 1
        if isinstance(val, Exception):
            raise val
        return val

    def dump_ui(self):
        val = self._take(self.uis, self.dump_calls)
        self.dump_calls += 1
        if isinstance(val, Exception):
            raise val
        return val

    def current_activity(self):
        return self.pkg

    def tap(self, x, y):
        self.taps.append((x, y))

    def swipe(self, *a):
        pass

    def long_press(self, *a):
        pass

    def press(self, key):
        pass

    def type_text(self, text):
        return "fake"

    def clear_text(self):
        return "已清空"

    def launch(self, package):
        return package


class FakeUsage:
    input_tokens = 11
    output_tokens = 7
    cache_read_input_tokens = 3
    cache_creation_input_tokens = 2

    def model_dump(self):
        return {
            "input_tokens": 11, "output_tokens": 7,
            "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2,
        }


class FakeStream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.resp


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self.responses.pop(0))


def tool_use(tid, name, args):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=args)


def text_block(t):
    return SimpleNamespace(type="text", text=t)


def thinking_block(t):
    return SimpleNamespace(type="thinking", thinking=t)


def response(content, stop_reason="tool_use"):
    return SimpleNamespace(content=list(content), stop_reason=stop_reason,
                           usage=FakeUsage())


def run_agent(shots, uis, turns, policy="full", max_images=2, pkg=None, experience="",
              retrieval=False, summarize_every=0, retrieval_nudge=False):
    phone = FakePhone(shots, uis, pkg) if pkg else FakePhone(shots, uis)
    # 队列项可以是块元组（自动包响应）或预构建响应（自定义 stop_reason 等）
    client = FakeClient([t if hasattr(t, "content") else response(t) for t in turns])
    tmp = Path(tempfile.mkdtemp())
    run_log = RunLogger(tmp, model="fake-model", context_policy=policy,
                        max_images=max_images)
    agent = Agent(phone, client, model="fake-model", run_dir=tmp, max_steps=len(turns),
                  context_policy=policy, max_images=max_images,
                  frozen_experience=experience, run_logger=run_log,
                  retrieval=retrieval, summarize_every=summarize_every,
                  retrieval_nudge=retrieval_nudge)
    with mock.patch("life_agent.agent.time.sleep"):
        agent.run("测试任务")
    return agent, phone, client, tmp


def read_run(tmp):
    return [json.loads(line) for line in
            (tmp / "run.jsonl").read_text(encoding="utf-8").splitlines()]


class PerceiveRetrySemantics(unittest.TestCase):
    """规划 §4.1：感知失败才重试，成功后不得重复采集。"""

    def test_success_first_attempt_is_not_repeated(self):
        phone = FakePhone([make_png()], [[node("按钮")]])
        agent = Agent(phone, FakeClient([]), model="fake")
        with mock.patch("life_agent.agent.time.sleep"):
            agent._perceive(0)
        self.assertEqual(phone.screenshot_calls, 1)
        self.assertEqual(phone.dump_calls, 1)

    def test_failure_retries_until_success_then_stops(self):
        phone = FakePhone([PhoneError("截图失败"), make_png()], [[node("按钮")]])
        agent = Agent(phone, FakeClient([]), model="fake")
        with mock.patch("life_agent.agent.time.sleep"):
            img, text, meta = agent._perceive(0)
        self.assertEqual(phone.screenshot_calls, 2)  # 1 败 1 成，不再继续
        self.assertEqual(phone.dump_calls, 1)
        self.assertTrue(meta["image_ok"])
        self.assertEqual(meta["image_attempts"], 2)
        self.assertEqual(meta["ui_attempts"], 1)

    def test_channels_degrade_independently(self):
        phone = FakePhone([PhoneError("息屏")], [[node("按钮")]])
        agent = Agent(phone, FakeClient([]), model="fake")
        with mock.patch("life_agent.agent.time.sleep"):
            img, text, meta = agent._perceive(0)
        self.assertIsNone(img)
        self.assertTrue(meta["ui_ok"])
        self.assertIn("截图暂不可用", text)


class SensitiveGateBoundary(unittest.TestCase):
    """同一按钮文案，按包权限区分：沙箱虚构流程放行，真实/未知包 fail-closed。"""

    def _agent(self, pkg):
        phone = FakePhone([make_png()], [[node("去结算")]], pkg)
        agent = Agent(phone, FakeClient([]), model="fake")
        agent._current_pkg = pkg.split("/")[0]
        agent._nodes = [node("去结算")]
        return agent, phone

    def test_sandbox_package_allows_danger_label(self):
        agent, phone = self._agent("com.lifeagent.researchenv/.MainActivity")
        out = agent._dispatch("tap", {"x": 5, "y": 5})
        self.assertIn("已点击", out)
        self.assertEqual(agent._last_gate["decision"], "sandbox_allow")
        self.assertEqual(phone.taps, [(5, 5)])

    def test_real_package_blocks_danger_label(self):
        agent, phone = self._agent("com.sankuai.meituan/.Main")
        with self.assertRaises(PhoneError):
            agent._dispatch("tap", {"x": 5, "y": 5})
        self.assertEqual(agent._last_gate["decision"], "blocked")
        self.assertEqual(phone.taps, [])

    def test_unknown_package_blocks_danger_label(self):
        agent, _ = self._agent("com.unknown.app/.Main")
        with self.assertRaises(PhoneError):
            agent._dispatch("tap", {"x": 5, "y": 5})

    def test_confirm_and_tap_auto_confirms_only_in_sandbox(self):
        agent, phone = self._agent("com.lifeagent.researchenv/.MainActivity")
        out = agent._dispatch("confirm_and_tap", {"x": 5, "y": 5, "description": "结算"})
        self.assertIn("自动确认", out)
        self.assertEqual(phone.taps, [(5, 5)])
        agent2, phone2 = self._agent("com.eg.android.AlipayGphone/.Main")
        with mock.patch("sys.stdin", SimpleNamespace(isatty=lambda: False)):
            out2 = agent2._dispatch("confirm_and_tap", {"x": 5, "y": 5, "description": "支付"})
        self.assertIn("无人值守", out2)
        self.assertEqual(phone2.taps, [])


class UnattendedBoundary(unittest.TestCase):
    """后台运行 stdin 可能继承 tty 句柄但 read 即 EOF：不得崩溃，一律按无人值守处理。"""

    def _agent(self):
        phone = FakePhone([make_png()], [[node("去结算")]])
        return Agent(phone, FakeClient([]), model="fake")

    def test_ask_human_eof_does_not_crash(self):
        agent = self._agent()
        with mock.patch("sys.stdin", SimpleNamespace(isatty=lambda: True)), \
             mock.patch("builtins.input", side_effect=EOFError):
            out = agent._dispatch("ask_human", {"question": "怎么办"})
        self.assertIn("无人值守", out)
        self.assertIn("need_human", out)

    def test_confirm_and_tap_eof_does_not_crash(self):
        agent = self._agent()
        agent._current_pkg = "com.eg.android.AlipayGphone"
        with mock.patch("sys.stdin", SimpleNamespace(isatty=lambda: True)), \
             mock.patch("builtins.input", side_effect=EOFError):
            out = agent._dispatch("confirm_and_tap", {"x": 5, "y": 5, "description": "支付"})
        self.assertIn("无人值守", out)
        self.assertEqual(agent.phone.taps, [])


class ContextPolicyIntegration(unittest.TestCase):
    """历史图像只在请求构造边界处理；文本/thinking 保真、tool 配对不变。"""

    SHOTS = [make_png((10, 10, 10)), make_png((60, 60, 60)), make_png((110, 110, 110))]
    UIS = [[node("列表")], [node("详情")], [node("清单")]]
    TURNS = (
        (text_block("先看详情"), tool_use("t1", "tap", {"x": 1, "y": 1})),
        (thinking_block("思考中"), text_block("去清单"), tool_use("t2", "tap", {"x": 2, "y": 2})),
        (text_block("完成"), tool_use("t3", "finish", {"status": "done", "summary": "好了"})),
    )

    @staticmethod
    def _image_counts(client):
        def count_blocks(blocks):
            n = 0
            for b in blocks or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "image":
                    n += 1
                elif b.get("type") == "tool_result":
                    n += count_blocks(b.get("content"))  # 历史图像在 tool_result 嵌套内容里
            return n

        counts = []
        for call in client.calls:
            n = 0
            for msg in call["messages"]:
                content = msg.get("content")
                n += count_blocks(content if isinstance(content, list) else [])
            counts.append(n)
        return counts

    def test_full_policy_keeps_all_history_images(self):
        _, _, client, tmp = run_agent(self.SHOTS, self.UIS, self.TURNS, policy="full")
        self.assertEqual(self._image_counts(client), [1, 2, 3])
        retained = [l["retained_image_steps"] for l in read_run(tmp)
                    if l.get("type") == "call"]
        self.assertEqual(retained, [[0], [0, 1], [0, 1, 2]])

    def test_recent_policy_keeps_only_latest_image(self):
        _, _, client, tmp = run_agent(self.SHOTS, self.UIS, self.TURNS,
                                      policy="recent", max_images=1)
        self.assertEqual(self._image_counts(client), [1, 1, 1])
        retained = [l["retained_image_steps"] for l in read_run(tmp)
                    if l.get("type") == "call"]
        self.assertEqual(retained, [[0], [1], [2]])

    def test_thinking_and_texts_survive_policy_rewrite(self):
        _, _, client, tmp = run_agent(self.SHOTS, self.UIS, self.TURNS,
                                      policy="recent", max_images=1)
        assistant2 = client.calls[-1]["messages"][3]["content"]  # 第 2 回合 assistant
        self.assertEqual([b.type for b in assistant2], ["thinking", "text", "tool_use"])
        self.assertEqual(assistant2[0].thinking, "思考中")
        checks = [l for l in read_run(tmp) if l.get("type") == "protocol_check"]
        self.assertEqual([c["problems"] for c in checks], [[], [], []])
        self.assertTrue(all(c["ok"] for c in checks))

    def test_validate_request_detects_text_tampering(self):
        spec = [{"role": "user", "kind": "message", "tool_result_ids": [],
                 "content_identity": [{"kind": "text", "sha256": "x"}]}]
        msgs = [{"role": "user", "content": [{"type": "text", "text": "原"}]}]
        self.assertTrue(validate_request(msgs, spec))  # 哈希不符必须报问题

    def test_evidence_policy_uses_verification_events(self):
        # 第 1 回合动作无效（画面不变）→ effect:1 failed；evidence 应保留其证据帧
        shots = [make_png((10, 10, 10)), make_png((10, 10, 10)), make_png((90, 90, 90))]
        uis = [[node("列表")], [node("列表")], [node("清单")]]
        turns = (
            (tool_use("t1", "tap", {"x": 1, "y": 1}),),
            (tool_use("t2", "tap", {"x": 2, "y": 2}),),
            (tool_use("t3", "finish", {"status": "done", "summary": "x"}),),
        )
        _, _, _, tmp = run_agent(shots, uis, turns, policy="evidence", max_images=2)
        verifs = [l for l in read_run(tmp) if l.get("kind") == "verification"]
        self.assertEqual(verifs[0]["state"], "failed")   # 画面未变 = 未观察到效果
        self.assertEqual(verifs[1]["state"], "verified")
        retained = [l["retained_image_steps"] for l in read_run(tmp)
                    if l.get("type") == "call"]
        self.assertIn(0, retained[2])  # 未解决检查的证据帧
        self.assertIn(2, retained[2])  # 最新帧恒保留

    def test_run_jsonl_projection_matches_event_interface(self):
        _, _, _, tmp = run_agent(self.SHOTS, self.UIS, self.TURNS, policy="full")
        causal = [l for l in read_run(tmp) if l.get("kind") in ("observation", "verification")]
        select(causal, "evidence")  # 严格校验不抛异常即通过


class VerificationSemantics(unittest.TestCase):
    def test_neutral_turn_is_pending_not_verified(self):
        turns = ((tool_use("t1", "finish", {"status": "done", "summary": "x"}),),)
        _, _, _, tmp = run_agent([make_png()], [[node("列表")]], turns)
        verifs = [l for l in read_run(tmp) if l.get("kind") == "verification"]
        self.assertEqual(verifs[-1]["state"], "pending")

    def test_missing_ui_channel_is_pending(self):
        turns = ((tool_use("t1", "tap", {"x": 1, "y": 1}),),)
        _, _, _, tmp = run_agent([make_png()], [[node("列表")], PhoneError("dump 失败")], turns)
        verifs = [l for l in read_run(tmp) if l.get("kind") == "verification"]
        self.assertEqual(verifs[-1]["state"], "pending")


class FrozenExperience(unittest.TestCase):
    def test_experience_injected_and_hash_recorded(self):
        turns = ((tool_use("t1", "finish", {"status": "done", "summary": "x"}),),)
        _, _, client, tmp = run_agent([make_png()], [[node("按钮")]], turns,
                                      experience="经验条目：先看库存标签。")
        sys_prompt = client.calls[0]["system"][0]["text"]
        self.assertIn("经验条目：先看库存标签。", sys_prompt)
        self.assertIn("冻结文件", sys_prompt)


class EpisodeScoring(unittest.TestCase):
    def test_recovered_requires_triggered_and_success(self):
        v = compute_verdict({"success": True, "counters": {"popup": {"triggered": 1}}}, "popup")
        self.assertEqual((v["success"], v["fault_triggered"], v["recovered"]), (True, True, True))
        v = compute_verdict({"success": True, "counters": {"popup": {"triggered": 0}}}, "popup")
        self.assertFalse(v["recovered"])  # 未触发不得计恢复
        v = compute_verdict({"success": False, "counters": {"popup": {"triggered": 2}}}, "popup")
        self.assertFalse(v["recovered"])

    def test_task_text_has_no_fault_or_expected_leakage(self):
        items = [{"id": "p1", "name": "纸巾"}, {"id": "p2", "name": "可乐"}]
        task = build_task(items, ["p1"])
        self.assertIn("纸巾", task)
        for leak in ("expected", "fault", "popup", "noop", "confusion", "delay", "promo", "code", "优惠", "p1"):
            self.assertNotIn(leak, task)


class RetrievalToolBoundary(unittest.TestCase):
    """历史取回（方法组件）：review_screenshot 显式钉住本地留存帧，逐出策略不得清掉。

    钉住槽优先物化，同帧全请求只发一份（原始槽让位，防重复计费）。
    """

    LEAK = ("expected", "fault", "popup", "noop", "confusion", "delay",
            "promo", "code", "优惠", "p1")

    @staticmethod
    def _agent(**kw):
        phone = FakePhone([make_png()], [[node("按钮")]])
        return Agent(phone, FakeClient([]), model="fake", **kw)

    def test_tool_absent_by_default_present_with_flag(self):
        base = self._agent()
        self.assertNotIn("review_screenshot", [t["name"] for t in base._tools])
        armed = self._agent(retrieval=True)
        self.assertIn("review_screenshot", [t["name"] for t in armed._tools])
        desc = next(t for t in armed._tools if t["name"] == "review_screenshot")["description"]
        for leak in self.LEAK:
            self.assertNotIn(leak, desc)

    def test_unknown_tool_without_flag(self):
        out, is_err = self._agent()._execute("review_screenshot", {"step": 0})
        self.assertTrue(is_err)
        self.assertIn("未知工具", out)

    def test_missing_step_lists_available(self):
        agent = self._agent(retrieval=True)
        agent._image_blocks[0] = {"type": "image"}
        out = agent._dispatch("review_screenshot", {"step": 9})
        self.assertIn("没有第 9 步", out)
        self.assertIn("可回看的步号：0", out)
        self.assertEqual(agent._retrieved_steps, set())

    def test_review_pins_evicted_frame_into_next_request(self):
        shots = [make_png((10, 10, 10)), make_png((60, 60, 60)), make_png((110, 110, 110))]
        uis = [[node("列表")], [node("详情")], [node("清单")]]
        turns = (
            (tool_use("t1", "tap", {"x": 1, "y": 1}),),
            (tool_use("t2", "review_screenshot", {"step": 0}),),
            (tool_use("t3", "finish", {"status": "done", "summary": "x"}),),
        )
        _, _, client, tmp = run_agent(shots, uis, turns, policy="recent", max_images=1,
                                      retrieval=True)
        self.assertEqual(ContextPolicyIntegration._image_counts(client), [1, 1, 2])
        retained = [l["retained_image_steps"] for l in read_run(tmp)
                    if l.get("type") == "call"]
        self.assertEqual(retained, [[0], [1], [0, 2]])
        acts = [l for l in read_run(tmp)
                if l.get("type") == "action" and l["name"] == "review_screenshot"]
        self.assertEqual(acts[0]["expectation"], "neutral")

    def test_pinned_frame_not_duplicated_in_same_request(self):
        shots = [make_png((10, 10, 10)), make_png((60, 60, 60))]
        uis = [[node("列表")], [node("详情")]]
        turns = (
            (tool_use("t1", "review_screenshot", {"step": 0}),),
            (tool_use("t2", "finish", {"status": "done", "summary": "x"}),),
        )
        _, _, client, _ = run_agent(shots, uis, turns, policy="full", retrieval=True)
        # 第 2 次请求：step0 只发一份（钉住槽优先，原始槽让位）+ step1 = 2 张
        self.assertEqual(ContextPolicyIntegration._image_counts(client), [1, 2])


class RetrievalTriggerNudge(unittest.TestCase):
    """取回触发提示（改进臂）：system 追加「不确定→回看」的连接话术。

    默认关闭；无取回工具时给提示属无效组合，显式拒绝。
    """

    @staticmethod
    def _agent(**kw):
        phone = FakePhone([make_png()], [[node("按钮")]])
        return Agent(phone, FakeClient([]), model="fake", **kw)

    def test_nudge_absent_by_default(self):
        self.assertNotIn(RETRIEVAL_NUDGE, self._agent(retrieval=True)._system_prompt())
        self.assertNotIn("历史取证", self._agent(retrieval=True)._system_prompt())

    def test_nudge_appends_guidance_with_retrieval(self):
        text = self._agent(retrieval=True, retrieval_nudge=True)._system_prompt()
        self.assertIn("## 历史取证", text)
        self.assertIn(RETRIEVAL_NUDGE, text)

    def test_nudge_without_retrieval_rejected(self):
        with self.assertRaises(ValueError):
            self._agent(retrieval_nudge=True)

    def test_nudge_composes_with_frozen_experience(self):
        agent = self._agent(retrieval=True, retrieval_nudge=True, frozen_experience="经验A")
        text = agent._system_prompt()
        self.assertIn("经验A", text)
        self.assertIn(RETRIEVAL_NUDGE, text)

    def test_nudge_run_smoke(self):
        shots = [make_png((10, 10, 10)), make_png((60, 60, 60))]
        uis = [[node("列表")], [node("详情")]]
        turns = (
            (tool_use("t1", "review_screenshot", {"step": 0}),),
            (tool_use("t2", "finish", {"status": "done", "summary": "x"}),),
        )
        agent, _, _, _ = run_agent(shots, uis, turns, policy="recent", max_images=1,
                                   retrieval=True, retrieval_nudge=True)
        self.assertEqual(agent._finished[0], "done")


class KeyframeBaseline(unittest.TestCase):
    """关键帧基线：每段屏幕状态游程保留最后一帧；零额外调用、确定性。

    状态指纹 = 图像字节+控件树（生产边界计算，观察事件携带）；
    画布像素变化可见，缺指纹的步各视为独立状态。
    """

    @staticmethod
    def _events(fps):
        ev = []
        for i, fp in enumerate(fps):
            e = {"kind": "observation", "step": i, "image_ref": f"step{i:02d}.png"}
            if fp is not None:
                e["screen_fingerprint"] = fp
            ev.append(e)
        return ev

    def test_keeps_last_frame_of_each_state_run(self):
        out = select(self._events(["A", "A", "B", "B", "C", "C"]), "keyframe", 4)
        self.assertEqual(out["retained_steps"], [1, 3, 5])

    def test_budget_prefers_most_recent_runs(self):
        out = select(self._events(["A", "A", "B", "B", "C", "C"]), "keyframe", 2)
        self.assertEqual(out["retained_steps"], [3, 5])

    def test_missing_fingerprint_is_distinct_state(self):
        out = select(self._events([None, "A", "A"]), "keyframe", 3)
        self.assertEqual(out["retained_steps"], [0, 2])

    def test_duplicate_states_dropped_unlike_recent(self):
        shots = [make_png((10, 10, 10)), make_png((10, 10, 10)), make_png((90, 90, 90))]
        uis = [[node("列表")], [node("列表")], [node("清单")]]
        turns = (
            (tool_use("t1", "tap", {"x": 1, "y": 1}),),
            (tool_use("t2", "tap", {"x": 2, "y": 2}),),
            (tool_use("t3", "finish", {"status": "done", "summary": "x"}),),
        )
        _, _, _, tmp = run_agent(shots, uis, turns, policy="keyframe", max_images=2)
        retained = [l["retained_image_steps"] for l in read_run(tmp)
                    if l.get("type") == "call"]
        # 第 0/1 步同屏幕状态 → 游程末帧=1；recent 会是 [0], [0,1], [1,2]
        self.assertEqual(retained, [[0], [1], [1, 2]])
        obs = [l for l in read_run(tmp) if l.get("kind") == "observation"]
        self.assertTrue(all(o.get("screen_fingerprint") for o in obs))


class SummaryDistillationBaseline(unittest.TestCase):
    """摘要基线：每 M 回合把将被逐出的帧蒸馏进文本记忆；额外调用计费入账。

    turns 序列里按调用顺序穿插蒸馏调用的假响应（主调用与蒸馏共用响应队列）。
    """

    SUMMARY_REPLY = (text_block("视觉摘要：第 0 步首屏顶部有红色横幅。"),)

    @staticmethod
    def _texts(content):
        out = []
        for b in content or []:
            if not isinstance(b, dict):
                continue  # 历史里的 SDK 块（thinking/text 原文）不在本次比对范围
            if b.get("type") == "text":
                out.append(b.get("text") or "")
            elif b.get("type") == "tool_result":
                out.extend(SummaryDistillationBaseline._texts(b.get("content")))
        return out

    def test_distills_evicted_frames_and_injects_text(self):
        shots = [make_png((10, 10, 10)), make_png((60, 60, 60)), make_png((110, 110, 110))]
        uis = [[node("列表")], [node("详情")], [node("清单")]]
        turns = (
            (tool_use("t1", "tap", {"x": 1, "y": 1}),),    # 主调用 1
            self.SUMMARY_REPLY,                            # 蒸馏调用（第 1 回合末）
            (tool_use("t2", "finish", {"status": "done", "summary": "x"}),),
        )
        agent, _, client, tmp = run_agent(shots, uis, turns, policy="recent", max_images=1,
                                          summarize_every=1)
        self.assertEqual(len(client.calls), 3)
        self.assertNotIn("tools", client.calls[1])  # 蒸馏调用不带工具
        sent_texts = [t for msg in client.calls[2]["messages"]
                      for t in self._texts(msg.get("content"))]
        self.assertTrue(any("视觉记忆摘要" in t for t in sent_texts))
        sc = [l for l in read_run(tmp) if l.get("type") == "summary_call"]
        self.assertEqual(len(sc), 1)
        self.assertEqual(sc[0]["source_steps"], [0])  # recent1 逐出的正是首帧
        self.assertIsNone(sc[0]["error"])
        self.assertEqual(agent.usage.input_tokens, 33)   # 3 次调用 × FakeUsage 11
        self.assertEqual(agent.usage.output_tokens, 21)  # 蒸馏调用计费入账

    def test_no_distillation_when_nothing_evicted(self):
        shots = [make_png((10, 10, 10)), make_png((60, 60, 60))]
        uis = [[node("列表")], [node("清单")]]
        turns = (
            (tool_use("t1", "tap", {"x": 1, "y": 1}),),
            (tool_use("t2", "finish", {"status": "done", "summary": "x"}),),
        )
        _, _, client, tmp = run_agent(shots, uis, turns, policy="full", summarize_every=1)
        self.assertEqual(len(client.calls), 2)  # full 无逐出 → 零额外调用
        self.assertEqual([l for l in read_run(tmp) if l.get("type") == "summary_call"], [])

    def test_empty_text_not_marked_distilled_and_retries(self):
        """空文本蒸馏（如 thinking 吃光输出上限被截断）不标已蒸馏，下次触发重试同批帧。"""
        shots = [make_png((10, 10, 10)), make_png((60, 60, 60)), make_png((110, 110, 110))]
        uis = [[node("列表")], [node("详情")], [node("清单")]]
        turns = (
            (tool_use("t1", "tap", {"x": 1, "y": 1}),),       # 主调用 1
            response((), "max_tokens"),                        # 蒸馏 1：空文本（截断）
            (tool_use("t2", "tap", {"x": 2, "y": 2}),),       # 主调用 2
            self.SUMMARY_REPLY,                                # 蒸馏 2：重试 [0] + 新逐出 [1]
            (tool_use("t3", "finish", {"status": "done", "summary": "x"}),),
        )
        agent, _, client, tmp = run_agent(shots, uis, turns, policy="recent", max_images=1,
                                          summarize_every=1)
        self.assertEqual(len(client.calls), 5)
        sc = [l for l in read_run(tmp) if l.get("type") == "summary_call"]
        self.assertEqual(len(sc), 2)
        self.assertEqual(sc[0]["source_steps"], [0])
        self.assertEqual(sc[0]["summary_chars"], 0)
        self.assertIsNone(sc[0]["summary_text"])
        self.assertEqual(sc[0]["stop_reason"], "max_tokens")   # 截断原因留档
        self.assertEqual(sc[1]["source_steps"], [0, 1])        # 失败帧 0 重试 + 新逐出 1
        self.assertEqual(sc[1]["stop_reason"], "tool_use")
        self.assertIsNotNone(sc[1]["summary_text"])
        # 空文本不注入记忆；成功蒸馏注入下一请求
        sent_texts = [t for msg in client.calls[2]["messages"]
                      for t in SummaryDistillationBaseline._texts(msg.get("content"))]
        self.assertFalse(any("视觉记忆摘要" in t for t in sent_texts))
        sent_texts = [t for msg in client.calls[4]["messages"]
                      for t in SummaryDistillationBaseline._texts(msg.get("content"))]
        self.assertTrue(any("视觉记忆摘要" in t for t in sent_texts))
        # 重试请求里第 0 步帧确实再次送出
        retry_texts = [t for msg in client.calls[3]["messages"]
                       for t in SummaryDistillationBaseline._texts(msg.get("content"))]
        self.assertTrue(any("第 0 步截图" in t for t in retry_texts))
        self.assertEqual(agent._summary_seen, {0, 1})  # 成功蒸馏才标已记忆化

    def test_truncated_partial_text_not_trusted_and_retries(self):
        """max_tokens 截断的残缺文本不可当记忆（实测丢码），整体判失败并重试。"""
        shots = [make_png((10, 10, 10)), make_png((60, 60, 60)), make_png((110, 110, 110))]
        uis = [[node("列表")], [node("详情")], [node("清单")]]
        turns = (
            (tool_use("t1", "tap", {"x": 1, "y": 1}),),
            response((text_block("0步：列表。红"),), "max_tokens"),  # 截断的残缺文本
            (tool_use("t2", "tap", {"x": 2, "y": 2}),),
            self.SUMMARY_REPLY,
            (tool_use("t3", "finish", {"status": "done", "summary": "x"}),),
        )
        agent, _, client, tmp = run_agent(shots, uis, turns, policy="recent", max_images=1,
                                          summarize_every=1)
        sc = [l for l in read_run(tmp) if l.get("type") == "summary_call"]
        self.assertEqual(len(sc), 2)
        self.assertEqual(sc[0]["summary_chars"], len("0步：列表。红"))  # 残缺原文留档
        self.assertEqual(sc[0]["stop_reason"], "max_tokens")
        self.assertEqual(sc[1]["source_steps"], [0, 1])   # 截断帧整体重试
        self.assertEqual(sc[1]["stop_reason"], "tool_use")
        self.assertEqual(agent._summary_seen, {0, 1})
        sent_texts = [t for msg in client.calls[2]["messages"]
                      for t in SummaryDistillationBaseline._texts(msg.get("content"))]
        self.assertFalse(any("视觉记忆摘要" in t for t in sent_texts))  # 残缺文本不注入
        sent_texts = [t for msg in client.calls[4]["messages"]
                      for t in SummaryDistillationBaseline._texts(msg.get("content"))]
        self.assertTrue(any("视觉记忆摘要" in t for t in sent_texts))


class SummaryBudgetScaling(unittest.TestCase):
    """蒸馏输出上限随批量帧数放宽（M=6 截断伪影的效度修复）。"""

    def test_budget_unchanged_for_small_batches(self):
        # ≤3 帧 = M=2/3 既有语义（1200），改动不得影响旧臂
        for n in (1, 2, 3):
            self.assertEqual(_summary_max_tokens(n), SUMMARY_MAX_TOKENS)

    def test_budget_scales_with_large_batches(self):
        self.assertEqual(_summary_max_tokens(6), 2400)
        self.assertEqual(_summary_max_tokens(9), 3600)

    def test_budget_floor_and_defensive_zero(self):
        self.assertGreaterEqual(SUMMARY_MAX_TOKENS, 1200)
        self.assertEqual(_summary_max_tokens(0), SUMMARY_MAX_TOKENS)


if __name__ == "__main__":
    unittest.main()
