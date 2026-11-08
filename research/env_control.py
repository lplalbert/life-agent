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


def _wait_state_episode(serial, episode, timeout=10.0):
    """等复位「完成」：接收器处理完并回写 state.json（episode_id 对上）。"""
    deadline = time.time() + timeout
    st = {}
    while time.time() < deadline:
        try:
            st = read_state(serial)
        except Exception:  # noqa: BLE001 — 快照读失败按未就绪重试
            st = {}
        if st.get("episode_id") == episode:
            return st
        time.sleep(0.2)
    raise RuntimeError(
        f"复位未生效：state.json episode_id={st.get('episode_id')!r} ≠ {episode!r}")


def broadcast_reset(serial=DEFAULT_SERIAL, **cfg):
    """平铺 extras 发复位广播；expected_ids 传 list，内部转逗号分隔。

    复位「完成」= 接收器处理完并回写 state.json（episode_id 对上）后才返回——
    调用方拿到的是已复位环境，首屏线索不再被感知竞态错过（2026-11-09 t1r4s2g1
    首屏抓到桌面、横幅从未入帧的事故后加的对账）。未给 episode 时退回固定等待。
    """
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
    episode = cfg.get("episode")
    if episode:
        _wait_state_episode(serial, episode)
    else:
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
    """拉起 app 并等「可观测」：前台焦点确认 + 渲染安定后才返回。

    固定 sleep 在冷启动（进程被杀后首跑）不够——2026-11-09 t1r4s2g1 首屏
    截到桌面即此因。焦点轮询到手再短安定，首帧即 app 首屏。
    """
    adb(serial, "shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    deadline = time.time() + 10.0
    while time.time() < deadline:
        out = adb(serial, "shell", "dumpsys window | grep mCurrentFocus", check=False)
        if PKG in out:
            time.sleep(0.5)
            return
        time.sleep(0.3)
    raise RuntimeError(f"app 未取得前台焦点：{PKG}")
