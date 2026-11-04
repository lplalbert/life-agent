package com.lifeagent.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Display
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine

/** 控件树节点：坐标为屏幕绝对包围盒。 */
data class UINode(
    val text: String,
    val desc: String,
    val cls: String,
    val clickable: Boolean,
    val bounds: Rect,
) {
    val label: String
        get() = text.ifEmpty { desc }

    fun center(): Pair<Int, Int> =
        ((bounds.left + bounds.right) / 2) to ((bounds.top + bounds.bottom) / 2)

    fun contains(x: Int, y: Int): Boolean = bounds.contains(x, y)
}

/**
 * 无障碍服务：感知（截图、控件树）与动作（手势、文本、全局按键）的唯一执行层。
 * 对界面含义的解释与操作策略放在 AgentLoop。
 */
class AgentService : AccessibilityService() {

    companion object {
        @Volatile
        var instance: AgentService? = null

        fun isEnabled(context: Context): Boolean {
            val expected = "${context.packageName}/${AgentService::class.java.name}"
            val enabled = Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
            ) ?: ""
            return enabled.split(':').any { it.equals(expected, ignoreCase = true) }
        }
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var lastNodes: List<UINode> = emptyList()

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    // ---------- 感知 ----------

    /** 截取默认显示屏，返回 JPEG 字节（长边缩到 1568，控制视觉 token）。 */
    suspend fun screenshotJpeg(maxSide: Int = 1568): ByteArray = suspendCoroutine { cont ->
        takeScreenshot(
            Display.DEFAULT_DISPLAY,
            Executors.newSingleThreadExecutor(),
            object : TakeScreenshotCallback {
                override fun onSuccess(screenshot: ScreenshotResult) {
                    try {
                        val buffer = screenshot.hardwareBuffer
                        val hw = try {
                            Bitmap.wrapHardwareBuffer(buffer, screenshot.colorSpace)
                        } finally {
                            buffer.close()  // ScreenshotResult 自身在 API 34 无需/无 close
                        }
                        if (hw == null) {
                            cont.resumeWithException(Exception("截图封装失败（wrapHardwareBuffer 返回空）"))
                            return
                        }
                        var bmp: Bitmap = hw.copy(Bitmap.Config.ARGB_8888, false)
                        hw.recycle()
                        val w = bmp.width
                        val h = bmp.height
                        val side = maxOf(w, h)
                        if (side > maxSide) {
                            val scale = maxSide.toFloat() / side
                            val scaled = Bitmap.createScaledBitmap(
                                bmp, (w * scale).toInt(), (h * scale).toInt(), true,
                            )
                            bmp.recycle()
                            bmp = scaled
                        }
                        val out = ByteArrayOutputStream()
                        bmp.compress(Bitmap.CompressFormat.JPEG, 82, out)
                        bmp.recycle()
                        cont.resume(out.toByteArray())
                    } catch (e: Exception) {
                        cont.resumeWithException(e)
                    }
                }

                override fun onFailure(errorCode: Int) {
                    cont.resumeWithException(Exception("截图失败（errorCode=$errorCode，可能有系统弹窗覆盖）"))
                }
            },
        )
    }

    /** 抓取当前活动窗口控件树，并缓存供安全闸门查询。 */
    fun dumpUi(): List<UINode> {
        val root = rootInActiveWindow
            ?: throw Exception("无法获取当前窗口（无障碍服务可能刚启动，请重试）")
        val nodes = mutableListOf<UINode>()
        fun walk(node: AccessibilityNodeInfo) {
            val rect = Rect()
            node.getBoundsInScreen(rect)
            nodes.add(
                UINode(
                    text = node.text?.toString()?.trim().orEmpty(),
                    desc = node.contentDescription?.toString()?.trim().orEmpty(),
                    cls = node.className?.toString().orEmpty(),
                    clickable = node.isClickable,
                    bounds = rect,
                ),
            )
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let { walk(it) }
            }
        }
        walk(root)
        lastNodes = nodes.filter { it.label.isNotEmpty() || it.clickable }
        return lastNodes
    }

    /** 安全闸门查询：返回包含 (x,y) 且带标签的最内层节点。 */
    fun nodeAt(x: Int, y: Int): UINode? =
        lastNodes.lastOrNull { it.label.isNotEmpty() && it.contains(x, y) }

    fun currentApp(): String =
        rootInActiveWindow?.packageName?.toString() ?: "unknown"

    /** 与 Python 版 compact_ui 一致的紧凑清单。 */
    fun compactUi(nodes: List<UINode>, maxLines: Int = 150): String {
        val lines = nodes.mapIndexed { i, n ->
            val kind = if (n.clickable) "click" else "view"
            "#$i \"${n.label}\" $kind [${n.bounds.left},${n.bounds.top},${n.bounds.right},${n.bounds.bottom}]"
        }
        val capped = if (lines.size > maxLines) {
            lines.take(maxLines - 2) + listOf("...(共 ${lines.size} 项，超出部分已省略)")
        } else lines
        return capped.ifEmpty { listOf("（无带标签控件，界面可能是自绘内容，请以截图为准）") }.joinToString("\n")
    }

    // ---------- 动作 ----------

    suspend fun tap(x: Int, y: Int) = gesture(listOf(x.toFloat() to y.toFloat()), 50)

    suspend fun longPress(x: Int, y: Int, durationMs: Int = 800) =
        gesture(listOf(x.toFloat() to y.toFloat()), durationMs.toLong())

    suspend fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int = 400) =
        gesture(
            listOf(x1.toFloat() to y1.toFloat(), x2.toFloat() to y2.toFloat()),
            durationMs.toLong(),
        )

    private suspend fun gesture(points: List<Pair<Float, Float>>, durationMs: Long) {
        val path = Path().apply {
            moveTo(points.first().first, points.first().second)
            points.drop(1).forEach { lineTo(it.first, it.second) }
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, durationMs.coerceAtLeast(1L))
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        val ok = suspendCoroutine<Boolean> { cont ->
            dispatchGesture(gesture, object : GestureResultCallback() {
                override fun onCompleted(g: GestureDescription) = cont.resume(true)
                override fun onCancelled(g: GestureDescription) = cont.resume(false)
            }, mainHandler)
        }
        if (!ok) throw Exception("手势被系统取消（可能有更高优先级窗口）")
    }

    /** 文本输入：ACTION_SET_TEXT 直写聚焦输入框，中文无需切换输入法。 */
    suspend fun typeText(text: String) {
        val root = rootInActiveWindow ?: throw Exception("无法获取当前窗口")
        val focused = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: throw Exception("没有聚焦的输入框，请先点击输入框")
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (!ok) throw Exception("输入失败：当前控件不支持直接写入文本")
    }

    suspend fun clearText() = typeText("")

    fun press(key: String): String {
        val action = when (key) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            else -> throw Exception("不支持的按键: $key（back/home/recents）")
        }
        if (!performGlobalAction(action)) throw Exception("按键 $key 执行失败")
        return "已按 $key"
    }

    fun launchApp(packageName: String) {
        val intent = packageManager.getLaunchIntentForPackage(packageName)
            ?: throw Exception("启动失败: $packageName（未安装或无启动器入口）")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
    }
}
