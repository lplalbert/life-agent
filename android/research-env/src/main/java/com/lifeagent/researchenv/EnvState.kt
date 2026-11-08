package com.lifeagent.researchenv

import android.content.Context
import android.os.Handler
import android.os.Looper
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 研测商城业务状态机（虚构购物流程）。
 *
 * 五种机制故障（每类以 fault 字段单开，正常对照 = fault=none）：
 *  1. delay      延迟完成：提交申请后 delayMs 才生效，期间界面无变化（纯延迟效果，无处理中提示）
 *  2. noop       动作无效果：前 noop_n 次提交被业务层丢弃（动作已被应用接收，≠手势投递失败）
 *  3. popup      意外弹窗：在谓词处弹出模态通知，须显式点「知道了」
 *  4. confusion  相似画面不同内部状态：进入清单页时预置干扰项翻转为无货；
 *                商品行在列表页/清单页看起来几乎一样，区分证据 = 行内「有货/无货」标签
 *  5. promo      瞬时线索（旧证据依赖）：复位首屏以画布横幅闪现一次「会员日优惠码 XXXX」，
 *                首个动作即消逝（线索过期）；结算页必须输入该码，缺失/错误拒单。
 *                码只以像素呈现（画布绘制，不进控件树文本）——区分证据独占图像通道，
 *                依赖历史图像取回而非文本历史（文本历史恒保真）。
 *
 * 约束：无网络、无凭证、无外部副作用；界面文字不含 fault_id / expected_answer / 路径提示。
 * 成功检查 success() 只看内部业务状态 + expected_ids 参数，与任何自报文本无关。
 */
object EnvState {
    const val ENV_VERSION = "research-env/0.1"

    data class Item(
        val id: String,
        val name: String,
        val spec: String,
        val priceCents: Int,
        var inStock: Boolean,
    )

    interface UiHooks {
        fun showPopup()
        fun dismissPopup()
        fun render()
    }

    var ui: UiHooks? = null

    // ---- episode 配置（广播 reset 携带并持久化到 files/config.json；Activity 重建时从该文件复位）----
    var episodeId = "ep-0"
    var taskSeed = 1L
    var faultSeed = 1L
    var faultType = "none"           // none|delay|noop|popup|confusion|promo
    var delayMs = 0L
    var noopFirstN = 0
    var popupAt = "enter_cart"       // enter_detail|after_add|enter_cart|enter_checkout
    var confusionAt = "enter_cart"   // enter_cart|enter_checkout
    var expectedIds: List<String> = emptyList()
    var initialCartIds: List<String> = emptyList()
    var catalogSize = 4                  // 长任务族可调（默认 4 = 既有语义）；上限 = NAME_POOL
    var promoRearm = false               // 多轮瞬时线索：每轮下单完成后武装下一轮新码（默认关 = 既有单轮语义）
    var promoClueRound = 1               // 线索武装轮次（1 = 复位首屏武装，既有语义；j>1 = 第 j 轮列表页武装）
    var promoGateAfter = 0               // 码闸门从已完成 ≥ N 单后生效（0 = 首次提交即闸，既有语义；
                                         // 1 = 轮 1 不闸、轮 2 起闸——线索在早期轮、收银台在后续轮）

    // ---- 业务状态 ----
    var screen = "list"              // list|detail|cart|checkout|result
    var detailItemId = ""
    var items: MutableList<Item> = mutableListOf()
    var cartIds: MutableList<String> = mutableListOf()
    var submittedIds: List<String> = emptyList()
    var orderStatus = "none"         // none|submitting|submitted
    var orderId = ""
    var note = ""
    var popupMessage = ""

    /** 多轮任务的订单账本（每轮 complete 时追加；单轮语义下最多 1 条，评估侧旧口径不受影响）。 */
    data class OrderRecord(
        val orderId: String,
        val itemIds: List<String>,
        val codeEntered: String,
        val promoCode: String,
    )

    var orders: MutableList<OrderRecord> = mutableListOf()
    var promoRound = 1               // 当前线索轮次（promoRearm 时每轮下单后递增）

    // ---- promo 瞬时线索（旧证据依赖）----
    var promoCode = ""               // 确定性生成（fault_seed）；只经画布横幅闪现一次
    var promoActive = false          // 横幅当前是否显示；首个动作即消逝 = 线索过期
    var codeEntered = ""             // 结算页输入框内容（TextWatcher 同步）

    // ---- 故障账本（attempted/triggered 由状态机记录；recovered = 触发后完成且成功）----
    var counters: JSONObject = JSONObject()
    var noopConsumed = 0
    var popupShown = false
    var popupShowing = false
    var confusionFlipped = false
    var pendingTimers = 0
    var resetSeq = 0
    private var eventSeq = 0

    private val handler = Handler(Looper.getMainLooper())
    private val timers = mutableListOf<Runnable>()

    // ================= 复位 =================

    fun resetFromConfigFile(ctx: Context) {
        val f = File(ctx.filesDir, "config.json")
        val cfg = if (f.exists()) JSONObject(f.readText(Charsets.UTF_8)) else JSONObject()
        reset(ctx.applicationContext, cfg)
    }

    fun reset(ctx: Context, cfg: JSONObject) {
        // 持久化配置：Activity/进程重建时从 config.json 复位同一 episode 初始态
        File(ctx.filesDir, "config.json").writeText(cfg.toString(), Charsets.UTF_8)
        // 清空挂起定时器
        timers.forEach { handler.removeCallbacks(it) }
        timers.clear()
        pendingTimers = 0
        // 清空残留弹窗
        ui?.dismissPopup()
        popupShowing = false

        episodeId = cfg.optString("episode_id", "ep-0")
        taskSeed = cfg.optLong("task_seed", 1L)
        faultSeed = cfg.optLong("fault_seed", 1L)
        faultType = cfg.optString("fault", "none")
        delayMs = cfg.optLong("delay_ms", 0L)
        noopFirstN = cfg.optInt("noop_n", 0)
        popupAt = cfg.optString("popup_at", "enter_cart")
        confusionAt = cfg.optString("confusion_at", "enter_cart")
        expectedIds = jsonStrings(cfg.optJSONArray("expected_ids"))
        initialCartIds = jsonStrings(cfg.optJSONArray("initial_cart"))
        catalogSize = cfg.optInt("catalog_size", 4).coerceIn(1, NAME_POOL.size)
        promoRearm = cfg.optBoolean("promo_rearm", false)
        promoClueRound = cfg.optInt("promo_clue_round", 1).coerceAtLeast(1)
        promoGateAfter = cfg.optInt("promo_gate_after", 0).coerceAtLeast(0)

        // 同 seed 同初态：商品列表由 task_seed 确定性生成
        items = generateItems(taskSeed, catalogSize)
        cartIds = initialCartIds.toMutableList()
        submittedIds = emptyList()
        orderStatus = "none"
        orderId = ""
        orders = mutableListOf()
        promoRound = 1
        screen = "list"
        detailItemId = ""
        note = ""
        noopConsumed = 0
        popupShown = false
        confusionFlipped = false
        eventSeq = 0
        counters = JSONObject()
        listOf("delay", "noop", "popup", "confusion", "promo").forEach {
            counters.put(it, JSONObject().put("attempted", 0).put("triggered", 0).put("recovered", 0))
        }

        // promo：线索轮次 = 1 时复位即武装（既有语义，首个动作使线索过期）；
        // j>1 时由 maybeArmNextRound 在进入第 j 轮列表页时武装（横幅画在列表屏，
        // 下一观察可见、随后动作过期——瞬时语义不变）。配对码恒为 generateCode(faultSeed)。
        promoCode = if (faultType == "promo" && promoClueRound <= 1) generateCode(faultSeed) else ""
        promoActive = promoCode.isNotEmpty()
        codeEntered = ""

        // 事件日志按 episode 截断（主机在 episode 结束后收取保留）
        File(ctx.filesDir, "events.jsonl").writeText("", Charsets.UTF_8)
        resetSeq++
        logEvent(ctx, "reset", JSONObject().put("episode_id", episodeId).put("fault", faultType))
        writeState(ctx)
        ui?.render()
    }

    // ================= 动作入口（UI 只调这里） =================

    fun onAction(ctx: Context, name: String) {
        val appCtx = ctx.applicationContext
        // promo：任何动作都使首屏瞬时线索过期（横幅不再出现，码只能从历史图像找回）
        if (promoActive) {
            promoActive = false
            bump("promo", "attempted")
            bump("promo", "triggered")
            logEvent(appCtx, "fault", JSONObject().put("type", "promo").put("at", "clue_expired"))
        }
        when {
            name.startsWith("open:") -> {
                detailItemId = name.removePrefix("open:")
                screen = "detail"
                note = ""
                maybePopup(appCtx, "enter_detail")
            }
            name == "add" -> {
                if (detailItemId.isNotEmpty() && !cartIds.contains(detailItemId)) {
                    cartIds.add(detailItemId)
                    note = "已加入清单"
                    logEvent(appCtx, "action", JSONObject().put("name", "add_to_cart").put("item", detailItemId).put("result", "ok"))
                    maybePopup(appCtx, "after_add")
                } else {
                    note = "该商品已在清单中"
                    logEvent(appCtx, "action", JSONObject().put("name", "add_to_cart").put("item", detailItemId).put("result", "noop_duplicate"))
                }
            }
            name == "open_cart" -> {
                screen = "cart"
                note = ""
                maybeConfusion(appCtx, "enter_cart")
                maybePopup(appCtx, "enter_cart")
            }
            name.startsWith("remove:") -> {
                val id = name.removePrefix("remove:")
                if (cartIds.remove(id)) {
                    note = "已从清单移除"
                    logEvent(appCtx, "action", JSONObject().put("name", "remove").put("item", id).put("result", "ok"))
                }
            }
            name == "checkout" -> {
                if (cartIds.isEmpty()) {
                    note = "清单为空，请先加入商品"
                    logEvent(appCtx, "action", JSONObject().put("name", "checkout").put("result", "rejected_empty"))
                } else {
                    screen = "checkout"
                    note = ""
                    maybeConfusion(appCtx, "enter_checkout")
                    maybePopup(appCtx, "enter_checkout")
                }
            }
            name == "submit" -> trySubmit(appCtx)
            name == "dismiss_popup" -> {
                popupShowing = false
                ui?.dismissPopup()
                logEvent(appCtx, "action", JSONObject().put("name", "dismiss_popup").put("result", "ok"))
            }
            name == "back" -> {
                screen = when (screen) {
                    "detail" -> "list"
                    "cart" -> "list"
                    "checkout" -> "cart"
                    else -> screen
                }
                note = ""
            }
            name == "restart" -> {
                // 只回列表页，不清业务状态：评估以 state.json 为准
                screen = "list"
                note = ""
            }
        }
        maybeArmNextRound(appCtx)
        writeState(appCtx)
        ui?.render()
    }

    /** 多轮瞬时线索武装（onAction 尾部：横幅画在列表屏，下一观察可见、随后动作过期——
     *  与复位首屏 step-0 瞬时语义一致；在 complete() 武装会画在 result 屏，看不见）：
     *  - promoRearm：每轮下单完成后武装下一轮新码（K 次独立机制事件）；
     *  - promoClueRound>1：一次性线索在第 j 轮列表页武装（配对码恒 generateCode(faultSeed)，跨策略同码）。 */
    private fun maybeArmNextRound(ctx: Context) {
        if (faultType != "promo" || screen != "list") return
        if (promoRearm) {
            if (orders.size < promoRound) return
            promoRound++
            promoCode = generateCode(faultSeed + promoRound * 7919L)
            promoActive = true
            codeEntered = ""
            logEvent(ctx, "fault", JSONObject().put("type", "promo").put("at", "clue_armed").put("round", promoRound))
        } else if (promoCode.isEmpty() && promoClueRound > 1 && orders.size >= promoClueRound - 1) {
            promoRound = promoClueRound
            promoCode = generateCode(faultSeed)
            promoActive = true
            logEvent(ctx, "fault", JSONObject().put("type", "promo").put("at", "clue_armed").put("round", promoRound))
        }
    }

    private fun trySubmit(ctx: Context) {
        if (orderStatus == "submitting") {
            // 延迟完成期间的重复提交幂等忽略（计入事件，不影响状态）
            logEvent(ctx, "action", JSONObject().put("name", "submit").put("result", "ignored_in_flight"))
            return
        }
        if (cartIds.isEmpty()) {
            note = "清单为空，请先加入商品"
            logEvent(ctx, "action", JSONObject().put("name", "submit").put("result", "rejected_empty"))
            return
        }
        if (promoCode.isNotEmpty() && orders.size >= promoGateAfter && codeEntered.trim() != promoCode) {
            // 优惠码闸门：缺失/错误都在提交处显式拒绝（表单语义，不泄漏正确码）。
            // promoGateAfter>0 时前 N 单不闸（线索在早期轮、收银台在后续轮，长程依赖由后续轮闸门强制）
            val blank = codeEntered.isBlank()
            note = if (blank) "请先输入优惠码" else "优惠码不正确"
            logEvent(ctx, "action", JSONObject().put("name", "submit")
                .put("result", if (blank) "rejected_code_missing" else "rejected_code_wrong"))
            return
        }
        if (faultType == "delay" || faultType == "noop") bump(faultType, "attempted")

        if (faultType == "noop" && noopConsumed < noopFirstN) {
            // 应用层业务失败：动作已被接收但无任何效果（事件可证接收 ≠ 手势投递失败）
            noopConsumed++
            bump("noop", "triggered")
            logEvent(ctx, "action", JSONObject().put("name", "submit").put("received", true).put("effect", false).put("result", "business_noop"))
            return
        }

        submittedIds = cartIds.toList()
        orderStatus = "submitting"
        val seq = submittedIds.size * 7 + taskSeed.toInt() * 31 + resetSeq
        orderId = "R" + (100000 + Math.floorMod(seq * 131 + faultSeed.toInt(), 900000))

        if (faultType == "delay" && delayMs > 0) {
            bump("delay", "triggered")
            pendingTimers++
            val appCtx = ctx.applicationContext
            val r = object : Runnable {
                override fun run() {
                    timers.remove(this)
                    pendingTimers--
                    complete(appCtx)
                }
            }
            timers.add(r)
            handler.postDelayed(r, delayMs)
            logEvent(ctx, "action", JSONObject().put("name", "submit").put("received", true).put("effect", "delayed").put("delay_ms", delayMs))
        } else {
            logEvent(ctx, "action", JSONObject().put("name", "submit").put("received", true).put("effect", true))
            complete(ctx)
        }
    }

    private fun complete(ctx: Context) {
        orderStatus = "submitted"
        screen = "result"
        // 订单账本：本轮提交快照（码字段记本轮线索码，重武装发生在之后的 onAction 尾部，不污染本轮记录）
        orders.add(OrderRecord(orderId, submittedIds.toList(), codeEntered, promoCode))
        // 下单完成即结算表单作废：码输入清空、清单清空（真实下单语义）。
        // 多轮任务下一轮须重新供码/加购——上一轮的表单残留不得架空后续轮的取证要求。
        codeEntered = ""
        cartIds.clear()
        // recovered：触发过的故障在流程完成且独立成功检查通过时计 1（未触发不得计恢复成功）
        val ok = success()
        listOf("delay", "noop", "popup", "confusion", "promo").forEach { f ->
            val c = counters.optJSONObject(f) ?: return@forEach
            if (c.optInt("triggered") > 0 && ok) bump(f, "recovered")
        }
        logEvent(ctx, "complete", JSONObject().put("order_id", orderId).put("success", ok).put("submitted_ids", JSONArray(submittedIds)))
        writeState(ctx)
        ui?.render()
    }

    // ================= 故障触发 =================

    private fun maybePopup(ctx: Context, at: String) {
        if (faultType != "popup" || popupShown || popupAt != at) return
        bump("popup", "attempted")
        bump("popup", "triggered")
        popupShown = true
        popupShowing = true
        popupMessage = MESSAGE_POOL[Math.floorMod(faultSeed.toInt(), MESSAGE_POOL.size)]
        logEvent(ctx, "fault", JSONObject().put("type", "popup").put("at", at))
        ui?.showPopup()
    }

    private fun maybeConfusion(ctx: Context, at: String) {
        if (faultType != "confusion" || confusionFlipped || confusionAt != at) return
        bump("confusion", "attempted")
        bump("confusion", "triggered")
        // 预置干扰项（initial_cart）翻转为无货：商品行外观与列表页几乎一致，仅「有货/无货」标签不同
        initialCartIds.forEach { id -> items.find { it.id == id }?.inStock = false }
        confusionFlipped = true
        logEvent(ctx, "fault", JSONObject().put("type", "confusion").put("at", at).put("flipped", JSONArray(initialCartIds)))
    }

    // ================= 独立成功检查 =================

    /** 只依据内部业务状态 + expected_ids 判定，与模型 finish 文本无关。 */
    fun success(): Boolean {
        if (orderStatus != "submitted") return false
        if (expectedIds.isEmpty()) return submittedIds.isNotEmpty()
        return submittedIds.sorted() == expectedIds.sorted()
    }

    /** 复位干净探针：当前状态是否仍是复位后无残留（复位后立即读才为 true）。 */
    fun postResetClean(): Boolean {
        val countersZero = listOf("delay", "noop", "popup", "confusion", "promo").all {
            val c = counters.optJSONObject(it) ?: return@all false
            c.optInt("attempted") == 0 && c.optInt("triggered") == 0 && c.optInt("recovered") == 0
        }
        // promo 干净态 = 线索已武装未过期、输入框空（promo 横幅在复位首屏属初态，不是残留）
        val promoClean = codeEntered.isEmpty() &&
            (promoCode.isEmpty() || (promoActive && promoCode.isNotEmpty()))
        return screen == "list" && orderStatus == "none" && pendingTimers == 0 &&
            !popupShowing && !popupShown && !confusionFlipped && countersZero && promoClean &&
            cartIds.toList() == initialCartIds && submittedIds.isEmpty() && orderId.isEmpty() &&
            orders.isEmpty() && promoRound == 1
    }

    // ================= 状态导出（主机控制器经 run-as 读取） =================

    fun snapshot(): JSONObject {
        val itemsArr = JSONArray()
        items.forEach {
            itemsArr.put(
                JSONObject()
                    .put("id", it.id).put("name", it.name).put("spec", it.spec)
                    .put("price_cents", it.priceCents).put("in_stock", it.inStock)
            )
        }
        val ordersArr = JSONArray()
        orders.forEach {
            ordersArr.put(
                JSONObject()
                    .put("order_id", it.orderId)
                    .put("item_ids", JSONArray(it.itemIds))
                    .put("code_entered", it.codeEntered)
                    .put("promo_code", it.promoCode)
            )
        }
        return JSONObject()
            .put("env_version", ENV_VERSION)
            .put("episode_id", episodeId)
            .put("reset_seq", resetSeq)
            .put("screen", screen)
            .put("items", itemsArr)
            .put("cart_ids", JSONArray(cartIds))
            .put("submitted_ids", JSONArray(submittedIds))
            .put("order_status", orderStatus)
            .put("order_id", orderId)
            .put("note", note)
            .put("counters", counters)
            .put("pending_timers", pendingTimers)
            .put("popup_showing", popupShowing)
            .put("popup_shown", popupShown)
            .put("noop_consumed", noopConsumed)
            .put("confusion_flipped", confusionFlipped)
            .put("promo_banner_active", promoActive)
            .put("promo_code", promoCode)
            .put("code_entered", codeEntered)
            .put("promo_round", promoRound)
            .put("orders", ordersArr)
            .put("success", success())
            .put("post_reset_clean", postResetClean())
            .put(
                "config",
                JSONObject()
                    .put("fault", faultType).put("delay_ms", delayMs).put("noop_n", noopFirstN)
                    .put("popup_at", popupAt).put("confusion_at", confusionAt)
                    .put("task_seed", taskSeed).put("fault_seed", faultSeed)
                    .put("expected_ids", JSONArray(expectedIds))
                    .put("initial_cart", JSONArray(initialCartIds))
                    .put("catalog_size", catalogSize).put("promo_rearm", promoRearm)
                    .put("promo_clue_round", promoClueRound).put("promo_gate_after", promoGateAfter)
            )
    }

    fun writeState(ctx: Context) {
        File(ctx.filesDir, "state.json").writeText(snapshot().toString(2), Charsets.UTF_8)
    }

    // ================= 内部工具 =================

    /** 结算页输入框同步（UI TextWatcher 调用）：立即落盘，控制器随时可读 ground truth。 */
    fun setCodeEntered(ctx: Context, s: String) {
        codeEntered = s
        writeState(ctx)
    }

    private fun bump(fault: String, key: String) {
        val o = counters.optJSONObject(fault) ?: JSONObject().also { counters.put(fault, it) }
        o.put(key, o.optInt(key) + 1)
    }

    private fun logEvent(ctx: Context, type: String, detail: JSONObject) {
        val line = JSONObject()
            .put("t", System.currentTimeMillis())
            .put("seq", ++eventSeq)
            .put("type", type)
            .put("detail", detail)
        File(ctx.filesDir, "events.jsonl").appendText(line.toString() + "\n", Charsets.UTF_8)
    }

    private fun jsonStrings(arr: JSONArray?): List<String> =
        arr?.let { a -> (0 until a.length()).map { a.getString(it) } } ?: emptyList()

    /** 同 fault_seed → 同优惠码（配对比较跨策略共享同一码）；字符集去掉易混的 0/O/1/I。 */
    private fun generateCode(seed: Long): String {
        val rnd = kotlin.random.Random(seed * 31 + 101)
        return (1..4).map { CODE_ALPHABET[rnd.nextInt(CODE_ALPHABET.length)] }.joinToString("")
    }

    /** 同 task_seed → 同商品列表（名称/规格/价格/顺序均确定）；n=4 时与旧版逐字节一致。 */
    private fun generateItems(seed: Long, n: Int = 4): MutableList<Item> {
        val rnd = kotlin.random.Random(seed.toInt())
        val picked = NAME_POOL.shuffled(rnd).take(n)
        return picked.mapIndexed { i, t ->
            Item(
                id = "p${i + 1}",
                name = t.first,
                spec = t.second,
                priceCents = t.third + rnd.nextInt(4) * 200,
                inStock = true,
            )
        }.toMutableList()
    }

    private val CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    private val NAME_POOL = listOf(
        Triple("帆布收纳袋", "米白色 · 中号", 2990),
        Triple("陶瓷马克杯", "雾灰色 · 350ml", 4590),
        Triple("桌面台灯", "暖光 · 可调光", 8900),
        Triple("折叠雨伞", "藏青色 · 八骨", 5900),
        Triple("棉麻抱枕", "浅灰色 · 45cm", 3600),
        Triple("玻璃保鲜盒", "圆形 · 800ml", 4200),
        Triple("原木衣架", "5 只装", 2600),
        Triple("香薰蜡烛", "雪松香 · 180g", 5200),
    )

    private val MESSAGE_POOL = listOf(
        "本周会员日活动进行中，精选商品享限时优惠，欢迎进店逛逛。",
        "您的账户有一张待使用的优惠券，本周内有效，记得关注。",
        "商城物流时效说明：普通配送预计 3 日内送达，节假日顺延。",
    )
}
