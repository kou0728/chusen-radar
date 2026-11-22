# Android端末で「アプリ限定の抽選」を読む

Webでは告知されず公式アプリの中だけで知らせる抽選（ヤマダ・コジマ・iAEON・平和堂・ゲオなど）を、
自宅に置いた専用のAndroid端末で自動で読み、サイトに載せる仕組みです。費用の目安は [docs/paid-plan.md](../docs/paid-plan.md) にまとめました。

```
 自宅
 ┌─────────────────────────────────────────────┐
 │ 母艦（いつも電源が入っているPC） ── USB ── 専用のAndroid端末 │
 │   15分ごとに android_agent が動き、                      │
 │   時間になったアプリを開いて「お知らせ」の文字を読む         │
 └──────────────────────┬──────────────────────┘
                        │ 見つかった抽選だけを data/app_found.json として送る
                        ▼
 GitHub（いつもの30分ごとの自動更新がこれを読み、サイトに載せる）
```

- **読むだけです。** 「応募する」「購入」「申し込む」などのボタンは、レシピに書いてもプログラムが押しません。
- 1つのアプリは60分おき、1〜6時は読みません（`config/apps.yaml` の `defaults` で変えられます）。
- 母艦のフォルダは `git pull` で最新にするだけです。GitHubには `data/app_found.json` の1ファイルだけを書き込みます。

## 1. 用意するもの

| もの | メモ |
|---|---|
| 専用のAndroid端末 | Android 13以上・メモリ4GB以上。充電を80〜90%で止める機能がある機種。普段使いのスマホとは分ける |
| 母艦 | いつも電源が入っているPC。Raspberry Pi・中古ミニPC・使っていないノートPCなど。Linux・Mac・Windowsのどれでも動く（以下はLinuxの例） |
| USBケーブル | データ通信ができるもの |
| GitHubのトークン | 結果を送るため（手順4） |

## 2. Android端末の準備

1. 初期化して、この用途だけに使うGoogleアカウントでセットアップする
2. 設定 → セキュリティ → 画面ロック → **なし**（スワイプも無し）
3. 設定 → 電池 → **充電の上限（80〜90%）をON**（24時間充電しっぱなしで電池が膨らむのを防ぐ）
4. 設定 → デバイス情報 → **ビルド番号を7回タップ** → 開発者向けオプション → **USBデバッグをON**（場所は機種で少し違う）
5. Google Play → 設定 → アプリの自動更新 → **Wi-Fi経由のみ**
6. 読みたいお店のアプリを入れ、**ログイン・通知の許可・最初の案内まで手で済ませる**
7. USBデバッグがONのままで各アプリが起動するか確かめる（起動しないアプリは対象外。そのお店は手動登録のまま）

## 3. 母艦の準備

```bash
sudo apt update && sudo apt install -y adb git python3-venv
git clone https://github.com/kou0728/chusen-radar.git ~/chusen-radar
cd ~/chusen-radar
python3 -m venv .venv
.venv/bin/pip install -r android_agent/requirements.txt
```

端末をUSBでつなぎ、端末の画面に出る「USBデバッグを許可しますか？」で **「このパソコンからは常に許可」にチェックしてOK**。

```bash
adb devices                                  # 「device」と出ればOK
.venv/bin/python -m android_agent doctor     # つながり・設定の確認
.venv/bin/python -m android_agent setup      # アニメーションOFFなど、読み取りが安定する設定を入れる
```

Macは `brew install android-platform-tools`、WindowsはGoogleの「SDK Platform-Tools」でadbを入れます。

## 4. GitHubに送る設定

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
   - Repository access: **Only select repositories** → `chusen-radar`
   - Permissions: **Contents → Read and write**（ほかは不要）／ 有効期限：1年など
2. 母艦で設定ファイルを作り、トークンを貼る

```bash
mkdir -p ~/.config
cp android_agent/agent.env.example ~/.config/chusen-android.env
chmod 600 ~/.config/chusen-android.env
nano ~/.config/chusen-android.env            # GITHUB_TOKEN= の後ろに貼る
```

Discordに不調を知らせたい時は、同じファイルの `DISCORD_WEBHOOK_URL` も入れます（サイトの通知と同じURLでOK）。

## 5. お店のアプリを登録する（レシピ作り）

`config/apps.yaml` に、ヤマダ・コジマなどの下書きが `enabled: false` で入っています。1つずつ、次の順で仕上げます。

1. 端末でそのアプリを開いた状態で、パッケージ名を調べる

   ```bash
   .venv/bin/python -m android_agent current
   ```

2. お知らせの画面まで手で進めながら、押す文字と位置を見る

   ```bash
   .venv/bin/python -m android_agent screen
   #  押せる ( 675,2300)  お知らせ  id=nav_news   ← こういう行が出る
   ```

3. GitHubの画面で `config/apps.yaml` を開き（鉛筆マーク）、`package` と `steps` を直して保存
4. 母艦で取り込んで試す（GitHubへは送らず、読めた結果を表示するだけ）

   ```bash
   git pull
   .venv/bin/python -m android_agent try yamada
   ```

5. 抽選が正しく読めたら、GitHubで `enabled: true` にして保存

母艦のファイルを直接直して試してもかまいません。うまくいったら同じ内容をGitHubにも反映し、母艦側は `git checkout config/apps.yaml` で戻してください（戻さないと自動の `git pull` が止まります）。

steps で使える命令は `config/apps.yaml` の冒頭に書いてあります。よく使う形：

```yaml
steps:
  - restart                         # アプリを開き直す（毎回同じ画面から始める）
  - dismiss: [閉じる, あとで]         # ポップアップが出ていたら閉じる
  - tap: {id: nav_news}             # 「お知らせ」タブ（同じ文字が2つある時は id で指定）
  - each: {match: [抽選], max: 3}    # 「抽選」を含むお知らせを順に開いて読む
```

## 6. 自動で回す

```bash
crontab -e
# 次の1行を足して保存
*/15 * * * * $HOME/chusen-radar/android_agent/run.sh >> $HOME/chusen-android.log 2>&1
```

15分ごとに起動し、時間になったアプリだけを読みます。動いているかは `tail -f ~/chusen-android.log`、
サイトでは「このサイトについて → 情報源と巡回状況 → 公式アプリを自動で読んでいる」で見られます。
端末から8時間以上届かないと、そこが「取得できず」になります。

## 7. 外から端末を操作する（Tailscale＋scrcpy、どちらも無料）

ログインのやり直しや、アプリの更新で出た案内を閉じる時に、外出先のPCから端末の画面を見て操作できます。

1. 母艦と自分のPCに [Tailscale](https://tailscale.com/) を入れ、同じアカウントでログイン（母艦は `curl -fsSL https://tailscale.com/install.sh | sh` → `sudo tailscale up`）
2. 母艦でSSHを有効にする（Raspberry Pi は `sudo raspi-config` → Interface Options → SSH、Ubuntuは `sudo apt install openssh-server`）
3. 自分のPCに [scrcpy](https://github.com/Genymobile/scrcpy) を入れる（Macは `brew install scrcpy`）
4. 触る前に自動の読み取りを止め、トンネルをつなぐ

   ```bash
   ssh ユーザー名@母艦 "touch ~/chusen-radar/android_agent/PAUSE && adb start-server"
   ssh -CN -L5038:localhost:5037 -L27183:localhost:27183 ユーザー名@母艦    # つないだままにする
   ```

5. 別のターミナルで画面を出す

   ```bash
   ADB_SERVER_SOCKET=tcp:localhost:5038 scrcpy --force-adb-forward
   # Windows（PowerShell）: $env:ADB_SERVER_SOCKET="tcp:localhost:5038"; scrcpy --force-adb-forward
   ```

6. 終わったら再開する：`ssh ユーザー名@母艦 "rm ~/chusen-radar/android_agent/PAUSE"`

## 8. 画像だけの告知を読む（任意・有料）

お知らせが画像（バナー）だけで文字が取れないアプリは、スクリーンショットを Claude API で読めます。料金の目安は [docs/paid-plan.md](../docs/paid-plan.md)。

```bash
.venv/bin/pip install -r android_agent/requirements-vision.txt
nano ~/.config/chusen-android.env            # ANTHROPIC_API_KEY= を入れる
.venv/bin/python -m android_agent try yamada --vision
```

うまく読めたら、そのアプリのブロックに `vision: true` を足します。
同じ画面は読み直さず、1日の枚数は `vision_daily_limit`（既定20枚）で止まります。今月の利用量は `doctor` で見られます。

## 9. 困ったとき

| 出たメッセージ・症状 | 対処 |
|---|---|
| Android端末が見つかりません | データ用のケーブルか、USBデバッグがONか、別のUSBポートを試す |
| unauthorized | 端末の画面で「USBデバッグを許可」（「常に許可」にチェック） |
| `adb devices` が no permissions（Linux） | `sudo usermod -aG plugdev $USER` → ログインし直す |
| 画面を読み取れませんでした | `setup` でアニメーションOFF。動画や広告が動き続ける画面は読めないことがあるので、別の画面で読む |
| 押す場所「…」が画面に見つかりません | アプリの更新で文字や配置が変わった。`screen` で確かめてレシピを直す |
| サイトで「Android端末から…届いていません」 | 母艦の電源・ネット・USB。`tail ~/chusen-android.log` と `doctor` を見る |
| GitHub へ送れませんでした | トークンの期限・権限（Contents: Read and write／対象リポジトリ） |
| 端末が熱くなっています | 充電上限をONにし、風通しの良い場所へ。ケースは外す |

## 10. ファイル

| ファイル | 中身 |
|---|---|
| `config/apps.yaml` | どのアプリを、どう開いて読むか（レシピ） |
| `android_agent/agent.py` | コマンド（run / try / screen …） |
| `android_agent/recipe.py` | レシピの実行。押さないボタンの一覧（`FORBIDDEN`）もここ |
| `android_agent/extract.py` | 画面の文字から抽選を取り出す（Web巡回と同じ日付ルール） |
| `android_agent/vision.py` | 画像の読み取り（任意） |
| `android_agent/push.py` | GitHubへの送信・Discord通知 |
| `android_agent/.state.json` など | 母艦だけで使う記録（GitHubには上げない） |
