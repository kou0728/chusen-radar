#!/usr/bin/env bash
# 母艦の cron から15分ごとに呼ぶ入口。
#   */15 * * * * $HOME/chusen-radar/android_agent/run.sh >> $HOME/chusen-android.log 2>&1
# 設定（トークンなど）は ~/.config/chusen-android.env に書く（android_agent/agent.env.example を参照）。
set -u
cd "$(dirname "$0")/.." || exit 1
if [ -f "$HOME/.config/chusen-android.env" ]; then
  set -a
  . "$HOME/.config/chusen-android.env"
  set +a
fi
# GitHub で直したレシピ（config/apps.yaml）を取り込む。失敗しても前の設定で続ける
git pull --ff-only --quiet || echo "git pull に失敗しました（前の設定のまま続けます）"
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" -m android_agent run "$@"
