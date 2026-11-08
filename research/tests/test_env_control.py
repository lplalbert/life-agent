"""env_control 复位对账与 app 就绪等待语义（2026-11-09 感知竞态修复）。

事故：t1r4s2g1 首屏截到桌面（app 冷启动慢于固定 sleep），画布横幅从未入帧
即被首动作过期——证据不可感知，长任务实验被环境伪影污染。修复落在生产
边界：复位「完成」=state.json episode_id 对账；app「可观测」=前台焦点确认。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research import env_control  # noqa: E402


class _FakeClock:
    """sleep 推进时间、time 只读——等待循环不真睡眠。"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.1)


def _fake_adb_sequence(outs):
    """adb mock：dumpsys 调用返回序列里的下一项，其余返回空串。"""
    it = iter(outs)

    def fake(serial, *args, check=True):
        if any("dumpsys" in str(a) for a in args):
            return next(it)
        return ""

    return fake


class TestResetAck(unittest.TestCase):
    def test_waits_until_state_episode_matches(self):
        clock = _FakeClock()
        reads = [{"episode_id": "old"}, {"episode_id": "old"},
                 {"episode_id": "ep1", "post_reset_clean": True}]
        with mock.patch.object(env_control, "time", clock), \
                mock.patch.object(env_control, "read_state",
                                  side_effect=reads) as m_read, \
                mock.patch.object(env_control, "adb", return_value=""):
            env_control.broadcast_reset(episode="ep1", fault="promo")
        self.assertEqual(m_read.call_count, 3)

    def test_raises_when_reset_never_takes(self):
        clock = _FakeClock()
        with mock.patch.object(env_control, "time", clock), \
                mock.patch.object(env_control, "read_state",
                                  return_value={"episode_id": "zzz"}), \
                mock.patch.object(env_control, "adb", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "复位未生效"):
                env_control.broadcast_reset(episode="ep1")

    def test_without_episode_keeps_fixed_wait(self):
        clock = _FakeClock()
        with mock.patch.object(env_control, "time", clock), \
                mock.patch.object(env_control, "read_state") as m_read, \
                mock.patch.object(env_control, "adb", return_value=""):
            env_control.broadcast_reset(fault="promo")
        m_read.assert_not_called()
        self.assertAlmostEqual(clock.t, 0.6)


class TestOpenAppReady(unittest.TestCase):
    def test_waits_for_foreground_focus(self):
        clock = _FakeClock()
        with mock.patch.object(env_control, "time", clock), \
                mock.patch.object(env_control, "adb", _fake_adb_sequence([
                    "mCurrentFocus=Window{launcher}",
                    "mCurrentFocus=Window{c1 u0 "
                    "com.lifeagent.researchenv/.MainActivity}",
                ])):
            env_control.open_app()  # 焦点到手 + 渲染安定后返回，不抛即过
        self.assertGreaterEqual(clock.t, 0.5)

    def test_raises_when_focus_never_arrives(self):
        clock = _FakeClock()
        with mock.patch.object(env_control, "time", clock), \
                mock.patch.object(env_control, "adb", _fake_adb_sequence(
                    ["mCurrentFocus=Window{launcher}"] * 100)):
            with self.assertRaisesRegex(RuntimeError, "前台焦点"):
                env_control.open_app()


if __name__ == "__main__":
    unittest.main()
