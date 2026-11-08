#!/usr/bin/env python3
"""research-env 主机控制器原语：复位广播、ground truth 读取。

控制器与评估器专用；agent 代码不得导入本模块读取隐藏状态或故障参数。
ground truth 只在 episode 结束后（或复位探针时）由控制器读取。
"""
import json
import subprocess
import time

PKG = "com.lifeagent.researchenv"
DEFAULT_SERIAL = "emulator-5554"


def adb(serial, *args, check=True):
    # adb 输出 UTF-8；本机 locale 为 GBK，须显式指定编码，否则解码抛错
    r = subprocess.run(["adb", "-s", serial] + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args} failed: {r.stderr}")
    return r.stdout


def broadcast_reset(serial=DEFAULT_SERIAL, **cfg):
    """平铺 extras 发复位广播；expected_ids 传 list，内部转逗号分隔。"""
    args = ["shell", "am", "broadcast", "-a", f"{PKG}.CONTROL",
            "-n", f"{PKG}/.ControlReceiver", "--es", "cmd", "reset"]
    for k, v in cfg.items():
        if v is None:
            continue
        if isinstance(v, list):
            if not v:
                continue  # 空列表与缺席同义；--es 传空串会在 adb shell 层丢失致解析错位
            v = ",".join(v)
        if isinstance(v, str) and v == "":
            continue
        args += ["--es", k, str(v)]
    adb(serial, *args)
    time.sleep(0.6)


def read_state(serial=DEFAULT_SERIAL):
    """读应用内部状态（含 success、counters、post_reset_clean）。"""
    out = adb(serial, "shell", "run-as", PKG, "cat", "files/state.json")
    return json.loads(out.strip().replace("\r", ""))


def read_events(serial=DEFAULT_SERIAL):
    """读应用侧事件日志（每 episode 截断），返回事件 dict 列表。"""
    out = adb(serial, "shell", "run-as", PKG, "cat", "files/events.jsonl")
    events = []
    for line in out.replace("\r", "").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def open_app(serial=DEFAULT_SERIAL):
    adb(serial, "shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    time.sleep(1.2)
