#!/bin/bash
# 干跑测试矩阵驱动：逐场景派发 → 等「任务结果」→ 等经验复盘落盘 → 接力下一个。
# 用 nohup 脱链启动（不随终端/会话退出而中断）：nohup bash matrix-driver.sh >/dev/null 2>&1 &
# 日志写 tmp/matrix-driver.log。安全边界内嵌于任务文本；监印官 gate-operator.sh 必须同时在岗。
SERIAL=A4FKVB4A25000146
ADB="adb -s $SERIAL"
RUNS=/sdcard/Android/data/com.lifeagent.app/files/runs
BASE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$BASE/tmp"
exec >> "$BASE/tmp/matrix-driver.log" 2>&1
echo "===== 矩阵驱动启动 $(date '+%F %T') ====="

run_scenario() {
  local name="$1" task="$2" prev d i
  echo "== $(date '+%T') 派发：$name"
  prev=$($ADB shell "ls -t $RUNS/ | head -1" | tr -d '\r')
  $ADB shell "am start -n com.lifeagent.app/.MainActivity --es task '$task'" >/dev/null 2>&1
  d=""
  for i in $(seq 1 30); do
    d=$($ADB shell "ls -t $RUNS/ | head -1" | tr -d '\r')
    [ -n "$d" ] && [ "$d" != "$prev" ] && break
    sleep 2
  done
  if [ -z "$d" ] || [ "$d" = "$prev" ]; then
    echo "== $(date '+%T') 派发失败：$name（无新运行目录，无障碍服务可能未运行）"
    return 1
  fi
  echo "== $(date '+%T') 运行目录 $d"
  for i in $(seq 1 180); do
    if $ADB shell "grep -q '任务结果' $RUNS/$d/log.txt 2>/dev/null"; then
      echo "== $(date '+%T') 完成：$name"
      $ADB shell "grep -m2 '任务结果' $RUNS/$d/log.txt" | tr -d '\r'
      sleep 90   # 等 Experience.reflect 写完经验库再接力，让下一场吃到上一场的经验
      return 0
    fi
    sleep 10
  done
  echo "== $(date '+%T') 超时：$name（30 分钟无任务结果，继续下一场景）"
  return 1
}

run_scenario "淘宝续跑" '干跑测试（绝不真实付款）继续上一次中断的任务：淘宝里已有一件「植护大包气垫抽纸悦色橙L码12包」（补贴后¥17.81）在购物车，上次在商品页因息屏中断。请进入购物车勾选该商品，进入结算页，用默认收货地址，一路操作到收银台（显示确认支付和付款方式的页面）。到达收银台后立即用 finish 结束，状态 done，summary 写明店铺、商品、金额和当前页面。绝对不要点击任何会实际扣款的按钮；若弹窗提示用户拒绝了该操作，表示已到测试边界，立即 finish，状态 done，总结现状，绝不可换方案重试支付。'
sleep 15
run_scenario "拼多多" '干跑测试（绝不真实付款）：在拼多多购物走到收银台。打开拼多多，搜索「可乐」，选一款销量高且便宜的商品进入商品页，加入购物车，进入结算页，用默认收货地址，一路操作到收银台（显示确认支付和付款方式的页面）。到达收银台后立即用 finish 结束，状态 done，summary 写明店铺、商品、金额和当前页面。绝对不要点击任何会实际扣款的按钮；若弹窗提示用户拒绝了该操作，表示已到测试边界，立即 finish，状态 done，总结现状，绝不可换方案重试支付。'
sleep 15
run_scenario "支付宝充值" '干跑测试（绝不真实付款）：在支付宝充话费走到收银台。打开支付宝，进入手机充值，给本机号码充 10 元话费，一路操作到收银台（显示确认支付和付款方式的页面）。到达收银台后立即用 finish 结束，状态 done，summary 写明金额和当前页面。绝对不要点击任何会实际扣款的按钮；若弹窗提示用户拒绝了该操作，表示已到测试边界，立即 finish，状态 done，总结现状，绝不可换方案重试支付。'
sleep 15
run_scenario "滴滴打车" '干跑测试（绝不真实叫车、绝不付款）：在滴滴做出行叫车前的全部准备，到确认呼叫页面即停。打开滴滴出行，输入目的地「光谷广场」，选择车型，到达确认呼叫的页面后立即用 finish 结束，状态 done，summary 写明出发地、目的地、车型、预估价和当前页面。绝对不要点击「呼叫」「叫车」类按钮——那会真实派单出动司机；若弹窗提示用户拒绝了该操作，表示已到测试边界，立即 finish，状态 done，总结现状，绝不可换方案重试。'
sleep 15
run_scenario "12306" '干跑测试（绝不真实付款）：在铁路12306 查票订票走到收银台。打开 12306，查询明天武汉到长沙的火车票，选一趟合适的车次和席别，添加乘车人，提交订单，一路操作到收银台（显示确认支付和付款方式的页面）。到达收银台后立即用 finish 结束，状态 done，summary 写明车次、席别、金额和当前页面。绝对不要点击任何会实际扣款的按钮；若弹窗提示用户拒绝了该操作，表示已到测试边界，立即 finish，状态 done，总结现状，绝不可换方案重试支付。'

echo "===== 矩阵全部完成 $(date '+%F %T') ====="
