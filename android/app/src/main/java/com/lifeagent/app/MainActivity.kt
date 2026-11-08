package com.lifeagent.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.PowerManager
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
    private var wakeLock: PowerManager.WakeLock? = null

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
        handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    // am start 复用已有实例走 onNewIntent（不触发 onResume），状态行也在此刷新
    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        refreshStatus()
        handleIntent(intent)
    }

    /**
     * 外部下发任务：adb shell am start -n com.lifeagent.app/.MainActivity --es task '...'
     * 可选 --ez auto_confirm true 开启自动确认。取完即删，防屏幕旋转重复执行。
     */
    private fun handleIntent(intent: Intent?) {
        if (intent == null) return
        val task = intent.getStringExtra("task")?.trim().orEmpty()
        if (task.isEmpty()) return
        if (intent.getBooleanExtra("auto_confirm", false)) autoConfirmBox.isChecked = true
        intent.removeExtra("task")
        intent.removeExtra("auto_confirm")
        taskInput.setText(task)
        startTask()
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
        val dataDir = getExternalFilesDir(null) ?: filesDir
        val runDir = File(dataDir, "runs/" + System.currentTimeMillis())
        runDir.mkdirs()
        val logFile = File(runDir, "log.txt")
        try { logFile.writeText("任务：$task\n") } catch (_: Exception) {}
        appendLog("=== 开始任务 ===")
        running = true
        // 亮屏锁：任务由目标 App 全屏驱动时控制面板退后台，FLAG_KEEP_SCREEN_ON 随之失效，
        // USB 一断 stay_on_while_plugged_in 也拦不住息屏——而息屏会让截图/手势/按键三通道全灭。
        @Suppress("DEPRECATION")
        wakeLock = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP, "lifeagent:run")
            .apply { acquire(30 * 60 * 1000L) }  // 上限 30 分钟自动释放，防泄漏
        scope.launch(Dispatchers.Default) {
            try {
                AgentLoop(service, autoConfirmBox.isChecked, dataDir) { line ->
                    runOnUiThread { appendLog(line) }
                    try { logFile.appendText(line + "\n", Charsets.UTF_8) } catch (_: Exception) {}
                }
                    .run(task, runDir)
                runOnUiThread { appendLog("运行记录：$runDir") }
                // 自学习闭环：复盘本次运行沉淀操作经验（失败不影响任务结果）
                try {
                    Experience.reflect(task, logFile, dataDir) { line ->
                        runOnUiThread { appendLog(line) }
                        // 复盘结论也落盘：外部工具靠 log.txt 判运行闭环，只写屏幕会漏判
                        try { logFile.appendText(line + "\n", Charsets.UTF_8) } catch (_: Exception) {}
                    }
                } catch (e: Exception) {
                    val msg = "[!] 经验复盘失败（不影响任务）: ${e.message}"
                    runOnUiThread { appendLog(msg) }
                    try { logFile.appendText(msg + "\n", Charsets.UTF_8) } catch (_: Exception) {}
                }
            } catch (e: Exception) {
                val msg = "[X] 任务异常: ${e.message}"
                runOnUiThread { appendLog(msg) }
                // 异常必须落盘并闭环「任务结果」，否则外部工具永远等不到运行结束
                try {
                    logFile.appendText(
                        "$msg\n--------------------------------------------------\n" +
                        "[失败] 任务结果：failed\n   运行异常终止：${e.message}\n",
                        Charsets.UTF_8
                    )
                } catch (_: Exception) {}
            } finally {
                wakeLock?.let { if (it.isHeld) it.release() }
                wakeLock = null
                runOnUiThread { running = false }
            }
        }
    }

    private fun appendLog(line: String) {
        logView.append(line + "\n")
    }
}
