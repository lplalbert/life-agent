# 未知突发环境下的 GUI Agent：鲁棒性与 token 效率调研

> 调研问题：**随时出现突发情况的子任务，只有一个大概的路线，途中会遇到很多未知问题——如何提高 agent 准确处理问题的能力，同时节约 token 消耗？**
>
> 调研方式：Semantic Scholar API + arXiv API（经本地代理，2026-11-06）。经典论文的 venue/年份/引用数经 S2 核对；2025-2026 新文为 arXiv 检索标题+摘要，未核对正式发表处。文末有核验说明。
>
> 对照对象：life-agent 现有实现（Experience.kt 经验库、SYSTEM_PROMPT 突发情况处理、compactUi 控件清单、gate-operator 干跑闸门）。

---

## 0. 先把问题拆成三层

「大概路线 + 未知突发 + 省 token」在文献里对应三个正交方向：

1. **经验层**：把「大概路线」变成越用越准的可复用知识（跨任务记忆、指南归纳、技能库）；
2. **执行层**：途中出事时准确认知-恢复（失败模式、动作验证、何时停止/重试/换路）；
3. **表示层**：每步发给模型的观察和历史怎么压（视觉 token、轨迹历史、缓存）。

三者解耦——先修执行层的保命能力，再谈经验层的复利，表示层则随时可以省钱。

---

## 1. 经验层：从「大概路线」到自我培养

| 工作 | 出处 | 与本问题的关联 |
|---|---|---|
| **Reflexion** | NeurIPS 2023，6585 引，[arXiv:2303.11366](https://arxiv.org/abs/2303.11366) | 失败轨迹→语言化反思→下一轮重试，不改参数。单任务内的试错闭环 |
| **ExpeL** | AAAI 2023，1276 引，[arXiv:2308.10144](https://arxiv.org/abs/2308.10144) | 跨任务积累 **insights（行动导向经验）** 注入提示词——与 Experience.kt 同路线 |
| **Voyager** | TMLR 2023，2807 引，[arXiv:2305.16291](https://arxiv.org/abs/2305.16291) | 经验沉淀为**可执行技能库**（代码），遇新情况组合调用 |
| **Agent Workflow Memory (AWM)** | ICML 2024，462 引，[arXiv:2409.07429](https://arxiv.org/abs/2409.07429) | 从轨迹归纳**工作流**（常用子流程），供后续任务复用 |
| WebATLAS | arXiv 2025 | 经验驱动记忆 + **动作模拟**（先预演再执行，降低真实环境试错） |
| Experience Compression Spectrum | arXiv 2026 | 把记忆/技能/规则统一为「经验压缩谱系」：原始轨迹→摘要→规则，越往上越省 token 越可复用 |
| Memex(RL) | arXiv 2026 | **索引式**经验记忆：长程任务只检索相关经验入上下文，而不是全量重放 |
| How Memory Management Impacts LLM Agents | arXiv 2025 | 实证：记忆的**管理策略**（存什么、何时取）比记忆本身更影响「经验跟随行为」 |
| Naive Visual Memory is Not Enough | arXiv 2026 | **失败模式研究**：朴素的 GUI 视觉经验记忆会反噬（错误经验被跟随）——经验必须带证据与冲突裁剪 |
| Evo-Memory | arXiv 2025 | 自进化记忆基准（test-time learning），测记忆「越用越好」是否真实成立 |

**对 life-agent 的含义**：Experience.kt（运行日志→REFLECT_PROMPT→experience.md→注入 SYSTEM_PROMPT）就是 ExpeL 路线的最小实现，方向正确。文献给出的三个升级点：

1. **冲突裁剪与证据**（Naive Visual Memory 的教训）：当前 REFLECT_PROMPT 已要求「被推翻的删掉」，继续保留；后续每条经验可带上「哪次运行验证」的来源标注，防止错误经验代代相传。
2. **检索化而非全量注入**（MemexRL/AWM）：experience.md 现在全文注入且截 12K 字符；经验多了以后应按当前 App/任务关键词检索子集注入，省 token 且减少干扰。
3. **规则化压缩**（Experience Compression Spectrum）：稳定经验升格为短规则（「遇到…就…」已经是规则式），不稳定细节留在轨迹层。

> 「试跑→归纳指南（Auto-Guide 类 guideline induction）」思路在检索中未能核验到确切出处，不列；其思想已由 ExpeL 的 insight 提取与 AWM 的工作流归纳覆盖。

---

## 2. 执行层：途中出事时怎么准确处理

| 工作 | 出处 | 与本问题的关联 |
|---|---|---|
| **VLAA-GUI** | arXiv 2026 | 明确三个时刻的判断：**何时停止、何时恢复、何时搜索**——对应「失败 2 次换思路」「不要原地连点」 |
| **Don't Act Blindly** | arXiv 2026 | **动作-效果验证**：每步执行后先核对界面是否如预期变化，再决定下一步（网络延迟/慢加载下动作常假成功） |
| **Reflect, Revise, Reuse** | arXiv 2026 | 弹窗、慢加载、控件挪位会让「出发前定死的计划」失效，主张执行中**技能演化**而非静态计划 |
| **GUI-HARVEST** | arXiv 2026 | 在 **harness 层**（观察怎么装配、动作怎么执行、验证/恢复/终止怎么控制）做证据驱动自改进——与我们改 AgentLoop 的路线同构 |
| LocalLSTC | arXiv 2026 | 长短期双记忆的控制架构：短期=当前页面状态，长期=持久控制信息，避免每步在长轨迹里重推理 |
| InfiGUI-R1 | arXiv 2025 | 从「反应式点按」到「审慎推理」（先想再动） |
| Do GUI Agents Believe Their Eyes? | arXiv 2026 | **像素 vs 结构双通道**信念研究：截图与控件树是冗余互补信源，单通道易被带偏 |
| The GUI Is Not the State | arXiv 2026 | 「状态混淆（aliasing）」：只看当前观察会误判页面状态，需要动作历史参与状态判断 |
| ALAS | arXiv 2025 | 中断感知规划（disruption-aware）：事务式步骤被打断后的恢复 |
| SolveBind | arXiv 2026 | 验证墙/反自动化（验证码）场景——对应 ask_human 人机分工边界 |

**对 life-agent 的含义**：

- SYSTEM_PROMPT 的「突发情况处理」章节（未知弹窗分类、页面残留清理、预期不符换路径、失败 2 次换思路）就是 VLAA-GUI/Reflect-Revise 思想的提示词级实现——**轻量、零成本、已在真机验证有效**（模型自主跳过开屏广告、恢复滴滴协议页都是它的功劳）。
- 2026-11-06 美团干跑暴露的案例是「Don't Act Blindly」的反面教材：感知三通道（截图/控件树/手势）在息屏时同灭，感知失败后模型全盲编故事认输。修复（截图与控件树独立降级 + 亮屏锁 + 错误文案给自救指路）正是 Don't Act Blindly 与「像素 vs 结构双通道」的工程落地：**一个通道挂了另一个要顶上，且失败本身要可感知、可恢复**。
- 双通道信念值得继续强化：控件清单（结构）+截图（像素）已并行；下一步是冲突时以谁为准的规则显式化（提示词里已有「以屏幕为准」雏形）。

---

## 3. 表示层：token 从哪里省

实测锚点（2026-11-06 美团干跑，24 步）：**输入 91,503 / 输出 6,637 / 缓存读 961,536**。缓存读是大头——每步把全部历史（含旧截图）重放一遍。

| 工作 | 出处 | 与本问题的关联 |
|---|---|---|
| **Set-of-Mark (SoM)** | 2023，273 引，[arXiv:2310.11441](https://arxiv.org/abs/2310.11441) | 在截图上叠标注序号替代大段描述——compactUi 的 `#序号 "标签" [bounds]` 就是它的文本版 |
| **LLMLingua** | EMNLP 2023，427 引，[arXiv:2310.05736](https://arxiv.org/abs/2310.05736) | 通用提示压缩（困惑度驱动删词） |
| AQuaUI | arXiv 2026 | GUI 截图**四叉树**视觉 token 裁剪：平坦区域少 token，细节区域多 |
| STP-GUI / Spatio-Temporal Token Pruning | arXiv 2026 | 空间感知/时空感知的截图 token 裁剪 |
| GUI-KV | arXiv 2025 | KV 缓存时空感知复用（多轮 GUI 的历史缓存重用） |
| TRACE | arXiv 2026 | 轨迹证据排序 + 缓存复用约束分析 |
| iSHIFT | arXiv 2025 | 慢快双模感知：粗看全局、细看焦点，按需决定感知精度 |
| Compress to Focus | arXiv 2026 | **多轮 GUI 的上下文膨胀**问题：历史坐标/观察压缩后保留长期上下文 |
| ToolTok | arXiv 2026 | 动作空间 token 化（坐标表示泛化 + 省 token） |

**对 life-agent 的含义（按收益排序）**：

1. **历史轮图像清理（最大杠杆）**：messages 里每一轮的截图永久留在历史中，24 步就是 20+ 张图随每次请求重放。改为只保留最近 1-2 帧图像，旧帧替换为其控件清单文本（或一行「第 N 步截图略」）——直接砍掉缓存读的大头。这是 Compress to Focus 指出的多轮膨胀主因。
2. **控件清单页面级去重**：页面没变化时不必每步重发全量清单（150 行 × 每步）；可对相同 uiText 做哈希，重复页面只发「界面未变化」。
3. **已有且保持**：截图长边缩 1568、compactUi 紧凑清单（SoM 思想）、system+tools 的 prompt cache（ephemeral）、输出 effort medium。
4. **暂缓**：视觉 token 裁剪类（AQuaUI/STP-GUI）多为模型侧/训练侧方案，对 API 调用方收益有限；LLMLingua 式删词压缩对结构化控件清单风险大于收益。

---

## 4. 背景与基准（评估口径）

| 工作 | 出处 | 用途 |
|---|---|---|
| AndroidWorld | ICLR 2024，531 引，[arXiv:2405.14573](https://arxiv.org/abs/2405.14573) | 安卓动态基准：116 任务/20 应用，可对照「突发处理」能力的量化口径 |
| Mobile-Agent-v2 | NeurIPS 2024，263 引，[arXiv:2406.01014](https://arxiv.org/abs/2406.01014) | 多 agent 分工（规划/执行/反思）的移动操作助手 |
| AppAgent | CHI 2023，593 引，[arXiv:2312.13771](https://arxiv.org/abs/2312.13771) | 探索式学习：自主探索 App 写使用文档 |
| UFO | NAACL 2024，184 引，[arXiv:2402.07939](https://arxiv.org/abs/2402.07939) | Windows UI 双 agent（规划/执行）架构 |

---

## 5. 落地路线（结合现状的优先级）

1. **历史图像裁剪**（token 大头，改动小）：AgentLoop messages 管理旧帧图像替换为文字化。预期缓存读显著下降。
2. **经验库检索化**（经验多于 ~50 条后做）：按 App/关键词检索子集注入，替代全文；每条经验带来源运行标注。
3. **动作-效果验证规则**强化（提示词已有雏形）：动作后对比「预期变化」显式化为检查点，配合 VLAA-GUI 的停止/恢复/搜索三判断。
4. **感知双通道解耦**（2026-11-06 已修）：截图挂了控件树顶上；后续可加「感知失败自动唤屏」的硬件层兜底（WakeLock 已加，自动化重试解锁可再进一步）。

---

## 核验说明

- Reflexion/ExpeL/Voyager/AWM/SoM/LLMLingua/AndroidWorld/Mobile-Agent-v2/AppAgent/UFO 的标题、venue、年份、引用数经 Semantic Scholar API 核对（2026-11-06）；arXiv ID 与标题一一对应核验通过。
- 2025-2026 年论文为 arXiv API 检索结果（标题、年份、摘要为原文摘录），**未核对正式发表 venue，引用数未查**。
- Auto-Guide（指南归纳）类工作未能核验到确切出处，未列。
- 引用数为 2026-11-06 查询值，随时间变化。
