# 研究执行记录：2026-11-06

本轮完成研究准备阶段，不代表论文实验完成或已证实方法有效。

## 已执行

- 读取交接文档、原调研、Android 核心实现、现有矩阵驱动；保留既有工作区修改。
- 核对 ICLR 2027 官方截止时间、ICML 2027 官方日期页、相关工作官方记录/摘要。未执行新的 S2 API 查询。
- 新增 docs/top-conference-plan-2026-11-06.md，包含主线、文献增量、12 周路线、180-episode pilot、统计与停止条件。
- 新增 research/context_policy.py：full/recent/evidence 三种图像引用选择；因果验证事件、明确图片预算与遗漏。
- 新增 research/audit_runs.py：只读盘点已有捕获与 SHA-256；结果保持任务成功、provider usage、费用未知。
- 执行 python -m unittest discover -s research/tests -v：7 项测试通过。初次受限运行有 1 项临时目录权限错误，具备目录权限后完整重跑通过。
- 执行本地捕获盘点和合成事件三策略演示；原始输出在忽略目录 research/results/，不含截图像素和控件原文。

## 观测与限制

本地仅发现一个包含 step 捕获的目录：runs/20261104-173828，24 个观察步，24 张图片，24 份 UI 文件，其中 18 种不同 UI 内容摘要；未发现该目录的 log.txt。
此为 11 月 4 日记录，不能认定为报告中 11 月 6 日的美团 24 步运行，也不能关联其 91.5K/961K usage。
在每观察一次就请求一次的假设下，完整图像历史累计引用 300 次图片，recent2 为 47 次。它们是文件引用计数，不是实测请求、token、账单、速度或恢复改进。
UI 摘要相同只说明文件字节相同，不能推断整个页面或应用内部状态相同。
合成例子中 recent2 选择 [2,3]，evidence2 选择 [0,3]，说明引用选择规则工作；不证明旧图对任务有益。

## 执行环境阻塞（11-06 状态，11-07 已部分解除，见下节）

- adb devices 未检测到设备。
- 本项目 toolchain/sdk 未包含 emulator/emulator.exe。
- 当前 Python 解释器未找到 android_world；未审计其他虚拟环境或系统位置。
- 尚无固定 AndroidWorld 版本、真实 pilot 任务映射、初始化/成功/清理器、运行 JSONL 与 provider 费用口径。

因此未构建/安装 APK，未运行真实业务矩阵、付费模型请求或 AndroidWorld rollout。
没有启动交接文档中的自动审批、唤屏看门狗和真实订单脚本。未测量 CPU/峰值内存或在线耗时，未宣称性能提升。

## 2026-11-07 环境攻坚记录

- 经用户手动执行 toolchain/enable-whpx.bat 并重启后，WHPX 启用成功：`emulator -accel-check` 报 `WHPX(10.0.19045) is installed and usable.`，HypervisorPresent=True。
- 模拟器 37.2.13（build 16740729）在本机两种模式均失败：
  - headless（`-gpu host` 与 swiftshader_indirect）均段错误 exit 139；minidump 实证为 qemu-system-x86_64-headless.exe 内部空指针读（0xc0000005，FAULTING MODULE 自身偏移 0x2664ad7），WinHvPlatform.dll/WinHvEmulation.dll 正常加载，非 WHPX/GPU 配置问题。
  - 带窗口模式不段错误但 vCPU 全冻结：watchdog 报 `detected a hanging thread 'QEMU2 CPU0..3 thread'` 与 main loop 无响应，CPU 时间近零增长。
  - `-cores 1` 单核假设被证伪：同样冻结（8 秒 CPU 仅 +0.016s）。
- 结论（源码/证据推断，非实测修复）：37.2.13 与本机（Xeon E5-2670 v2 + WHPX）不兼容，换版本而非调参数。
- 已按 repository2-3.xml 校验和（sha1 8ef10c10…）下载并换装 Emulator 37.3.8（build 16847572），旧版备份为 toolchain/sdk/emulator-37.2.13-broken-backup。
- **验收 A 达成（11-07）**：37.3.8 headless（`-accel on -gpu host -no-window`）引导至 `sys.boot_completed=1`，adb 可连、APK 可装。注意 37.3.8 headless 进程名为 `qemu-system-x86_64-headless`，按 `qemu-system-x86_64` 查进程会误判死亡。
- **验收 B 达成（11-07）**：research-env-debug.apk 安装后 `python research/env_smoke.py` 七场景全过（normal/delay/noop/popup/confusion/reset/success），含四机制故障对照、复位探针、独立成功检查，全程确定性脚本、零模型调用。
- 验收中修复两处（生产边界修复，非下游绕过）：
  1. env_smoke.py `adb()` 未显式 UTF-8 解码，本机 GBK locale 使 adb UTF-8 输出解码抛错 → 边界处显式 `encoding="utf-8"`。
  2. 弹窗「知道了」按钮（AlertDialog 标准按钮）无 contentDescription，违反本应用「可交互控件 contentDescription=动作名」契约 → MainActivity 给按钮设 `dismiss_popup`，脚本按动作名定位。
- 残余观察：控制台输出中文乱码仅是终端显示层（GBK console），不影响判定；未修复亦不影响验收。

## 2026-11-07 阶段 C 记录

架构决策：研究载体 = 主机侧 Python agent（`life_agent/`），因 AndroidWorld 是主机侧 Python 接口、pilot 需快速迭代；Android :app AgentLoop.kt 属产品赛道，仅做 §4.1 重试语义修正。

交付组件：
- `life_agent/runlog.py`：运行级 JSONL（run_header/call/action/protocol_check/observation/verification）；call 记 usage 原始字段、延迟、保留图像步；validate_request 在请求构造边界校验 tool_use/tool_result 配对与 thinking/文本保真（图像按策略缺席是唯一允许差异）。
- `life_agent/agent.py`：perceive 双通道独立降级（截图 3 次/控件树 2 次，失败才重试、成功即返）；`_image_slot` 图像槽机制——历史仅在 `_build_messages` 请求构造边界物化/丢弃；context_policy 三策略经 research/context_policy.select() 唯一权威选择；effect:step 效果校验（有 change 期望且指纹变→verified，未变→failed，neutral/通道缺失→pending，单回合一检查）；敏感闸门 SANDBOX_PACKAGES={com.lifeagent.researchenv} 虚构流程放行、真实/未知包 fail-closed；冻结经验注入系统提示并记 SHA-256。
- `research/episode_runner.py` + `research/env_control.py`：控制器路径复位→派任务（只给商品名，无故障词）→agent 运行→结束后才写 ground truth→独立评分（recovered = triggered>0 且 success）。控制器原语独立模块，agent 不得导入。

验证（全确定性、零模型费用）：`python -m unittest discover -s research/tests -t .` 25 项全过（18 新增 + 7 既有）。覆盖规划 §4 五项：perceive 成功后不重试、单通道降级状态、运行级 JSONL 原始 usage/延迟、历史图像仅请求边界处理且 thinking/文本/工具配对保真（按实际发送请求断言 full=[1,2,3] 图、recent=各 1 图、evidence 保留未解决检查证据帧）。`episode_runner --dry-reset` 验证控制器路径 dry_reset_ok，任务文本无泄漏。

验收中修复（生产边界修复）：
1. agent._build_messages 未递归 tool_result 嵌套内容，历史图像槽永不物化 → 递归处理嵌套槽（文本/thinking/工具块原样）。
2. episode_runner 空列表参数经 `--es key ""` 在 adb shell 层丢失致 am 解析错位 → env_control.broadcast_reset 跳过空列表/空串（与缺席同义）。
3. AgentLoop.kt perceive `repeat(3)/repeat(2)` 成功后仍重复采集 → 成功即 break（产品赛道，:app 重编译 BUILD SUCCESSFUL）。

限制：效果校验、策略保真均以假件（FakePhone/FakeClient）验证，真实 MiMo 端点 usage 字段形状、图像媒体类型兼容性、单 episode 真实费用均未验证；真实模型行为未验证。

## 2026-11-07 阶段 D 记录

- 预算门预检（1 条最小真实请求）：MiMo 端点接受完整请求形状（adaptive thinking、output_config、cache_control、betas/fallbacks）；usage 原始字段含 MiMo 专属 fallback_credit/inference_geo/iterations/speed 等，run.jsonl 的 raw 捕获设计与实际字段吻合。
- **smoke-D1（正常任务，full 策略，max-steps 15）完成**：独立成功检查 success=true、order_status=submitted、submitted_ids=[p1]，与模型 finish 自报一致但判定独立。
  - 6 次调用，延迟 4.3–7.6s；protocol_check 6/6 全过（真实端点往返 thinking/文本/工具配对保真，0 问题）。
  - full 策略实际保留步 [0]→[0..5] 递增；effect:1–5 verified、effect:6(neutral) pending，验证语义在真实运行中正确。
  - usage 合计：输入 10,966 + 缓存读 31,232，输出 583，缓存写 0；端点对该负载 cache_read 报告非空而 cache_creation 报 null（原始值已留档，不改写）。
  - 证据文件齐全：task.txt、step00–06.png/ui、run.jsonl、ground_truth（agent 结束后写出）。
  - 单 episode 耗时 58.8s（15 步上限，实际 6 步完成）。费用金额未知——MiMo token 计划单价未核实，usage 为唯一可用口径。
- **smoke-D2（受扰 popup@enter_cart，evidence 策略，max-steps 15）完成**：verdict success=true、fault_triggered=true、triggered_count=1、**recovered=true**——模型自行处置弹窗后完成下单；应用侧计数器 popup attempted/triggered/recovered=1/1/1，独立于模型 finish 自报。
  - 7 次调用，延迟 2.9–6.9s，耗时 65.7s；protocol_check 7/7 全过。
  - evidence 策略实际保留步 [0]→[0,1]→[1,2]→…→[5,6]：所有检查快速 verified 后按最新帧+余量滑窗，与 select() 语义一致（本次无长期未解决检查，evidence 帧优先未在真实运行中被触发——留待 pilot 受扰失败场景观察）。
  - effect:1–6 全 verified、effect:7(neutral) pending。usage：输入 23,167 + 缓存读 14,848，输出 856，缓存写 0。
  - 观察：部分控件树 label 回退到 class 名（「decor_content_parent」）而非动作名——模型靠坐标/截图完成点击，未影响结果，但 label 质量是 pilot 前可改进点（非阻塞）。
- **阶段 D 小结**：两条 smoke 的证据与计费记录完整（run.jsonl 原始 usage、延迟、协议校验、验证事件、ground truth 分离）。单 episode 约 1 分钟、新输入 11–23K token + 缓存读 15–31K、输出 0.6–0.9K。**费用金额未核实**（MiMo token 计划单价不在手头），pilot 硬预算只能以 token 量为口径；180-episode 按此推算 token 量级（不计缓存命中差异）约新输入 2–4M + 输出 0.1–0.15M，待用户确认预算口径后才启动 pilot。

## 2026-11-07 pilot manifest 与 dry-run 记录

- 12 开发实例已固定（research/pilot_runner.py 内表 + research/pilot_manifest.json 落盘核对）：任务 ID/商品名全部来自真实复位枚举（seed 1–6 各 4 件商品），未编造。4 任务家族 = F1 单件·首件 / F2 多件·相邻 / F3 单件·后件 / F4 多件·跨件；每实例带 1 个干扰项初值（confusion 需可翻转目标；正常条件下也须不误提交干扰项）。
- 5 条件 = 正常 + delay(3s)/noop(2)/popup/confusion（触发点 enter_cart）；3 策略 = full/recent(2)/evidence(2)；12×5×3=180 episode，同 (实例,条件) 的三策略共享同一 reset 配置 = 同初始快照（应用确定性复位已验证）。执行顺序固定种子 20261107 随机化。
- **dry-run 180/180 全过（160s，零模型）**：复位探针全 clean、invalid_reset=0、任务文本 9 种全部零泄漏（无故障词/ID/路径提示）。dry-run 产物标 dry_reset_ok，live 运行会自动重跑（续跑只跳过 completed）。
- 运行器能力：断点续跑、invalid_reset 留档不静默重跑（--retry-invalid 显式重试）、失败留档不剔除、硬预算帽（默认未缓存输入 4M + 输出 250K，达帽即停可续跑）、缓存读单独统计。
- 计划缺口标注：计划 §故障条件含「单通道缺失/过期」（分别报告截图缺失/树过期），research-env 目前不注入感知层故障（四机制为 delay/noop/popup/confusion）；该条件留待后续以 ADB 层注入补充，单列不混入本轮 5 条件。

## 2026-11-07 preflight 第一轮中断与修复

- preflight 第一轮在 i01-c2（noop 条件）第 4 步崩溃：模型调用 ask_human，后台进程 stdin 继承 tty 句柄骗过 isatty() 判定但 read 即 EOF，`input()` 抛 EOFError 终止整个 runner。
- 生产边界修复（2 处 + 1 处证据保全）：
  1. agent.py 新增 `_input_or_none`：isatty 判定不可靠，input() 的 EOFError/OSError 一律按无人值守 fail-closed（返回「请调用 finish(need_human)」），ask_human 与 confirm_and_tap 两个交互入口统一走该原语。
  2. episode_runner 重跑/续跑前归档旧尝试的 run.jsonl 与 step 证据到 attempt-<ts>/（run.jsonl 为追加写、截图按步号命名，不归档会混写两次运行；保留原始，不清理不覆盖）。
  3. 回归测试 +2（UnattendedBoundary：isatty 谎报 + input 抛 EOF 下 ask_human/confirm_and_tap 不崩溃、不点击、引导 need_human），全量 27/27 过。
- 语义说明：noop 条件下模型 ask_human 属诚实行为；无人值守应答后模型自行重试或 finish(need_human)，计为「人工介入」指标（计划 §主要指标），不算恢复成功。

## 2026-11-07 preflight 结果（i01 × 5 条件 × 3 策略 = 15 条真实）

- 15/15 completed，invalid=0。判定全部符合语义：c0 未触发（triggered=0，不计恢复）；delay/noop 触发 2 次、popup/confusion 各 1 次，12/12 受扰 episode recovered=true 且独立成功检查 success=true。本轮无 ask_human 调用。
- 用量（15 条）：未缓存输入 465K、输出 44K、缓存读 616K，耗时 33.7 分钟。单 episode 未缓存输入 12–66K、输出 1–10K。
- **缓存前缀现象（计划 §4.5）**：full 策略的未缓存输入反而低于 recent/evidence（如 c0-full 12K vs c0-evidence 26K）——full 稳定前缀命中缓存，recent/evidence 轮换图像破坏前缀。公平比较必须分列缓存读/写，不能只比图片数或未缓存输入。raw usage 已留档。
- **天花板效应风险**：i01（F1 单件家族）12/12 受扰全恢复，策略在成功率上无区分度；区分信号可能落在费用/时延/无效动作上，或需更难实例（F2/F4 多件+干扰）/更强故障参数。是否调难度按计划 §12/04 方向门由 pilot 全量数据决定，不得事后补填。
- **全量 pilot 已启动**（2026-11-07）：断点续跑跳过 i01 已完成 15 条，余 165 条按固定种子随机序执行；预算帽上调为未缓存输入 8M + 输出 800K（≈1.4× 实测外推 5.6M/0.5M），达帽即停可续跑；runner 已加逐条异常留档（API/设备毛刺不终止长跑）。

## 2026-11-07 pilot 完成（180/180，1 种子）

- 完成 180/180：1 条 APIConnectionError 一次重试成功（i05-c1-recent，runner 留档+续跑机制验证有效）。
- **总体**：独立成功检查 178/180 = 98.9%；故障触发 144 条（c1–c4 各 36），**恢复 142/144 = 98.6%**。
- **分条件**（triggered→recovered / success）：delay 36→36 / 36；noop 36→35 / 35；popup 36→36 / 36；confusion 36→35 / 35；正常 36 条全成功且 0 触发（未触发不计恢复 ✓）。
- **分策略恢复率**（recovered/triggered）：full 46/48（c2 11/12、c4 11/12 各失 1）；recent 47/47；evidence 48/48。差异每格仅 1 条，**1 种子下不构成统计证据**，仅是方向门观察。
- **两条真实失败均在 i03-full**（同实例同策略，恰为 F1 家族）：
  1. i03-c2-full：noop 耗尽两次提交机会后未再重试，止步 max_steps，order=none；
  2. i03-c4-full：confusion 翻转干扰项 p2 后**未移除**，提交 [p1,p2] ≠ expected [p1]——独立成功检查如实判负（模型 finish 自报不算数 ✓）。
- **费用（180 条合计）**：未缓存输入 6.22M、输出 548K、缓存读 10.17M。
- **分策略成本结构（计划 §公平比较的关键现象）**：
  | 策略 | 平均未缓存输入/条 | 缓存读合计 | 输出合计 |
  |---|---|---|---|
  | full | 20.6K | 5.99M | 217K |
  | recent2 | 42.7K | 2.00M | 166K |
  | evidence2 | 40.1K | 2.11M | 158K |
  full 的未缓存输入约为其他策略一半（前缀稳定命中缓存），但缓存读 3 倍；若缓存读有折扣，full 实际计费可能最低，若按全价则最高。**「谁更便宜」完全取决于 MiMo 缓存读单价——该口径未核实，是 12/04 方向门前必须向 provider 核实的唯一计费项**。图片数/token 数/未缓存输入任何一个单独口径都会得出相反结论。
- **天花板效应确认**：12/12 实例恢复率 98.6%，成功率维度基本无区分度；方向门判据「若 recent2 已同样可靠且便宜，优先接受简单策略」在本难度下成立（recent2/evidence2 同为全恢复，成本接近）；若需区分策略上限，须按计划用更强故障参数/更大历史压力，不得事后改写本轮数据。
- 家族维度：F1 43/45（两失败均在此）、F2/F3/F4 全成功——多件与干扰项家族反而不难，难度集中在特定实例×full 组合，样本太小不下结论。

## 2026-11-07 瞬时线索机制（promo）与区分度实验

**动机**：180-pilot 确认天花板效应——现有四机制的判别线索始终留在最新帧直到被处置，从不需要旧证据，三策略恢复率 98.6% 无区分。论文动机「压缩历史会丢恢复证据」在该设置下测不出来，须制造旧证据依赖。

**机制设计（promo「会员日优惠码」）**，已实现于 EnvState.kt/MainActivity.kt：
- 复位首屏画布横幅闪现一次「会员日 · 优惠码 XXXX」（4 位，fault_seed 确定性生成，同种子跨策略同码=配对比较）；**首个动作即过期**（promoActive 翻 false，counters.promo attempted/triggered=1）——线索此后只存在于历史图像。
- 结算页强制输入（EditText contentDescription=code_field，TextWatcher 同步 ground truth）；提交时缺码拒（rejected_code_missing）/错码拒（rejected_code_wrong），码正确才走原有提交语义；成功检查不变（submitted==expected）。
- **证据独占图像通道**：横幅纯画布绘制（无 text/contentDescription），实测控件树不含码文本（连「优惠码」都不在树里）。原因：本框架文本历史恒保真、只有图像按策略逐出——若码进控件树，任何策略都能从文本历史取回，策略无区分。该设计即实验自变量（图像历史）的干净操纵。
- recovered 语义：线索过期（triggered>0）且独立成功检查通过才计恢复；复位探针覆盖 promo 干净态（横幅武装未过期、输入空、计数器零）。
- 关于「界面文字不含 expected_answer」：码不是测试参数而是世界内业务数据（如同商品名），且以像素呈现、不入界面文字——字面与实质均合规。

**确定性验收（env_smoke，零模型，8/8 场景全过）**：promo 正确码路径（输入同步 ground truth、recovered=1）、拒单路径（缺码/错码各自事件留档、不重复计触发）、对照（fault=none 无输入框）、复位探针（promo 跑完复位干净）、同 fault_seed 同码、控件树不含码文本断言。截图目检：红底白字横幅清晰可读（R98K）。单元测试 27/27 过。

**区分度实验（已启动，84 条真实）**：
- 主实验：c5(promo) × {full, recent1, recent2, evidence2} × 12 实例 = 48 条——区分信号应在恢复率/成功率（full 应能从历史图像取回码，recent1 逐出最早）。
- 对照：{c0, c3, popup, c4, confusion} × recent1 = 36 条——排除「recent1 全局过弱」混淆（c3/c4 判别线索留在最新帧，recent1 应仍恢复；c0 为平凡基线）。
- 后验分析维度：run.jsonl 含全部 thinking/文本——可区分「第 0 步即抄录码」vs「结算时才从历史取回」，前者说明模型即时蒸馏规避了图像逐出（合法策略发现），后者才体现图像历史价值。
- dry-run 84/84 全过；执行顺序固定种子 20261107 随机化；预算帽每段 4M 未缓存输入 + 400K 输出。
- 限制声明：1 种子、单模型（mimo-v2.6-pro）、12 开发实例；本轮只回答「机制能否制造策略区分」，不回答泛化。
**区分度实验结果（84/84，2026-11-08 跑完）**：

主实验 c5（promo，12 实例 × 1 种子，恢复 = 线索过期后独立成功）：

| 策略 | success | recovered | 归因（正确码输入时横幅帧是否在上下文） | 未缓存输入 | 输出 | 缓存读 |
|---|---|---|---|---|---|---|
| full | 12/12 | 12/12 | 12/12 且全部**横幅帧在上下文** | 219K | 22K | 1,230K |
| recent2 | 1/12 | 1/12 | 1 例（i10）**帧已逐出**=即时蒸馏 | 852K | 58K | 930K |
| recent1 | 0/12 | 0/12 | 0（从未输入正确码） | 533K | 68K | 870K |
| evidence2 | 0/12 | 0/12 | 0（从未输入正确码） | 741K | 42K | 776K |

对照（recent1，排除「recent1 全局过弱」）：c0 12/12、c3(popup) 12/12 recovered、c4(confusion) 12/12 recovered——判别线索留在最新帧的故障 recent1 全部恢复；**策略×故障类型交互干净**：旧证据依赖（c5）上 full 12/12 vs recent1 0/12（Fisher 精确 p≈7e-7；但独立单元按商品目录算仅 6 个，须加种子复证，见下）。

失败形态（23/24 非 full 失败）：模型**知道**需要优惠码（输入框里敲「会员日优惠码」字面、拼音「huiyuanriyouhui」、「VIP」、「MEMBERDAY」）——语义记忆（文本蒸馏）存活、码本体（像素细节）丢失；随后 ask_human（无人值守→need_human，计入人工介入指标）或耗尽 max_steps（6 例）。full 成功例均 9–13 步直接输入正确码提交，0 次 ask_human。

成本结构（token 口径，c5 每 episode 均值）：full 未缓存输入 18K / 输出 1.8K / 缓存读 103K——**成功率最高的同时未缓存输入和输出最低**（失败臂反复试探烧 44–71K 输入）；缓存读高约 1.4×（稳定前缀命中）。四类分列见上表，不做货币化结论（用户 2026-11-07 决定）。

方法学要点：
- full 12 例成功全部在「横幅帧仍在上下文」时输入码 → 成功机制确系历史图像取回，非巧合抄录；recent2 唯一成功（i10）是读码即时蒸馏进文本记忆（输入时帧已逐出仍通过）——文本蒸馏是绕过图像逐出的合法捷径，出现率 1/12，全程可从 run.jsonl 归因。
- 残余限制：实例共享 6 个商品目录（每目录 2 实例），独立单元存疑；单模型、单种子；evidence 在无验证事件可锚定时退化为近窗（与 recent2 同样丢首帧）——evidence 的差异化贡献需在有验证事件的场景另测。
- **结论（开发集范围）**：promo 机制成功制造了旧证据依赖，压缩图像历史直接损失恢复成功率——主假设在 research-env 上得到首个正面证据。
- 后验归因工具 research/flash_analysis.py：type_text 的码 vs ground truth + 输入时刻 step-0 横幅帧是否仍在 retained_image_steps（在=历史取回，不在=即时蒸馏或猜测）。

**MiMo 缓存读计费口径调研（12/04 方向门唯一外部依赖）**：NOT_ENOUGH_INFO——公开渠道无法确认。本机代理 7892 未监听（直连可用）；cn.bing 检索「xiaomimimo token plan / MiMo API 计费缓存」无有效命中；token-plan-cn.xiaomimimo.com 仅 API 服务（/ /pricing /docs 均 404，无公开价目）。
**用户决定（2026-11-07）**：成本口径以 token 用量为准、不换算金钱——方向门不再依赖 provider 单价核实。报告口径固定为：未缓存输入 / 缓存读 / 缓存写 / 输出 四类 token 分列，「谁更便宜」以 token 结构陈述，不做货币化结论。

**计划 §5 基线/消融的落地设计（跑完解锁代码后实施；本轮代码冻结未动）**：
1. 基线 recent1/2/4：已支持（POLICY_VARIANTS）✓。
2. 通用关键帧基线：context_policy.select() 增 keyframe 模式——按 UI 指纹变化保留帧（确定性、零额外调用），与 evidence（验证驱动）对照出「验证信号」的贡献。
3. 固定长度摘要基线：每 M 回合一次额外模型调用，把被逐出帧压成 ≤N token 文本摘要注入后续回合；摘要调用计费计入成本（计划 §5 明确）。
4. 消融：去验证状态（evidence 不锚定未解决检查→退化为近窗）、去通道新鲜度（最新帧不恒留）、去历史取回（见 5）、仅重试（只保留效果校验触发的重试循环，无任何历史保留策略）。
5. **历史取回（方法组件，当前缺失）**：计划原文「丢弃图像仍保留本地原始引用」「证据不足时允许重新观察或检索」——agent 需 review_screenshot(step) 工具把本地留存帧重新注入上下文（计费计时）；消融=移除该工具。当前 84 条跑的是「纯策略」对比（无取回），若 recent 失败而 full 成功，下一步补「recent+取回」臂才能回答成本匹配问题。
6. 注意事项：实现时全部在本轮跑完之后；臂间 agent 代码必须一致，混版本运行会使对照无效。

## 2026-11-08 批次 2（稳健性种子）启动与批次 3 组件实现

**批次 2（已完成 60/60，2026-11-08）**：i13–i24（种子集 2，seed 7–12 真实目录枚举，家族×种子布局镜像 i01–i12）× c5 × {full, recent1, recent2, recent4, evidence2} = 60 条。59 条一次跑完，i19-c5-evidence2 遇 APIConnectionError 留档后断点续跑补齐（该条加载批 3 后代码、纯策略臂默认关组件；请求构造等价性由 38 项测试锁定）。预算实耗：未缓存输入 3.55M / 输出 219K / 缓存读 4.63M（帽 4M/400K）。对照 c3/c4 × recent1（新种子，24 条）拆为批次 2b 补跑——批次 1 已证 recent1 在判别线索留新帧的故障上 12/12，本次目标对照在同一实例集内配对成立，full 同实例全成功已排除「新目录全局更难」混淆；对照仅为保险。

**批次 2 结果（12 新实例 × 1 种子，恢复 = 线索过期后独立成功）**：

| 策略 | success | recovered | 归因（正确码输入时横幅帧） | 未缓存输入 | 输出 | 缓存读 |
|---|---|---|---|---|---|---|
| full | 12/12 | 12/12 | 12/12 帧在上下文 | 247K | 24K | 1,219K |
| recent4 | 0/12 | 0/12 | 从未输入正确码 | 1,190K | 52K | 786K |
| recent2 | 0/12 | 0/12 | 从未输入正确码 | 783K | 45K | 840K |
| recent1 | 0/12 | 0/12 | 从未输入正确码 | 588K | 59K | 1,080K |
| evidence2 | 0/12 | 0/12 | 从未输入正确码 | 752K | 41K | 717K |

- **复现干净**：full 12/12 vs recent1 0/12 在 6 个全新商品目录（seed 7–12，每目录 2 实例）上完全复现批次 1 的对比；两批合并 24 实例/12 目录：full 24/24 vs recent1 0/24、evidence2 0/24、recent2 1/24、recent4 0/12。
- **recent2 批 1 的蒸馏捷径未再现**（批 1 为 1/12，批 2 为 0/12）——1/12 是罕见文本蒸馏事件，不构成稳定通道；失败形态与批 1 同：知道要码、丢码本体（输入字面/拼音/「VIP」），18 例 need_human 或耗尽步数。
- **recent4 = 0/12**：4 帧宽松窗仍丢 step-0 线索（任务深度 ≥5 帧），且未缓存输入反随窗口增大升至 99K/条——成本随保留帧数线性涨而成功率不回。
- **成本结构（token 四类分列，均值/条）**：full 未缓存输入 20.6K / 输出 2.0K / 缓存读 101.6K——成功率最高的同时未缓存输入与输出最低（失败臂反复试探烧 49–99K 输入）；缓存读高 1.2–1.7×（稳定前缀命中）。不做货币化结论。
- **统计口径**：全 12 实例配对对比符号检验 p=2⁻¹²≈2.4e-4（独立单元=目录）；实例级 Fisher 精确 p≈1.5e-7（但每目录 2 实例非独立，以此为上限参考）。两批合并的聚类统计待 3 种子全量后给。
- **失败形态样例（i15-c5-recent2，17 步）**：正确加入目标商品、清掉干扰项、进结算页，被拒后试拼音、试 del 重输、ask_human（无人值守应答），最终 finish(need_human) 并请求人工输入「会员日优惠码」——语义记忆完好、像素细节丢失的典型形态。

**批次 3 组件实现（2026-11-08，测试全过 38/38，零模型费用）**：

1. **历史取回工具 review_screenshot（方法组件，臂级开关 retrieval）**：计划原文「丢弃图像仍保留本地原始引用；证据不足时允许重新观察或检索」。实现为钉住槽语义——工具结果附 `{"type": "_image_slot", "step": k, "pin": true}`，请求构造边界钉住槽优先物化、同帧全请求只发一份（原始槽让位，防重复计费）；钉住帧不走逐出策略、保留至 episode 结束（取回=显式取证决定，注入上下文即计费计时）。`retained_image_steps` 如实含钉住帧（flash_analysis 归因不受影响）。消融「去历史取回」= 整体移除（未开臂连分发都拒，不只藏工具表）。臂：recent1retr / recent2retr。
2. **通用关键帧基线 keyframe（context_policy 新模式）**：按屏幕指纹变化保留状态游程末帧。屏幕指纹在感知生产边界计算（图像字节+控件树），与效果校验的 ui 指纹分工不同——画布像素差异只在屏幕指纹可见。**严格语义**：重复状态不发、不用近期帧填预算（发帧数可少于 max_images，成本差异如实进 token 列）；缺指纹的步各视为独立状态。臂：keyframe2 / keyframe4。与 evidence 对照出「验证信号」的贡献（同为预算优先级选择，锚不同）。
3. **固定长度摘要基线（summary，臂级开关 summarize_every=M）**：每 M 回合把「下一请求将看不到」且未蒸馏的帧交给额外模型调用压成 ≤200 字文本（调用上限 300 token），摘要文本随本回合 tool_result 注入（新文本进契约、追加式缓存友好，不改系统提示避免缓存前缀重算）；候选集 = 本地帧 −（下一请求保留集 ∪ 钉住帧）− 已蒸馏，最新帧恒在保留集不蒸馏。摘要调用计费计入 episode usage（计划 §5 明确），run.jsonl 独立记 summary_call（不占 call 序号，step↔seq 映射不变），失败留档不终止。臂：recent1sum3。
4. **管道与记录**：run_episode/Agent/pilot_runner 全链路透传 retrieval/summarize_every；run_header 与 episode.json 记录臂配置；POLICY_VARIANTS 变为 (policy, max_images, extras) 三元组（旧标签默认 extras={}，行为不变）；episode_runner --policy 支持 keyframe。
5. **测试（27→38 项）**：取回（默认缺席/开关注入/描述零泄漏词/未知步列可用步号/钉住逐出帧进下一请求 [1,1,2]/同请求不重复发帧 [1,2]/动作 expectation=neutral）、关键帧（游程末帧/预算取新游程/缺指纹独立状态/集成 retained [0],[1],[1,2] 且屏幕指纹落 run.jsonl）、摘要（蒸馏调用无 tools、摘要文本进下一请求、source_steps=[0]、3 次调用 usage 入账、full 无逐出零额外调用）。跑中 4 处失败均为语义决定点：keyframe 的「近期填充」违背按变化保帧的字面语义→改产线为严格语义；review 分发在未开臂可被调→补整体移除闸门。

**残余限制**：摘要调用的最小 Messages 形状已由 preflight 验证并修复（见下节）；钉住槽与 validate_request 的图像豁免由假件测试覆盖，真实往返未验（recent1retr/recent2retr 臂会真实验证）；summary 蒸馏质量单样本正向（见下节），系统回答由 recent1sum3 臂给出。

## 2026-11-08 批次 2b 结果、批次 3 preflight 与启动

**批次 2b（对照，24/24 completed，2026-11-08）**：i13–i24 × {c3(popup), c4(confusion)} × recent1，预算实耗 572K 未缓存输入 / 44.8K 输出 / 680K 缓存读（帽 4M/400K）。归因 research/results/flash-batch2b.json：

| 条件 | success | recovered | 未缓存输入 | 输出 | 缓存读 |
|---|---|---|---|---|---|
| c3×recent1 | 12/12 | 12/12 | 288K | 21K | 355K |
| c4×recent1 | 12/12 | 12/12 | 285K | 24K | 324K |

判别线索留在最新帧的故障上 recent1 在新种子目录（seed 7–12）全恢复——「recent1 全局过弱」混淆在两套实例上均排除；c5×recent1 的 0/12 只能归因于旧证据依赖。该批加载批 3 后代码、新组件默认关（行为等价由 39 项测试锁定）。

**批次 3 preflight（摘要调用形状单条先验，脚本 research/preflight_summary_shape.py 可复跑）**：
- **最小形状被 MiMo 接受**：无 thinking/tools/betas、system 纯字符串、单 user 消息（text/image 交错）——与主调用的全部形状差异点验证通过。
- **发现生产缺陷（preflight 价值所在）**：MiMo 即使请求不带 thinking 参数也默认先输出 thinking 块（1 图 ~242 字符）。原 SUMMARY_MAX_TOKENS=300 被 thinking 吃光，text 截断为零（2 图实测 stop_reason=max_tokens、chars=0）；且空文本也把帧标「已蒸馏」、永不重试——recent1sum3 臂会静默退化成 recent1。
- **产线修复（3 处 + 1 回归测试，39/39 过）**：① SUMMARY_MAX_TOKENS 300→800（thinking 余量 + ≤200 字文本）；② 空文本不标已蒸馏，下次触发重试同批帧（有意义失败原因）；③ summary_call 记 stop_reason（截断归因）。
- **修复复验（真实 3 候选帧 = recent1sum3 首次触发精确形状，帧取 i01-c5-recent1 step00–03）**：stop_reason=end_turn 不再截断（输出 436 = thinking 338 字 + 文本 233 字）；**单样本蒸馏读出了像素里的码**（摘要含「会员日·优惠码 W3JG」，3 帧中第 0 帧）；空文本重试语义生效（成功后二次调用零请求）。单样本仅记录，系统结论待臂。

**批次 3（已启动，跑中）**：i01–i12 × c5 × {recent1retr, recent2retr, keyframe2, keyframe4, recent1sum3} = 60 条，dry-run 60/60 过，固定种子 20261107 随机化，预算帽 4M 未缓存输入 + 400K 输出。范围选 i01–i12：批 1 的四条基线（full 12/12、recent1 0/12、recent2 1/12、evidence2 0/12）就在此实例集，配对最密；i13–i24 上的复证并入 3 种子全量。跑中代码冻结于启动时模块。问题：取回/摘要能否恢复 full 的成功率、关键帧是否天然保住横幅帧、各自 token 结构。

## 2026-11-08 批次 3 结果（五臂，60/60）

- **完成**：53 条一次跑完，收尾时段 7 条遇 APIConnectionError 留档后断点续跑补齐（runner 不终止机制第 2 次实战）。总实耗 3.65M 未缓存输入 / 253K 输出 / 3.64M 缓存读（帽 4M/400K）。蒸馏调用 38 次 err=0（preflight 的形状修复在真实臂全部生效）。归因 research/results/flash-batch3.json。

主表（i01–i12 × c5，12 实例 1 种子，恢复 = 线索过期后独立成功；full/recent1/recent2/evidence2 为批 1 同实例基线）：

| 策略 | success | recovered | 归因（正确码输入时横幅帧） | 未缓存输入 | 输出 | 缓存读 |
|---|---|---|---|---|---|---|
| full（基线） | 12/12 | 12/12 | 12/12 被动保留（帧在上下文） | 219K | 22K | 1,230K |
| **recent1sum3** | **11/12** | **11/12** | **11/11 摘要抄录**（帧已逐出、码在 summary_text） | 540K | 51K | 505K |
| recent2retr | 2/12 | 2/12 | 2/2 显式取回（review(0)） | 792K | 58K | 893K |
| recent1retr | 2/12 | 2/12 | 2/2 显式取回 | 511K | 54K | 841K |
| keyframe2 | 1/12 | 1/12 | 1 例帧已逐出=自发即时蒸馏 | 669K | 42K | 700K |
| keyframe4 | 0/12 | 0/12 | 从未输入正确码 | 1,133K | 48K | 695K |
| recent1（基线） | 0/12 | 0/12 | — | 533K | 68K | 870K |
| recent2（基线） | 1/12 | 1/12 | 1 例即时蒸馏 | 852K | 58K | 930K |
| evidence2（基线） | 0/12 | 0/12 | — | 741K | 42K | 776K |

统计口径：sum3 vs recent1 配对 11:0 不一致对，精确符号检验双侧 p≈9.8e-4；sum3 vs full 仅 1 个不一致对，无法区分（p=1）；retr/keyframe 对 recent1 各 2:0 / 1:0，无统计力。独立单元=实例（每目录 2 实例非独立），聚类统计待 3 种子。

**三路归因（方法问题的直接回答）**：
1. **被动摘要蒸馏是稳定通道**：sum3 11/12 且 11/11 全走「码在摘要文本、输入时帧已逐出」路径；每 episode 蒸馏 2–4 次，11 个成功例全部至少 1 次摘要抄到码。批 1 里 recent2 的 1/12 自发蒸馏即此通道的稀有自发形态——summarize-on-eviction 把它变成制度化通道。
2. **显式取回几乎不被自发调用**：两臂合计仅 4/24 episode 调用 review_screenshot(0)（且全部成功——工具功效 4/4）；其余 20 例从未想起回看。「知道要码」的不确定感连不到「回看旧截图」这个动作——瓶颈是调用率不是工具功效。方法含义：被动蒸馏比主动检索稳健；若要取回生效需更强的触发提示（消融/改进方向）。
3. **关键帧基线救不了瞬时线索**：keyframe2 1/12、keyframe4 0/12——严格关键帧保住状态变化标记但预算窗仍逐出 step-0；keyframe4 未缓存输入反而最高（94K/条，失败臂反复试探）。

**成本（token 四类分列，均值/条）**：sum3 45.0K 未缓存输入 / 4.2K 输出 / 42.1K 缓存读（含蒸馏调用约 11K 输入 + 1.9K 输出）；full 18.3K / 1.8K / 102.5K——本任务规模下 full 仍最便宜且最可靠（历史装得下、稳定前缀命中缓存）；压缩的价值在历史超出上下文的长任务，待预算曲线/长任务家族验证。失败臂 44–94K 输入且输出最高（反复试探）。

**失败形态（sum3 唯一失败 i06）与截断修复**：首蒸馏（source=[0,1,2]）被 max_tokens 截断、残缺文本仅 11 字「0步：研测商城列表。红」（码正要写出）→ 残缺文本被当已蒸馏标掉、横幅帧永不重试 → 后续摘要只抄到结算页占位符字面「会员日优惠码」→ 敲字面、ask_human、need_human。**同批 i03 是活对照**：两次 chars=0 截断被「空文本不标已蒸馏」语义救回（第三次连 [0..8] 重试成功、码入摘要）。跑批 3 后产线修复（40/40 测试）：max_tokens 截断整体判蒸馏失败（残缺文本不注入、不标已蒸馏、原文留档），下次触发重试同批帧；SUMMARY_MAX_TOKENS 800→1200（thinking 随候选帧数增长，实测 3/38 调用在 800 截断）。定点复验（修复后代码重跑 i06-c5-recent1sum3 至独立目录 runs/spotcheck-truncfix，批次数据不动）：**recovered=True**（code_entered=9SYZ、订单提交），首蒸馏 end_turn 217 字含码。如实注记：该次蒸馏未截断（截断与否有随机性），截断-重试路径由单元测试 test_truncated_partial_text_not_trusted_and_retries 覆盖。

**限制**：1 种子、单模型、12 实例（独立单元=目录 6 个）；sum3 的 M=3 未扫参；截断复验是单条定点；keyframe 屏幕指纹对画布像素变化有效（promo 横幅），对纯控件树变化的故障未单测。

## 2026-11-08 批次 4 消融设计与启动（四臂，48 条）

计划 §5 line 115 消融四项对方法 M（§4 全栈 = 最新帧 + 验证锚定证据 + 近窗填充 + 蒸馏 + 取回）的映射；优先用既有数据，只补 M 组合臂与去新鲜度变体（「不要为每个微小实现细节堆砌实验」）：

| 消融项 | 臂 | 与 M 的单点差异 | 数据状态 |
|---|---|---|---|
| —（方法 M） | ev2sum3retr（evidence 预算 2 + sum3 + retr） | 全组件 | 新跑 |
| 去验证状态 | rec2sum3retr（recent 预算 2 + sum3 + retr） | 锚定关（纯近窗填充） | 新跑 |
| 去通道新鲜度 | ev2nfsum3retr（evidence_nofresh + sum3 + retr） | 最新帧不恒留 | 新跑 |
| 去历史取回 | ev2sum3（evidence 预算 2 + sum3） | 无 review_screenshot | 新跑 |
| 仅重试 | recent1 | 无任何历史保留（只留当前帧） | 既有 0/12 |

另有既有对照：full 12/12（天花板）、recent2 1/12（预算匹配的纯近窗地板）、recent1sum3 11/12（蒸馏单组件）、evidence2 0/12（锚定无蒸馏）。既定消融已在 c5 给出方向：取回组件 retr 2/12 vs 0/12、锚定组件 evidence2 0/12 vs recent2 1/12——M 系臂回答「组合后蒸馏之外还有无增益」。

实现（加法式、跑前落地，44/44 测试）：`context_policy.select` 新增 `evidence_nofresh`（不预置最新帧、预算全按「未解决检查证据源→近序」分配；无检查时退化等于 recent；证据+最新都能装下时与 evidence 相同）+ 3 条 select 层回归；episode_runner --policy choices 与 POLICY_VARIANTS 加 4 标签。dry-run 96/96（manifest 含 i01–i24 复位全部通过）；配对原则只实跑 i01–i12（与批次 3 同实例同种子），i13–i24 留给 3 种子全量。2026-11-08 后台启动，预算帽 4M/400K，顺序种子 20261107。

**预期与判读**（结果见下节）：c5 的瞬时线索只存在于 step-0 像素，验证锚定与新鲜度都不指向它，预期四臂均落在蒸馏通道上；若 M≈去验证状态≈去新鲜度≈ev2sum3，则结论为「蒸馏是唯一承载组件，锚定/新鲜度/取回在瞬时线索恢复上正交」——这是本设置下的诚实边界，锚定的价值应在 c1–c4（旧证据依赖）场景判读（那里近天花板，须更难故障或预算曲线才能拉开）。

## 2026-11-08 批次 4 结果（四臂消融，48/48）

- **完成**：48 条一次跑完、0 失败、无 API 毛刺。总实耗 2.48M 未缓存输入 / 196K 输出 / 1.96M 缓存读（帽 4M/400K）。归因 research/results/flash-batch4.json。

主表（i01–i12 × c5，12 实例 1 种子；「码通道」= 到达收银台后输入正确码，归因为互斥主路径）：

| 臂（消融项） | success | recovered | 码通道 | 归因主路径 | 未缓存输入 | 输出 | 缓存读 |
|---|---|---|---|---|---|---|---|
| ev2sum3retr（方法 M） | 12/12 | 12/12 | 12/12 | 11 摘要抄录 + 1 显式取回 | 634K | 50K | 510K |
| rec2sum3retr（去验证状态） | 12/12 | 12/12 | 12/12 | 9 摘要抄录 + 3 显式取回 | 694K | 49K | 430K |
| ev2nfsum3retr（去通道新鲜度） | 11/12 | 11/12 | 12/12 | 10 摘要抄录 + 2 显式取回 | 554K | 47K | 545K |
| ev2sum3（去历史取回） | 11/12 | 11/12 | 11/12 | 11 摘要抄录（无取回通道） | 596K | 50K | 474K |

既有对照（批 1/3，同实例）：full 12/12、recent1sum3 11/12、recent2 1/12、evidence2 0/12、recent1 0/12（仅重试地板）、recent1retr/recent2retr 各 2/12、keyframe2 1/12、keyframe4 0/12。

**消融判读（预判成立）**：
1. **蒸馏是唯一承载组件**：四臂 11–12/12 全部近满分，M 与三个单点消融间最多 1 个不一致对（无统计力，p=1 量级）；对照仅重试 recent1 0/12，每臂对其 ≥11:0 不一致对（符号检验双侧 p≤1e-3）。去验证状态、去通道新鲜度、去历史取回在瞬时线索恢复上均正交——c5 线索只在 step-0 像素，锚定不指向它、新鲜度只保最新帧、取回是低频备援。
2. **码通道全绿**：48 条里 47 条到达收银台，47/47 输对码（错码尝试 0）；41 条经摘要抄录（帧已逐出、码在 summary_text），6 条经显式取回（自发调用 review(0) 后钉住帧在上下文），0 条被动保留（预算 2 下 typing 时 step-0 必已逐出；此前 img@typing 的 6 例即取回钉住帧）。取回自发调用率 6/48=12.5%（批 3 为 4/24=16.7%），调用后 6/6 成功——「想不起来回看」瓶颈复现，工具功效仍满格。
3. **两例失败均离机制**（非线索保留问题）：i02-c5-ev2nfsum3retr 码通道成功（CS3J 经摘要抄录、输入正确）但结算清单多带预置棉麻抱枕、目标参数不匹配 → 业务状态判失败；i07-c5-ev2sum3 首蒸馏已抄到码（摘要含「会员日·优惠码 CS3J」）但第 4 步在清单页对「预置商品是否一起买」ask_human 后 finish 交人、从未到达收银台。两例均为任务跟随/交互变差，换臂复现的同实例其余臂都正常移除预置商品——失败与被消融组件无关。

**成本（token 四类分列，均值/条）**：ev2sum3 49.6K 未缓存输入 / 4.2K 输出 / 39.5K 缓存读；ev2nfsum3retr 46.2K / 3.9K / 45.4K；ev2sum3retr（M）52.8K / 4.2K / 42.5K；rec2sum3retr 57.8K / 4.1K / 35.8K。四臂成本同量级（差异主要来自取回调用有无与失败臂重试）；对照 full 18.3K / 1.8K / 102.5K——本任务规模 full 仍最便宜最可靠，压缩价值待长任务验证的结论不变。

**统计口径**：独立单元=目录 6 个（每目录 2 实例非独立），实例级符号检验为下界近似；M vs 各消融无区分力（≤1 不一致对），仅「蒸馏系 vs 无蒸馏系」的对比（11–12:0）稳定。聚类 bootstrap 待 3 种子。

**限制**：1 种子、单模型、12 实例；消融只在 c5（瞬时线索）设置下判读，锚定/新鲜度对 c1–c4 旧证据依赖故障的作用本设置测不到（那里近天花板）；ev2nf 的新鲜度消融行为差异只在 ≥2 个未解决检查证据源挤占预算时出现，c5 中少见，其 11/12 不构成「新鲜度无用」的一般结论。

## 2026-11-08 批次 5 设计与启动（M 扫参 + 取回触发 A/B，48 条）

两个问题、四臂（i01–i12 × c5，配对批 3/4 同实例）：

| 问题 | 臂 | 备注 |
|---|---|---|
| M 扫参 | recent1sum2 / recent1sum3fx / recent1sum6 | recent1 基座、同代码三档蒸馏频率 |
| 取回触发 A/B | recent1retrnudge（+提示） | 对照 = 批 3 recent1retr 2/12（取回路径代码未变，复用先例 = 批 3 复用批 1 基线） |

- **M=3 为何重跑**：批 3 的 recent1sum3（11/12）跑在截断修复前语义（上限 800、截断文本可信）；修复后（上限 1200、截断=失败重试）M=3 须重跑才能与 M=2/6 同曲线。新标签 recent1sum3fx 保证不覆盖批 3 目录（断点续跑按目录判完成，同名会跳过/覆盖旧数据）。
- **取回触发提示（retrieval_nudge，默认关、加法式）**：实测取回工具在上下文也极少被自发调用（批 3 4/24、批 4 6/48，调后 10/10 成）——「不确定感」连不到「回看」。system 追加通用取证话术（RETRIEVAL_NUDGE：不确定→review_screenshot 取证、不猜不弃；无任务答案/故障词），无取回工具时显式拒绝组合。49/49 测试（+5：默认不注入、注入含话术、组合拒绝、与冻结经验共存、跑通 smoke）。
- **判读预设**：M 扫参看恢复率×蒸馏调用成本×截断重试率的权衡（M 小→调用多、候选少、thinking 短、截断少；M 大→反向）；A/B 看自发调用率是否从 ~2/12-6/48 抬升，以及恢复是否随之受益（蒸馏臂里取回是备援，retr-only 臂里是主通道）。n=12/臂对调用率差异检验力有限，调用率对照历史池化基线 6/48 判读。

## 2026-11-08 顶会差距评估（arXiv 相近工作侦察 + 缺口清单）

arXiv 检索（经 7892 代理）发现相邻工作密集，直接影响新颖性定位：
- **ECHOES**（arXiv:2610.22408，清华+StepFun，2026-10）：与本工作最相邻——「文本摘要丢关键视觉状态」正是本工作的出发点，其用训练时特权信息（教师看多图）做 OPD-RL 把历史压缩蒸馏进权重，部署时不保留截图，另出 ECHOESBMK 诊断基准。**差异空间**：我们是冻结模型/免训练的推理时上下文预算（保留本地原始帧引用、可取回/重观察、三路因果归因、瞬时像素独占线索机制探针、验证锚定保留）。须全文精读后重写定位。
- MemU-Bench/AppMem（arXiv:2611.00191）：用户历史记忆（跨会话模糊指令）+免训练结构化记忆检索——不同问题域，但「免训练 GUI 记忆」话术空间被占。
- GHD（arXiv:2608.06065）：训练时后见蒸馏（教师看下一屏）——训练方法，非推理时上下文管理。
- Chart the Invisible（arXiv:2610.35237）：视口隐藏目标探索——相邻但不同问题。
- 其余相邻：Darwinian Memory（2026-01）、HiconAgent（2025-12）、Less is More（2025-07）、STAMP、GUIFlow、DeltaReplay 等。

**缺口清单（按杀伤力排序）**：
1. **价值域实验（命门）**：当前规模 full 又便宜又好（18.3K/条 vs 压缩臂 45–58K）——「为什么不用 full」不解决则动机不成立。须预算曲线 + 更长任务家族把 full 推过上下文/成本墙，证明压缩在该域保恢复。
2. **新颖性定位重写**：对 ECHOES 明确差异化（冻结权重 vs 训练蒸馏；证据溯源+可取回 vs 部署丢弃截图；机制探针+归因 vs 基准+指标）。若重叠过大，贡献叙事须转向「免训练证据保留的上下文预算 + 瞬时线索恢复的机制归因」。
3. **规模与统计**：1 种子/12 实例（6 独立单元）/1 backbone；3 种子×24 实例+聚类 bootstrap、第二 backbone 未做。
4. **外部效度**：research-env 单一任务族；AndroidWorld（阶段 F）未启动；未见故障组合未测。
5. **基线完整性**：计划 §5 的「固定长度摘要」基线未跑（ECHOES 式对比只能讨论不可比性：需要训练权限）。
6. **叙事诚实性**：计划 §4 的核心假设（验证状态锚定保留）在数据里正交/天花板，论文主贡献实为蒸馏——计划与结果的叙事要在写作期统一。

**时间线判断**：ICML 2027（截稿约 2027-01，剩 ~2 个月）装下 1–4 不现实；NeurIPS 2027（约 2027-05，剩 ~6.5 个月）可达，前提价值域实验立刻启动。当前水准 ≈ 强 workshop；补齐 1–4 并完成定位重写可到主会竞争力。

## 2026-11-09 批次 5 结果（M 扫参 + 取回触发 A/B，48/48）

执行：48/48 completed，无 invalid_reset、无 API 失败；执行顺序随机化（order_seed 20261107）；耗时 11178s；预算帽内（未缓存输入 2.15M/4M、输出 222K/400K）。全部 48 条 promo 均触发（triggered=1），恢复=成功口径一致。

| 臂 | 恢复 | 机制归因（flash_analysis） | 未缓存输入 | 输出 | 缓存读 |
|---|---|---|---|---|---|
| recent1sum2（M=2） | **12/12** | 摘要抄录 12/12，打字时横幅帧均已逐出 | 470K | 50.8K | 467K |
| recent1sum3fx（M=3，截断修复语义） | **12/12** | 摘要抄录 12/12 | 508K | 53.3K | 477K |
| recent1sum6（M=6） | 9/12 | 摘要抄录 9/12、从未输码 3（i05/i07/i08） | 681K | 65.8K | 734K |
| recent1retrnudge（取回+提示） | **10/12** | 取回钉住 7 + 文本通道携带 3；从未输码 2（i04/i12） | 493K | 52.6K | 815K |

对照（既有）：recent1 仅重试 0/12；recent1retr 无提示 2/12；recent1sum3 修复前语义 11/12。

**M 曲线判读**：M=2/3 满分、M=6 掉 3 例。蒸馏调用统计：M=2 54 调 0 截断；M=3fx 34 调 2 截断（i05/i06 各 1 次，重试同批帧后成功，12/12——重试语义生效）；M=6 20 调 8 截断散布 6 episode，i05 三次全截断=零蒸馏记忆→从未输码，i07/i08 各 1 调截断且 episode 结束前无第二次调用。**效度注意（混杂）**：SUMMARY_MAX_TOKENS=1200 固定上限 vs 一次批量帧数（M=6 ≈ 6 帧）——M=6 失利含「批量×固定输出预算」截断伪影，不能全部记为频率效应。复核 = recent1sum6b（上限随批帧数放宽）重跑 12 条；若回升则结论表述为「蒸馏可靠性随批量增长崩于截断」，若不回升才是「频率不足」。两种表述都支持工程上取 M≤3。

**nudge A/B 判读**：对照 recent1retr 2/12 → nudge 10/12。取回自发调用 2/12 → 8/12（7 次直取横幅帧），调后 8/8 成。弱形式（system 话术）把「不确定→回看」真正接上了。失利 2 例（i04/i12）通路一致且均 **0 次 review_screenshot**：把输入框占位提示「会员日优惠码」当答案打字 → ADB 无法输入中文转拼音 → clear → ask_human，fail-closed 正确闭合（未提交，verdict 如实记未恢复）。**这正是 Provenance Gate 硬触发的动机**：提交时刻值无出处是确定性可判的，提示接不上的模型路径，闸门可以接上。n=12 对 8/12 vs 2/12 的符号检验 p=0.012（单侧），效应量大。

**第四通路「自发抄录」**：i03/i06 无取回也成功，码首次出现在 type_text——携带通道是模型自己把横幅复述进对话文本（文本历史恒保真）。flash 三路归因（被动保留/显式取回/摘要抄录）外的第四类，写作时并入归因分类。取回臂 12 例的完整分布：取回 7 / 自发抄录 3 / 失利 2。

**成本四类（整批 48 条）**：未缓存输入 2,152,299｜缓存读 2,493,568｜缓存写 0（端点未回报 cache_creation）｜输出 222,529。M=6 臂反而最贵（失利 episode 跑满 20 步）。

数据产物：`research/results/flash-batch5.json`；episode 原始目录 `runs/episodes/{i01..i12}-c5-{recent1sum2,recent1sum3fx,recent1sum6,recent1retrnudge}`（本地保留，不清理）。

## 2026-11-09 批次 6 结果（M=6 效度复核 recent1sum6b，12/12）

执行：12/12 completed，无 invalid_reset、无 API 失败；预算帽内。目的：分离批次 5 M=6 失利的两种解释——「固定 1200 输出上限 × 6 帧批量」截断伪影 vs「蒸馏频率不足」。修复语义（batch 启动前入码）：蒸馏 max_tokens 随候选帧数放宽（≤3 帧维持 1200，>3 帧 400/帧 = 6 帧批量 2400），截断仍记失败并重试同批帧。

| 臂 | 恢复 | 机制归因（flash_analysis） | 蒸馏调用 | 未缓存输入 | 输出 | 缓存读 |
|---|---|---|---|---|---|---|
| recent1sum6b（M=6，批量感知输出预算） | 10/12（败 i03/i12） | 摘要抄录 10/10、从未输码 2 | 16 调 **0 截断 0 空** | 510K | 44.1K | 591K |

对照（同实例同码配对）：批 5 M=6 失利集 {i05,i07,i08} 修复后**全部成功**；修复后新失利集 {i03,i12}（批 5 该二例均成功）。失败集换血、总失败 3→2。

**截断伪影定性成立（i05 直接对照）**：批 5 i05 三次蒸馏全截断=零蒸馏记忆→从未输码；批 6 同实例蒸馏带码（摘要「红横幅『会员日·优惠码 KPBN』」）→ 输码成功。批 5 的 8/20 截断路径在批 6 归零（0/16）。

**第二失效机制（截断之外）**：i03/i12 的两次蒸馏调用均正常返回（无截断）但**码未入摘要**——6 帧批量的内容遗漏，预算充足也会丢瞬时横幅。二例随后把输入框占位提示「会员日优惠码」当答案打字 → ADB 中文输入失败转拼音 → fail-closed 未提交（0 次 review_screenshot）——与批 5 nudge 失利通路同型，是 PG 硬触发动机的第二个独立观察（提交时刻无出处值确定性可判）。

**M 曲线判读（终稿）**：M=2 12/12、M=3fx 12/12、M=6=9/12（截断混杂）→ 截断修复后 10/12。批量蒸馏有**两个独立失效机制**：①截断（输出预算不足，可修复）；②内容遗漏（码未入摘要，预算充足仍发生，2/12）。M≤3 两个机制均未观察到（批 5：0 遗漏、0 残留截断）。工程取 M≤3 结论不变，理由从「截断」扩展为「双失效」；n=12 只报计数，不做强推断。

**成本四类（12 条）**：未缓存输入 510,148｜缓存读 591,360｜缓存写 0（端点未回报）｜输出 44,066。

数据产物：`research/results/flash-batch6.json`（本地）；episode 原始目录 `runs/episodes/{i01..i12}-c5-recent1sum6b`（本地保留，不清理）。

## 2026-11-09 PG preflight（pg-preflight-1→4：两次 gate miss、修正与闭环验证）

同一配置（task_seed=4/fault_seed=4/promo/expected=p1，recent1retrpg 口径：recent+1 图+retrieval+PG，20 步）四次预检，规则两度修正：

| 预检 | 规则版本 | gate 观察 | 业务 |
|---|---|---|---|
| pg-preflight-1（11-08） | 通用字面出处 | 「会员日优惠码」pass（字面命中控件树文本） | fail |
| pg-preflight-2 | + 渠道化出处（ui 不算） | 「会员日优惠码」仍 pass（step 10）+ 拼音垃圾 ×2 | fail |
| pg-preflight-3 | + 归因仪表 | 「会员日」pass（matched_kinds=note×6） | fail |
| pg-preflight-4 | + 占位提示规则（含片段） | 「会员日优惠码」**blocked**（field_placeholder，note×5）→ `review_screenshot(step 0)` → 「QV6T」pass（note 自发抄录） | **success / recovered** |

- **修正 1（渠道化出处）**：控件树文本（ui）不构成提交值出处，合法渠道 = task/summary/note/feedback；消息文本前缀统一归类（`任务：`/`[当前 ` /`[视觉记忆摘要`，构造与归类共用常量），content0 任务/观察分块。动机 = 占位提示住在 ui 的实测失败通路（批 5 nudge 失利 ×2、批 6 i03/i12、pg-preflight-1）。
- **pg-preflight-2 观察**（渠道过滤后仍放行）→ 推断「转录洗白」：屏幕文案被抄进思考/工具回显后成了「出处」。由 preflight-3 的 matched_kinds 仪表坐实（note 转录 ×6）。
- **修正 2（占位提示规则 / 字段语义）**：V 属于当前屏输入框（EditText）占位提示/标签文案（相等或片段）→ 无视一切转录出处直接拦下（`trigger_reason="field_placeholder"`，专用文案「问题文案不是答案」）；gate 事件新增 trigger_reason/matched_kinds 归因字段。片段条款由 preflight-3 的「会员日」催生（等值 → 包含）。
- **pg-preflight-4（终验）**：step 8 拦截后模型依指路 review_screenshot(0) 取证横幅帧 → step 10 输真码 QV6T（note 自发抄录，横幅像素通路未被规则误伤）→ 提交成功，finish 自述出处「商城首页红色横幅」。**PG 局面首个 recovered=True**。
- **残余限制**：拼音转写垃圾（huiyuanri 等）非屏幕文案抄录，字面规则不拦（业务失败，非闸门缺陷）；占位提示内嵌建议词的误拦限制见 docs §4.1（research-env 无此类提示）。
- 测试 109/109（新增 ChannelPolicy/PlaceholderRule/agent 级占位提示与转录洗白共 14 测）。token 四类（未缓存输入/缓存读/缓存写/输出）：p2 62,523/129,792/0/15,475；p3 44,258/28,800/0/4,159；p4 66,731/29,440/0/4,924。episode 原始目录 `runs/episodes/pg-preflight-{1..4}` 本地保留不清理。

## 2026-11-09 PG A/B 批次（recent1retrpg vs recent1retrnudgeb，48/48）

**强形式 vs 弱形式直接对照**：24 实例（i01–i24，含种子集 2 复证）× c5（promo 瞬时线索）× 2 臂，同 task_seed/fault_seed 同码配对，执行顺序种子 20261107 随机化，预算帽 4M 输入/250K 输出（未触及）。对照臂 recent1retrnudgeb = 批 5 recent1retrnudge 同参数新标签在同 APK 环境重跑（批 5 原臂跑在旧 APK，跨环境不公；沿用 recent1sum6b 先例）。PG 臂 recent1retrpg = recent1+1 图+retrieval+Provenance Gate（首触拦截、重提=override、占位提示规则）。

| 指标 | recent1retrpg | recent1retrnudgeb |
|---|---|---|
| 业务 recovered | **23/24** | 16/24 |
| fault triggered | 24/24 | 24/24 |
| review_screenshot 调用 | 24 次（24 ep） | 15 次（15 ep） |
| gate blocked | 23（全 field_placeholder） | —（无闸门） |
| gate pass | 25 | — |
| override（重提确认） | 0 | — |
| 闸门命中率 / 假触发 | 23/23 = 100% / 0 | — |
| token 四类（未缓存输入/缓存读/缓存写/输出） | 1,205,492/1,605,888/0/88,415 | 1,251,997/1,461,504/0/100,217 |

- **配对：7 胜 0 负 17 平**（符号检验单侧 p≈0.0078）。nudge 失利 8 例（i02/i06/i07/i12/i16/i17/i19/i20）机理单一：占位提示「会员日优惠码」当答案打字 → ADB 中文失败转拼音垃圾（huiyuanri…）→ fail-closed 收场，7/8 零取回——弱话术不改变行为模式。
- **PG 机制链路 24/24**：每例首触拦占位提示（field_placeholder，转录洗白命中记 matched_kinds=note）→ 指路 review_screenshot 取证 → 真码入账；23 例业务成功。真码（像素出处）全部直接放行，假触发 0、override 0（被拦后均走取证路径而非重提确认）。
- **i19 归因（唯一业务失利）＝环境故障（ANR），非幻觉/非闸门/非输入污染**：ground truth promo_code=R9AH，step 11 打入「R9AH」与真码一致（闸门 pass）；step11 dump 字段仅入「R9A」（末字丢失，输入派发卡顿）→ step 12 补打「H」→ step14 dump 字段=完整「R9AH」——「H 污染成 R9AHH」假设证伪。step 13 提交时 research-env ANR（"研测商城 isn't responding" 系统弹窗），step 13–20 连续 ANR，第二笔 submit 从未到达业务层（gt 仅 1 次 submit=rejected_code_missing）。机制链路（拦占位→取证→真码入账）完整达成。全 48 条扫描 ANR 仅此一例；按协议留档不剔除，计入分母。归因分类新增第四类：**环境故障（ANR/崩溃）**与离机制模型失败、闸门触发、正确执行分列。
- **成本判读**：PG 臂输入/输出均略低于 nudge（1.21M/88K vs 1.25M/100K）——24 次取回的增量成本小于失败长尾烧掉的 token；闸门没有带来成本惩罚。
- 产物：`research/results/pg-ab-batch.json`（逐 episode 判分/gate 事件/归因）、`runs/episodes/i{01..24}-c5-{recent1retrpg,recent1retrnudgeb}/` 本地保留不清理。

## 2026-11-09 长任务 preflight 感知竞态事故与修复

**事故（观测）**：longtask preflight 第一条 t1r4s2g1 的 step00.png = 桌面启动器——app 冷启动慢于 open_app 固定 1.2s sleep（长时间空闲后首跑），画布横幅从未入帧即被首动作过期，证据不可感知；若不修复，曲线实验的失败将混入环境伪影。**竞态偶发非结构性**：同批 t1r2s2g1 的 step00.png 完美捕获横幅「会员日 · 优惠码 CS3J」；短任务 108 条 c5 扫描基本无此问题。全 48 条 PG A/B 扫描 ANR 仅 i19 一例（上节），长任务首条即撞冷启动竞态——长历史任务对环境就绪更敏感。

**归因链**：step00.png=桌面 → summary「第0步=启动页」→ env_control 固定 sleep 无前台保证 → state.json episode_id 是复位就绪信号（ControlReceiver 复位即回写，无 startActivity 依赖）→ ep2 完美对照坐实冷启动偶发。定性：**环境故障（感知竞态）**，归因四分类中「环境故障」的第二实证（第一例=ANR）。

**修复（生产边界，af552e2）**：
- 复位「完成」=state.json episode_id 对账：`broadcast_reset` 等 `_wait_state_episode`（轮询 read_state 直到 episode_id 对上，10s 超时抛「复位未生效」；无 episode 参数退固定等待 0.6s）。
- app「可观测」=前台焦点确认：`open_app` 轮询 `dumpsys window | grep mCurrentFocus` 直到 PKG 命中 + 0.5s 渲染安定，10s 超时抛「app 未取得前台焦点」——首帧即 app 首屏，横幅必入帧。
- 测试：research/tests/test_env_control.py 5 个语义用例（复位等待/超时/无 episode 退化、焦点等待/超时），unittest 全套件 114/114。

**留档（不清理）**：竞态污染的 t1r4s2g1-promo-recent1sum3（completed，未缓存输入 205K 留档计入事故成本）归档至 `runs/episodes/archive-2026-11-09-preflight-race/`——不归档会被曲线续跑当 completed 跳过。其余 8 条 T1 dry_reset_ok 目录不挡 live（live 只跳过 completed，stale run*/step* 自动归档 attempt-<ts>/）。

**复验（通过）**：重跑 preflight 2 条 T1 recent1sum3——t1r4s2g1 / t1r2s2g1 的 step00.png 均含横幅「会员日 · 优惠码 CS3J」（首帧即 app 首屏，与事故时桌面截图直接对照；同 fault_seed 决定论同码）；两条均 completed/recovered=True（rounds 4/4、2/2，triggered=1），token 四类 161,757/238,208/0/10,263 与 70,005/49,536/0/5,062。被杀残留自动归档 attempt-<ts>/ 可见。修复闭环成立，曲线批次启动。

## 2026-11-09 曲线批次第一段：系统代理断连事故（36 条 APIConnectionError）

**事故（观测）**：T1–T3 曲线批次（45 specs，full/recent1sum3/ev2sum3retrpg）第一段前 8 条正常（7 成功 + 1 跳过 preflight 成品），约 40 分钟后连发 36 条 `APIConnectionError`——连接层即败、零 token 损失，全部留档（协议：API 失败不剔除），断点续跑自动重试。

**归因**：Windows 系统代理 127.0.0.1:7892 上游 TLS 断连（进程在监听但握手失败）；anthropic 客户端 trust_env 默认经系统代理注册表建连 → 全部 API 调用走坏代理。curl 直连端点正常（http 404=链路通）；`httpx2.Client(trust_env=False)` 烟测直连成功（in=8/out=1）；`NO_PROXY=token-plan-cn.xiaomimimo.com` 前缀烟测同样成功。**归因类：环境故障（网络通路）**——ANR（i19）、感知竞态（长任务 preflight）之后第三实证，与模型失败分列。

**处置**：启动层 `NO_PROXY` 域名绕过续跑（不改代码、不动用户系统代理），36 条由续跑自动重试；两次启动间被导入模块零改动，臂内代码一致性保持。token 四类第一段 1,047,321/2,912,000/0/72,649（7 成功条）。

**残余**：结构性修复（API 建连不隐式依赖系统代理——trust_env=False 或显式通路参数）待批次完结后做；跑中改被导入模块违反冻结与臂内一致性，记入下一协议版本。教训：**模型 API 的网络通路是实验环境的一部分，须显式固定（不隐式依赖系统代理）**。

## 2026-11-09 曲线批次第二段：模拟器 system_server 假死与重启

**事故（观测）**：第一段 4 条成功后（含 t1r8s1g1-ev2pg 8/8 轮 570K 输入的最长任务），[13] t1r8s2g1-recent1sum3（K=8，22 分钟）completed 但 recovered=False（rounds 4/8，**曲线首条真实数据点**：蒸馏臂在长任务断轮，非环境非闸门）；其后 30 条 reset 即败 `cmd: Can't find service: activity`——模拟器 system_server 假死（WindowManager 活着、ActivityManager 丢失），host GPU 渲染崩溃（ColorBuffer 报错风暴）+ load average ~7 为伴生信号。续跑一段（33.8s 跑 0 条）确认非瞬时。

**处置**：TaskStop 停模拟器进程 → 同参数重启（零语义变化，episode 数据全在主机盘）→ 验收后续跑。**验收教训**：`sys.boot_completed=1` 不等于服务就绪——activity 服务注册晚于 boot 标志，须 `service check activity` 显示 found 才可跑（本次 boot 后首次检查 0 命中、15s 后 found）。research-env APK 在 userdata 持久保留（重启后 `pm list packages` 确认在）。

**归因类**：环境故障（模拟器系统假死）——ANR、感知竞态、网络通路之后第四实证。30 条失败留档零 token 损失，续跑自动重试。

**续跑再现与 GPU 后端切换（同日）**：重启后续跑 30 条再度全挂，但故障变形——`app 未取得前台焦点`（新就绪闸正确拦下，零 token）；手诊发现 **systemui ANR 弹窗抢占 mCurrentFocus**（research-env 实为 topResumedActivity，app 无责），back 键/force-stop systemui 均无效，host GPU ColorBuffer 崩溃风暴为拖垮源。处置：**模拟器 GPU 后端 `-gpu host` → `-gpu swiftshader_indirect`（软件渲染）**——guest 可见证据路径（screencap 帧缓冲、UI dump）不变，仅 host 渲染后端；启动参数变化在此留痕。验收口径三则补钉：①服务陆续注册，`service check activity` found 早于 window/package，须三者齐；②FallbackHome 临时桌面阶段 `am start` 报 Error type 3（PMS 未暴露组件），非安装损坏；③须等真 launcher 接管 + `sys.boot_completed=1` 后拉起 app 至焦点确认。

**systemui 反复 ANR 与弹窗治理（同日）**：swiftshader 后续跑 30 条仍全挂同一闸——诊断序列（probe 复位→episode 复位→open_app）复现坐实：**systemui 反复 ANR、`Application Not Responding` 弹窗独占 mCurrentFocus**（app 始终 topResumed，焦点闸 fail-closed 正确拦下；back/enter/force-stop systemui 均无法关闭弹窗）。处置：`settings put global hide_error_dialogs 1`（headless 长跑环境隐藏系统错误对话框，ANR 事件照常入 logcat）+ `uiautomator dump` 定位「Close app」按钮 (540,1236) 点掉存量弹窗，焦点回 research-env，诊断序列 open_app 通过。教训两条：**焦点闸验收不能只看 topResumed——mCurrentFocus 可能被系统弹窗独占；headless 批跑环境须 hide_error_dialogs=1 并在批前清点存量弹窗**。

## 下一执行顺序

1. 【完成】阶段 C：运行级 JSONL、成功后重试修正、冻结经验与三种上下文策略；请求协议和敏感动作边界验证。
2. 【完成】阶段 D：smoke 与预算门口径已过、12 实例 manifest 已固定并 dry-run 验证、180-episode pilot 完成。
3. 【完成】180-episode pilot（2026-11-07，180/180，1 种子）；方向门观察：本难度下三策略恢复率近天花板（98.6%），区分度落在成本结构；待核实 MiMo 缓存读计费口径后按计划决定是否增加种子/提高难度，禁止补填成功率或假造恢复实验。
4. 【完成】瞬时线索（promo）区分度实验：批次 1（84 条）+ 批次 2 稳健性种子（60 条，完美复现）+ 批次 2b 对照（24 条，recent1 在线索留新帧故障上两套实例均 12/12）——旧证据依赖的策略×故障交互在 24 实例/12 目录上干净成立。
5. 【完成】批次 3 五臂已成稿（60/60）：摘要蒸馏 11/12 全走「码入摘要」通道、显式取回仅 4/24 自发调用（4/4 功效）、关键帧 1/12+0/12；截断残缺文本修复 + 上限 1200（40/40 测试）。
6. 【完成】批次 4 消融四臂已成稿（48/48，2.48M 输入/196K 输出）：四臂 11–12/12 近满分、互相不可区分，蒸馏是唯一承载组件（41/47 摘要抄录、6/47 取回钉住、0 被动保留）；两例失败均离机制（清单多带预置商品 / ask_human 早退）。仅重试=recent1 走既有 0/12。
7. 【完成】批次 5 已成稿（48/48，未缓存输入 2.15M/输出 222K/缓存读 2.49M）：M 扫参 M=2/3=12/12、M=6=9/12（含截断混杂，拟 recent1sum6b 效度复核）；nudge A/B 恢复 2/12→10/12、取回调用 2/12→8/12（调后 8/8 成），失利 2 例=零取回把占位提示当答案。详见「批次 5 结果」节。
8. 【实现 + PG A/B 批次完成】创新方法与价值域实验：**Provenance Gate（证据溯源闸门）** 把三路归因机制变成动作时刻的闭环（无出处值→浮出可回看取证，不注入答案、不依赖 ground truth），对 ECHOES（arXiv:2610.22408，训练期特权蒸馏、部署丢像素）差异化为免训练/可取回/冻结权重可用；价值域 = 长任务族 T1–T3 × 历史长度/预算曲线，预算墙判据预声明（上下文 ≥90% 或成本 ≥3×）。见 [docs/provenance-gate-and-value-domain-2026-11-08.md](../docs/provenance-gate-and-value-domain-2026-11-08.md)。判定核心（渠道化出处 + 占位提示字段语义规则）+ agent.py type_text 挂钩（默认关）+ gate 归因仪表已实现，pg-preflight-1→4 两次 gate miss 两度修正后终验闭环成立；**PG A/B 批次已成稿（48/48）：PG 23/24 vs nudge 16/24（7 胜 0 负 17 平），机制链路 24/24、假触发 0、override 0，唯一失利 i19=ANR 环境故障**（见「PG A/B 批次」节）。随后顺序：长任务族 T1–T3 预算曲线 → 3 种子全量+聚类统计 → 第二 backbone → AndroidWorld → 写作定位。
9. 【基本完成，余预算曲线批次】效度与使能衔接：recent1sum6b 复核已成稿（截断伪影成立 + 第二失效机制「批量内容遗漏」，M≤3 结论不变）；长任务族 manifest/verdict 已实现（T1–T3 规格生成、真实复位条件解析、逐单判分），ControlReceiver 已补 promo_clue_round/promo_gate_after 透传，research-env APK 已构建并安装（2026-11-09，含订单账本、catalog_size、下单即表单作废），run_episode 已接 task_builder/verdict_fn 钩子（longtask_runner 可跑），longtask dry-run 15/15 验证通过（T1/T2/T3 规格与真实复位枚举全部正确）。余：T1–T3 预算曲线批次（启动后 runner 模块冻结）。
10. 阶段 F AndroidWorld 泛化验证；实体机与商业 App 后置可选。

研究产物均为新增文件；未覆盖既有 Android 源码、调研报告、交接文档或凭证。2026-11-09 经用户确认整理推送至 github.com:lplalbert/life-agent（公开仓库，含 docs/ 研究计划）；凭证核验：android/local.properties 与 .env 未入库、提交树无 token 串。未投稿。
