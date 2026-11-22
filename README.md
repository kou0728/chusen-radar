# 抽選レーダー（トレカ抽選まとめサイト）

ポケモンセンターオンラインや量販店・カードショップの**公式ページを30分おきに巡回**して、
トレカの抽選（受付期間・当選発表・応募ページ）を自動で集め、締切順に並べるサイトです。
まとめアカウントの投稿をコピーするのではなく、ショップが告知している大元のページから読みます。

サーバー代は0円です（GitHub の無料機能だけで動きます）。

```
 30分ごと（GitHub Actions）
 ┌────────────────────────────────────────────┐
 │ config/sources.yaml の公式ページを巡回        │
 │   ├ ふつうのページ → そのまま読む              │
 │   ├ JSで描画するページ（ポケセン等）→ ブラウザで開いて読む │
 │   └ ショップのX（任意・APIトークンが必要）       │
 │ 「抽選」＋「期間」が書いてある箇所を抜き出す       │
 │ config/manual.yaml（手動登録）と合体           │
 │ site/ を作って GitHub Pages に公開             │
 │ 新着があれば Discord に通知（任意）              │
 └────────────────────────────────────────────┘
```

## どこから集めているか

| 種類 | 例 | 読み方 |
|---|---|---|
| メーカー・公式通販 | ポケモンセンターオンライン、プレミアムバンダイ、コナミスタイル、各タイトルの公式ニュース | ページを読む／更新を検知 |
| 量販店・通販 | ヨドバシ、イオン、セブンネット、ゲオ、エディオン、ドラスタ | ページを読む |
| ショップのX | カードラボ、晴れる屋2、MINT、ブックオフ各店など | X APIのトークンを入れた時だけ |
| 公式アプリ | ヤマダ、コジマ、iAEON、平和堂、ゲオなど | 自宅のAndroid端末で読む（任意・[android_agent/README.md](android_agent/README.md)） |
| 手動登録 | アプリ限定・店頭QRの抽選 | `config/manual.yaml` |

自動で拾えない穴をお金で埋める時の目安（Android端末・X API・Claude API）は [docs/paid-plan.md](docs/paid-plan.md) にまとめています。

手動登録では `pref: 東京都` のように書くか、店名に地名（所沢・津田沼など）があれば、東京・千葉・埼玉などのエリアが自動で付きます。
エリアの種類は `config/areas.yaml` で変えられます（神奈川を足すなど）。

## 公開手順（15分くらい）

1. GitHub で新しいリポジトリを作る（名前は例：`chusen-radar`、**Public**）。
2. このフォルダの中身を全部アップロードする（「Add file → Upload files」にドラッグでOK）。
   `.github` フォルダも忘れずに。
3. リポジトリの **Settings → Pages → Build and deployment → Source** を **GitHub Actions** にする。
4. **Settings → Actions → General → Workflow permissions** を **Read and write permissions** にする。
5. **Actions** タブ →「抽選情報を更新して公開」→ **Run workflow**。
   数分で `https://<ユーザー名>.github.io/chusen-radar/` に公開されます。
6. `config/site.yaml` の `url` を公開URLに、`operator` を運営名に書き換えて保存。

以降は30分ごとに自動で更新されます。

独自ドメインを使う場合は Settings → Pages → Custom domain に入れて、`site.yaml` の `url` も合わせてください。

## ふだんの運用

| やりたいこと | 触るファイル |
|---|---|
| アプリ限定・店頭QRの抽選を載せる | `config/manual.yaml` に1行足す（書き方はファイル冒頭） |
| アプリ限定の抽選を自動で読む | `config/apps.yaml` のレシピを仕上げて `enabled: true`（[android_agent/README.md](android_agent/README.md)） |
| 巡回するショップを増やす | `config/sources.yaml` に URL を足す（Xアカウントなら `pref: 東京都` も書くとエリアが付く） |
| エリアを増やす（神奈川など） | `config/areas.yaml` に1行足す |
| 新弾が出た | `config/keywords.yaml` の `products` に商品名を足す（自動でまとまる） |
| 新しいトレカのタイトルを増やす | `config/keywords.yaml` の `games` に1ブロック足す（表示設定にも自動で出る） |
| 掲載を止めたい抽選 | `manual.yaml` で `hide: true` |
| ショップから掲載停止の依頼が来た | `sources.yaml` でそのショップを `enabled: false` |

GitHub の画面でファイルを開いて鉛筆マークから編集 → 保存すると、すぐ再公開されます。

巡回が成功しているかはサイトの「このサイトについて → 情報源と巡回状況」で確認できます。

## 任意の設定（Settings → Secrets and variables → Actions → New repository secret）

| 名前 | 何が起きるか |
|---|---|
| `DISCORD_WEBHOOK_URL` | 新着の抽選と「中身が変わった公式ページ」を Discord に通知。X投稿の下書きにも使えます |
| `X_BEARER_TOKEN` | `sources.yaml` の `x_user`（ショップのXアカウント）からも抽選告知を拾う。X API の従量課金（読んだ投稿1件 $0.005）で、新しい投稿だけを読む。月の上限は Variables の `X_MONTHLY_BUDGET_USD`（既定 $5）で、超えたらその月は止まる |

`config/site.yaml` の `amazon_tag` にアソシエイトIDを入れると、Amazon招待リクエストのリンクに自動で付きます。

## 自動取得の限界と対策

- **ログインが必要・画像だけの告知**は読めません。`type: watch` にしておくと「ページが変わった」ことだけ通知されるので、確認して `manual.yaml` に足してください。
- **ショップのXでしか告知しない店舗**（個人店・各支店）は、X API なしでは拾えません。ここは手動登録が現実的です。
- **アプリの中だけで告知する抽選**は、自宅にAndroid端末を置けば自動で読めます（`android_agent/`）。置かない場合は手動登録です。
- サイトの作りが変わると抽出できなくなることがあります。巡回状況が「取得できませんでした」になったら、URLが変わっていないか確認してください。
- 巡回は robots.txt を守り、同じサイトへは3秒以上あけて、30分に1回だけアクセスします。

## 情報提供フォームをつなぐ（無料）

サイトの「抽選情報を送る」から届いた投稿をGoogleスプレッドシートに溜め、
**「掲載する」にチェックを入れた行だけ** が次の自動更新でサイトに載ります。

1. 新しいGoogleスプレッドシートを作る → 拡張機能 → Apps Script
2. `tools/form/Code.gs` の中身を貼り付けて保存 → 関数「setup」を実行（権限を許可）
3. デプロイ → 新しいデプロイ → ウェブアプリ（実行ユーザー：自分／アクセス：全員）
4. 出てきたURL（…/exec）を `config/site.yaml` の `form_endpoint` に貼って保存

`form_endpoint` が空のあいだは、サイトにボタンは出ません。

## ローカルで試す

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m pytest -q tests                 # 日付・抽出のテスト
python -m collector.main --offline        # 巡回せず manual.yaml だけでサイトを作る
python -m collector.main                  # 実際に巡回
open site/index.html
```

## フォルダ構成

```
collector/      巡回・抽出・公開ファイル生成（Python）
android_agent/  自宅のAndroid端末で公式アプリを読む（任意。母艦で動かす）
mobilerun_hub/  Mobilerun Portal の中継サーバー（ngrok の代わり。VPS で動かす。mobilerun_hub/README.md）
config/         巡回先・手動登録・アプリのレシピ・キーワード・サイト設定
web/            サイトの見た目（HTML/CSS/JS）
data/           巡回結果の保存（自動で更新される。app_found.json はAndroid端末から届く）
docs/           費用の目安など
tests/          テスト
.github/        30分ごとの自動実行
```
