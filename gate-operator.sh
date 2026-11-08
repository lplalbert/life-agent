#!/bin/bash
# 干跑测试的「监印官」：LifeAgent 消费确认弹窗（PromptActivity）出现时自动审批。
# 规则：下单/结算/充值提交类放行；付款/叫车类一律否决（真实付款/派单前必停）。
# 付款类按钮再按弹窗前的屏幕内容区分「下单提交」（放行）与「收银台」（否决）。
SERIAL=A4FKVB4A25000146
ADB="adb -s $SERIAL"
RUNS=/sdcard/Android/data/com.lifeagent.app/files/runs

node_bounds() { # $1 = 单个 node 文本行 → 输出 bounds="[l,t][r,b]"
  echo "$1" | grep -o 'bounds="[^"]*"' | head -1 | sed 's/bounds="//; s/"$//'
}
tap_bounds() { # $1 = [l,t][r,b]
  local l t r b
  l=$(echo "$1" | sed 's/^\[\([0-9]*\),.*/\1/')
  t=$(echo "$1" | sed 's/^\[[0-9]*,\([0-9]*\)].*/\1/')
  r=$(echo "$1" | sed 's/.*\]\[\([0-9]*\),.*/\1/')
  b=$(echo "$1" | sed 's/.*,\([0-9]*\)\]$/\1/')
  $ADB shell "input tap $(( (l + r) / 2 )) $(( (t + b) / 2 ))"
}
tap_text() { # $1 = 精确按钮文字，$2 = node 集
  local line=$(echo "$2" | grep "text=\"$1\"" | grep 'bounds=' | head -1)
  local b=$(node_bounds "$line")
  if [ -n "$b" ]; then tap_bounds "$b"; else echo "WARN 找不到按钮 $1"; fi
}

while true; do
  # 息屏自愈看门狗：USB 掉线走电池后 15s 息屏，截图/手势/按键三通道全灭。
  # 主机侧每轮查电源态，灭了就唤屏+解滑动锁（无 PIN 场景）。
  wake=$($ADB shell "dumpsys power 2>/dev/null | grep -m1 mWakefulness" | tr -d '\r')
  case "$wake" in
    *Awake*) ;;
    *) $ADB shell "input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard" ;;
  esac
  top=$($ADB shell "dumpsys activity activities 2>/dev/null | grep -m1 topResumedActivity")
  case "$top" in
    *PromptActivity*)
      # 先删旧文件：dump 失败时 cat 不得吐出上次的幽灵界面误判
      xml=$($ADB shell "rm -f /sdcard/gate_win.xml; uiautomator dump /sdcard/gate_win.xml >/dev/null 2>&1; cat /sdcard/gate_win.xml" 2>/dev/null)
      if [ -z "$xml" ] || ! echo "$xml" | grep -q '<node'; then sleep 2; continue; fi
      nodes=$(echo "$xml" | sed 's/<node/\n<node/g')

      if echo "$nodes" | grep -q 'text="需要你的帮助"'; then
        q=$(echo "$nodes" | grep -o 'text="[^"]*"' | sed 's/text="//; s/"$//' | grep -v '^$' \
            | grep -vE '^(需要你的帮助|提交|跳过)$' | head -1)
        tap_text "跳过" "$nodes"
        echo "ASK 自动跳过：${q:0:60}"
        sleep 3
        continue
      fi

      # 确认模式：弹窗全部文案（去掉固定标题和按钮）作为判定依据
      alltext=$(echo "$nodes" | grep -o 'text="[^"]*"' | sed 's/text="//; s/"$//' | grep -v '^$' \
          | grep -vE '^(敏感操作确认|拒绝|确认)$' | tr '\n' ' ')
      if echo "$alltext" | grep -qE '呼叫|叫车'; then
        tap_text "拒绝" "$nodes"
        echo "DENY 派单拦截：${alltext:0:80}"
      elif echo "$alltext" | grep -qE '支付|付款|免密|刷脸|扣款'; then
        newest=$($ADB shell "ls -t $RUNS/ | head -1" | tr -d '\r')
        uifile=$($ADB shell "ls -t $RUNS/$newest/step*_ui.txt 2>/dev/null | head -1" | tr -d '\r')
        uitxt=$($ADB shell "cat $uifile 2>/dev/null")
        if echo "$uitxt" | grep -qE '微信支付|支付宝|银行卡|零钱|余额|花呗|云闪付|收银台|支付密码|指纹|免密'; then
          tap_text "拒绝" "$nodes"
          echo "DENY 收银台拦截（真实付款前停）：${alltext:0:80}"
        else
          tap_text "确认" "$nodes"
          echo "APPROVE 下单提交（页面无支付要素）：${alltext:0:80}"
        fi
      elif echo "$alltext" | grep -qE '购物车|结算|提交订单|确认订单|立即购买|立即下单|下单|充值|预订|下一步|同意|继续|选好了|加入'; then
        tap_text "确认" "$nodes"
        echo "APPROVE 消费流程：${alltext:0:80}"
      else
        tap_text "拒绝" "$nodes"
        echo "DENY 未知按钮保守拒绝：${alltext:0:80}"
      fi
      sleep 3
      ;;
    *) sleep 2 ;;
  esac
done
