package com.lifeagent.researchenv

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.os.Bundle
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/**
 * 研测商城界面：纯原生 View，跨页面流程 = list → detail → cart → checkout → result。
 * 界面文字只呈现虚构商品业务，不出现任何故障标签、预期答案或路径提示。
 * 所有可交互控件带 contentDescription（= 动作名），供确定性脚本按语义定位点击。
 * promo 瞬时线索横幅用画布绘制（不设 text/contentDescription）：码只存在于像素，
 * 控件树无该文本——区分证据独占图像通道，符合「界面文字不含 expected_answer」。
 */
class MainActivity : Activity() {

    private lateinit var root: LinearLayout
    private var popup: AlertDialog? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val scroll = ScrollView(this)
        root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(24), dp(24), dp(24))
        }
        scroll.addView(root)
        setContentView(scroll)

        EnvState.ui = object : EnvState.UiHooks {
            override fun showPopup() = runOnUiThread { showPopupNow() }
            override fun dismissPopup() = runOnUiThread { popup?.dismiss(); popup = null }
            override fun render() = runOnUiThread { renderNow() }
        }
        // 首次启动（或进程重建）从配置文件复位，保证可复位协议在 Activity 侧也成立
        EnvState.resetFromConfigFile(applicationContext)
    }

    override fun onDestroy() {
        EnvState.ui = null
        popup = null
        super.onDestroy()
    }

    // ================= 弹窗（意外弹窗机制） =================

    private fun showPopupNow() {
        if (isFinishing) return
        popup?.dismiss()
        popup = AlertDialog.Builder(this)
            .setTitle("商城通知")
            .setMessage(EnvState.popupMessage)
            .setCancelable(false) // 须显式点击按钮，保证可观察、可复位
            .setPositiveButton("知道了") { _, _ ->
                EnvState.onAction(applicationContext, "dismiss_popup")
            }
            .show()
        // 契约：所有可交互控件 contentDescription = 动作名
        popup?.getButton(AlertDialog.BUTTON_POSITIVE)?.contentDescription = "dismiss_popup"
    }

    // ================= 渲染 =================

    private fun renderNow() {
        root.removeAllViews()
        when (EnvState.screen) {
            "list" -> renderList()
            "detail" -> renderDetail()
            "cart" -> renderCart()
            "checkout" -> renderCheckout()
            "result" -> renderResult()
        }
        if (EnvState.note.isNotEmpty()) {
            root.addView(text(EnvState.note, 15f, Color.parseColor("#666666")))
        }
    }

    private fun renderList() {
        if (EnvState.promoActive) {
            // 瞬时线索：仅首屏显示一次，任何动作后不再出现（见 EnvState.onAction）
            root.addView(
                PromoBannerView(this, EnvState.promoCode),
                LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(72)),
            )
        }
        root.addView(text("研测商城", 22f, Color.BLACK, bold = true))
        root.addView(text("商品列表", 15f, Color.parseColor("#666666")))
        root.addView(space())
        EnvState.items.forEach { item ->
            root.addView(itemRow(item, showStock = false))
            root.addView(action("查看详情", "open:${item.id}"))
            root.addView(space())
        }
        root.addView(action("查看清单（" + EnvState.cartIds.size + " 件）", "open_cart"))
    }

    private fun renderDetail() {
        val item = EnvState.items.find { it.id == EnvState.detailItemId } ?: EnvState.items.first()
        root.addView(text("商品详情", 22f, Color.BLACK, bold = true))
        root.addView(space())
        root.addView(itemRow(item, showStock = true))
        root.addView(space())
        root.addView(action("加入清单", "add"))
        root.addView(action("返回列表", "back"))
        root.addView(action("查看清单（" + EnvState.cartIds.size + " 件）", "open_cart"))
    }

    private fun renderCart() {
        root.addView(text("购物清单", 22f, Color.BLACK, bold = true))
        root.addView(space())
        if (EnvState.cartIds.isEmpty()) {
            root.addView(text("清单为空", 15f, Color.parseColor("#666666")))
        }
        EnvState.cartIds.forEach { id ->
            val item = EnvState.items.find { it.id == id } ?: return@forEach
            root.addView(itemRow(item, showStock = true))
            root.addView(action("移除", "remove:${item.id}"))
            root.addView(space())
        }
        root.addView(action("去结算", "checkout"))
        root.addView(action("返回列表", "back"))
    }

    private fun renderCheckout() {
        root.addView(text("确认结算", 22f, Color.BLACK, bold = true))
        root.addView(space())
        var total = 0
        EnvState.cartIds.forEach { id ->
            val item = EnvState.items.find { it.id == id } ?: return@forEach
            root.addView(itemRow(item, showStock = true))
            total += item.priceCents
        }
        root.addView(space())
        root.addView(text("合计：" + fenToYuan(total), 17f, Color.BLACK, bold = true))
        if (EnvState.promoCode.isNotEmpty()) {
            root.addView(space())
            root.addView(text("请输入优惠码", 14f, Color.parseColor("#444444")))
            root.addView(codeField())
        }
        root.addView(space())
        root.addView(action("提交申请", "submit"))
        root.addView(action("返回清单", "back"))
    }

    private fun renderResult() {
        root.addView(text("申请已提交", 22f, Color.BLACK, bold = true))
        root.addView(space())
        root.addView(text("受理编号：" + EnvState.orderId, 15f, Color.parseColor("#666666")))
        root.addView(space())
        EnvState.submittedIds.forEach { id ->
            val item = EnvState.items.find { it.id == id } ?: return@forEach
            root.addView(itemRow(item, showStock = true))
        }
        root.addView(space())
        root.addView(action("继续购物", "restart"))
    }

    /** 商品行：名称/规格/价格恒显；清单语境多一个库存标签（= 相似画面的区分证据）。 */
    private fun itemRow(item: EnvState.Item, showStock: Boolean): LinearLayout {
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val info = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        info.addView(text(item.name, 16f, Color.BLACK, bold = true))
        info.addView(text(item.spec, 14f, Color.parseColor("#444444")))
        val stockLine = if (showStock) {
            if (item.inStock) "库存充足" else "已无货"
        } else ""
        if (stockLine.isNotEmpty()) {
            info.addView(
                text(stockLine, 14f, if (item.inStock) Color.parseColor("#2E7D32") else Color.parseColor("#C62828"))
            )
        }
        row.addView(info, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        row.addView(text(fenToYuan(item.priceCents), 16f, Color.BLACK, bold = true))
        return row
    }

    // ================= View 工具 =================

    private fun text(s: String, sp: Float, color: Int, bold: Boolean = false): TextView =
        TextView(this).apply {
            text = s
            textSize = sp
            setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    /** 动作按钮：contentDescription = 动作名，确定性脚本按其定位点击。 */
    private fun action(label: String, name: String): Button =
        Button(this).apply {
            text = label
            contentDescription = name
            textSize = 15f
            setOnClickListener { EnvState.onAction(applicationContext, name) }
        }

    private fun space(): TextView = TextView(this).apply { text = ""; textSize = 8f }

    /** 优惠码输入框：contentDescription="code_field" 供脚本定位；内容实时同步状态机。 */
    private fun codeField(): EditText =
        EditText(this).apply {
            contentDescription = "code_field"
            hint = "会员日优惠码"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS
            setText(EnvState.codeEntered)  // 重渲染保留已输入内容
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) = Unit
                override fun afterTextChanged(s: Editable?) {
                    EnvState.setCodeEntered(applicationContext, s?.toString() ?: "")
                }
            })
        }

    private fun fenToYuan(fen: Int): String = "¥" + (fen / 100) + "." + String.format("%02d", fen % 100)

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}

/**
 * 会员日优惠码横幅：纯画布绘制。不设 text/contentDescription，控件树无码文本——
 * 码只存在于截图像素（图像通道独占证据，promo 机制的核心）。
 */
private class PromoBannerView(context: Context, code: String) : View(context) {
    private val bg = Paint().apply { color = Color.parseColor("#B71C1C") }
    private val fg = Paint().apply {
        color = Color.WHITE
        textSize = 20f * context.resources.displayMetrics.scaledDensity
        isAntiAlias = true
        typeface = Typeface.DEFAULT_BOLD
    }
    private val label = "会员日 · 优惠码 $code"

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), bg)
        val baseline = height / 2f - (fg.ascent() + fg.descent()) / 2f
        canvas.drawText(label, 16f * context.resources.displayMetrics.density, baseline, fg)
    }
}
