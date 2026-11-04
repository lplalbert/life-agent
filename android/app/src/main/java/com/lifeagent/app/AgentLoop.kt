package com.lifeagent.app

import android.content.Context
import android.util.Base64
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** 涉及资金动作的按钮文案：tap 直接拒绝，必须走 confirm_and_tap。 */
val DANGER_KEYWORDS = listOf(
    "提交订单", "确认订单", "去支付", "立即支付", "确认支付", "支付订单",
    "去付款", "立即付款", "确认付款", "立即购买", "立即下单", "确认交易",
    "去结算", "合并结算", "提交结算", "开通免密", "免密支付", "刷脸支付",
    "确认开通", "自动续费", "开通会员",
)

val SYSTEM_PROMPT = """你是一名覆盖全生活服务的手机操作助手，通过截图和控件清单操控用户的安卓手机，完成外卖点餐、买菜生鲜、打车出行、订票订酒店、网购、充值缴费、电影娱乐等各类生活任务。

## 感知
每一步你会收到：
1. 当前屏幕截图；
2. 控件清单：每行 `#序号 "标签" 类型 [l,t,r,b]`，坐标是控件包围盒，点击请用包围盒中心点。
截图与清单以工具结果的形式随动作返回。页面可能有动画，动作结果未达预期时先观察再重试。
注意：任务启动后的第一屏可能是 LifeAgent 控制面板（含任务输入框和「开始任务」按钮），
那是你的控制界面，不是任务目标——不要点击「开始任务」，直接开始执行任务（如 launch_app 打开目标应用）。

## 行动规范
- 一次只做一件事：发出一个动作，看到结果后再决定下一步。
- 优先点击控件清单中的控件中心点；只有清单没有合适目标时才按截图估算坐标。
- 浏览列表用 swipe（看下方更多内容：y1 > y2，距离约 800-1500 像素）。
- 需要输入文字时用 type_text（支持中文，直接写入输入框）。
- 任务涉及哪个 App 就打开哪个；跨 App 的任务按需用 launch_app 切换。
- 不做与任务无关的操作，不浏览隐私内容，不更改系统设置（处理系统弹窗除外）。

## 消费与付款（核心要求）
- 任务要一路走到付款完成，绝不停在购物车或订单确认页（除非用户任务本身只要求到那一步）。
- 消费按钮（提交订单/去支付/去结算/立即购买/开通免密/自动续费类）必须用 confirm_and_tap 请求确认，说明按钮和金额；直接 tap 会被安全闸门拒绝。若用户开启了「自动确认」模式，confirm_and_tap 会自动通过，请直接继续执行。
- 用户确认后按顺序完成：提交订单 → 进入收银台 → 点击「确认支付/立即支付」。
- 点击支付后若出现指纹/密码/面容验证或支付密码框：这一步在系统层面只能由用户完成，立即 finish(status="need_human")，summary 写明订单内容、金额，请用户完成验证。
- 若订单因免密支付直接完成 → finish(status="done")。
- 遇到验证码（滑块、点选、短信验证）→ ask_human 请用户处理。
- 遇到系统安全弹窗（如「"LifeAgent"想要打开"美团"」的应用跳转提醒）→ 点「允许」继续，这仅是允许打开目标应用。
- 同一动作失败 2 次就换思路；失败 3 次应 finish(status="failed") 说明卡在哪里。

## 打开应用
- 优先用 launch_app（包名见下方）；包名不确定或启动失败时，按 home 回桌面，从截图中找到目标 App 图标点击打开。
- 已知包名：美团 com.sankuai.meituan｜饿了么 me.ele｜大众点评 com.dianping.v1｜淘宝 com.taobao.taobao｜京东 com.jingdong.app.mall｜拼多多 com.xunmeng.pinduoduo｜支付宝 com.eg.android.AlipayGphone｜微信 com.tencent.mm｜铁路12306 com.MobileTicket｜携程 ctrip.android.view｜去哪儿 com.Qunar｜滴滴 com.sdu.didi.psnger｜麦当劳 com.mcdonalds.gma.cn｜肯德基 com.yek.android.kfc.activitys

## 结束
到达终点时调用 finish：
- status="done"：任务完成（如订单已支付、打车已叫到、充值已到账），summary 写清做了什么、金额、结果。
- status="need_human"：需要用户在手机上完成支付验证/验证码/选择，summary 写清需要用户做什么、订单金额和当前状态。
- status="failed"：无法完成，summary 写明原因和已尝试步骤。
"""

fun buildTools(): JSONArray = JSONArray().apply {
    put(tool("tap", "点击屏幕坐标 (x, y)。优先点击控件清单中控件的包围盒中心点。", propXY(), listOf("x", "y")))
    put(tool("swipe", "从 (x1,y1) 滑动到 (x2,y2)。浏览列表下方内容：y1 > y2。",
        JSONObject().apply {
            put("x1", intProp()); put("y1", intProp())
            put("x2", intProp()); put("y2", intProp())
            put("duration_ms", JSONObject().put("type", "integer").put("description", "滑动时长，默认 400"))
        }, listOf("x1", "y1", "x2", "y2")))
    put(tool("long_press", "长按坐标 (x, y)，用于弹出菜单等场景。",
        JSONObject().apply {
            put("x", intProp()); put("y", intProp())
            put("duration_ms", JSONObject().put("type", "integer").put("description", "按住时长，默认 800"))
        }, listOf("x", "y")))
    put(tool("type_text", "向当前已聚焦的输入框写入文字（先用 tap 点中输入框），支持中文。",
        JSONObject().put("text", JSONObject().put("type", "string").put("description", "要输入的文本")), listOf("text")))
    put(tool("clear_text", "清空当前聚焦的输入框。", JSONObject(), listOf()))
    put(tool("press", "按全局按键。",
        JSONObject().put("key", JSONObject().put("type", "string")
            .put("enum", JSONArray(listOf("back", "home", "recents")))), listOf("key")))
    put(tool("launch_app", "启动指定包名的应用（包名见系统提示的已知应用表）。",
        JSONObject().put("package", JSONObject().put("type", "string").put("description", "应用包名")), listOf("package")))
    put(tool("ask_human", "向用户提问并等待回答（支付、验证码、偏好选择等场景）。",
        JSONObject().put("question", JSONObject().put("type", "string")), listOf("question")))
    put(tool("confirm_and_tap", "请求用户确认后点击敏感按钮（提交订单/支付/结算/开通类）。普通点击用 tap。",
        JSONObject().apply {
            put("x", intProp()); put("y", intProp())
            put("description", JSONObject().put("type", "string").put("description", "该按钮是什么、涉及多少金额"))
        }, listOf("x", "y", "description")))
    put(tool("finish", "结束任务并汇报结果。",
        JSONObject().apply {
            put("status", JSONObject().put("type", "string").put("enum", JSONArray(listOf("done", "need_human", "failed"))))
            put("summary", JSONObject().put("type", "string").put("description", "做了什么、金额、停在哪/需要用户做什么"))
        }, listOf("status", "summary")))
}

private fun intProp() = JSONObject().put("type", "integer")
private fun propXY() = JSONObject().apply { put("x", intProp()); put("y", intProp()) }

private fun tool(name: String, desc: String, props: JSONObject, required: List<String>) =
    JSONObject().apply {
        put("name", name)
        put("description", desc)
        put("input_schema", JSONObject()
            .put("type", "object")
            .put("properties", props)
            .put("required", JSONArray(required)))
    }

/** 任务运行统计。 */
data class RunStats(
    var inputTokens: Int = 0,
    var outputTokens: Int = 0,
    var cacheRead: Int = 0,
    var cacheCreation: Int = 0,
)

/**
 * Agent 主循环：感知 → 模型决策 → 安全闸门 → 执行。
 * 与 Python 版 life_agent/agent.py 语义对齐。
 */
class AgentLoop(
    private val service: AgentService,
    private val autoConfirm: Boolean = false,
    private val log: (String) -> Unit,
) {
    var maxSteps = 30
    val stats = RunStats()
    private var finished: Pair<String, String>? = null

    suspend fun run(task: String, runDir: File): RunStats {
        finished = null
        val tools = buildTools()
        val messages = JSONArray()
        val (imgBlock0, uiText0) = perceive(0, runDir)
        messages.put(JSONObject().put("role", "user").put("content", JSONArray().apply {
            put(imgBlock0)
            put(JSONObject().put("type", "text").put("text", "任务：$task\n\n$uiText0"))
        }))
        var nudges = 0

        for (step in 1..maxSteps) {
            log(">>> 第 $step 步")
            val response = try {
                ApiClient.call(messages, SYSTEM_PROMPT, tools)
            } catch (e: Exception) {
                log("  [X] 模型调用失败: ${e.message}")
                finished = finished ?: ("failed" to "模型调用失败: ${e.message}")
                break
            }
            addUsage(response)

            for (i in 0 until response.content.length()) {
                val block = response.content.getJSONObject(i)
                when (block.optString("type")) {
                    "text" -> {
                        val t = block.optString("text").trim()
                        if (t.isNotEmpty()) log("  >> ${t.take(400)}")
                    }
                    "thinking" -> {
                        val t = block.optString("thinking").trim()
                        if (t.isNotEmpty()) log("  .. ${t.lineSequence().first().take(120)}")
                    }
                }
            }
            messages.put(JSONObject().put("role", "assistant").put("content", response.content))

            when (response.stopReason) {
                "pause_turn" -> continue
                "refusal" -> {
                    log("  [!] 模型拒绝了该请求")
                    finished = finished ?: ("failed" to "模型拒绝了该请求")
                    break
                }
                "max_tokens" -> {
                    log("  [!] 输出被 max_tokens 截断")
                    finished = finished ?: ("failed" to "输出截断")
                    break
                }
            }

            val toolUses = (0 until response.content.length())
                .map { response.content.getJSONObject(it) }
                .filter { it.optString("type") == "tool_use" }
            if (toolUses.isEmpty()) {
                nudges++
                if (nudges <= 2) {
                    messages.put(JSONObject().put("role", "user").put("content", JSONArray().put(
                        JSONObject().put("type", "text")
                            .put("text", "请继续调用工具完成任务；结束时必须调用 finish。"))))
                    continue
                }
                log("  [!] 模型连续未调用工具，停止")
                finished = finished ?: ("failed" to "模型停止调用工具")
                break
            }

            val results = JSONArray()
            for (tu in toolUses) {
                val args = tu.optJSONObject("input") ?: JSONObject()
                val argStr = args.keys().asSequence().joinToString(", ") { "$it=${args.get(it)}" }
                log("  -> ${tu.getString("name")}(${argStr.take(120)})")
                val (out, isErr) = execute(tu.getString("name"), args)
                log("    ${if (isErr) "X" else "OK"} ${out.take(200)}")
                val result = JSONObject()
                    .put("type", "tool_result")
                    .put("tool_use_id", tu.getString("id"))
                    .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", out)))
                if (isErr) result.put("is_error", true)
                results.put(result)
            }

            // 动作之后重新感知，附在最后一个 tool_result 上
            val (imgBlock, uiText) = perceive(step, runDir)
            val last = results.getJSONObject(results.length() - 1)
            val lastContent = last.getJSONArray("content")
            lastContent.put(imgBlock)
            lastContent.put(JSONObject().put("type", "text").put("text", uiText))
            messages.put(JSONObject().put("role", "user").put("content", results))

            if (finished != null) break
        }

        val (status, summary) = finished
            ?: ("failed" to "达到最大步数 $maxSteps 或提前终止，任务未完成")
        val icon = when (status) {
            "done" -> "[OK]"
            "need_human" -> "[人工]"
            else -> "[失败]"
        }
        log("--------------------------------------------------")
        log("$icon 任务结果：$status")
        log("   $summary")
        log("   token：输入 ${stats.inputTokens} | 输出 ${stats.outputTokens} | 缓存读 ${stats.cacheRead} | 缓存写 ${stats.cacheCreation}")
        log("   计费：${BuildConfig.MODEL} 按所接入服务的 token 计划计费")
        return stats
    }

    private fun addUsage(response: ModelResponse) {
        val u = response.usage
        stats.inputTokens += u.optInt("input_tokens", 0)
        stats.outputTokens += u.optInt("output_tokens", 0)
        stats.cacheRead += u.optInt("cache_read_input_tokens", 0)
        stats.cacheCreation += u.optInt("cache_creation_input_tokens", 0)
    }

    private suspend fun perceive(step: Int, runDir: File): Pair<JSONObject, String> {
        val jpeg = service.screenshotJpeg()
        val nodes = service.dumpUi()
        runDir.mkdirs()
        File(runDir, "step%02d.jpg".format(step)).writeBytes(jpeg)
        val uiText = service.compactUi(nodes)
        File(runDir, "step%02d_ui.txt".format(step)).writeText(uiText, Charsets.UTF_8)
        val imageBlock = JSONObject()
            .put("type", "image")
            .put("source", JSONObject()
                .put("type", "base64")
                .put("media_type", "image/jpeg")
                .put("data", Base64.encodeToString(jpeg, Base64.NO_WRAP)))
        val text = "[当前 ${service.currentApp()}]\n控件清单（包围盒 [l,t][r,b]，点击用中心点）：\n$uiText"
        return imageBlock to text
    }

    private suspend fun execute(name: String, args: JSONObject): Pair<String, Boolean> = try {
        dispatch(name, args) to false
    } catch (e: Exception) {
        ("操作失败: ${e.message}" to true)
    }

    private suspend fun dispatch(name: String, args: JSONObject): String = when (name) {
        "tap" -> {
            val x = args.getInt("x")
            val y = args.getInt("y")
            val label = service.nodeAt(x, y)?.label.orEmpty()
            if (label.isNotEmpty() && DANGER_KEYWORDS.any { label.contains(it) }) {
                throw Exception(
                    "安全闸门：($x,$y) 是敏感按钮「$label」（提交/支付/开通类）。" +
                        "请改用 confirm_and_tap 请用户确认；最终支付交给用户完成。",
                )
            }
            service.tap(x, y)
            "已点击 ($x,$y)" + if (label.isNotEmpty()) " 「$label」" else ""
        }
        "swipe" -> {
            service.swipe(args.getInt("x1"), args.getInt("y1"), args.getInt("x2"), args.getInt("y2"),
                args.optInt("duration_ms", 400))
            "已滑动 (${args.getInt("x1")},${args.getInt("y1")}) → (${args.getInt("x2")},${args.getInt("y2")})"
        }
        "long_press" -> {
            service.longPress(args.getInt("x"), args.getInt("y"), args.optInt("duration_ms", 800))
            "已长按 (${args.getInt("x")},${args.getInt("y")})"
        }
        "type_text" -> {
            val text = args.getString("text")
            service.typeText(text)
            "已输入：$text"
        }
        "clear_text" -> {
            service.clearText()
            "已清空输入框"
        }
        "press" -> service.press(args.getString("key"))
        "launch_app" -> {
            service.launchApp(args.getString("package"))
            "已启动 ${args.getString("package")}"
        }
        "ask_human" -> {
            val answer = PromptActivity.ask(service, args.optString("question", ""))
            if (answer != null) "用户回答：$answer" else "用户未回答，请自行处理或 finish(need_human)"
        }
        "confirm_and_tap" -> {
            val x = args.getInt("x")
            val y = args.getInt("y")
            val desc = args.optString("description", "")
            val label = service.nodeAt(x, y)?.label.orEmpty()
            val btn = label.ifEmpty { desc }
            if (autoConfirm) {
                service.tap(x, y)
                "自动确认模式：已点击「$btn」。继续执行到付款完成。"
            } else {
                val ok = PromptActivity.confirm(service, btn, desc)
                if (!ok) {
                    "用户拒绝了该操作。请停止或换方案。"
                } else {
                    service.tap(x, y)
                    "用户已确认，已点击「$btn」。请继续执行直到付款完成。"
                }
            }
        }
        "finish" -> {
            val status = args.optString("status", "done")
            val summary = args.optString("summary", "")
            finished = status to summary
            "finish($status): $summary"
        }
        else -> throw Exception("未知工具: $name")
    }
}
