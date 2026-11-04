package com.lifeagent.app

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine

/**
 * 半透明确认/问答弹窗：覆盖在目标应用之上，用户作答后立即结束。
 * confirm() = 敏感按钮确认；ask() = 自由问答（验证码、偏好等）。
 */
class PromptActivity : Activity() {

    companion object {
        private const val EXTRA_TITLE = "title"
        private const val EXTRA_DESC = "desc"
        private const val EXTRA_MODE = "mode"
        private const val MODE_CONFIRM = "confirm"
        private const val MODE_ASK = "ask"

        @Volatile private var resumeConfirm: ((Boolean) -> Unit)? = null
        @Volatile private var resumeAsk: ((String?) -> Unit)? = null

        suspend fun confirm(ctx: Context, title: String, desc: String): Boolean =
            suspendCoroutine { cont ->
                resumeConfirm = { cont.resume(it) }
                launch(ctx, MODE_CONFIRM, title, desc)
            }

        suspend fun ask(ctx: Context, question: String): String? =
            suspendCoroutine { cont ->
                resumeAsk = { cont.resume(it) }
                launch(ctx, MODE_ASK, question, "")
            }

        private fun launch(ctx: Context, mode: String, title: String, desc: String) {
            ctx.startActivity(Intent(ctx, PromptActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                putExtra(EXTRA_MODE, mode)
                putExtra(EXTRA_TITLE, title)
                putExtra(EXTRA_DESC, desc)
            })
        }

        private fun fireConfirm(result: Boolean) {
            val f = resumeConfirm
            resumeConfirm = null
            f?.invoke(result)
        }

        private fun fireAsk(result: String?) {
            val f = resumeAsk
            resumeAsk = null
            f?.invoke(result)
        }
    }

    private val mode: String get() = intent.getStringExtra(EXTRA_MODE) ?: MODE_CONFIRM

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val title = intent.getStringExtra(EXTRA_TITLE).orEmpty()
        val desc = intent.getStringExtra(EXTRA_DESC).orEmpty()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 40, 48, 32)
            setBackgroundColor(Color.argb(246, 255, 255, 255))
        }
        root.addView(TextView(this).apply {
            text = if (mode == MODE_ASK) "需要你的帮助" else "敏感操作确认"
            textSize = 18f
            setPadding(0, 0, 0, 12)
        })
        root.addView(TextView(this).apply {
            text = title
            textSize = 16f
            setPadding(0, 0, 0, 8)
        })
        if (desc.isNotEmpty() && desc != title) {
            root.addView(TextView(this).apply {
                text = desc
                textSize = 14f
                setPadding(0, 0, 0, 8)
            })
        }

        val input = EditText(this).apply {
            hint = "输入你的回答…"
            inputType = InputType.TYPE_CLASS_TEXT
        }
        val buttons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            setPadding(0, 16, 0, 0)
        }

        if (mode == MODE_ASK) {
            root.addView(input, LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
            buttons.addView(Button(this).apply {
                text = "提交"
                setOnClickListener {
                    fireAsk(input.text.toString().ifEmpty { null })
                    finish()
                }
            })
            buttons.addView(Button(this).apply {
                text = "跳过"
                setOnClickListener {
                    fireAsk(null)
                    finish()
                }
            })
        } else {
            buttons.addView(Button(this).apply {
                text = "拒绝"
                setOnClickListener {
                    fireConfirm(false)
                    finish()
                }
            })
            buttons.addView(Button(this).apply {
                text = "确认"
                setOnClickListener {
                    fireConfirm(true)
                    finish()
                }
            })
        }
        root.addView(buttons)
        setContentView(root)
    }

    // 返回键 = 拒绝/跳过，不悬挂协程
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (mode == MODE_ASK) fireAsk(null) else fireConfirm(false)
        finish()
    }

    // 意外销毁兜底：默认拒绝，避免协程永久挂起
    override fun onDestroy() {
        if (mode == MODE_ASK) fireAsk(null) else fireConfirm(false)
        super.onDestroy()
    }
}
