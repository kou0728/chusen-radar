# Mobilerun Portal 中継サーバー（ngrok の代わり）

端末の Mobilerun Portal は、自分から外のサーバーへつなぎに行く仕組み（逆接続）です。
その接続先を、ngrok 経由の iMac から **VPS（いつも動いている小さなサーバー）** に変えます。

```
 端末（通常Portal）──wss──▶ VPS：中継サーバー（台帳・切断の見張り・通知）◀──Tailscale── iMac（USB登録・操作）
                          https://portal.自分のドメイン                               http://hub:8701
```

- **iMac の再起動・スリープ・自宅回線の不調で端末が切れない。** 端末の接続先は VPS なので、iMac が止まっても端末はつながったまま
- **ngrok の上限・URLの変化がない。** 自分のドメインで、転送量はVPSの範囲（国内VPSはほぼ無制限）
- **端末一覧（台帳）はサーバーが持つ。** いまつながっているか、最後につながった・切れた時刻、機種を記録。CSVで書き出せる
- **切断に気づける。** 25秒ごとに生存確認し、2分以上切れたら Discord に通知、戻ったらまた通知
- **操作する入口は公開しない。** インターネットに出すのは端末の受け口だけ。管理画面・管理APIは Tailscale からだけ
- **端末ごとに別のトークン。** サーバーには元のトークンを保存せず、ハッシュだけを持つ

## 費用

| もの | 目安 | メモ |
|---|---|---|
| VPS | 月800〜1,600円 | Ubuntu 24.04、メモリ1GBで数十台まで足りる。例：さくらのVPS 1GB 月807円・2GB 月1,594円（年契約） |
| ドメイン | 年1,500〜3,000円 | `.com` などを1つ。`portal.自分のドメイン` を VPS に向ける |
| HTTPS の証明書 | 0円 | Caddy が Let's Encrypt で自動で取って更新 |
| Tailscale | 0円 | Personal プラン（6ユーザー・端末数無制限、個人の非商用）。仕事で使うなら有料プランを確認 |
| ngrok | 0円に | 全台を移したら止める |

## 1. VPS の準備（最初の1回だけ・30分くらい）

1. VPS を契約し、OS は **Ubuntu 24.04** を選ぶ（東京リージョン）
2. ドメインの DNS に **A レコード**を作る：`portal` → VPS の IP アドレス
3. VPS に SSH で入り、次を順に貼る

```bash
# 必要なものを入れる
sudo apt update && sudo apt install -y git python3-venv caddy
sudo useradd --system --home /opt/mobilerun-hub --shell /usr/sbin/nologin mobilerun

# このプログラムを置く（main に取り込んだ後は -b の指定は不要）
sudo git clone -b claude/sleepy-cerf-4x7qyo https://github.com/kou0728/chusen-radar.git /opt/mobilerun-hub
cd /opt/mobilerun-hub
sudo python3 -m venv .venv && sudo .venv/bin/pip install -r mobilerun_hub/requirements.txt

# Tailscale（iMac から管理画面に入るための専用の通り道）
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname hub        # 表示されたURLを開いてログイン
tailscale ip -4                          # 100.x.y.z をメモ

# 設定ファイル
sudo cp mobilerun_hub/deploy/hub.env.example /etc/mobilerun-hub.env
sudo chmod 600 /etc/mobilerun-hub.env
.venv/bin/python -m mobilerun_hub token  # 出てきた文字列が管理トークン（メモする）
sudo nano /etc/mobilerun-hub.env         # HUB_PUBLIC_URL・HUB_ADMIN_TOKEN・HUB_ADMIN_HOST（100.x.y.z）を書く

# 起動（落ちても自動で立ち上がる・VPSを再起動しても自動で起動）
sudo cp mobilerun_hub/deploy/mobilerun-hub.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mobilerun-hub
sudo cp mobilerun_hub/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile           # portal.example.jp を自分のドメインに
sudo systemctl reload caddy

# ファイアウォール（SSH・HTTPS・Tailscale だけ通す）
sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw allow in on tailscale0 && sudo ufw enable

# 確認：{"ok": true} と出ればOK
curl https://portal.自分のドメイン/healthz
```

## 2. iMac の準備

1. iMac に [Tailscale](https://tailscale.com/download) を入れ、VPS と同じアカウントでログイン
2. ターミナルで次を貼る

```bash
brew install android-platform-tools      # adb
git clone -b claude/sleepy-cerf-4x7qyo https://github.com/kou0728/chusen-radar.git ~/mobilerun-hub
mkdir -p ~/.config
cp ~/mobilerun-hub/mobilerun_hub/deploy/imac.env.example ~/.config/mobilerun-hub.env
chmod 600 ~/.config/mobilerun-hub.env
nano ~/.config/mobilerun-hub.env         # HUB_ADMIN_TOKEN（VPSと同じ）と PORTAL_APK（公式PortalのAPKの場所）
cd ~/mobilerun-hub && python3 -m mobilerun_hub list   # 「まだ端末がありません」と出ればOK
```

ブラウザで `http://hub:8701/` を開くと端末一覧が出ます（最初に管理トークンを入れる）。

## 3. 端末を登録する

| 順番 | 自動で行うこと | 自分で操作・確認すること |
|---|---|---|
| 1 USBでつなぐ | `mac/USBで自動登録.command` を開いておくと、つないだ新しい端末を自動で登録し始める。1台だけなら `mac/Android端末を追加.command` でも同じ | 初回はUSBデバッグをONにし、端末の「USBデバッグを許可」を押す |
| 2 Portalを入れる | Portal が無ければ入れる。ユーザー補助・重ねて表示・電池の最適化の対象外・バックグラウンド動作を許可する | PIN や初めて見る許可画面で止まったら、端末の画面を確認 |
| 3 接続先を設定 | 台帳に端末を足し、その端末専用のトークンを発行。Portal の接続先を中継サーバーにし、再起動後の再接続をONにする | ― |
| 4 確認 | 中継サーバーにつながり、操作に返事をし、画面を読めるかを確かめる。機種・Androidの版・USBシリアルを台帳に書く | 「登録完了」が出たらUSBを抜く |
| 5 画面点灯 | 登録中だけUSB給電中の点灯を保ち、終わったら元に戻す | ― |

番号を指定したい時は `python3 -m mobilerun_hub.register --number 12 --label "Pixel 赤"`。
登録済みの端末を設定し直す時（トークンが分からなくなった等）は `--number 12 --rotate`。

## 4. ふだんの使い方

| やりたいこと | 方法 |
|---|---|
| 端末一覧を見る | ブラウザで `http://hub:8701/`、または `python3 -m mobilerun_hub list` |
| 画面を見る | 一覧の「画面」、または `python3 -m mobilerun_hub shot 12` |
| 操作する間だけ点灯 | 一覧の「点灯ON／OFF」、または `python3 -m mobilerun_hub awake 12 on`（終わったら `off`） |
| 命令を送る | `python3 -m mobilerun_hub rpc 12 tap '{"x":200,"y":400}'` |
| 台帳を表計算に | 一覧の「台帳をCSVで保存」 |
| 切断の通知 | `/etc/mobilerun-hub.env` に `DISCORD_WEBHOOK_URL` を書いて `sudo systemctl restart mobilerun-hub` |

エージェントなど別のプログラムから操作する時は、管理APIに送ります（Portal の命令をそのまま使える）。

```
POST http://hub:8701/api/devices/12/rpc
Authorization: Bearer <管理トークン>
{"method": "tap", "params": {"x": 200, "y": 400}}
→ {"id": "...", "status": "success", "result": ...}
```

使える命令（`tap`・`swipe`・`keyboard/input`・`screenshot`・`state`・`app`・`app/stop`・`global` など）は
[Portal の説明](https://github.com/droidrun/mobilerun-portal/blob/main/docs/local-api.md) にあります。

## 5. ngrok からの乗り換え

1. 上の1と2を済ませる（この時点では ngrok もそのまま動かしておく）
2. 端末を1台ずつ USB でつなぎ、**今の端末番号のまま** 登録する：`python3 -m mobilerun_hub.register --number <今の番号>`
   - これで Portal の接続先が中継サーバーに変わります
3. 一覧で全台が「接続中」になったら ngrok を止める

**復旧アプリについて：** 今の復旧アプリは iMac（ngrok 経由）につないだままでも動きます。通信量はわずかなので、乗り換えのあいだはそのまま使えます。
中継サーバーには復旧アプリ用の受け口（下）が用意してあるので、作り直した見張り役アプリにつなぎ替えると、一覧の「復旧」ボタンや自動復旧が使えるようになります。

## 6. 見張り役アプリ（復旧アプリ）の受け口

通常 Portal とは別の線で中継サーバーにつながり、Portal が切れた時の立て直しを受け持つアプリのための約束事です。

- 接続先：`wss://portal.自分のドメイン/guardian/{端末番号}`、`Authorization: Bearer <見張り役のトークン>`（登録完了の画面に出る）
- アプリから送る：つないだ直後に `{"type":"hello", ...状態}`、その後ときどき `{"type":"status", "portal_installed":true, "a11y_enabled":false, "battery":80}` など（中身は自由。一覧に出る）
- サーバーから届く：`{"type":"recover","id":"…","reason":"manual" または "auto"}`
- アプリが返す：`{"type":"recover_result","id":"…（同じID）","ok":true,"detail":"ユーザー補助を付け直しました"}`

自動復旧（`HUB_AUTO_RECOVER=1`）は、Portal が5分切れていて見張り役がつながっている時だけ、1時間に3回までにしてあります。

## 7. 台数を増やす前の負荷試験

本番の端末を切り替える前に、VPS の上で偽の Portal をつないで測ります（本番の台帳には触りません）。

```bash
cd /opt/mobilerun-hub
ulimit -n 4096
.venv/bin/python -m mobilerun_hub.loadtest --devices 200 --shot-kb 1500 --parallel 20
```

一斉再接続にかかる時間、軽い命令とスクショの応答時間、メモリを出します。
スクショの同時取得数（`--parallel`）を実際の使い方に合わせて変え、メモリが VPS の半分を超えるなら上のプランを考えます。

## 8. 困ったとき

| 症状 | 確認すること |
|---|---|
| `curl https://…/healthz` がつながらない | DNS の A レコード、`sudo ufw status` で 80/443、`sudo journalctl -u caddy -n 50` |
| 端末がずっと「切断」 | 端末の電源・回線、Portal の「Connect to Host」がONか。`sudo journalctl -u mobilerun-hub -n 50` に「認証に失敗」があればトークン違い → `register --number <番号> --rotate` |
| iMac で「中継サーバーにつながりません」 | iMac の Tailscale がONか、`HUB_ADMIN_URL` の名前、VPS の `HUB_ADMIN_HOST` が `tailscale ip -4` の値か |
| 再起動すると戻らない | 登録で「再起動後の再接続をONにしました」が出たか。電池の最適化、メーカー独自の省電力（自動起動の許可・アプリのロック） |
| 中継サーバーが起動しない | `sudo journalctl -u mobilerun-hub -n 50`。`HUB_ADMIN_TOKEN` は24文字以上、`HUB_PUBLIC_URL` は `wss://` から |

## ファイル

| ファイル | 中身 |
|---|---|
| `hub.py` | 中継サーバー本体（端末の受け口・台帳・切断の見張り・管理API） |
| `admin.html` | 端末一覧の画面 |
| `register.py` | iMac の USB 登録 |
| `client.py` / `cli.py` | iMac から使うコマンド（標準ライブラリだけで動く） |
| `store.py` | 台帳（VPS の `/var/lib/mobilerun-hub/devices.json`） |
| `deploy/` | VPS の設定（Caddy・systemd・環境変数の見本） |
| `mac/` | iMac でダブルクリックして使う登録の入口 |
