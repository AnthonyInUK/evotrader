#!/usr/bin/env bash
# 自动重试 + 断点续跑的回测包装。
#
# 解决的痛点：DeepSeek 偶发 402/503/超时会让回测进程崩溃，需要手动算断点日期再续跑。
# 本脚本以 nav_curve.csv 的最后一天为断点，崩了自动等待重试、自动从下一交易日续跑，
# 跑到 end_date 为止。全程不加 --reset（除非首次且无历史）。
#
# 用法：
#   bash scripts/run_until_done.sh <config> <start> <end> [--memory]
# 例：
#   bash scripts/run_until_done.sh evo2025_q1_mem 2025-01-02 2025-03-31 --memory
#
# 建议在 screen 里跑：TERM=xterm screen -S evo, 然后执行本脚本, Ctrl+A D 挂起。

set -u

CONFIG="${1:?需要 config 名}"
START="${2:?需要起始日 YYYY-MM-DD}"
END="${3:?需要结束日 YYYY-MM-DD}"
shift 3
EXTRA_FLAGS="$*"          # 例如 --memory

cd "$(dirname "$0")/.." || exit 1   # 进到 evotraders/
export SKIP_EXPERIMENT_PREFLIGHT=1

NAV="$CONFIG/nav_curve.csv"
LOG="/tmp/${CONFIG}_auto.log"
RETRY_WAIT=60             # 崩溃后等待秒数（503 过载给服务器喘息）
MAX_ATTEMPTS=200         # 安全上限，防止死循环

echo "=== run_until_done: $CONFIG  $START → $END  flags='$EXTRA_FLAGS' ===" | tee -a "$LOG"

last_date() {
    # 返回 nav_curve 最后一个交易日；无文件则空
    [ -f "$NAV" ] || { echo ""; return; }
    tail -n +2 "$NAV" | tail -1 | cut -d',' -f1
}

next_day() {
    # macOS BSD date：给定 YYYY-MM-DD，返回 +1 天
    date -j -v+1d -f "%Y-%m-%d" "$1" "+%Y-%m-%d"
}

attempt=0
while [ "$attempt" -lt "$MAX_ATTEMPTS" ]; do
    attempt=$((attempt + 1))

    LAST="$(last_date)"
    if [ -z "$LAST" ]; then
        RUN_START="$START"
        RESET="--reset"          # 首次且无历史 → 全新开始
    else
        # 已完成到 LAST → 从下一日续跑，绝不 reset
        if [[ "$LAST" > "$END" || "$LAST" == "$END" ]]; then
            echo "✅ 已完成到 $LAST ≥ $END，回测结束。" | tee -a "$LOG"
            exit 0
        fi
        RUN_START="$(next_day "$LAST")"
        RESET=""
    fi

    echo "--- [尝试 $attempt] $(date '+%H:%M:%S')  start=$RUN_START reset='$RESET' ---" | tee -a "$LOG"

    python3 run_backtest.py \
        --start "$RUN_START" --end "$END" \
        --config "$CONFIG" $RESET $EXTRA_FLAGS >> "$LOG" 2>&1
    code=$?

    NEW_LAST="$(last_date)"
    if [[ -n "$NEW_LAST" && ( "$NEW_LAST" > "$END" || "$NEW_LAST" == "$END" ) ]]; then
        echo "✅ 完成到 $NEW_LAST，回测结束 (exit=$code)。" | tee -a "$LOG"
        exit 0
    fi

    # SystemExit(欠费守卫) 用 exit code 1，但消息已写日志；其余崩溃同样处理
    if grep -q "DashScope embedding 欠费" "$LOG"; then
        echo "❌ DashScope 欠费，守卫已中断。请充值后重跑本脚本（会自动续跑）。" | tee -a "$LOG"
        exit 2
    fi

    echo "⚠️ 进程退出 (exit=$code)，最后完成 $NEW_LAST。等待 ${RETRY_WAIT}s 后续跑..." | tee -a "$LOG"
    sleep "$RETRY_WAIT"
done

echo "❌ 达到最大尝试次数 $MAX_ATTEMPTS，停止。" | tee -a "$LOG"
exit 3
