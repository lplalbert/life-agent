"""Agent 循环：感知（截图+控件树）→ Claude 决策 → 手机动作 → 结果校验。

安全设计：
- 涉及提交订单/支付/开通类按钮，tap 被安全闸门拒绝，必须走 confirm_and_tap 人工确认；
- 最终支付（指纹/密码/面容）永远由用户在手机上完成，agent 到收银台即停；
- 每一步截图与控件清单落盘留证。
"""

from __future__ import annotations

import base64
import io
import time
import hashlib
from dataclasses import dataclass

from PIL import Image

from .phone import Phone, PhoneError, UINode, compact_ui
from .runlog import RunLogger, validate_request
from research.context_policy import select as select_images

MODEL_DEFAULT = "mimo-v2.6-pro"  # 小米 MiMo Anthropic 兼容端点模型，可用 --model 或 ANTHROPIC_MODEL 覆盖
MAX_IMAGE_SIDE = 1568  # 超出后等比缩小，控制视觉 token

# 沙箱应用：虚构业务流程（无真实资金/派单副作用），敏感按钮闸门不适用；
# 其余（真实商业 App、未知包）一律按闸门拒绝敏感文案，fail-closed。
SANDBOX_PACKAGES = frozenset({"com.lifeagent.researchenv"})

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


def _usage_raw(usage) -> dict:
    """usage 原始字段：优先 SDK model_dump()，退回已知字段。"""
    try:
        return usage.model_dump()
    except Exception:
        return {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
        }


def _input_or_none(prompt: str) -> "str | None":
    """交互输入；无人值守（stdin 非 tty，或已关闭/EOF）返回 None，绝不崩溃。

    isatty() 判定不可靠：后台进程可继承 tty 句柄但 read 即 EOF，
    因此 input() 的 EOFError/OSError 一律按无人值守处理（fail-closed）。
    """
    import sys
    if not sys.stdin.isatty():
        return None
    try:
        return input(prompt)
    except (EOFError, OSError):
        return None


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


_INT_ARGS = {"x", "y", "x1", "y1", "x2", "y2", "duration_ms", "step"}

# 期望产生可观察界面变化的动作；其余（wait/ask_human/finish）不产生效果期望，
# 运行时校验如实标 pending，不得自动标 verified。
_EXPECT_CHANGE = frozenset({
    "tap", "swipe", "long_press", "press", "launch_app",
    "confirm_and_tap", "type_text", "clear_text",
})

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

# 历史取回工具（方法组件，retrieval=True 的臂才注入）：把本地留存帧重新注入上下文。
# 消融「去历史取回」= 不注入本工具。描述不含任务答案/故障词（通用取证话术）。
REVIEW_TOOL = _tool(
    "review_screenshot",
    "回看此前观察过的屏幕截图（本地留存原始帧，与当时像素一致）。"
    "当记忆中的界面细节（文字、编号、价格、按钮文案等）拿不准时，先回看取证再操作。"
    "step=0 是任务开始后的第一屏。",
    {"step": {"type": "integer", "description": "要回看的观察步号（0 = 第一屏）"}},
    ["step"],
)

# 取回触发提示（消融/改进臂专用，retrieval_nudge=True 才注入 system）：
# 实测工具在上下文里也极少被自发调用（批 3 4/24、批 4 6/48）——「不确定感」连不到
# 「回看」这个动作。本提示把该连接写明；不含任何任务答案/故障词（通用取证话术）。
RETRIEVAL_NUDGE = (
    "当任务需要早前屏幕上的具体信息（如优惠码、金额、编号、按钮文案）而你记不清时，"
    "用 review_screenshot(step) 回看该步截图取证；截图被移出上下文后本地仍保留，"
    "可以随时取回。不要凭猜测填值，也不要在还能取证时放弃。"
)

SUMMARY_MAX_TOKENS = 1200  # 摘要蒸馏调用输出上限：MiMo 默认先输出 thinking 且随候选帧数
# 增长（实测 3 帧 387–436、9 帧 752 token，800 时 3/38 调用仍截断）；1200 留余量，
# 截断兜底靠「截断=蒸馏失败、下次重试」语义，上限只是硬界不是目标


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
        context_policy: str = "full",
        max_images: int = 2,
        frozen_experience: str = "",
        run_logger: RunLogger | None = None,
        retrieval: bool = False,
        summarize_every: int = 0,
        retrieval_nudge: bool = False,
    ):
        if retrieval_nudge and not retrieval:
            raise ValueError("retrieval_nudge 需要 retrieval=True（无取回工具时提示无对象）")
        self.phone = phone
        self.client = client
        self.model = model
        self.run_dir = run_dir
        self.max_steps = max_steps
        self.context_policy = context_policy
        self.max_images = max_images
        self.frozen_experience = frozen_experience
        self.run_log = run_logger
        self.retrieval = retrieval
        self.summarize_every = summarize_every
        self.retrieval_nudge = retrieval_nudge
        self.usage = Usage()
        self._tools = list(TOOLS) + ([REVIEW_TOOL] if retrieval else [])
        self._nodes: list[UINode] = []
        self._finished: tuple[str, str] | None = None
        self._step = 0
        self._current_pkg = "unknown"
        self._messages: list = []  # 规范历史（图像以 _image_slot 占位，仅请求构造边界替换）
        self._image_blocks: dict[int, dict] = {}  # step → 图像块（本地产物，原始引用不删）
        self._ui_fingerprints: dict[int, str] = {}  # step → 控件树指纹（效果校验用）
        self._screen_fps: dict[int, str] = {}  # step → 屏幕指纹（像素+控件树，关键帧基线用）
        self._result_blocks: list = []  # 当前工具结果附带的额外块（review_screenshot 回看帧）
        self._retrieved_steps: set[int] = set()  # 取回钉住的帧步号（钉住槽优先物化）
        self._summary_seen: set[int] = set()  # 已蒸馏进文本记忆的帧步号
        self._last_sent_steps: list[int] = []  # 上一请求实际发出的图像步（含钉住帧）
        self._last_actions_expect: list[str] = []  # 本回合动作的期望效果
        self._last_gate: dict = {}  # 最近一次 tap/confirm_and_tap 的闸门判定

    # ---------- 感知 ----------

    def _capture(self, fn, attempts: int, label: str):
        """失败才重试，成功立即返回（成功后不得重复采集）；返回 (值, 尝试次数, 错误)。"""
        err: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return fn(), attempt, None
            except PhoneError as exc:
                err = exc
                if attempt < attempts:
                    time.sleep(1.0 if label == "截图" else 0.5)
        return None, attempts, err

    def _perceive(self, step: int) -> tuple[dict | None, str, dict]:
        """截图与控件树独立降级：缺哪路就明说哪路，不以「以截图为准」统一含糊处理。"""
        png, img_attempts, img_err = self._capture(self.phone.screenshot_png, 3, "截图")
        nodes, ui_attempts, ui_err = self._capture(self.phone.dump_ui, 2, "控件树")
        self._nodes = nodes or []
        screen = self.phone.current_activity()
        self._current_pkg = screen.split("/")[0] if screen else "unknown"

        ui_text = compact_ui(self._nodes) if nodes is not None else None
        image_ref = None
        ui_ref = None
        if self.run_dir and png:
            image_ref = f"step{step:02d}.png"
            (self.run_dir / image_ref).write_bytes(png)
        if self.run_dir and ui_text is not None:
            ui_ref = f"step{step:02d}_ui.txt"
            (self.run_dir / ui_ref).write_text(ui_text, encoding="utf-8")
        self._ui_fingerprints[step] = (
            hashlib.sha256(ui_text.encode("utf-8")).hexdigest()
            if ui_text is not None else ""
        )
        # 屏幕指纹（图像字节+控件树）：关键帧基线判「屏幕状态是否变化」；
        # 与效果校验的 ui 指纹分工不同——画布绘制的像素差异只在这里可见。
        self._screen_fps[step] = (
            hashlib.sha256((png or b"") + (ui_text or "").encode("utf-8")).hexdigest()
            if (png or ui_text is not None) else ""
        )

        image_block = None
        if png:
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

        head = f"[当前 {screen}]"
        if nodes is not None:
            body = (
                f"控件清单（包围盒 [l,t][r,b]，点击用中心点）：\n{ui_text}"
            )
            if not png:
                body += (
                    f"\n[截图暂不可用：{img_err}。若屏幕息屏或锁屏，先用 swipe 上滑解锁，"
                    "恢复后再继续，不要终止任务。]"
                )
        elif png:
            body = f"[控件树暂不可用：{ui_err}。以截图为准继续操作，必要时 wait 等界面稳定。]"
        else:
            body = (
                f"[感知失败：截图（{img_err}）与控件树（{ui_err}）均不可用。]"
                "用 swipe 上滑解锁或 wait 2-5 秒后重试，不要因此终止任务。"
            )
        meta = {
            "image_ref": image_ref, "ui_ref": ui_ref, "screen": screen,
            "image_ok": png is not None, "ui_ok": nodes is not None,
            "image_attempts": img_attempts, "ui_attempts": ui_attempts,
            "image_error": str(img_err) if img_err else None,
            "ui_error": str(ui_err) if ui_err else None,
            "screen_fingerprint": self._screen_fps[step],
        }
        return image_block, f"{head}\n{body}", meta

    def _verify_turn_effect(self, step: int) -> None:
        """运行时效果校验：比较动作前后控件树指纹（证据来自真实感知，不读评估器）。

        单回合一律只产生一个检查（感知粒度）；多个动作同回合时无法逐动作归因，
        如实记录在 basis 里。未知效果不得标 verified。
        """
        if not self.run_log:
            return
        expects = self._last_actions_expect
        check_id = f"effect:{step}"
        source_steps = [step - 1, step] if step > 0 else [step]
        fp_prev = self._ui_fingerprints.get(step - 1, "")
        fp_cur = self._ui_fingerprints.get(step, "")
        basis = f"ui_fingerprint prev={fp_prev[:12] or 'missing'} cur={fp_cur[:12] or 'missing'}"
        want_change = any(e == "change" for e in expects)
        if not want_change:
            state = "pending"
            basis += "; no change expectation in this turn"
        elif not fp_prev or not fp_cur:
            state = "pending"
            basis += "; ui channel missing at one endpoint"
        else:
            changed = fp_prev != fp_cur
            state = "verified" if changed else "failed"
            basis += f"; want_change=True changed={changed}"
        if len(expects) > 1:
            basis += "; multi-action turn, attribution per action unavailable"
        self.run_log.log_verification(step, check_id, source_steps, state, basis)

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
            sandbox = self._current_pkg in SANDBOX_PACKAGES
            if label and any(k in label for k in DANGER_KEYWORDS):
                self._last_gate = {
                    "decision": "sandbox_allow" if sandbox else "blocked",
                    "label": label, "package": self._current_pkg,
                }
                if not sandbox:
                    raise PhoneError(
                        f"安全闸门：({x},{y}) 是敏感按钮「{label}」（提交/支付/开通类）。"
                        "请改用 confirm_and_tap 请用户确认；最终支付交给用户完成。"
                    )
            else:
                self._last_gate = {"decision": "ordinary", "label": label,
                                   "package": self._current_pkg}
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
            answer = _input_or_none("你的回答（回车=稍后由你手动处理）: ")
            if answer is None:
                return (
                    "当前无人值守，无法回答。"
                    "请调用 finish(status='need_human') 说明需要用户做什么。"
                )
            return f"用户回答：{answer.strip() or '（用户稍后自行处理）'}"

        if name == "confirm_and_tap":
            x, y = int(args["x"]), int(args["y"])
            desc = str(args.get("description", ""))
            node = self._node_at(x, y)
            label = node.label if node else ""
            if self._current_pkg in SANDBOX_PACKAGES:
                # 沙箱应用为虚构业务流程，无真实资金/派单副作用：自动确认并如实记录
                self._last_gate = {"decision": "sandbox_auto_confirm", "label": label,
                                   "package": self._current_pkg}
                self.phone.tap(x, y)
                return f"沙箱应用（虚构业务流程）：已自动确认并点击「{label or desc}」。"
            self._last_gate = {"decision": "human_confirm", "label": label,
                               "package": self._current_pkg}
            print("\n" + "=" * 46)
            print("[!] 敏感操作确认请求")
            print(f"   按钮：{label or desc or '(未知)'} @ ({x},{y})")
            if desc and desc != label:
                print(f"   说明：{desc}")
            if self.run_dir:
                shot = self.run_dir / f"step{self._step:02d}.png"
                print(f"   证据截图：{shot}")
            print("=" * 46)
            answer = _input_or_none("确认点击？y=确认，其他=拒绝: ")
            if answer is None:
                return "无人值守，无法确认。请改用 finish(status='need_human') 交给用户。"
            if answer.strip().lower() not in ("y", "yes"):
                return "用户拒绝了该操作。请停止或换方案。"
            self.phone.tap(x, y)
            return f"用户已确认，已点击「{label or desc}」。最终支付仍由用户完成。"

        if name == "review_screenshot":
            if not self.retrieval:
                raise PhoneError("未知工具: review_screenshot")  # 消融=整体移除，不只是藏工具表
            step = int(args["step"])
            if step not in self._image_blocks:
                avail = "、".join(str(s) for s in sorted(self._image_blocks)) or "（无）"
                return f"没有第 {step} 步的截图。可回看的步号：{avail}"
            self._retrieved_steps.add(step)
            self._result_blocks.append({"type": "_image_slot", "step": step, "pin": True})
            return f"已回看第 {step} 步截图（当时的屏幕，见本结果附图）。"

        if name == "finish":
            status = str(args.get("status", "done"))
            summary = str(args.get("summary", ""))
            self._finished = (status, summary)
            return f"finish({status}): {summary}"

        raise PhoneError(f"未知工具: {name}")

    # ---------- 请求构造（历史图像只在这里处理，文本/thinking 原文不动） ----------

    def _system_prompt(self) -> str:
        text = SYSTEM_PROMPT
        if self.frozen_experience:
            text += (
                "\n\n## 你的操作经验（冻结文件，研究期间不变；与当前屏幕冲突时以屏幕为准）\n"
                + self.frozen_experience
            )
        if self.retrieval_nudge:
            text += "\n\n## 历史取证\n" + RETRIEVAL_NUDGE
        return text

    def _retained_steps(self) -> set[int]:
        if self.run_log is None:
            return set(self._image_blocks)
        selection = select_images(
            self.run_log.causal_events(), self.context_policy, self.max_images
        )
        return set(selection["retained_steps"]) & set(self._image_blocks)

    def _build_messages(self, retained: set[int]) -> list:
        # 图像槽可出现在消息顶层或 tool_result 嵌套内容里，递归处理；
        # 文本/thinking/工具块原样保留（保真契约见 runlog.validate_request）。
        # review_screenshot 的钉住槽（pin=True）不走逐出策略、优先物化；
        # 同一步的原始槽让位，全请求同一帧只发一份（不重复计费）。
        pinned_steps: set[int] = set()

        def scan(content) -> None:
            for block in content or []:
                if isinstance(block, dict) and block.get("type") == "_image_slot":
                    if block.get("pin"):
                        pinned_steps.add(block["step"])
                elif (isinstance(block, dict) and block.get("type") == "tool_result"
                      and isinstance(block.get("content"), list)):
                    scan(block["content"])

        for msg in self._messages:
            if isinstance(msg.get("content"), list):
                scan(msg["content"])

        pinned_emitted: set[int] = set()
        plain_emitted: set[int] = set()
        sent: set[int] = set()

        def materialize(content: list) -> list:
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "_image_slot":
                    step = block["step"]
                    if step in pinned_steps:
                        if block.get("pin") and step not in pinned_emitted:
                            pinned_emitted.add(step)
                            if step in self._image_blocks:
                                new_content.append(self._image_blocks[step])
                                sent.add(step)
                    elif step in retained and step not in plain_emitted:
                        plain_emitted.add(step)
                        if step in self._image_blocks:
                            new_content.append(self._image_blocks[step])
                            sent.add(step)
                elif (isinstance(block, dict)
                      and block.get("type") == "tool_result"
                      and isinstance(block.get("content"), list)):
                    new_content.append({**block, "content": materialize(block["content"])})
                else:
                    new_content.append(block)
            return new_content

        out = []
        for msg in self._messages:
            content = msg.get("content")
            if isinstance(content, list):
                out.append({**msg, "content": materialize(content)})
            else:
                out.append(dict(msg))
        self._last_sent_steps = sorted(sent)
        return out

    def _log_observation(self, step: int, meta: dict) -> None:
        if self.run_log:
            self.run_log.log_observation(
                step=step, image_ref=meta["image_ref"], ui_ref=meta["ui_ref"],
                screen=meta["screen"], image_ok=meta["image_ok"], ui_ok=meta["ui_ok"],
                image_attempts=meta["image_attempts"], ui_attempts=meta["ui_attempts"],
                image_error=meta["image_error"], ui_error=meta["ui_error"],
                screen_fingerprint=meta.get("screen_fingerprint"),
            )

    # ---------- 模型调用 ----------

    def _call_model(self, messages: list):
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "medium"},
            system=[{
                "type": "text",
                "text": self._system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }],
            tools=self._tools,
            messages=messages,
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            return stream.get_final_message()

    # ---------- 摘要基线（固定长度文本蒸馏；额外调用计费计入 episode 成本） ----------

    SUMMARY_SYSTEM = "你是一个只输出摘要文本的操作记录摘要器。"
    SUMMARY_PROMPT = (
        "上面这些截图是手机操作过程中已从对话上下文丢弃的历史屏幕。"
        "请按标注的步号压缩成简短的视觉事实记录，供以后回忆：\n"
        "1. 只写图上可见事实：屏幕主题、文字（名称、编号、价格、提示语、按钮文案）、关键控件；\n"
        "2. 不臆测、不评价、不给建议；\n"
        "3. 总长度不超过 200 字。"
    )

    def _distill_evicted(self):
        """把「下一请求将看不到」且未蒸馏的帧压成文本记忆；失败留档不终止 episode。

        候选 = 本地帧 -（下一请求保留集 ∪ 钉住帧）- 已蒸馏。最新帧恒在保留集，
        不会被蒸馏（刚看到的不需要记忆化）。
        """
        keep = self._retained_steps() | self._retrieved_steps
        candidates = sorted(
            s for s in self._image_blocks
            if s not in keep and s not in self._summary_seen
        )
        if not candidates:
            return None
        content: list = []
        for s in candidates:
            content.append({"type": "text", "text": f"[第 {s} 步截图]"})
            content.append(self._image_blocks[s])
        content.append({"type": "text", "text": self.SUMMARY_PROMPT})
        ts_start = time.time()
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=SUMMARY_MAX_TOKENS,
                system=self.SUMMARY_SYSTEM,
                messages=[{"role": "user", "content": content}],
            ) as stream:
                response = stream.get_final_message()
            ts_end = time.time()
        except Exception as exc:  # noqa: BLE001 — 蒸馏失败不终止 episode
            if self.run_log:
                self.run_log.log_summary_call(
                    ts_start, time.time(), {}, candidates, 0,
                    error=f"{type(exc).__name__}: {exc}")
            return None
        self.usage.add(response.usage)
        text = "".join(
            b.text for b in response.content if getattr(b, "type", "") == "text"
        ).strip()
        truncated = str(response.stop_reason) == "max_tokens"
        if text and not truncated:
            # 空文本或 max_tokens 截断 = 蒸馏没产出可靠记忆（残缺文本实测丢关键
            # 细节：i06 首蒸馏 11 字丢码），不标已蒸馏、不注入，下次触发重试同批帧。
            self._summary_seen.update(candidates)
        if self.run_log:
            self.run_log.log_summary_call(
                ts_start, ts_end, _usage_raw(response.usage), candidates, len(text),
                summary_text=text or None, stop_reason=str(response.stop_reason))
        return (text, candidates) if text and not truncated else None

    # ---------- 主循环 ----------

    def run(self, task: str) -> "Usage":
        self._finished = None
        image, text, meta = self._perceive(0)
        if image is not None:
            self._image_blocks[0] = image
        content0 = []
        if image is not None:
            content0.append({"type": "_image_slot", "step": 0})
        content0.append({"type": "text", "text": f"任务：{task}\n\n{text}"})
        self._messages.append({"role": "user", "content": content0})
        self._log_observation(0, meta)
        if self.run_log:
            self.run_log.note_message("user", content0, "task")
        nudges = 0

        for step in range(1, self.max_steps + 1):
            self._step = step
            print(f"\n>>> 第 {step} 步")
            retained = self._retained_steps()
            messages = self._build_messages(retained)
            problems = validate_request(messages, self.run_log.contract) if self.run_log else []
            ts_start = time.time()
            response = self._call_model(messages)
            ts_end = time.time()
            self.usage.add(response.usage)
            if self.run_log:
                seq = self.run_log.log_call(
                    ts_start, ts_end, str(response.stop_reason),
                    _usage_raw(response.usage), list(self._last_sent_steps),
                )
                self.run_log.log_protocol_check(seq, problems)

            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(f"  >> {block.text.strip()[:400]}")
                elif block.type == "thinking":
                    thinking = (getattr(block, "thinking", "") or "").strip()
                    if thinking:
                        print(f"  .. {thinking.splitlines()[0][:120]}")

            self._messages.append({"role": "assistant", "content": response.content})
            if self.run_log:
                self.run_log.note_message("assistant", response.content, "assistant")

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
                    content_nudge = [{
                        "type": "text",
                        "text": "请继续调用工具完成任务；结束时必须调用 finish。",
                    }]
                    self._messages.append({"role": "user", "content": content_nudge})
                    if self.run_log:
                        self.run_log.note_message("user", content_nudge, "nudge")
                    continue
                print("  [!] 模型连续未调用工具，停止")
                self._finished = self._finished or ("failed", "模型停止调用工具")
                break

            self._last_actions_expect = []
            results = []
            for tu in tool_uses:
                args = {
                    k: (int(v) if k in _INT_ARGS else v)
                    for k, v in dict(tu.input).items()
                }
                arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
                print(f"  -> {tu.name}({arg_str[:120]})")
                self._last_gate = {}
                self._result_blocks = []
                out, is_err = self._execute(tu.name, args)
                expect = "change" if tu.name in _EXPECT_CHANGE else "neutral"
                self._last_actions_expect.append(expect)
                if self.run_log:
                    self.run_log.log_action(
                        step, tu.id, tu.name, args, out, is_err, expect,
                        dict(self._last_gate),
                    )
                print(f"    {'X' if is_err else 'OK'} {out[:200]}")
                content = [{"type": "text", "text": out}]
                if not is_err:
                    content.extend(self._result_blocks)  # review_screenshot 回看帧
                result = {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": content,
                }
                if is_err:
                    result["is_error"] = True
                results.append(result)

            # 动作之后重新感知，附在最后一个 tool_result 上
            image, text, meta = self._perceive(step)
            if image is not None:
                self._image_blocks[step] = image
            self._log_observation(step, meta)
            # 先校验再蒸馏：未解决检查锚定的帧进保留集，不得进摘要候选
            self._verify_turn_effect(step)
            last = results[-1]
            if image is not None:
                last["content"].append({"type": "_image_slot", "step": step})
            last["content"].append({"type": "text", "text": text})
            if self.summarize_every and not self._finished and step % self.summarize_every == 0:
                distilled = self._distill_evicted()
                if distilled:
                    summary_text, src_steps = distilled
                    last["content"].append({
                        "type": "text",
                        "text": (
                            f"[视觉记忆摘要（蒸馏自已丢弃的第 "
                            f"{'、'.join(map(str, src_steps))} 步截图）]\n{summary_text}"
                        ),
                    })
            self._messages.append({"role": "user", "content": results})
            if self.run_log:
                self.run_log.note_message(
                    "user", results, "tool_results",
                    tool_result_ids=[r["tool_use_id"] for r in results],
                )

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
