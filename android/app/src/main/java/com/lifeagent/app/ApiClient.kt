package com.lifeagent.app

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/** 模型调用结果：内容块保留原始 JSON，保证 thinking 块等信息无损回传。 */
class ModelResponse(val raw: JSONObject) {
    val content: JSONArray get() = raw.getJSONArray("content")
    val stopReason: String get() = raw.optString("stop_reason", "")
    val usage: JSONObject get() = raw.optJSONObject("usage") ?: JSONObject()
}

/**
 * Anthropic 兼容 messages 端点客户端（小米 MiMo 网关 / 官方 API 通用）。
 * 认证用 x-api-key 头；BASE_URL/TOKEN/MODEL 来自 BuildConfig（local.properties）。
 */
object ApiClient {

    fun call(messages: JSONArray, system: String, tools: JSONArray): ModelResponse {
        val body = JSONObject().apply {
            put("model", BuildConfig.MODEL)
            put("max_tokens", 32000)
            put("thinking", JSONObject().put("type", "adaptive").put("display", "summarized"))
            put("output_config", JSONObject().put("effort", "medium"))
            put("system", JSONArray().put(
                JSONObject()
                    .put("type", "text")
                    .put("text", system)
                    .put("cache_control", JSONObject().put("type", "ephemeral"))
            ))
            put("tools", tools)
            put("messages", messages)
        }

        val url = URL(BuildConfig.BASE_URL.trimEnd('/') + "/v1/messages")
        val conn = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 15_000
            readTimeout = 120_000
            doOutput = true
            setRequestProperty("content-type", "application/json")
            setRequestProperty("x-api-key", BuildConfig.AUTH_TOKEN)
            setRequestProperty("anthropic-version", "2023-06-01")
        }
        try {
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader()?.use(BufferedReader::readText) ?: ""
            if (code !in 200..299) {
                throw ApiError("模型接口返回 $code: ${text.take(300)}")
            }
            return ModelResponse(JSONObject(text))
        } finally {
            conn.disconnect()
        }
    }
}

class ApiError(message: String) : Exception(message)
