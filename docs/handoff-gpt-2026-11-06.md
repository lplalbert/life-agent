# life-agent 交接说明（2026-11-06）

## 1. 项目目标、技术栈与关键文件

**目标**：手机 GUI Agent——自然语言指令自动操作生活服务 App（外卖/网购/充值/打车/订票）。产品定位（2026-11-06 定调）：培养一个「了解手机 App 各种操作、能处理突发情况」的通用 agent，不是逐 App 硬编码；App 种类繁多且常更新，遇到没见过的界面/弹窗要能自己应对，并靠自学习沉淀经验。

**技术栈**：原生 Android App（Kotlin，AccessibilityService 驱动）+ 主机侧 bash 脚本（adb 编排）。Provider 固定小米 MiMo Anthropic 兼容端点（`ANTHROPIC_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic`，模型 mimo-v2.6-pro）；凭证在 `.env` / `android/local.properties`（已 gitignore）。

**项目根**：`C:\Users\24976\Desktop\code\life-agent`（独立项目，与 REA 仓库无关）。设备：荣耀 PTP-AN00，MagicOS/Android 16，1264x2800，serial `A4FKVB4A25000146`。

**关键文件**（`android/app/src/main/java/com/lifeagent/app/`）：
- `AgentLoop.kt`——核心循环（感知→模型→安全闸门→执行）；SYSTEM_PROMPT、DANGER_KEYWORDS、confirm_and_tap、token 统计
- `AgentService.kt`——无障碍实现：截图/控件树/dispatchGesture/tap/type_text/nodeAt
- `MainActivity.kt`——控制台 + intent 任务下发（singleTop）、WakeLock 亮屏、运行日志落盘
- `PromptActivity.kt`——敏感确认 / ask_human 弹窗（suspend 等待，返回键=拒绝，兜底默认拒绝）
- `Experience.kt`——自学习：运行日志 → reflect 复盘 → `files/experience.md` → 注入 SYSTEM_PROMPT
- `ApiClient.kt`——Messages API 调用（prompt cache、thinking adaptive）
- 主机侧：`gate-operator.sh`（监印官，自动审批确认弹窗）、`matrix-driver.sh`（干跑矩阵驱动）
- 文档：`docs/agent-robustness-token-research.md`（鲁棒性/token 调研报告）

## 2. 已完成事项与重要设计决定

**已完成**：九 App 可达性探测；美团/淘宝全流程干跑实测（美团 24 步到「去结算」done；淘宝加购成功后息屏中断 need_human）；自学习闭环 + SYSTEM_PROMPT「突发情况处理」章节；干跑安全三层闸门；感知双通道解耦、亮屏 WakeLock、nodeAt 50px 半径回退、截图错误文案修正、监印官看门狗 + 幽灵 XML 修复、confirm_and_tap 落点竞态修复（最后两项代码已改、**未构建安装**）；调研报告。

**重要设计决定及原因**：
- **安全三层**：① tap 硬闸门（DANGER_KEYWORDS 标签匹配）→ ② confirm_and_tap 弹窗（监印官批）→ ③ gate-operator.sh 判收银台（看弹窗前 stepNN_ui.txt 有无支付要素：有→拒，无→下单提交放行）。原因：模型会裸 tap 敏感按钮（美团 5 次未拦），单层不够。
- **感知双通道独立降级**（截图与控件树分开重试，一挂一顶）：息屏全盲会让模型编故事死亡（美团事故），看得见障碍才可能自救。
- **confirm_and_tap 点「确认」后 delay(600) 再 tap**：PromptActivity.finish() 是异步的，立刻点被消失中的弹窗挡掉（「手势被系统取消」）——美团 step13、淘宝 step28 同签名可复现。
- **经验库=全文注入 SYSTEM_PROMPT**（ExpeL 路线），暂未做检索化；token 最大杠杆是历史图像裁剪（缓存读是大头），列入调研未实施。
- **息屏自愈靠主机侧看门狗**（gate-operator 每轮查 mWakefulness，灭了唤屏 + dismiss-keyguard）：MagicOS 下 App WakeLock 保不住屏（USB 掉线走电池 15s 必灭）。

## 3. 当前任务与未解决问题

**当前任务**（用户授权持续有效）：手机干跑测试矩阵——美团✅／淘宝半程⏸／拼多多／支付宝充值／滴滴／12306。每单走到收银台（确认支付页）即停，绝不真实付款；打车类连「呼叫」都不可触发。每次运行同时是经验库训练数据。

**待办顺序**：
1. 构建安装 fix 批 2（confirm_and_tap delay + 复盘日志落盘，代码已改未编译），装机后**必须重开无障碍**（覆盖安装会撤销授权）
2. nohup 脱链启动 `gate-operator.sh` + `matrix-driver.sh`（后台任务随会话退出已被杀 3 次）
3. 矩阵剩余：淘宝续跑（购物车有植护抽纸 ¥17.81）→ 拼多多 → 支付宝充值 → 滴滴（只到确认呼叫页）→ 12306
4. 后续：token 优化（历史图像裁剪、经验库检索化）

**未解决**：MagicOS 息屏物理根因（USB 不稳）只能看门狗兜底；confirm_and_tap 修复未经真机验证；clear_text 在美团搜索框失效；矩阵未完整跑过一轮。

## 4. 已尝试过的方法及结果

- **美团干跑**：必胜客 ¥14 到「去结算」done。中途模型编故事（谎称「用户拒绝已到测试边界」）→ 真因息屏三通道同灭 + perceive 全盲 → 修感知解耦/错误文案/亮屏锁。
- **淘宝干跑**：搜索抽纸→销量排序→植护大包气垫抽纸（悦色橙 L 码 12 包，补贴后 ¥17.81）加购成功→step28 confirm_and_tap 竞态失败→息屏连锁→need_human 精确交棒（货在购物车）。
- **监印官收银台判定**：按弹窗前 stepNN_ui.txt 支付要素（微信支付/支付宝/收银台/指纹…）区分「下单提交放行 vs 付款拒绝」。
- **文献调研**（`curl -x http://127.0.0.1:7892` 走 Semantic Scholar/arXiv API；WebSearch/WebFetch 当日不可用）：验证 10 篇经典（Reflexion/ExpeL/Voyager/AWM/AndroidWorld/Mobile-Agent-v2/AppAgent/UFO/SoM/LLMLingua）+ 2025-26 新文三层归类，映射见调研报告。
- **踩坑**：后台任务随会话退出被杀（须 nohup）；设备 toybox grep 不支持 `\|`（用 `grep -E` 或拉回 Windows 分析）；adb 传中文/空格须 `adb shell "命令 --es task '文本'"` 内嵌单引号；adb pull 须 `MSYS_NO_PATHCONV=1` + Windows 式目标路径。

## 5. 如何运行和验证

构建（Git Bash）：
```bash
cd /c/Users/24976/Desktop/code/life-agent/android
JAVA_HOME="$PWD/../toolchain/jdk/jdk17.0.21_0" "$PWD/../toolchain/gradle-8.5/bin/gradle" assembleDebug
```

安装 + 重开无障碍（锁屏/息屏下安装报 INSTALL_FAILED_ABORTED，先唤屏解锁）：
```bash
cd app/build/outputs/apk/debug
MSYS_NO_PATHCONV=1 adb -s A4FKVB4A25000146 install -r -t app-debug.apk
adb -s A4FKVB4A25000146 shell settings put secure enabled_accessibility_services com.lifeagent.app/com.lifeagent.app.AgentService
adb -s A4FKVB4A25000146 shell settings put secure accessibility_enabled 1
```

启动编排（须 nohup 脱链）：
```bash
cd /c/Users/24976/Desktop/code/life-agent
nohup bash gate-operator.sh > tmp/gate.log 2>&1 &
nohup bash matrix-driver.sh >/dev/null 2>&1 &    # 自写日志到 tmp/matrix-driver.log
```

单任务下发（干跑安全措辞见 matrix-driver.sh 内任务文本）：
```bash
adb shell "am start -n com.lifeagent.app/.MainActivity --es task '任务文本'"
```

验证：
- 运行记录 `/sdcard/Android/data/com.lifeagent.app/files/runs/<epoch_ms>/`：`log.txt`（终态 marker=`任务结果`）+ `stepNN.jpg` + `stepNN_ui.txt`
- 矩阵进度 `tail -f tmp/matrix-driver.log`；监印官决策 `tmp/gate.log`（APPROVE/DENY）
- 经验库 `runs/../experience.md`（每单复盘后增长）；token 用量看 log.txt 末行
- 网络调研走 `curl -x http://127.0.0.1:7892`（S2 API 并发会 429，串行调用）

## 6. 需要遵守的约束

1. **绝不真实付款**：干跑到收银台（确认支付页）即停，绝不点「确认支付/立即支付」等扣款按钮；下单提交（产生未付款订单）可放行。指纹/密码/面容是安卓安全输入框，无障碍永远无法代按。
2. **打车类绝不点「呼叫/叫车」**（真实派单司机）；滴滴只跑到确认呼叫页。
3. **token 是账单凭证**：只放环境变量/本地配置（.env、android/local.properties），绝不硬编码进提交的代码；APK 内含 token 只限自用，别外传。
4. 敏感按钮必须走 confirm_and_tap 弹窗闸门，不得绕过三层安全设计。
5. 长驻脚本必须 nohup 脱链，否则随会话退出被杀。
6. 中文交流；adb 任务文本避免单引号（传参限制）。
7. 运行日志/截图是训练与诊断证据，保留不清理。
