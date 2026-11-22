"""Android端末で公式アプリを開き、アプリ限定の抽選のお知らせを読む仕組み。

自宅の常時起動PC（母艦）で動かし、USBでつないだ専用のAndroid端末を adb で操作します。
読み取った結果は data/app_found.json としてGitHubへ送り、30分ごとの自動更新でサイトに載ります。
セットアップは android_agent/README.md を見てください。
"""
