package com.lifeagent.researchenv

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

/**
 * 主机实验控制器专用广播入口（adb am broadcast）。
 *
 * 命令：
 *   cmd=reset   复位整个 episode。配置经平铺 extras 传入（避开 adb shell JSON 转义）：
 *               --es episode <id> --ei task_seed <n> --ei fault_seed <n>
 *               --es fault <none|delay|noop|popup|confusion|promo> --ei delay_ms <n> --ei noop_n <n>
 *               --es popup_at <enter_detail|after_add|enter_cart|enter_checkout>
 *               --es confusion_at <enter_cart|enter_checkout>
 *               --es expected_ids <p1,p3> --es initial_cart <p2>
 *               --es catalog_size <1..8>（默认 4） --es promo_rearm <true|false>（默认 false）
 *               --es promo_clue_round <n>（线索在第 n 轮武装，默认 1）
 *               --es promo_gate_after <n>（第 n 轮起结算须码，默认 0=立即闸）
 *               复位后写 state.json，post_reset_clean 供独立探针确认。
 *   cmd=state   只重写 state.json（不改业务状态），供控制器随时读取 ground truth。
 *
 * 该接口不注册为 Agent 工具；界面不含任何故障标签/预期答案。
 * ground truth 读取通道：adb shell run-as com.lifeagent.researchenv cat files/state.json
 */
class ControlReceiver : BroadcastReceiver() {

    override fun onReceive(ctx: Context, intent: Intent) {
        val appCtx = ctx.applicationContext
        when (intent.getStringExtra("cmd")) {
            "reset" -> {
                val cfg = JSONObject()
                intent.getStringExtra("episode")?.let { cfg.put("episode_id", it) }
                intent.getStringExtra("task_seed")?.let { cfg.put("task_seed", it.toLongOrNull() ?: 1L) }
                intent.getStringExtra("fault_seed")?.let { cfg.put("fault_seed", it.toLongOrNull() ?: 1L) }
                intent.getStringExtra("fault")?.let { cfg.put("fault", it) }
                intent.getStringExtra("delay_ms")?.let { cfg.put("delay_ms", it.toLongOrNull() ?: 0L) }
                intent.getStringExtra("noop_n")?.let { cfg.put("noop_n", it.toIntOrNull() ?: 0) }
                intent.getStringExtra("popup_at")?.let { cfg.put("popup_at", it) }
                intent.getStringExtra("confusion_at")?.let { cfg.put("confusion_at", it) }
                strList(intent.getStringExtra("expected_ids"))?.let { cfg.put("expected_ids", JSONArray(it)) }
                strList(intent.getStringExtra("initial_cart"))?.let { cfg.put("initial_cart", JSONArray(it)) }
                intent.getStringExtra("catalog_size")?.let { cfg.put("catalog_size", it.toIntOrNull() ?: 4) }
                intent.getStringExtra("promo_rearm")?.let { cfg.put("promo_rearm", it.equals("true", ignoreCase = true)) }
                intent.getStringExtra("promo_clue_round")?.let { cfg.put("promo_clue_round", it.toIntOrNull() ?: 1) }
                intent.getStringExtra("promo_gate_after")?.let { cfg.put("promo_gate_after", it.toIntOrNull() ?: 0) }
                EnvState.reset(appCtx, cfg)
            }
            else -> EnvState.writeState(appCtx) // 未知命令不改状态，仅刷新快照
        }
    }

    private fun strList(raw: String?): List<String>? =
        raw?.split(",")?.map { it.trim() }?.filter { it.isNotEmpty() }
}
