/**
 * 抽選レーダー 情報提供フォームの受け口（Google Apps Script）
 *
 * 使い方
 *  1. 新しいGoogleスプレッドシートを作る → 拡張機能 → Apps Script
 *  2. このファイルの中身を全部貼り付けて保存
 *  3. 上の関数選択で「setup」を選んで実行（初回だけ権限の許可が出ます）
 *  4. デプロイ → 新しいデプロイ → 種類「ウェブアプリ」
 *       実行ユーザー：自分 / アクセスできるユーザー：全員 → デプロイ
 *  5. 表示された「ウェブアプリのURL」（…/exec）を、config/site.yaml の form_endpoint に貼る
 *
 * 届いた投稿は「投稿」シートに溜まります。「掲載する」にチェックを入れた行だけがサイトに載ります。
 */
const SHEET = '投稿';
const HEAD = ['受付日時', 'タイトル', '商品名', '店舗名', '都道府県', '応募方法', '受付開始', '受付締切', '当選発表', '応募ページURL', 'メモ', '掲載する'];
const COL_OK = 12;

function setup() {
  const ss = SpreadsheetApp.getActive();
  const sh = ss.getSheetByName(SHEET) || ss.insertSheet(SHEET);
  sh.getRange(1, 1, 1, HEAD.length).setValues([HEAD]).setFontWeight('bold');
  sh.setFrozenRows(1);
  sh.getRange('G:I').setNumberFormat('@'); // 日時は文字のまま保存
  sh.getRange(2, COL_OK, sh.getMaxRows() - 1, 1).insertCheckboxes();
}

function doPost(e) {
  try {
    const d = JSON.parse((e.postData && e.postData.contents) || '{}');
    if (d.website) return out({ ok: true }); // ボット対策の見えない欄に入力があれば捨てる
    const cache = CacheService.getScriptCache();
    const n = Number(cache.get('n') || 0);
    if (n > 30) return out({ ok: false, error: '送信が混み合っています。少し待ってから送ってください。' });
    cache.put('n', String(n + 1), 60);
    if (!d.store || !d.apply_end) return out({ ok: false, error: '店舗名と受付締切は必須です。' });
    // 先頭が = + - @ の値は数式として動かないよう文字にする
    const s = (v, max) => String(v || '').trim().slice(0, max).replace(/^[=+\-@]/, "'$&");
    const sh = SpreadsheetApp.getActive().getSheetByName(SHEET);
    sh.appendRow([new Date(), s(d.game, 30), s(d.product, 60), s(d.store, 60), s(d.pref, 10), s(d.form, 10),
      s(d.apply_start, 20), s(d.apply_end, 20), s(d.announce, 20), s(d.url, 300), s(d.note, 300), false]);
    sh.getRange(sh.getLastRow(), COL_OK).insertCheckboxes();
    return out({ ok: true });
  } catch (err) {
    return out({ ok: false, error: '送信を受け付けられませんでした。' });
  }
}

/** サイトの自動更新が、掲載OKの行だけを読みに来る */
function doGet() {
  const sh = SpreadsheetApp.getActive().getSheetByName(SHEET);
  const rows = sh.getDataRange().getDisplayValues().slice(1);
  const items = rows.filter((r) => r[COL_OK - 1] === 'TRUE').map((r) => ({
    game: r[1], product: r[2], store: r[3], pref: r[4], form: r[5],
    apply_start: r[6], apply_end: r[7], announce: r[8], url: r[9], note: r[10],
  }));
  return out({ items });
}

function out(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
