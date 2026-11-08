#!/usr/bin/env python3
"""research-env 确定性验收脚本（验收 B）：不消耗模型，纯 adb 驱动。

覆盖：五种机制故障（delay/noop/popup/confusion/promo）、每类正常对照、参数化、
复位探针（post_reset_clean）、独立成功检查（success 只看内部状态 + expected_ids）。

用法：
    python research/env_smoke.py            # 全量
    python research/env_smoke.py delay      # 单机制

机制驱动方式：uiautomator dump 找 contentDescription（= 动作名）节点，点击其中心；
状态判定：adb shell run-as com.lifeagent.researchenv cat files/state.json。
"""
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from env_control import PKG, adb as _adb, broadcast_reset as _broadcast_reset
from env_control import open_app as _open_app, read_state as _read_state

SERIAL = sys.argv[2] if len(sys.argv) > 2 else "emulator-5554"


def adb(*args, check=True):
    return _adb(SERIAL, *args, check=check)


def broadcast_reset(**cfg):
    _broadcast_reset(SERIAL, **cfg)


def read_state():
    return _read_state(SERIAL)


def dump_nodes():
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    xml = adb("shell", "cat", "/sdcard/ui.xml")
    root = ET.fromstring(xml)
    return [n for n in root.iter("node")]


def find_node(cd, text=None):
    for n in dump_nodes():
        if n.get("content-desc") == cd and (text is None or n.get("text") == text):
            return n
    return None


def tap(cd, text=None, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        n = find_node(cd, text)
        if n is not None:
            b = n.get("bounds")  # [x1,y1][x2,y2]
            inner = b.split("][")
            x1, y1 = map(int, inner[0].lstrip("[").split(","))
            x2, y2 = map(int, inner[1].rstrip("]").split(","))
            adb("shell", "input", "tap", (x1 + x2) // 2, (y1 + y2) // 2)
            time.sleep(0.8)
            return True
        time.sleep(0.5)
    raise AssertionError(f"node not found: content-desc={cd} text={text}")


def assert_state(cond, msg, state=None):
    if not cond:
        state = state or read_state()
        raise AssertionError(f"{msg}\nstate={json.dumps(state, ensure_ascii=False)[:800]}")


def open_app():
    _open_app(SERIAL)


# ---------- 各机制场景 ----------

def scenario_normal():
    """正常对照：无故障，加购 → 提交 → success。"""
    broadcast_reset(episode="n1", task_seed=1, fault="none", expected_ids=["p1"])
    open_app()
    tap("open:p1")
    tap("add")
    tap("open_cart")
    tap("checkout")
    tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "submitted", "正常对照应提交成功", s)
    assert_state(s["success"] is True, "正常对照 success 应为 true", s)
    assert_state(s["submitted_ids"] == ["p1"], "提交集合应等于 expected", s)
    print("  [ok] normal")


def scenario_delay(ms=3000):
    """延迟完成：提交后 pending，delay 后才成功。"""
    broadcast_reset(episode="d1", task_seed=1, fault="delay", delay_ms=ms, expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart"); tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "submitting", "延迟中应为 submitting", s)
    assert_state(s["pending_timers"] == 1, "应有 1 个挂起定时器", s)
    assert_state(s["counters"]["delay"]["triggered"] == 1, "delay 应触发", s)
    time.sleep(ms / 1000 + 1.5)
    s = read_state()
    assert_state(s["order_status"] == "submitted", "延迟后应提交成功", s)
    assert_state(s["success"] is True, "延迟后 success 应为 true", s)
    assert_state(s["counters"]["delay"]["recovered"] == 1, "触发后完成应计 recovered", s)
    print("  [ok] delay")

    # 正常对照（delay_ms=0）
    broadcast_reset(episode="d0", task_seed=1, fault="delay", delay_ms=0, expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart"); tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "submitted" and s["pending_timers"] == 0,
                 "delay_ms=0 应立即完成", s)
    assert_state(s["counters"]["delay"]["triggered"] == 0, "delay_ms=0 不得计触发", s)
    print("  [ok] delay(对照)")


def scenario_noop(n=2):
    """动作无效果：前 n 次提交被业务层丢弃（received=true, effect=false）。"""
    broadcast_reset(episode="x1", task_seed=1, fault="noop", noop_n=n, expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart"); tap("checkout")
    for i in range(n):
        tap("submit")
        s = read_state()
        assert_state(s["order_status"] == "none", f"第{i+1}次提交应无效果", s)
        assert_state(s["noop_consumed"] == i + 1, f"noop_consumed 应为 {i+1}", s)
    tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "submitted", "触发后再次提交应成功", s)
    assert_state(s["success"] is True, "success 应为 true", s)
    assert_state(s["counters"]["noop"]["triggered"] == n, f"noop triggered 应为 {n}", s)
    assert_state(s["counters"]["noop"]["recovered"] == 1, "触发后完成应计 recovered", s)
    # 事件层证明「动作已被应用接收」：events.jsonl 中有 received=true/effect=false
    ev = adb("shell", "run-as", PKG, "cat", "files/events.jsonl")
    assert any('"received":true' in ln and '"effect":false' in ln for ln in ev.splitlines()), \
        "events 应记录 received=true effect=false（≠手势投递失败）"
    print("  [ok] noop")

    # 正常对照
    broadcast_reset(episode="x0", task_seed=1, fault="none", expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart"); tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "submitted" and s["noop_consumed"] == 0, "对照应一次成功", s)
    print("  [ok] noop(对照)")


def scenario_popup():
    """意外弹窗：进清单页弹模态通知，须显式点「知道了」才能继续。"""
    broadcast_reset(episode="p1", task_seed=1, fault="popup", popup_at="enter_cart",
                    expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart")
    s = read_state()
    assert_state(s["popup_showing"] is True, "进入清单页应弹窗", s)
    assert_state(s["counters"]["popup"]["triggered"] == 1, "popup 应触发", s)
    tap("dismiss_popup", timeout=5)
    s = read_state()
    assert_state(s["popup_showing"] is False, "点知道了后弹窗应消失", s)
    tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["success"] is True, "弹窗恢复后应能完成任务", s)
    assert_state(s["counters"]["popup"]["recovered"] == 1, "触发后完成应计 recovered", s)
    print("  [ok] popup")

    # 正常对照
    broadcast_reset(episode="p0", task_seed=1, fault="none", expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart")
    s = read_state()
    assert_state(s["popup_shown"] is False, "对照不得弹窗", s)
    print("  [ok] popup(对照)")


def scenario_confusion():
    """相似画面不同内部状态：预置干扰项在进清单页翻转为无货；
    区分证据 = 控件树「已无货」文字（信息可得）；成功检查只认内部状态。"""
    broadcast_reset(episode="c1", task_seed=1, fault="confusion", confusion_at="enter_cart",
                    expected_ids=["p1"], initial_cart=["p2"])
    open_app()
    s = read_state()
    assert_state("p2" in s["cart_ids"], "初态应含预置干扰项 p2", s)
    tap("open:p1"); tap("add"); tap("open_cart")
    s = read_state()
    assert_state(s["confusion_flipped"] is True, "进清单页应翻转内部状态", s)
    p2 = next(i for i in s["items"] if i["id"] == "p2")
    assert_state(p2["in_stock"] is False, "p2 应为无货", s)
    # 区分证据在 UI 文字（uiautomator 可见）
    assert any(n.get("text") == "已无货" for n in dump_nodes()), "UI 应含「已无货」区分证据"
    tap("remove:p2")          # 按证据行动：移除无货项
    tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["submitted_ids"] == ["p1"], "只应提交目标项", s)
    assert_state(s["success"] is True, "success 应为 true", s)
    assert_state(s["counters"]["confusion"]["recovered"] == 1, "触发后完成应计 recovered", s)
    print("  [ok] confusion")

    # 正常对照：同样预置 p2，但不翻转 → p2 有货，提交两项都合法（expected 只在 p1 时 success=false 也验证判定独立）
    broadcast_reset(episode="c0", task_seed=1, fault="none",
                    expected_ids=["p1", "p2"], initial_cart=["p2"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart")
    s = read_state()
    assert_state(s["confusion_flipped"] is False, "对照不得翻转", s)
    tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["success"] is True, "对照 expected=[p1,p2] 应 success", s)
    print("  [ok] confusion(对照)")


def scenario_promo():
    """瞬时线索（旧证据依赖）：优惠码横幅只在复位首屏以像素闪现（控件树不含码文本），
    首个动作即过期；结算必须输入码，缺失/错误拒单，正确才提交。"""
    # ---- 正确码路径 + 确定性（同 fault_seed 同码） ----
    broadcast_reset(episode="m1", task_seed=1, fault="promo", fault_seed=7, expected_ids=["p1"])
    open_app()
    s = read_state()
    code = s["promo_code"]
    assert_state(s["promo_banner_active"] is True, "复位后横幅应处于显示态", s)
    assert_state(len(code) == 4, "优惠码应为 4 位", s)
    assert_state(s["code_entered"] == "", "复位后 code_entered 应为空", s)
    assert_state(s["post_reset_clean"] is True, "promo 复位探针应干净", s)
    # 图像独占证据契约：控件树不得出现码文本（文本历史恒保真，码入树则策略无区分）
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    xml = adb("shell", "cat", "/sdcard/ui.xml")
    assert_state(code not in xml, "控件树不得含优惠码文本（应只存在于像素）", s)
    # 同 fault_seed 同码（配对比较跨策略共享）
    broadcast_reset(episode="m1b", task_seed=1, fault="promo", fault_seed=7, expected_ids=["p1"])
    assert_state(read_state()["promo_code"] == code, "同 fault_seed 应得同优惠码")
    broadcast_reset(episode="m1c", task_seed=1, fault="promo", fault_seed=7, expected_ids=["p1"])
    open_app()
    # 首个动作即线索过期
    tap("open:p1")
    s = read_state()
    assert_state(s["promo_banner_active"] is False, "首个动作后横幅应过期", s)
    assert_state(s["counters"]["promo"]["triggered"] == 1, "线索过期应计触发", s)
    tap("add"); tap("open_cart"); tap("checkout")
    assert_state(find_node("code_field") is not None, "promo 结算页应有优惠码输入框", s)
    tap("code_field")
    adb("shell", "input", "text", code)
    s = read_state()
    assert_state(s["code_entered"] == code, "输入应同步到状态机 ground truth", s)
    tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "submitted", "正确码应提交成功", s)
    assert_state(s["success"] is True, "success 应为 true", s)
    assert_state(s["counters"]["promo"]["recovered"] == 1, "触发后完成应计 recovered", s)
    print("  [ok] promo")

    # ---- 缺失/错码拒单路径 ----
    broadcast_reset(episode="m2", task_seed=1, fault="promo", fault_seed=7, expected_ids=["p1"])
    open_app()
    code = read_state()["promo_code"]
    tap("open:p1"); tap("add"); tap("open_cart"); tap("checkout")
    tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "none", "无码提交应被拒", s)
    wrong = ("Z" if code[0] != "Z" else "Y") + code[1:]
    tap("code_field")
    adb("shell", "input", "text", wrong)
    tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "none", "错码提交应被拒", s)
    ev = adb("shell", "run-as", PKG, "cat", "files/events.jsonl")
    assert '"result":"rejected_code_missing"' in ev, "事件应记录缺码拒绝"
    assert '"result":"rejected_code_wrong"' in ev, "事件应记录错码拒绝"
    assert s["counters"]["promo"]["triggered"] == 1, "拒单不重复计触发"
    print("  [ok] promo(拒单)")

    # ---- 对照：fault=none 无输入框、直接成功 ----
    broadcast_reset(episode="m0", task_seed=1, fault="none", expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart"); tap("checkout")
    assert_state(find_node("code_field") is None, "对照不得有优惠码输入框")
    tap("submit")
    s = read_state()
    assert_state(s["success"] is True, "对照应直接成功", s)
    assert_state(s["promo_banner_active"] is False and s["promo_code"] == "",
                 "对照不得武装 promo", s)
    print("  [ok] promo(对照)")

    # ---- 复位探针覆盖 promo 残留 ----
    broadcast_reset(episode="m3", task_seed=1, fault="none", expected_ids=["p1"])
    s = read_state()
    assert_state(s["post_reset_clean"] is True, "promo 跑完后复位应干净", s)
    print("  [ok] promo(复位)")


def scenario_reset_probe():
    """复位探针：跑到 delay 中途 reset → post_reset_clean 且挂起定时器清零。"""
    broadcast_reset(episode="r1", task_seed=2, fault="delay", delay_ms=8000,
                    expected_ids=["p1"])
    open_app()
    tap("open:p1"); tap("add"); tap("open_cart"); tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["pending_timers"] == 1, "应有挂起定时器", s)
    broadcast_reset(episode="r2", task_seed=2, fault="none", expected_ids=["p1"])
    s = read_state()
    assert_state(s["post_reset_clean"] is True, "复位探针应确认干净", s)
    assert_state(s["pending_timers"] == 0, "复位须清空挂起定时器", s)
    assert_state(s["order_status"] == "none", "复位须清空订单状态", s)
    assert_state(s["screen"] == "list", "复位须回到列表页", s)
    # 同 seed 同初态
    items_a = s["items"]
    broadcast_reset(episode="r3", task_seed=2, fault="none", expected_ids=["p1"])
    s2 = read_state()
    assert_state(s2["items"] == items_a, "同 seed 应得同商品列表", s2)
    print("  [ok] reset-probe")


def scenario_success_independent():
    """独立成功检查：流程走完但提交集合 ≠ expected → success=false（不认自报）。"""
    broadcast_reset(episode="s1", task_seed=1, fault="none", expected_ids=["p1"])
    open_app()
    tap("open:p2"); tap("add")     # 故意提交错误目标
    tap("open_cart"); tap("checkout"); tap("submit")
    s = read_state()
    assert_state(s["order_status"] == "submitted", "流程本身应走完", s)
    assert_state(s["success"] is False, "提交 p2 ≠ expected p1 必须 success=false", s)
    print("  [ok] success-independent")


SCENARIOS = {
    "normal": scenario_normal,
    "delay": scenario_delay,
    "noop": scenario_noop,
    "popup": scenario_popup,
    "confusion": scenario_confusion,
    "promo": scenario_promo,
    "reset": scenario_reset_probe,
    "success": scenario_success_independent,
}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only and only not in SCENARIOS:
        print(f"unknown scenario {only!r}; choose from {list(SCENARIOS)}")
        sys.exit(2)
    names = [only] if only else list(SCENARIOS)
    failed = []
    for name in names:
        print(f"== {name} ==")
        try:
            SCENARIOS[name]()
        except Exception as e:  # noqa: BLE001 — 验收脚本逐项报错不中断
            failed.append((name, e))
            print(f"  [FAIL] {name}: {e}")
    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(names)}: {[n for n, _ in failed]}")
        sys.exit(1)
    print(f"ALL {len(names)} SCENARIOS PASSED")


if __name__ == "__main__":
    main()
