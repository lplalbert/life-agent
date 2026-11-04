package com.lifeagent.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.Gravity
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.io.File

/**
 * 控制台：输入任务 → 启动 Agent 循环 → 滚动日志。
 * 无障碍权限开关跳系统设置；确认/问答由 PromptActivity 弹窗承接。
 */
class MainActivity : Activity() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var statusView: TextView
    private lateinit var taskInput: EditText
    private lateinit var autoConfirmBox: CheckBox
    private lateinit var logView: TextView
    private var running = false  // 防重入：模型误点「开始任务」或用户连点不会再开第二个循环

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 48, 32, 24)
        }

        statusView = TextView(this).apply { textSize = 14f }
        root.addView(statusView)

        val permBtn = Button(this).apply {
            text = "开启无障碍权限"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }
        root.addView(permBtn)

        taskInput = EditText(this).apply {
            hint = "任务：如「外卖点午餐」「打车去公司」「买菜买牛奶」「充50话费」「订电影票」"
            minLines = 2
        }
        root.addView(taskInput, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        // 全生活任务模板：点击填入输入框，可再编辑
        val templates = listOf(
            "外卖" to "在美团外卖帮我点一份午餐，选评分高的店，提交订单并付款",
            "打车" to "在滴滴打车，目的地【在这里填地址】，叫车",
            "买菜" to "在美团买菜买鸡蛋和牛奶，提交订单并付款",
            "充值" to "在支付宝给我充50元话费，提交订单并付款",
        )
        val chips = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        for ((label, tpl) in templates) {
            chips.addView(Button(this).apply {
                text = label
                textSize = 12f
                setOnClickListener { taskInput.setText(tpl) }
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        }
        root.addView(chips)

        autoConfirmBox = CheckBox(this).apply {
            text = "下单/支付自动确认（不弹窗，直接执行消费操作）"
            textSize = 12f
        }
        root.addView(autoConfirmBox)

        val startBtn = Button(this).apply {
            text = "开始任务"
            setOnClickListener { startTask() }
        }
        root.addView(startBtn)

        logView = TextView(this).apply {
            textSize = 12f
            gravity = Gravity.START
        }
        val scroll = ScrollView(this).apply {
            addView(logView)
        }
        root.addView(scroll, LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        setContentView(root)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    // am start 复用已有实例走 onNewIntent（不触发 onResume），状态行也在此刷新
    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        refreshStatus()
    }

    private fun refreshStatus() {
        statusView.text = if (AgentService.isEnabled(this)) {
            "无障碍服务：已开启 | 模型：${BuildConfig.MODEL}"
        } else {
            "无障碍服务：未开启（点上方按钮去系统设置里打开 LifeAgent）"
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun startTask() {
        if (running) {
            appendLog("[!] 任务运行中，忽略重复启动")
            return
        }
        val task = taskInput.text.toString().trim()
        if (task.isEmpty()) {
            appendLog("[X] 请先输入任务")
            return
        }
        val service = AgentService.instance
        if (service == null) {
            appendLog("[X] 无障碍服务未运行：请先在系统设置里开启 LifeAgent")
            return
        }
        val runDir = File(getExternalFilesDir(null), "runs/" + System.currentTimeMillis())
        appendLog("=== 开始任务 ===")
        running = true
        scope.launch(Dispatchers.Default) {
            try {
                AgentLoop(service, autoConfirmBox.isChecked) { line -> runOnUiThread { appendLog(line) } }
                    .run(task, runDir)
                runOnUiThread { appendLog("运行记录：$runDir") }
            } catch (e: Exception) {
                runOnUiThread { appendLog("[X] 任务异常: ${e.message}") }
            } finally {
                runOnUiThread { running = false }
            }
        }
    }

    private fun appendLog(line: String) {
        logView.append(line + "\n")
    }
}
