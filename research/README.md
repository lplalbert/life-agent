# Recovery-evidence research prototype

本目录是 life-agent 的独立研究准备，不改变现有 Android 产品流程。
完整规划：`../docs/top-conference-plan-2026-11-06.md`。
当前只有离线引用选择与本地证据盘点，不是完整 Agent、token 预算器或故障恢复基准。

在项目根运行（Python 标准库，无新增依赖、无模型费用）：

```powershell
python -m unittest discover -s research/tests -v
python -m research.context_policy research/example_events.json --policy recent --max-images 2
python -m research.context_policy research/example_events.json --policy evidence --max-images 2
python -m research.audit_runs runs
```

`example_events.json` 是人工构造的协议例子，引用的图片不存在。它只能检查选择行为，不能证明方法有效。
evidence 在两帧约束下优先保留最新可用图像和较早的未解决检查证据，并报告未容纳的证据。
所有原图引用保持不变；选择器不修改文件或发送 API 请求。

## 事件接口

JSON 顶层为按时间顺序排列的事件数组；`step` 是非负整数，允许同一步先观察再验证。

- observation：`kind`、`step`、`image_ref`（字符串或 null）。每步只允许一个 observation。
- verification：`kind`、`step`、`check_id`、`source_steps`、`state`。
- state 只能是 pending、failed、verified；同一 check_id 的新事件更新状态。
- source_steps 必须引用此前已经出现的 observation，不能引用未来。

调用时只能传入当前已知前缀，不能将运行结束后的 verified 状态回填到早先决策。
check_id 跨多次更新时保留身份；新动作的检查使用新身份。运行时检查器的误判也要单独评估。
故障控制器标签与独立最终评估结果不属于 agent 可见事件，不应放进此接口。

当前输出保留帧号、图像引用、未解决检查 ID 与未保留来源；它不输出实际 Messages 请求。
没有 runtime verification 的旧截图不能自动标为 verified，也不能据此离线评估恢复率。

## 证据盘点

audit_runs 只检查 runs 的直接子目录中的 stepNN.png/jpg/jpeg 和 stepNN_ui.txt；根目录散图不当 episode。
输出相对路径、大小与 SHA-256，不包含截图像素或 UI 文本。
task_success、provider_usage、actual_cost 保持 null；存在 log.txt 也不自动推断成功。
假想 replay 假设每次观察后调用一次模型，比较完整图片历史与最近两个图像文件的引用累计次数/字节数。
同一步多个图片文件会分别计数，因此这是文件级指标，不能自动当作帧数。
真实循环存在 pause_turn、重复调用、提前完成；这些假想数不代表实测 token、账单或性能。

## 在线接入完成条件

1. 固定 AndroidWorld 提交、任务映射、依赖、模型标识与价格口径。
2. 独立记录 fault controller 和 agent-visible event stream；初始化/结果检查/清理独立验证。
3. 在实际 Android 消息边界应用选择器等价策略，验证工具配对和 thinking 保真。
4. 冻结经验和系统提示，记录每次真实请求 usage、耗时与干预。
5. 跑正常/受扰配对任务后，才报告成功率或费用改进。

暂未实现：Android 接入、总 token 上限、摘要、动态检索、故障注入、统计器与 rollout runner。
不提供会自动操作真实交易 App 的研究脚本。
