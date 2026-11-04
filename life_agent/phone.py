"""ADB 手机控制层：截图、控件树解析、点击/滑动/输入、启动应用。

本层只负责和设备对话；对界面含义的解释与操作策略放在 agent 层。
"""

from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

UI_DUMP_PATH = "/sdcard/life_agent_ui.xml"

KEYCODES = {
    "home": 3,
    "back": 4,
    "recents": 187,
    "enter": 66,
    "search": 84,
    "del": 67,
    "paste": 279,
    "tab": 61,
}

ADBKEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"


class PhoneError(RuntimeError):
    """设备侧操作失败，携带可直接展示给用户的原因。"""


def sh_quote(text: str) -> str:
    """用单引号包裹，供设备端 shell 安全解析。"""
    return "'" + text.replace("'", "'\\''") + "'"


@dataclass
class UINode:
    text: str
    desc: str
    rid: str
    cls: str
    clickable: bool
    bounds: tuple[int, int, int, int]  # left, top, right, bottom

    @property
    def label(self) -> str:
        if self.text:
            return self.text
        if self.desc:
            return self.desc
        return self.rid.split("/")[-1] if self.rid else ""

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return ((left + right) // 2, (top + bottom) // 2)

    def contains(self, x: int, y: int) -> bool:
        left, top, right, bottom = self.bounds
        return left <= x <= right and top <= y <= bottom


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_ui(xml: str) -> list[UINode]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise PhoneError(f"控件树解析失败: {exc}") from exc
    nodes: list[UINode] = []
    for el in root.iter("node"):
        m = _BOUNDS_RE.match(el.get("bounds", ""))
        if not m:
            continue
        nodes.append(
            UINode(
                text=el.get("text", "").strip(),
                desc=el.get("content-desc", "").strip(),
                rid=el.get("resource-id", "").strip(),
                cls=el.get("class", "").strip(),
                clickable=el.get("clickable") == "true",
                bounds=tuple(int(g) for g in m.groups()),
            )
        )
    return nodes


def compact_ui(nodes: list[UINode], max_lines: int = 150) -> str:
    """输出紧凑控件清单：保留有文本标签或可点击的节点，行数封顶。

    仅有 resource-id 的不可点击容器（如全屏布局）对决策没有帮助，直接丢弃。
    """
    interesting = [n for n in nodes if n.text or n.desc or n.clickable]
    lines = []
    for i, n in enumerate(interesting):
        kind = "click" if n.clickable else "view"
        lines.append(f'#{i} "{n.label}" {kind} {list(n.bounds)}')
    if len(lines) > max_lines:
        head = lines[: max_lines - 2]
        head.append(f"...(共 {len(lines)} 项，超出部分已省略)")
        lines = head
    return "\n".join(lines) if lines else "（无带标签控件，界面可能是自绘内容，请以截图为准）"


def configure_console() -> None:
    """Windows 控制台默认 GBK，遇到 emoji 等字符会抛 UnicodeEncodeError。"""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


class Phone:
    def __init__(self, serial: str | None = None, adb: str = "adb"):
        self.adb = adb
        self.serial = serial or self._detect_serial()

    # ---- 基础通道 ----

    def _run(self, *args: str, timeout: float = 25.0) -> bytes:
        try:
            proc = subprocess.run(
                [self.adb, "-s", self.serial, *args],
                capture_output=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise PhoneError("找不到 adb，请安装 Android platform-tools 并加入 PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise PhoneError(f"adb 超时: {' '.join(args)}") from exc
        return proc.stdout

    def _shell(self, command: str, timeout: float = 25.0) -> str:
        return self._run("shell", command, timeout=timeout).decode("utf-8", "replace")

    def _detect_serial(self) -> str:
        try:
            out = (
                subprocess.run([self.adb, "devices"], capture_output=True, timeout=10)
                .stdout.decode("utf-8", "replace")
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise PhoneError("adb 不可用，请确认 platform-tools 已安装") from exc
        states = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                states.append((parts[0], parts[1]))
        for serial, state in states:
            if state == "device":
                return serial
        if any(state == "unauthorized" for _, state in states):
            raise PhoneError("设备未授权：请在手机弹窗上允许 USB 调试授权后重试")
        raise PhoneError("未检测到已连接的安卓设备：请用 USB 连接手机并开启 USB 调试")

    # ---- 感知 ----

    def screenshot_png(self) -> bytes:
        data = self._run("exec-out", "screencap", "-p")
        if not data.startswith(b"\x89PNG"):
            raise PhoneError("截图失败（输出不是 PNG）")
        return data

    def dump_ui(self) -> list[UINode]:
        """获取当前界面控件树。动画中会失败，内部重试 3 次。"""
        last = ""
        for _ in range(3):
            self._shell(f"uiautomator dump {UI_DUMP_PATH}")
            xml = self._run("exec-out", "cat", UI_DUMP_PATH).decode("utf-8", "replace")
            if "<hierarchy" in xml:
                return parse_ui(xml)
            last = xml.strip()[:200]
            time.sleep(0.6)
        raise PhoneError(f"uiautomator dump 失败（界面可能正在动画中）: {last}")

    def screen_size(self) -> tuple[int, int]:
        out = self._shell("wm size")
        m = re.search(r"(\d+)x(\d+)", out)
        if not m:
            raise PhoneError(f"无法解析屏幕尺寸: {out.strip()[:80]}")
        return int(m.group(1)), int(m.group(2))

    def current_activity(self) -> str:
        out = self._shell("dumpsys activity activities")
        m = re.search(r"ResumedActivity[^\n]*?u\d+\s+(\S+)", out)
        return m.group(1).rstrip("}") if m else "unknown"

    # ---- 动作 ----

    def tap(self, x: int, y: int) -> None:
        self._shell(f"input tap {int(x)} {int(y)}")

    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
        self._shell(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(duration_ms)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
        self._shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")

    def press(self, key: str) -> None:
        code = KEYCODES.get(key)
        if code is None:
            raise PhoneError(f"未知按键: {key}，可用: {sorted(KEYCODES)}")
        self._shell(f"input keyevent {code}")

    def type_text(self, text: str) -> str:
        """向当前聚焦的输入框输入文字，返回实际使用的输入方式。

        系统 `input text` 只支持 ASCII；中文走 ADBKeyboard 广播（需先安装并启用），
        输入完成后自动切回用户原来的输入法。否则抛出 PhoneError，由上层请用户手动输入。
        """
        if all(ord(ch) < 128 for ch in text):
            self._shell(f"input text {sh_quote(text)}")
            return "input text (ASCII)"
        if not self._ime_enabled(ADBKEYBOARD_IME):
            raise PhoneError(
                "无法通过 ADB 输入中文（input text 仅支持 ASCII，ADBKeyboard 未启用）。"
                "安装并启用 ADBKeyboard 后重试，或请用户手动输入。"
            )
        prev = self._current_ime()
        self._set_ime(ADBKEYBOARD_IME)
        try:
            time.sleep(0.8)  # 等输入法服务绑定完成，否则广播会被丢弃
            self._shell(f"am broadcast -a ADB_INPUT_TEXT --es msg {sh_quote(text)}")
            time.sleep(1.0)  # 文字落地有延迟，等它提交进输入框
        finally:
            if prev and prev != ADBKEYBOARD_IME:
                self._set_ime(prev)
        return "ADBKeyboard"

    def clear_text(self) -> str:
        """清空当前聚焦输入框：优先全选+删除，失败则逐字退格。"""
        self._shell("input keycombination 113 29")  # CTRL + A
        self._shell("input keyevent 67")  # DEL
        return "全选删除"

    def launch(self, package: str) -> str:
        out = self._shell(
            f"monkey -p {sh_quote(package)} -c android.intent.category.LAUNCHER 1"
        )
        if "No activities found" in out or "monkey aborted" in out:
            raise PhoneError(f"启动失败: {package}（{out.strip()[:120]}）")
        time.sleep(1.5)  # 等待冷启动
        return package

    def _current_ime(self) -> str:
        return self._shell("settings get secure default_input_method").strip()

    def _ime_enabled(self, ime: str) -> bool:
        return ime in self._shell("ime list -s")

    def _set_ime(self, ime: str) -> None:
        self._shell(f"ime set {sh_quote(ime)}")
