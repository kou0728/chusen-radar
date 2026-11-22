# 何にいくら払うか（2026年11月時点）

抽選レーダーの「自動で拾えない穴」を、お金をかけて埋めるときの目安です。
いまのサイト本体（GitHub Actions＋GitHub Pages）は今後も0円のままで動きます。
ドル建ての料金は **1ドル＝150円** で計算しています。

## 結論

| 優先 | 埋める穴 | 払うもの | 目安 |
|---|---|---|---|
| ① | アプリ限定の抽選（ヤマダ・コジマ・iAEON・平和堂・ゲオなど） | **自宅に置く専用のAndroid端末**（中古）＋常時起動の母艦。どちらも買い切り | 初期 0〜7万円 / 月 約250〜300円（電気代） |
| ② | Xでしか告知しないお店 | **X API の従量課金**（読んだ投稿1件 $0.005） | 月 150〜750円（上限 $5 で自動停止） |
| ③ | 画像だけの告知（バナー） | **Claude API**（任意） | 月 0〜1,800円（1日の枚数上限で頭打ち） |
| － | 端末の遠隔操作（画面を見る・触る） | Tailscale（無料）＋ scrcpy（無料） | 0円 |

おすすめの進め方は、**①を手持ちのPCと中古スマホで小さく試す → 続けると決めたら母艦を買う → ②を有効にする → 必要なら③**、の順です。
①と②だけなら、月の支払いは **1,000円以下** に収まります。

## ① アプリ限定の抽選：専用のAndroid端末を自宅に置く

`android_agent/` が、USBでつないだAndroid端末で公式アプリを開き、お知らせ画面の文字を読んでサイトに載せます（応募の操作はしません）。
セットアップは [android_agent/README.md](../android_agent/README.md) にまとめました。

### なぜクラウドではなく自宅の実機なのか

- **ログインが保てる。** 会員アプリの多くはログインしないとお知らせが見えません。クラウドの試験用端末は使うたびに初期化されます。
- **本物の端末なので弾かれにくい。** エミュレータや改造端末を拒むアプリがあります。
- **日本の家庭回線から見に行ける。** 海外のサーバーからのアクセスを断るアプリがあります。
- **月額がほぼ電気代だけ。**

### 初期費用

| もの | 目安 | 選び方 |
|---|---|---|
| 専用のAndroid端末 | 手持ちの古い端末なら0円。中古で 1.5〜4万円 | Android 13以上・メモリ4GB以上。**充電を80〜90%で止める機能**がある機種（Galaxy「バッテリー保護」、Xperia「いたわり充電」、AQUOS「インテリジェントチャージ」、Pixel「充電の最適化」など。対応はOSの版と機種で違うので購入前に確認）。普段使いのスマホとは分ける |
| 母艦（いつも電源が入っているPC） | 手持ちのノートPCなら0円。中古ミニPCで 2〜4万円前後 | 消費電力が10W前後のもの。Raspberry Pi 5 は2026年のメモリ高騰で値上がりし（4GBで $85〜110）、中古ミニPCとほぼ同じ値段になった |
| USBケーブル | 1,000円前後 | データ通信ができるもの（充電専用は不可） |
| （任意）スマートプラグ | 2,000円前後 | 母艦が固まった時に、外から電源を入れ直せる |

中古相場は2026年に入って上がっています。例えば Pixel 7a の中古は、フリマで2万円前後、保証付きの店舗で4万円前後でした。

### 月額

| もの | 月額 |
|---|---|
| 電気代（母艦10W＋スマホ2Wを24時間、31円/kWh） | 約270円 |
| Tailscale（外から母艦に入る）Personal プラン | 0円（6ユーザー・端末数無制限、個人の非商用利用） |
| scrcpy（スマホの画面をPCに出して操作） | 0円（オープンソース） |
| SIM | 不要（Wi-Fiで動く） |

古いデスクトップPCを24時間つけっぱなしにすると、それだけで月700〜1,300円ほどかかります。母艦は省電力なものを選んでください。

## ② Xでしか告知しないお店：X API の従量課金

2026年から、X API は **使った分だけ払う方式** が標準になり、新規の無料枠はなくなりました。
読み取りは **投稿1件ごとに $0.005**。同じ投稿は24時間（UTCの1日）以内なら1回分の料金で、空振りの検索は無料。最低利用額はなく、先にクレジットを買っておく前払い式です。
以前の定額プラン（Basic $200/月）を契約する必要はありません。

このリポジトリでは、従量課金でむだが出ないように `collector/xsource.py` を直しました。

- 前回より新しい投稿だけを読む（以前の作りでは、3日分を30分ごとに読み直していて、同じ投稿に何日も課金されていた）
- 別料金になる投稿者の情報（user）は付けない
- **月の上限額で自動停止**（`X_MONTHLY_BUDGET_USD`、既定 $5 ≒ 750円）

試算：いまの18店で「抽選」「応募」を含む投稿が1日10〜30件なら、月 $1.5〜4.5（約230〜680円）。初回だけ過去3日分を読むので +$0.5 ほど。

使い始める手順：

1. [X Developer Console](https://developer.x.com/) でアプリを作り、クレジットを $5〜10 買う（電話番号の確認が必要）
2. Bearer Token を GitHub の Settings → Secrets and variables → Actions → **Secrets** に `X_BEARER_TOKEN` として登録
3. 上限を $5 から変えたい時は、同じ画面の **Variables** タブに `X_MONTHLY_BUDGET_USD`（例 `3`）を足す
4. 今月どれだけ読んだかは `data/state.json` の `"x"` → `"spend"`（`posts` が件数、`usd` が金額の目安）で見られる

Xの告知をAndroid端末のXアプリで自動で読む方法は使いません。Xの規約は自動でのデータ収集（スクレイピング）を禁止していて、アカウント凍結のおそれがあります。Xは公式APIで読みます。

## ③ 画像だけの告知：Claude API（任意）

アプリのお知らせが文字ではなく画像（バナー）だけのことがあります。`config/apps.yaml` で `vision: true` にすると、その画面のスクリーンショットを Claude に読ませて抽選の情報を取り出します。

- 既定のモデルは Claude Opus 5.5（入力 $4 / 出力 $20、100万トークンあたり）。画像は縮小して送るので、**1枚あたり約2〜3円**
- **同じ画面は読み直さない。** 画面が変わった時だけ送ります
- **1日の上限枚数**（`vision_daily_limit`、既定20枚）を超えたらその日は止まるので、最大でも **月1,500〜1,800円程度**
- `vision_model: claude-haiku-5-5` に変えると料金は約1/40（月数十円）。読み取りの精度は自分の画面で確かめてから選んでください
- Anthropic の Console で、月の利用上限（Spend limit）も設定しておくと安心です

まずは `vision: false` のまま始め、文字で読めないアプリが出てきたら、そのアプリだけ `vision: true` にするのがおすすめです。

## 払わなくていいもの

| よく候補に挙がるもの | 料金の例 | 使わない理由 |
|---|---|---|
| クラウドのAndroidエミュレータ（Genymotion SaaS など） | 従量 $0.06/分（24時間動かすと月 約$2,600）、定額 $179〜219/台/月 | 高い。エミュレータを弾くアプリがある |
| 試験用のクラウド実機（AWS Device Farm など） | 従量 $0.17/分、定額 $250/台/月 | 毎回初期化されログインが消える。1回150分まで |
| 格安のクラウドスマホ（月数百円〜） | ― | 海外のデータセンターで動き、お店の会員アカウント（住所・電話番号入り）を預けることになる。海外IPで弾かれることもある |
| 遠隔操作アプリの有料プラン | ― | Tailscale＋scrcpy で無料でできる |
| GitHub Actions のセルフホストランナー | 0円（2026年3月に予定された $0.002/分の課金は延期） | 公開リポジトリでは、他人のプルリクエスト経由で自宅のPCでコードを動かされるおそれがある。母艦は自分で15分ごとに動かし、結果だけを送る作りにした |
| X API の定額プラン（Basic） | $200/月 | 従量課金で足りる |

## 無料のままだと詰まりやすいところ

- **GitHub Actions の中でエミュレータを動かす**：毎回まっさらなのでログインが消える。アプリを公式の手段で入れられない。エミュレータを弾くアプリがある。公開リポジトリだと実行ログを誰でも見られる
- **普段使いのスマホを兼用する**：操作がぶつかる。電池が早く傷む
- **無料のクラウドサーバー＋Androidのコンテナ**：空きが無くて作れないことが多い。海外IP。Googleのサービスが無く、動かないアプリがある

## 守ること

- **読むだけ。** 応募・購入・送信のボタンは、レシピに書いてもプログラムが押しません
- **間隔を空ける。** 1アプリ60分おき、深夜（1〜6時）は読まない。人が時々アプリを開くのと同じくらいの負荷にとどめる
- **各アプリの利用規約を確認する。** 自動での利用を禁止しているアプリは、これまでどおり手動登録にする
- **USBデバッグがONだと起動しないアプリがある。** そのお店も手動登録のまま

## 出典（2026年11月に確認）

- X API の料金：[docs.x.com（Pricing）](https://docs.x.com/x-api/getting-started/pricing)、[docs.x.com（How billing works）](https://docs.x.com/x-api/fundamentals/post-cap)、[TechCrunch（従量課金のテスト開始）](https://techcrunch.com/2025/10/21/x-is-testing-a-pay-per-use-pricing-model-for-its-api/)
- Tailscale：[Pricing](https://tailscale.com/pricing/)、[Pricing v4 のお知らせ（2026年4月）](https://tailscale.com/blog/pricing-v4)
- Raspberry Pi の値上げ：[Raspberry Pi 公式](https://raspberrypi.com/news/more-memory-driven-price-rises)、[Phoronix](https://phoronix.com/news/raspberry-pi-2026-dram-pricing)、[Tom's Hardware](https://www.tomshardware.com/raspberry-pi/component-shortages-drive-raspberry-pi-prices-up-by-up-to-23-percent-escalating-lpddr4-lpddr5-costs-trigger-the-third-price-hike-of-the-year)
- Genymotion SaaS：[Pricing](https://www.genymotion.com/pricing)、[Docs](https://docs.genymotion.com/usage/saas/pricing/)
- AWS Device Farm：[料金](https://aws.amazon.com/jp/device-farm/pricing/)、[制限](https://docs.aws.amazon.com/devicefarm/latest/developerguide/limits.html)
- GitHub Actions のセルフホストランナー課金の延期：[GitHub Changelog](https://github.blog/changelog/2025-12-16-coming-soon-simpler-pricing-and-a-better-experience-for-github-actions/)、[The Register](https://www.theregister.com/2025/12/17/github_charge_dev_own_hardware/)
- 中古相場：[価格ランク（Pixel 7a）](https://price-rank.com/p/77818/14)、[@IT（2026年の中古PC選び）](https://atmarkit.itmedia.co.jp/ait/articles/2604/10/news015.html)
- Claude API の料金：Anthropic の公式料金表（Opus 5.5：$4/$20、Haiku 5.5：$0.10/$0.50、100万トークンあたり）

料金は変わることがあります。契約の前に各サービスの公式ページで確かめてください。
