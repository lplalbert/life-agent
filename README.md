# life-agent

自然语言下指令，自动操作安卓手机上的生活服务 App（美团外卖等）：
选店 → 选菜 → 购物车 → 下单到**待支付页停下**，最终支付由你本人在手机上完成。

工作原理：ADB 截图（视觉）+ uiautomator 控件树（文本/坐标）→ 大模型决策下一步动作
（tap/swipe/type_text…）→ 执行 → 观察结果，循环直到任务完成或需要人工。

## 前提

- 手机开启 USB 调试并用 USB 连接（`adb devices` 显示 `device`）
- Python 3.10+
- 模型凭证：在项目根目录建 `.env`（已 gitignore，凭证不进仓库），默认走小米 MiMo 的 Anthropic 兼容端点：
  ```ini
  ANTHROPIC_BASE_URL=https://token-plan-cn.xiaomimimo.com/anthropic
  ANTHROPIC_AUTH_TOKEN=tp-你的token
  ANTHROPIC_MODEL=mimo-v2.6-pro
  ```
  也可换回 Anthropic 官方 API：不设 BASE_URL/AUTH_TOKEN，改设 `ANTHROPIC_API_KEY`，并用 `--model claude-opus-5-5`。

## 安装与使用

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

# 1. 先验证手机链路（不调用模型，不花钱）
.venv/Scripts/python -m life_agent --dry-run

# 2. 单个任务
.venv/Scripts/python -m life_agent "帮我点一份麦当劳板烧鸡腿堡套餐，到下单前停下"

# 3. 交互模式
.venv/Scripts/python -m life_agent
```

常用参数：`--max-steps 30`（单任务最大步数）、`--serial`（多台设备时指定）、`--model`。

## 安全设计

- **支付永远人工**：涉及「提交订单/支付/结算/开通」类按钮，直接点击会被安全闸门拒绝；
  Agent 必须走 `confirm_and_tap` 在终端向你确认（展示按钮、金额说明、截图证据）。
  即使确认提交订单，到收银台/支付页 Agent 就停下（`finish(need_human)`），
  指纹/密码/面容由你本人在手机上完成。
- **验证码交还人工**：滑块、点选、短信验证 → `ask_human`。
- **全程留证**：每一步截图与控件清单保存在 `runs/<时间戳>/`。
- 每次任务结束打印 token 用量与估算成本。

## 中文输入（可选）

系统 `input text` 只支持 ASCII，中文输入需要 [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard)
输入法（开源、约 20KB）作为桥接：

```bash
# Android 16 用带 A16 修复的 v2.5-dev 构建
adb install keyboardservice-debug.apk
adb shell ime enable com.android.adbkeyboard/.AdbIME
```

Agent 在输入中文时会临时切到 ADBKeyboard、输入完成立即切回你原来的输入法，不影响日常打字。
荣耀等国产 ROM 首次安装会弹「未知来源 / ICP 核准」确认框，需在手机上点「继续 → 知道了」。
未安装时中文输入会报可读错误，Agent 会改为拼音搜索/请你手动输入。

## 已知限制

- **不适合抢瞬时放票**（12306 放票秒杀）：ADB 一步操作数百毫秒起步，抢不过专业工具；
  12306 官方候补本身更稳。本工具适合常规下单、查票监控类任务。
- 平台有行为风控，自动化可能触发验证码或限流；仅建议低频、本人账号使用。
- 登录态需人工维护：首次登录/掉线后扫码短信验证由你完成。
- 自绘控件（Flutter/游戏化界面）在控件清单里可能只有坐标没有文字，此时以截图为准。

## Android 版（手机独立运行，无需电脑）

`android/` 是原生 Android App：Agent 循环内置，通过无障碍服务读控件树、截图、注入手势。
覆盖全生活服务：外卖、买菜、打车、订票订酒店、网购、充值缴费、电影娱乐等，支持美团/饿了么/
淘宝/京东/拼多多/支付宝/微信/滴滴/12306/携程等主流 App（包名不详时可回桌面从图标启动）。

**付款能力**：任务一路走到付款完成，不停在购物车/订单确认页。系统级硬限制：
指纹/密码/面容验证是安卓安全输入框，无障碍服务无法代按——点「确认支付」后若弹验证，
App 立即提示你按一下即完成；开通了小额免密的订单可全程自动付掉。

**安装（QQ 传 APK 或 adb install 均可）：**

1. 安装 `android/app/build/outputs/apk/debug/app-debug.apk`（荣耀弹「未知来源」→ 继续 → 知道了）
2. 打开 LifeAgent → 点「开启无障碍权限」→ 系统设置里打开 LifeAgent 开关 → 返回 App
3. 输入任务（或点「外卖/打车/买菜/充值」模板快捷填入）→ 点「开始任务」。
   手机用 4G/5G 即可，不依赖电脑/Wi-Fi
4. 荣耀手机首次会弹「LifeAgent 想要打开美团」跳转提醒：勾「今日不再提醒」后点允许，
   或在 设置 → 隐私和安全 → 其他隐私和安全功能 关闭「应用自动跳转提醒」

**安全设计**：消费按钮（提交订单/支付/结算/开通类）直接点击会被安全闸门拒绝，必须走
确认弹窗（想全自动可勾选「下单/支付自动确认」）；验证码弹窗交还人工；每步截图存 App
私有目录 `runs/`。

**编译**（开发者）：免安装工具链在 `toolchain/`（JDK17 + Android SDK + Gradle），
API 凭证写在 `android/local.properties`（不入 git，编译时注入 APK，别外传）。

## 架构

```
life_agent/
  phone.py   ADB 控制层：截图、控件树解析、点击/滑动/输入、启动 App
  agent.py   Agent 循环：感知 → Claude 工具调用 → 安全闸门 → 执行
  runlog.py  运行记录：run.jsonl 逐步留证 + token 用量四类分列
  cli.py     命令行入口（单任务 / 交互 / --dry-run）
```

## 仓库结构（研究平台）

- `android/research-env/` 研测商城：合成购物环境（五类机制故障、promo 瞬时线索、
  多轮订单账本、状态导出），主机经广播复位并收取 ground truth；
  界面文字不含任何故障标签、预期答案或路径提示。
- `research/` 实验平台：episode 矩阵运行器（pilot_runner/episode_runner）、
  上下文策略（full/recent/evidence/keyframe 及消融变体）、摘要蒸馏与历史取回、
  证据出处账本（provenance.py）、离线归因分析（flash_analysis）。
  测试：`python -m unittest discover -s research/tests`。
- `docs/` 研究计划与协议；执行记录见 `research/EXECUTION-*.md`。
- 本地不入库：`runs/`（截图与原始日志）、`research/results/`（生成产物）、
  `toolchain/`、`.venv/`、`android/local.properties`（真实凭证，勿外传）。
