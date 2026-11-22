#!/bin/bash
# ダブルクリックすると、USB でつないだ新しい Android を次々に自動で登録します（止めるときはウィンドウを閉じる）。
cd "$(dirname "$0")/../.." || exit 1
python3 -m mobilerun_hub.register --watch
