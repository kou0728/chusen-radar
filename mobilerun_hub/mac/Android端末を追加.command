#!/bin/bash
# ダブルクリックで、USB でつないだ Android を中継サーバーに登録します。
cd "$(dirname "$0")/../.." || exit 1
python3 -m mobilerun_hub.register "$@"
echo
read -r -p "Enter キーで閉じます"
