package com.lifeagent.app

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 操作经验库：跨运行自我培养的核心。
 *
 * 每次任务结束后调用 reflect()，把运行日志复盘成可复用的 App 操作经验
 * （入口特点、弹窗坑、突发情况处理手法），写入 dataDir/experience.md；
 * 下次运行由 AgentLoop 注入系统提示词。经验是设备本地文件，不上传。
 */
object Experience {

    private const val FILE_NAME = "experience.md"
    private const val MAX_CHARS = 12_000

    private val REFLECT_PROMPT = """你是手机 App 操作经验整理器。给你一次任务的运行日志和现有经验库，输出更新后的经验库。

要求：
- 只提炼可复用的手机 App 操作知识：App 的入口/交互特点、容易踩的坑（弹窗、起送价、付费陷阱、协议页）、突发情况的处理手法、跨 App 通用的操作法则。
- 与本次任务具体内容相关的（买了什么、商品价格、地址等）不要记。
- 保留仍然有效的旧经验；与新证据冲突且被推翻的删掉；重复的合并。
- 按「## 通用」「## 应用名」分节；每条一行，以「遇到…就…」的行动导向书写。
- 全文不超过 80 行。
- 只输出更新后经验库的 Markdown 正文，不要任何解释。"""

    fun fileOf(dataDir: File): File = File(dataDir, FILE_NAME)

    fun load(dataDir: File): String = try {
        fileOf(dataDir).takeIf { it.exists() }?.readText(Charsets.UTF_8)?.take(MAX_CHARS) ?: ""
    } catch (_: Exception) {
        ""
    }

    /** 运行结束后复盘日志、更新经验库。任何失败都不影响任务本身。 */
    fun reflect(task: String, logFile: File, dataDir: File, onLog: (String) -> Unit) {
        val logText = try { logFile.readText(Charsets.UTF_8) } catch (_: Exception) { return }
        val condensed = logText.lineSequence()
            .filter { it.isNotBlank() && !it.startsWith("  .. ") }
            .map { it.take(160) }
            .toList()
            .takeLast(240)
            .joinToString("\n")
        val old = load(dataDir)
        val messages = JSONArray().put(JSONObject().put("role", "user").put("content",
            "任务：$task\n\n== 现有经验库 ==\n${old.ifEmpty { "（空）" }}\n\n== 本次运行日志 ==\n$condensed\n\n请输出更新后的经验库。"))

        val resp = ApiClient.call(messages, REFLECT_PROMPT, JSONArray())
        val sb = StringBuilder()
        for (i in 0 until resp.content.length()) {
            val b = resp.content.getJSONObject(i)
            if (b.optString("type") == "text") sb.append(b.optString("text"))
        }
        val updated = sb.toString().trim().take(16_000)
        if (updated.isNotEmpty()) {
            fileOf(dataDir).writeText(updated + "\n", Charsets.UTF_8)
            onLog("已复盘并更新操作经验（${updated.lineSequence().count()} 行）")
        }
    }
}
