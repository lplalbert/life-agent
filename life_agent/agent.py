"""Agent 循环：感知（截图+控件树）→ Claude 决策 → 手机动作 → 结果校验。

安全设计：
- 涉及提交订单/支付/开通类按钮，tap 被安全闸门拒绝，必须走 confirm_and_tap 人工确认；
- 最终支付（指纹/密码/面容）永远由用户在手机上完成，agent 到收银台即停；
- 每一步截图与控件清单落盘留证。
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from PIL import Image

from .phone import Phone, PhoneError, UINode, compact_ui

MODEL_DEFAULT = "mimo-v2.6-pro"  # 小米 MiMo Anthropic 兼容端点模型，可用 --model 或 ANTHROPIC_MODEL 覆盖
MAX_IMAGE_SIDE = 1568  # 超出后等比缩小，控制视觉 token

# 涉及资金动作的按钮文案：tap 直接拒绝，必须走 confirm_and_tap。
DANGER_KEYWORDS = (
    "提交订单", "确认订单", "去支付", "立即支付", "确认支付", "支付订单",
    "去付款", "立即付款", "确认付款", "立即购买", "立即下单", "确认交易",
    "去结算", "合并结算", "提交结算", "开通免密", "免密支付", "刷脸支付",
    "确认开通", "自动续费", "开通会员",
)

SYSTEM_PROMPT = """你是一名手机操作助手，通过截图和控件清单操控用户的安卓手机，完成外卖点餐等生活服务任务（当前主要使用美团 App）。

## 感知
每一步你会收到：
1. 当前屏幕截图；
2. 控件清单：每行 `#序号 "标签" 类型 [l,t,r,b]`，坐标是控件包围盒，点击请用包围盒中心点。
截图与清单以工具结果的形式随动作返回。页面可能有动画，动作结果未达预期时先观察再重试。

## 行动规范
- 一次只做一件事：发出一个动作，看到结果后再决定下一步。
- 优先点击控件清单中的控件中心点；只有清单没有合适目标时才按截图估算坐标。
- 浏览列表用 swipe（看下方更多内容：y1 > y2，距离约 800-1500 像素）。
- 需要输入文字时用 type_text。中文输入可能失败：可改输拼音再从下拉建议中选择，或 ask_human 请用户代输。
- 不做与任务无关的操作，不浏览隐私内容，不更改系统设置。

## 安全规则（必须遵守）
- 点击【提交订单 / 支付 / 结算 / 购买 / 开通免密 / 自动续费】类按钮前必须用 confirm_and_tap 说明按钮和金额，等待用户确认；直接 tap 会被安全闸门拒绝。
- 即使用户确认提交订单，最终支付（指纹/密码/面容）永远由用户在手机上完成：到达收银台或支付页面后立即 finish(status="need_human")，summary 写明订单内容、金额和待支付状态。
- 遇到验证码（滑块、点选、短信验证）→ ask_human 请用户处理。
- 同一动作失败 2 次就换思路；失败 3 次应 finish(status="failed") 说明卡在哪里。

## 已知应用包名
美团 com.sankuai.meituan｜大众点评 com.dianping.v1｜铁路12306 com.MobileTicket｜携程 ctrip.android.view｜去哪儿 com.Qunar｜淘宝 com.taobao.taobao｜拼多多 com.xunmeng.pinduoduo｜支付宝 com.eg.android.AlipayGphone｜微信 com.tencent.mm｜麦当劳 com.mcdonalds.gma.cn｜肯德基 com.yek.android.kfc.activitys｜滴滴 com.sdu.didi.psnger

## 结束
到达终点时调用 finish：
- status="done"：按要求走到终点（如订单已提交、停在待支付页），summary 写清做了什么、金额、现在停在哪。
- status="need_human"：需要用户在手机上完成支付/验证/选择，summary 写清需要用户做什么。
- status="failed"：无法完成，summary 写明原因和已尝试步骤。
"""


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


_INT_ARGS = {"x", "y", "x1", "y1", "x2", "y2", "duration_ms"}

TOOLS = [
    _tool(
        "tap",
        "点击屏幕坐标 (x, y)。优先点击控件清单中控件的包围盒中心点。",
        {"x": {"type": "integer", "description": "像素横坐标"},
         "y": {"type": "integer", "description": "像素纵坐标"}},
        ["x", "y"],
    ),
    _tool(
        "swipe",
        "从 (x1,y1) 滑动到 (x2,y2)。浏览列表下方内容：y1 > y2。",
        {"x1": {"type": "integer"}, "y1": {"type": "integer"},
         "x2": {"type": "integer"}, "y2": {"type": "integer"},
         "duration_ms": {"type": "integer", "description": "滑动时长，默认 400"}},
        ["x1", "y1", "x2", "y2"],
    ),
    _tool(
        "long_press",
        "长按坐标 (x, y)，用于弹出菜单等场景。",
        {"x": {"type": "integer"}, "y": {"type": "integer"},
         "duration_ms": {"type": "integer", "description": "按住时长，默认 800"}},
        ["x", "y"],
    ),
    _tool(
        "type_text",
        "向当前已聚焦的输入框输入文字（先用 tap 点中输入框）。中文可能失败，失败时按提示处理。",
        {"text": {"type": "string", "description": "要输入的文本"}},
        ["text"],
    ),
    _tool(
        "clear_text",
        "清空当前聚焦的输入框。",
        {},
        [],
    ),
    _tool(
        "press",
        "按下物理/虚拟按键。",
        {"key": {"type": "string",
                 "enum": ["back", "home", "recents", "enter", "search", "del", "paste"]}},
        ["key"],
    ),
    _tool(
        "launch_app",
        "启动指定包名的应用（包名见系统提示的已知应用表）。",
        {"package": {"type": "string", "description": "应用包名，如 com.sankuai.meituan"}},
        ["package"],
    ),
    _tool(
        "ask_human",
        "向用户提问并等待回答（支付、验证码、偏好选择、代输文字等场景）。",
        {"question": {"type": "string", "description": "需要用户回答或处理的问题"}},
        ["question"],
    ),
    _tool(
        "confirm_and_tap",
        "请求用户确认后点击敏感按钮（提交订单/支付/结算/开通类）。普通点击用 tap。",
        {"x": {"type": "integer"}, "y": {"type": "integer"},
         "description": {"type": "string", "description": "该按钮是什么、涉及多少金额"}},
        ["x", "y", "description"],
    ),
    _tool(
        "finish",
        "结束任务并汇报结果。",
        {"status": {"type": "string", "enum": ["done", "need_human", "failed"]},
         "summary": {"type": "string", "description": "做了什么、金额、停在哪/需要用户做什么"}},
        ["status", "summary"],
    ),
]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0

    def add(self, u) -> None:
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_creation += getattr(u, "cache_creation_input_tokens", 0) or 0

    def estimate_cost_usd(self) -> float:
        """按 Opus 5.5 定价估算（$4/$20 每百万 token，缓存读 $0.20，缓存写 $5）。"""
        return (
            self.input_tokens * 4
            + self.output_tokens * 20
            + self.cache_read * 0.2
            + self.cache_creation * 5
        ) / 1_000_000


class Agent:
    def __init__(
        self,
        phone: Phone,
        client,
        model: str = MODEL_DEFAULT,
        run_dir=None,
        max_steps: int = 30,
    ):
        self.phone = phone
        self.client = client
        self.model = model
        self.run_dir = run_dir
        self.max_steps = max_steps
        self.usage = Usage()
        self._nodes: list[UINode] = []
        self._finished: tuple[str, str] | None = None
        self._step = 0

    # ---------- 感知 ----------

    def _perceive(self, step: int) -> tuple[dict, str]:
        png = self.phone.screenshot_png()
        self._nodes = self.phone.dump_ui()
        if self.run_dir:
            (self.run_dir / f"step{step:02d}.png").write_bytes(png)
            (self.run_dir / f"step{step:02d}_ui.txt").write_text(
                compact_ui(self._nodes), encoding="utf-8"
            )
        img = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = img.size
        side = max(w, h)
        if side > MAX_IMAGE_SIDE:
            scale = MAX_IMAGE_SIDE / side
            img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82)
        image_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(buf.getvalue()).decode(),
            },
        }
        text = (
            f"[屏幕 {w}x{h} | 当前 {self.phone.current_activity()}]\n"
            f"控件清单（包围盒 [l,t][r,b]，点击用中心点）：\n"
            f"{compact_ui(self._nodes)}"
        )
        return image_block, text

    def _node_at(self, x: int, y: int) -> UINode | None:
        for node in self._nodes:
            if node.label and node.contains(x, y):
                return node
        return None

    # ---------- 工具执行 ----------

    def _execute(self, name: str, args: dict) -> tuple[str, bool]:
        """执行工具，返回 (结果文本, 是否错误)。"""
        try:
            return self._dispatch(name, args), False
        except PhoneError as exc:
            return f"操作失败: {exc}", True
        except (KeyError, TypeError, ValueError) as exc:
            return f"参数错误: {exc}，请重新调用该工具", True

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "tap":
            x, y = int(args["x"]), int(args["y"])
            node = self._node_at(x, y)
            label = node.label if node else ""
            if label and any(k in label for k in DANGER_KEYWORDS):
                raise PhoneError(
                    f"安全闸门：({x},{y}) 是敏感按钮「{label}」（提交/支付/开通类）。"
                    "请改用 confirm_and_tap 请用户确认；最终支付交给用户完成。"
                )
            self.phone.tap(x, y)
            return f"已点击 ({x},{y})" + (f" 「{label}」" if label else "")

        if name == "swipe":
            self.phone.swipe(
                int(args["x1"]), int(args["y1"]), int(args["x2"]), int(args["y2"]),
                int(args.get("duration_ms", 400)),
            )
            return f"已滑动 ({args['x1']},{args['y1']}) → ({args['x2']},{args['y2']})"

        if name == "long_press":
            x, y = int(args["x"]), int(args["y"])
            self.phone.long_press(x, y, int(args.get("duration_ms", 800)))
            return f"已长按 ({x},{y})"

        if name == "type_text":
            text = str(args["text"])
            way = self.phone.type_text(text)
            return f"已通过 {way} 输入：{text}"

        if name == "clear_text":
            return self.phone.clear_text()

        if name == "press":
            self.phone.press(str(args["key"]))
            return f"已按 {args['key']}"

        if name == "launch_app":
            pkg = self.phone.launch(str(args["package"]))
            return f"已启动 {pkg}"

        if name == "ask_human":
            question = str(args.get("question", "")).strip()
            print(f"\n[?] 请求协助：{question}")
            import sys
            if not sys.stdin.isatty():
                return (
                    "当前无人值守，无法回答。"
                    "请调用 finish(status='need_human') 说明需要用户做什么。"
                )
            answer = input("你的回答（回车=稍后由你手动处理）: ").strip()
            return f"用户回答：{answer or '（用户稍后自行处理）'}"

        if name == "confirm_and_tap":
            x, y = int(args["x"]), int(args["y"])
            desc = str(args.get("description", ""))
            node = self._node_at(x, y)
            label = node.label if node else ""
            print("\n" + "=" * 46)
            print("[!] 敏感操作确认请求")
            print(f"   按钮：{label or desc or '(未知)'} @ ({x},{y})")
            if desc and desc != label:
                print(f"   说明：{desc}")
            if self.run_dir:
                shot = self.run_dir / f"step{self._step:02d}.png"
                print(f"   证据截图：{shot}")
            print("=" * 46)
            import sys
            if not sys.stdin.isatty():
                return "无人值守，无法确认。请改用 finish(status='need_human') 交给用户。"
            answer = input("确认点击？y=确认，其他=拒绝: ").strip().lower()
            if answer not in ("y", "yes"):
                return "用户拒绝了该操作。请停止或换方案。"
            self.phone.tap(x, y)
            return f"用户已确认，已点击「{label or desc}」。最终支付仍由用户完成。"

        if name == "finish":
            status = str(args.get("status", "done"))
            summary = str(args.get("summary", ""))
            self._finished = (status, summary)
            return f"finish({status}): {summary}"

        raise PhoneError(f"未知工具: {name}")

    # ---------- 模型调用 ----------

    def _call_model(self, messages: list):
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "medium"},
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=TOOLS,
            messages=messages,
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            return stream.get_final_message()

    # ---------- 主循环 ----------

    def run(self, task: str) -> "Usage":
        self._finished = None
        image, text = self._perceive(0)
        messages = [{
            "role": "user",
            "content": [image, {"type": "text", "text": f"任务：{task}\n\n{text}"}],
        }]
        nudges = 0

        for step in range(1, self.max_steps + 1):
            self._step = step
            print(f"\n>>> 第 {step} 步")
            response = self._call_model(messages)
            self.usage.add(response.usage)

            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(f"  >> {block.text.strip()[:400]}")
                elif block.type == "thinking":
                    thinking = (getattr(block, "thinking", "") or "").strip()
                    if thinking:
                        print(f"  .. {thinking.splitlines()[0][:120]}")

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "refusal":
                print("  [!] 模型拒绝了该请求（stop_reason=refusal）")
                self._finished = self._finished or ("failed", "模型拒绝了该请求")
                break
            if response.stop_reason == "max_tokens":
                print("  [!] 输出被 max_tokens 截断")
                self._finished = self._finished or ("failed", "输出截断")
                break

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                nudges += 1
                if nudges <= 2:
                    messages.append({
                        "role": "user",
                        "content": [{
                            "type": "text",
                            "text": "请继续调用工具完成任务；结束时必须调用 finish。",
                        }],
                    })
                    continue
                print("  [!] 模型连续未调用工具，停止")
                self._finished = self._finished or ("failed", "模型停止调用工具")
                break

            results = []
            for tu in tool_uses:
                args = {
                    k: (int(v) if k in _INT_ARGS else v)
                    for k, v in dict(tu.input).items()
                }
                arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
                print(f"  -> {tu.name}({arg_str[:120]})")
                out, is_err = self._execute(tu.name, args)
                print(f"    {'X' if is_err else 'OK'} {out[:200]}")
                result = {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": [{"type": "text", "text": out}],
                }
                if is_err:
                    result["is_error"] = True
                results.append(result)

            # 动作之后重新感知，附在最后一个 tool_result 上
            image, text = self._perceive(step)
            results[-1]["content"].append(image)
            results[-1]["content"].append({"type": "text", "text": text})
            messages.append({"role": "user", "content": results})

            if self._finished:
                break

        status, summary = self._finished or (
            "failed",
            f"达到最大步数 {self.max_steps} 或提前终止，任务未完成",
        )
        icon = {"done": "[OK]", "need_human": "[人工]", "failed": "[失败]"}.get(status, "[*]")
        print("\n" + "-" * 46)
        print(f"{icon} 任务结果：{status}")
        print(f"   {summary}")
        print(
            f"   token：输入 {self.usage.input_tokens} | 输出 {self.usage.output_tokens}"
            f" | 缓存读 {self.usage.cache_read} | 缓存写 {self.usage.cache_creation}"
        )
        if self.model.startswith("claude"):
            print(f"   估算成本：${self.usage.estimate_cost_usd():.3f}（Claude 定价，估算值）")
        else:
            print(f"   计费：{self.model} 按所接入服务的 token 计划计费（token 用量如上）")
        if self.run_dir:
            print(f"   运行记录：{self.run_dir}")
        return self.usage
