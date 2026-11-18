(() => {
  "use strict";
  const FORM = { web: "WEB", app: "アプリ", store: "店頭", x: "X" };
  const WD = "日月火水木金土";
  const CLOSING_HOURS = 24;
  const $ = (s, el = document) => el.querySelector(s);

  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* 保存できない環境では無視 */ } },
  };

  const state = {
    data: null,
    status: "open",
    game: "all",
    form: "all",
    q: "",
    hideApplied: store.get("hideApplied", false),
    applied: new Set(store.get("applied", [])),
    seen: store.get("seenAt", null),
    // 外した店舗だけを保存するので、あとから増えた店舗は最初から表示される
    excluded: new Set(store.get("excludedStores", [])),
    excludedGames: new Set(store.get("excludedGames", [])),
    areas: new Set(store.get("areas", [])), // 空ならすべてのエリア
  };

  // ---------- 日付 ----------
  const d = (iso) => (iso ? new Date(iso) : null);
  const md = (t) => `${t.getMonth() + 1}/${t.getDate()}(${WD[t.getDay()]})`;
  const hm = (t) => `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
  const full = (t) => (t ? `${md(t)} ${hm(t)}` : "");
  const startFmt = (t) => (t.getHours() === 0 && t.getMinutes() === 0 ? md(t) : full(t));
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

  function statusOf(it, now) {
    const s = d(it.apply_start), e = d(it.apply_end), a = d(it.announce);
    if (it.ongoing && !e) return "open";
    if (s && now < s) return "upcoming";
    if (e && now <= e) return e - now <= CLOSING_HOURS * 3600e3 ? "closing" : "open";
    if (a && now < a) return "waiting";
    return "closed";
  }
  const tabOf = (st) => (st === "closing" ? "open" : st);

  function left(ms) {
    const m = Math.max(0, Math.floor(ms / 60000));
    if (m < 60) return [m, "分"];
    const h = Math.floor(m / 60);
    if (h < 48) return [h, "時間"];
    return [Math.floor(h / 24), "日"];
  }

  // ---------- 描画 ----------
  function stub(el, it, st, now) {
    const lead = $(".stub-lead", el), num = $(".stub-num", el), unit = $(".stub-unit", el), time = $(".stub-time", el);
    const e = d(it.apply_end), s = d(it.apply_start), a = d(it.announce);
    const set = (l, n, u, t) => { lead.textContent = l; num.textContent = n; unit.textContent = u; time.textContent = t; };
    if (st === "open" && !e) { el.classList.add("compact"); return set("締切", "未定", "", "招待・常設"); }
    if (st === "open" || st === "closing") { const [n, u] = left(e - now); return set("締切まで あと", n, u, `${md(e)}\n${hm(e)}まで`); }
    if (st === "upcoming") { const [n, u] = left(s - now); return set("受付開始まで", n, u, `${md(s)} ${hm(s)}〜`); }
    if (st === "waiting") { const [n, u] = left(a - now); return set("当選発表まで", n, u, it.announce_allday ? md(a) : `${md(a)} ${hm(a)}`); }
    el.classList.add("compact");
    return set("受付", "終了", "", e ? `${md(e)}まで` : "");
  }

  function ticket(it, now) {
    const st = statusOf(it, now);
    const node = $("#ticketTpl").content.firstElementChild.cloneNode(true);
    node.dataset.status = st;
    node.id = "l-" + it.id;
    if (state.applied.has(it.id)) node.classList.add("is-applied");
    if (state.seen && it.first_seen && new Date(it.first_seen) > new Date(state.seen)) node.classList.add("is-new");
    stub($(".stub", node), it, st, now);
    $(".stub-time", node).style.whiteSpace = "pre-line";

    $(".store", node).textContent = it.store;
    const g = state.data.games[it.game];
    if (g) $(".game", node).textContent = g; else $(".game", node).remove();
    if (FORM[it.form]) $(".form", node).textContent = FORM[it.form]; else $(".form", node).remove();
    const prefLabel = it.pref ? it.pref.replace(/[都府県]$/, "") : it.area === "national" ? "全国" : "";
    if (prefLabel) $(".pref", node).textContent = prefLabel; else $(".pref", node).remove();
    $(".name", node).textContent = it.product ? `${it.product}${it.title && !it.title.includes(it.product) ? "　" + it.title : ""}` : it.title;

    const dl = $(".dates", node);
    const row = (k, v) => { if (!v) return; const dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = k; dd.textContent = v; dl.append(dt, dd); };
    if (it.apply_end) row("受付", `${it.apply_start ? startFmt(d(it.apply_start)) : ""}〜${full(d(it.apply_end))}`);
    row("当選発表", it.announce ? (it.announce_allday ? md(d(it.announce)) : full(d(it.announce))) : "");
    if (it.purchase_end) row("購入", `${it.purchase_start ? startFmt(d(it.purchase_start)) : ""}〜${full(d(it.purchase_end))}`);

    const ul = $(".conds", node);
    (it.conditions || []).forEach((c) => { const li = document.createElement("li"); li.textContent = c; ul.append(li); });
    if (!ul.children.length) ul.remove();

    const a = $(".apply", node);
    const url = affiliate(it.url);
    if (url) {
      a.href = url;
      if (/x\.com|twitter\.com/.test(it.url)) a.textContent = "告知ポストを開く";
      if (st === "closed" || st === "waiting") a.textContent = "ページを開く";
    } else if (st === "waiting" || st === "closed") {
      a.remove();
    } else {
      a.textContent = it.form === "app" ? "公式アプリから応募" : it.form === "store" ? "店頭で応募" : "応募方法は公式で確認";
      a.setAttribute("aria-disabled", "true");
      a.removeAttribute("href");
    }

    const cal = $(".cal", node);
    if (st === "closed" || !it.apply_end) cal.remove();
    else cal.addEventListener("click", () => downloadIcs(it, st));

    $(".share", node).href = shareUrl(it);

    const chk = $(".applied input", node);
    chk.checked = state.applied.has(it.id);
    chk.addEventListener("change", () => {
      chk.checked ? state.applied.add(it.id) : state.applied.delete(it.id);
      store.set("applied", [...state.applied]);
      node.classList.toggle("is-applied", chk.checked);
      if (state.hideApplied && chk.checked) setTimeout(render, 250);
    });
    return node;
  }

  function affiliate(url) {
    const tag = state.data.site && state.data.site.amazon_tag;
    if (!url || !tag || !/amazon\.co\.jp/.test(url)) return url;
    try { const u = new URL(url); u.searchParams.set("tag", tag); return u.toString(); } catch { return url; }
  }

  function matches(it) {
    if (state.excluded.has(it.store) || state.excludedGames.has(it.game)) return false;
    if (state.areas.size && !state.areas.has(it.area)) return false;
    if (state.game !== "all" && it.game !== state.game) return false;
    if (state.form !== "all" && it.form !== state.form) return false;
    if (state.hideApplied && state.applied.has(it.id)) return false;
    if (state.q) {
      const hay = `${it.store} ${it.product || ""} ${it.title || ""} ${state.data.games[it.game] || ""} ${it.pref || ""}`.toLowerCase();
      if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  function sortKey(it, st) {
    const big = 8.64e15;
    if (st === "upcoming") return +d(it.apply_start);
    if (st === "waiting") return +d(it.announce);
    if (st === "closed") return -(+d(it.apply_end) || 0);
    return it.apply_end ? +d(it.apply_end) : big;
  }

  function groupLabel(it, st, now) {
    const t = st === "upcoming" ? d(it.apply_start) : st === "waiting" ? d(it.announce) : d(it.apply_end);
    if (!t) return { key: "none", d: "期限なし", rel: "" };
    const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
    const verb = st === "upcoming" ? "受付開始" : st === "waiting" ? "当選発表" : st === "closed" ? "受付終了" : "締切";
    let rel = "";
    if (sameDay(t, now)) rel = "今日";
    else if (sameDay(t, tomorrow)) rel = "明日";
    return { key: t.toDateString(), d: md(t), rel, verb };
  }

  function render() {
    const now = new Date();
    const items = state.data.items.map((it) => ({ it, st: statusOf(it, now) }));
    // タブの件数（タイトル・方法の絞り込みは反映）
    const counts = { open: 0, upcoming: 0, waiting: 0, closed: 0 };
    items.forEach(({ it, st }) => { if (matches(it)) counts[tabOf(st)]++; });
    document.querySelectorAll(".tabs button").forEach((b) => {
      $("b", b).textContent = counts[b.dataset.status];
      b.setAttribute("aria-selected", String(b.dataset.status === state.status));
    });

    // タイトル別の件数は、いま開いているタブの件数にそろえる
    const per = {}; let all = 0;
    items.forEach(({ it, st }) => {
      if (tabOf(st) !== state.status) return;
      const okOther = (state.form === "all" || it.form === state.form) && !(state.hideApplied && state.applied.has(it.id)) && !state.excluded.has(it.store) && !state.excludedGames.has(it.game) && (!state.areas.size || state.areas.has(it.area));
      if (!okOther) return;
      per[it.game] = (per[it.game] || 0) + 1; all++;
    });
    const perArea = {}; let allA = 0;
    items.forEach(({ it, st }) => {
      if (tabOf(st) !== state.status) return;
      const saved = state.areas; state.areas = new Set(); const ok = matches(it); state.areas = saved;
      if (!ok) return;
      perArea[it.area] = (perArea[it.area] || 0) + 1; allA++;
    });
    document.querySelectorAll("#areaChips .chip").forEach((c) => {
      const n = c.querySelector(".n"); if (n) n.textContent = c.dataset.value === "all" ? allA : (perArea[c.dataset.value] || 0);
    });
    document.querySelectorAll("#gameChips .chip").forEach((c) => {
      const n = c.querySelector(".n"); if (n) n.textContent = c.dataset.value === "all" ? all : (per[c.dataset.value] || 0);
    });

    const list = $("#list");
    list.textContent = "";
    const shown = items.filter(({ it, st }) => tabOf(st) === state.status && matches(it))
      .sort((a, b) => sortKey(a.it, a.st) - sortKey(b.it, b.st));

    if (!shown.length) {
      const box = document.createElement("div");
      box.className = "empty";
      const msg = {
        open: ["受付中の抽選はありません", "「開始前」に、これから始まる抽選が並んでいます。"],
        upcoming: ["予定されている抽選はまだありません", "公式ページは30分おきに確認しています。新しい告知が出たらここに並びます。"],
        waiting: ["発表待ちの抽選はありません", "応募した抽選の発表日が近づくとここに並びます。"],
        closed: ["終了した抽選はありません", ""],
      }[state.status];
      const filtered = state.game !== "all" || state.form !== "all" || state.q || excludedCount() > 0 || state.excludedGames.size > 0;
      box.innerHTML = `<h2></h2><p></p>`;
      $("h2", box).textContent = filtered ? "条件に合う抽選がありません" : msg[0];
      $("p", box).textContent = filtered ? "絞り込みや「表示設定」を見直すと、ほかの抽選も表示されます。" : msg[1];
      list.append(box);
    }

    let lastKey = null;
    const frag = document.createDocumentFragment();
    shown.forEach(({ it, st }) => {
      const g = groupLabel(it, st, now);
      if (g.key !== lastKey) {
        lastKey = g.key;
        const h = document.createElement("h2");
        h.className = "day";
        h.innerHTML = `<span class="d"></span><span class="rel"></span><span class="v"></span>`;
        $(".d", h).textContent = g.d;
        $(".rel", h).textContent = g.rel;
        $(".v", h).textContent = g.verb || "";
        frag.append(h);
      }
      frag.append(ticket(it, now));
    });
    list.append(frag);
    summary(items, now);
  }

  function summary(items, now) {
    const vis = items.filter(({ it }) => !state.excluded.has(it.store) && !state.excludedGames.has(it.game) && (!state.areas.size || state.areas.has(it.area)));
    const open = vis.filter(({ st }) => st === "open" || st === "closing").length;
    const today = vis.filter(({ it, st }) => (st === "open" || st === "closing") && it.apply_end && sameDay(d(it.apply_end), now)).length;
    const el = $("#summary");
    el.innerHTML = "";
    const a = document.createElement("strong"); a.textContent = `${open}件`;
    el.append("受付中 ", a);
    if (today) { const b = document.createElement("strong"); b.className = "hot"; b.textContent = `${today}件`; el.append("。今日締切が ", b, " あります。"); }
    else el.append("。");
  }

  function chips() {
    const has = new Set(state.data.items.map((it) => it.game));
    const games = [["all", "すべて"], ...Object.entries(state.data.games).filter(([k]) => has.has(k) && !state.excludedGames.has(k))];
    if (state.game !== "all" && !games.some(([k]) => k === state.game)) state.game = "all";
    const gc = $("#gameChips");
    gc.textContent = "";
    games.forEach(([k, label]) => gc.append(chip(label, 0, k, "game")));
    const fc = $("#formChips");
    if (!fc.children.length) [["all", "応募方法すべて"], ...Object.entries(FORM)].forEach(([k, label]) => fc.append(chip(label, null, k, "form")));
  }

  function areaChips() {
    const box = $("#areaChips");
    box.textContent = "";
    const mk = (label, value) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip"; b.dataset.value = value; b.textContent = label;
      const n = document.createElement("span"); n.className = "n"; b.append(n);
      b.setAttribute("aria-pressed", String(value === "all" ? state.areas.size === 0 : state.areas.has(value)));
      b.addEventListener("click", () => {
        if (value === "all") state.areas.clear();
        else state.areas.has(value) ? state.areas.delete(value) : state.areas.add(value);
        if (state.areas.size === (state.data.areas || []).length) state.areas.clear();
        store.set("areas", [...state.areas]);
        box.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.value === "all" ? state.areas.size === 0 : state.areas.has(c.dataset.value))));
        syncUrl(); render();
      });
      return b;
    };
    box.append(mk("全エリア", "all"));
    (state.data.areas || []).forEach((a) => box.append(mk(a.label, a.id)));
  }

  function chip(label, n, value, key) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = label;
    if (n != null) { const s = document.createElement("span"); s.className = "n"; s.textContent = n; b.append(s); }
    b.setAttribute("aria-pressed", String(state[key] === value));
    b.addEventListener("click", () => {
      state[key] = value;
      b.parentElement.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === b)));
      syncUrl(); render();
    });
    b.dataset.value = value;
    return b;
  }

  // ---------- カレンダー・共有 ----------
  function icsDate(t) { return t.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, ""); }
  function downloadIcs(it, st) {
    const end = d(it.apply_end);
    const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//chusen-radar//JA", "CALSCALE:GREGORIAN"];
    const ev = (uid, t, title, alarm) => {
      lines.push("BEGIN:VEVENT", `UID:${uid}@chusen-radar`, `DTSTAMP:${icsDate(new Date())}`,
        `DTSTART:${icsDate(new Date(+t - 30 * 60e3))}`, `DTEND:${icsDate(t)}`,
        `SUMMARY:${title.replace(/[,;]/g, " ")}`, `URL:${it.url || location.href}`,
        `DESCRIPTION:${(it.url || "").replace(/[,;]/g, " ")}`);
      if (alarm) lines.push("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:締切まもなく", "TRIGGER:-PT3H", "END:VALARM");
      lines.push("END:VEVENT");
    };
    const name = `${it.store} ${it.product || it.title}`;
    if (st === "upcoming" && it.apply_start) ev(it.id + "-s", d(it.apply_start), `【受付開始】${name}`, false);
    ev(it.id + "-e", end, `【抽選締切】${name}`, true);
    if (it.announce) ev(it.id + "-a", d(it.announce), `【当選発表】${name}`, false);
    lines.push("END:VCALENDAR");
    const blob = new Blob([lines.join("\r\n")], { type: "text/calendar" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `抽選_${it.store}.ics`;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function shareUrl(it) {
    const e = d(it.apply_end);
    const site = state.data.site || {};
    const text = `【${it.store}】${it.product || it.title} 抽選` + (e ? `\n締切 ${full(e)}` : "") + "\n";
    const pageUrl = (site.url && !/example\./.test(site.url) ? site.url : location.href.split("#")[0]) + "#l-" + it.id;
    const via = site.x_account ? `&via=${encodeURIComponent(site.x_account)}` : "";
    return `https://x.com/intent/post?text=${encodeURIComponent(text)}&url=${encodeURIComponent(pageUrl)}${via}`;
  }


  // ---------- 表示する店舗の設定 ----------
  const groups = () => (state.data.areas || [{ id: "national", label: "通販・全国" }, { id: "other", label: "その他の地域" }]).map((a) => [a.id, a.label]);
  let storeIdx = null;

  function buildStoreIndex() {
    const now = new Date();
    const m = new Map();
    state.data.items.forEach((it) => {
      const e = m.get(it.store) || { name: it.store, areas: {}, total: 0, live: 0 };
      e.total++;
      if (statusOf(it, now) !== "closed") e.live++;
      const a = it.area || "other";
      e.areas[a] = (e.areas[a] || 0) + 1;
      m.set(it.store, e);
    });
    m.forEach((e) => { e.group = Object.entries(e.areas).sort((a, b) => b[1] - a[1])[0][0]; });
    storeIdx = [...m.values()].sort((a, b) => b.live - a.live || a.name.localeCompare(b.name, "ja"));
  }
  const excludedCount = () => (storeIdx || []).filter((e) => state.excluded.has(e.name)).length;

  function saveExcluded() {
    store.set("excludedStores", [...state.excluded]);
    store.set("excludedGames", [...state.excludedGames]);
    updateStoreBtn();
    chips();
    render();
  }

  function updateStoreBtn() {
    const total = storeIdx.length, on = total - excludedCount();
    const gTotal = Object.keys(state.data.games).length, gOn = gTotal - [...state.excludedGames].filter((g) => g in state.data.games).length;
    const filtered = on < total || gOn < gTotal;
    $("#storeCount").textContent = filtered ? "絞り込み中" : "";
    $("#storeBtn").classList.toggle("is-filtered", filtered);
    $("#storeSel").textContent = `タイトル ${gOn}/${gTotal}・店舗 ${on}/${total}`;
    $("#gameSel").textContent = `${gOn}/${gTotal}`;
    $("#storeSelH").textContent = `${on}/${total}`;
  }

  function renderGames() {
    const now = new Date();
    const live = {};
    state.data.items.forEach((it) => { if (statusOf(it, now) !== "closed") live[it.game] = (live[it.game] || 0) + 1; });
    const box = $("#gameList");
    box.textContent = "";
    Object.entries(state.data.games).forEach(([k, label]) => {
      const [row] = checkRow(label, !state.excludedGames.has(k), (v) => {
        v ? state.excludedGames.delete(k) : state.excludedGames.add(k);
        saveExcluded();
      }, live[k] ? `${live[k]}件` : "0", true);
      if (!live[k]) $(".n", row).classList.add("zero");
      box.append(row);
    });
  }

  function checkRow(label, checked, onChange, n, live) {
    const lab = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = checked;
    cb.addEventListener("change", () => onChange(cb.checked));
    const t = document.createElement("span"); t.textContent = label;
    lab.append(cb, t);
    if (n != null) { const c = document.createElement("span"); c.className = "n" + (live ? " live" : ""); c.textContent = n; lab.append(c); }
    return [lab, cb];
  }

  function renderSheet() {
    const q = $("#storeQ").value.trim().toLowerCase();
    const box = $("#storeList");
    box.textContent = "";
    groups().forEach(([g, label]) => {
      const rows = storeIdx.filter((e) => e.group === g && (!q || e.name.toLowerCase().includes(q)));
      if (!rows.length) return;
      const sec = document.createElement("div");
      sec.className = "sheet-group";
      const onN = rows.filter((e) => !state.excluded.has(e.name)).length;
      const [head, hcb] = checkRow(label, onN === rows.length, (v) => {
        rows.forEach((e) => (v ? state.excluded.delete(e.name) : state.excluded.add(e.name)));
        saveExcluded(); renderSheet();
      }, `${onN}/${rows.length}`);
      hcb.indeterminate = onN > 0 && onN < rows.length;
      const ul = document.createElement("ul");
      rows.forEach((e) => {
        const li = document.createElement("li");
        const [row] = checkRow(e.name, !state.excluded.has(e.name), (v) => {
          v ? state.excluded.delete(e.name) : state.excluded.add(e.name);
          saveExcluded();
          const gOn = rows.filter((x) => !state.excluded.has(x.name)).length;
          hcb.checked = gOn === rows.length; hcb.indeterminate = gOn > 0 && gOn < rows.length;
          $(".n", head).textContent = `${gOn}/${rows.length}`;
        }, e.live ? `${e.live}件` : "終了", e.live > 0);
        li.append(row); ul.append(li);
      });
      sec.append(head, ul);
      box.append(sec);
    });
    if (!box.children.length) box.innerHTML = '<p class="sheet-note">その名前の店舗はまだ掲載されていません。</p>';
  }

  let opener = null;
  function openSheet() {
    opener = document.activeElement;
    $("#storeQ").value = "";
    renderGames();
    renderSheet();
    $("#storeSheet").hidden = false;
    document.body.classList.add("locked");
    $("#storeSheet .sheet-x").focus();
  }
  function closeSheet() {
    $("#storeSheet").hidden = true;
    document.body.classList.remove("locked");
    if (opener) opener.focus();
  }

  function bindStores() {
    buildStoreIndex();
    updateStoreBtn();
    $("#storeBtn").addEventListener("click", openSheet);
    document.querySelectorAll("#storeSheet [data-close]").forEach((el) => el.addEventListener("click", closeSheet));
    $("#storeAll").addEventListener("click", () => { state.excluded.clear(); saveExcluded(); renderSheet(); });
    $("#storeNone").addEventListener("click", () => { storeIdx.forEach((e) => state.excluded.add(e.name)); saveExcluded(); renderSheet(); });
    let t;
    $("#storeQ").addEventListener("input", () => { clearTimeout(t); t = setTimeout(renderSheet, 120); });
    document.addEventListener("keydown", (e) => {
      const sheet = $("#storeSheet");
      if (sheet.hidden) return;
      if (e.key === "Escape") closeSheet();
      if (e.key === "Tab") { // ダイアログの中でフォーカスを回す
        const f = [...sheet.querySelectorAll("button, input")].filter((x) => x.offsetParent);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
        else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
      }
    });
  }


  // ---------- 設定をリンクで移す ----------
  const b64e = (str) => btoa(String.fromCharCode(...new TextEncoder().encode(str))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const b64d = (b) => new TextDecoder().decode(Uint8Array.from(atob(b.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0)));

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 2600);
  }

  function settingsUrl() {
    const cfg = { v: 1, a: [...state.areas], xg: [...state.excludedGames], xs: [...state.excluded], h: state.hideApplied ? 1 : 0 };
    const site = state.data.site || {};
    const base = site.url && !/example\./.test(site.url) ? site.url : location.href.split(/[?#]/)[0];
    return `${base}?cfg=${b64e(JSON.stringify(cfg))}`;
  }

  // リンクで開かれた時：設定を取り込んでこの端末に保存
  function importSettingsFromUrl() {
    const p = new URLSearchParams(location.search);
    const raw = p.get("cfg");
    if (!raw) return;
    try {
      const cfg = JSON.parse(b64d(raw));
      state.areas = new Set(cfg.a || []);
      state.excludedGames = new Set(cfg.xg || []);
      state.excluded = new Set(cfg.xs || []);
      state.hideApplied = !!cfg.h;
      store.set("areas", [...state.areas]);
      store.set("excludedGames", [...state.excludedGames]);
      store.set("excludedStores", [...state.excluded]);
      store.set("hideApplied", state.hideApplied);
      p.delete("cfg");
      try { history.replaceState(null, "", p.toString() ? "?" + p : location.pathname); } catch { /* 埋め込み表示では無視 */ }
      setTimeout(() => toast("リンクの設定を読み込みました"), 300);
    } catch {
      setTimeout(() => toast("設定リンクを読み込めませんでした"), 300);
    }
  }

  function bindCfg() {
    $("#cfgCopy").addEventListener("click", async () => {
      const url = settingsUrl();
      const box = $("#cfgUrl");
      box.value = url;
      try {
        await navigator.clipboard.writeText(url);
        toast("設定リンクをコピーしました");
        box.hidden = true;
      } catch {
        box.hidden = false; box.focus(); box.select();
        toast("リンクを選択しました。長押しでコピーしてください");
      }
    });
  }


  // ---------- 情報提供フォーム ----------
  function bindPostForm() {
    const endpoint = state.data.site && state.data.site.form_endpoint;
    if (!endpoint) return;
    const btn = $("#formBtn"), sheet = $("#formSheet"), form = $("#postForm"), err = $("#formErr");
    btn.hidden = false;
    const sel = $("#fGame");
    Object.entries(state.data.games).forEach(([, label]) => { const o = document.createElement("option"); o.textContent = label; sel.append(o); });
    let back = null;
    const open = () => { back = document.activeElement; err.hidden = true; sheet.hidden = false; document.body.classList.add("locked"); form.store.focus(); };
    const close = () => { sheet.hidden = true; document.body.classList.remove("locked"); if (back) back.focus(); };
    btn.addEventListener("click", open);
    form.addEventListener("input", (e) => e.target.removeAttribute("aria-invalid"));
    const contact = document.getElementById("contactLink");
    if (contact) contact.addEventListener("click", (e) => { e.preventDefault(); open(); });
    sheet.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (e) => { if (!sheet.hidden && e.key === "Escape") close(); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      err.hidden = true;
      const missing = [form.store, form.apply_end].filter((f) => !f.value.trim());
      [form.store, form.apply_end].forEach((f) => f.setAttribute("aria-invalid", String(!f.value.trim())));
      if (missing.length) { err.textContent = "店舗名と受付締切を入れてください。"; err.hidden = false; missing[0].focus(); return; }
      const data = Object.fromEntries(new FormData(form).entries());
      const submit = $("#formSubmit");
      submit.disabled = true; submit.textContent = "送信中…";
      try {
        const r = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify(data) });
        const j = await r.json();
        if (!j.ok) throw new Error(j.error || "");
        form.reset();
        close();
        toast("送信しました。確認してから掲載します");
      } catch (x) {
        err.textContent = x instanceof TypeError || !(x && x.message) ? "送信できませんでした。通信状態を確認して、もう一度送ってください。" : x.message;
        err.hidden = false;
      } finally {
        submit.disabled = false; submit.textContent = "送信する";
      }
    });
  }

  // ---------- URLと状態 ----------
  function syncUrl() {
    const p = new URLSearchParams();
    if (state.status !== "open") p.set("s", state.status);
    if (state.game !== "all") p.set("g", state.game);
    if (state.form !== "all") p.set("f", state.form);
    if (state.q) p.set("q", state.q);
    if (state.areas.size) p.set("a", [...state.areas].join(","));
    const qs = p.toString();
    try { history.replaceState(null, "", qs ? "?" + qs : location.pathname); } catch { /* 埋め込み表示では無視 */ }
  }
  function readUrl() {
    const p = new URLSearchParams(location.search);
    state.status = p.get("s") || "open";
    state.game = p.get("g") || "all";
    state.form = p.get("f") || "all";
    state.q = p.get("q") || "";
    if (p.get("a")) state.areas = new Set(p.get("a").split(","));
  }

  function bind() {
    document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
      state.status = b.dataset.status; syncUrl(); render(); $("#list").scrollIntoView({ block: "start" });
      window.scrollBy(0, -$("#controls").offsetHeight);
    }));
    const q = $("#q");
    q.value = state.q;
    let t;
    q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value.trim(); syncUrl(); render(); }, 150); });
    const ha = $("#hideApplied");
    ha.checked = state.hideApplied;
    ha.addEventListener("change", () => { state.hideApplied = ha.checked; store.set("hideApplied", ha.checked); render(); });
    const controls = $("#controls");
    new IntersectionObserver(([e]) => controls.classList.toggle("stuck", e.intersectionRatio < 1), { threshold: [1], rootMargin: "-1px 0px 0px 0px" }).observe(controls);
    const contact = state.data.site && state.data.site.contact_url;
    if (contact) $("#contactLink").href = contact;
  }

  async function load() {
    if (window.__DATA__) return window.__DATA__;
    const r = await fetch("data.json", { cache: "no-store" });
    return r.json();
  }

  load().then((data) => {
    state.data = data;
    importSettingsFromUrl();
    readUrl();
    areaChips();
    chips();
    bind();
    bindStores();
    bindCfg();
    bindPostForm();
    render();
    // 共有リンクから来たら、その抽選が含まれるタブを開いてスクロール
    if (location.hash.startsWith("#l-")) {
      const id = location.hash.slice(3);
      const it = data.items.find((x) => x.id === id);
      if (it) { state.status = tabOf(statusOf(it, new Date())); render(); const el = document.getElementById("l-" + id); if (el) el.scrollIntoView({ block: "center" }); }
    }
    store.set("seenAt", data.updated);
    setInterval(() => { if (!$("#list").contains(document.activeElement)) render(); }, 60e3);
  }).catch(() => {
    $("#list").innerHTML = '<div class="empty"><h2>データを読み込めませんでした</h2><p>ページを再読み込みしてください。続く場合は時間をおいて開いてください。</p></div>';
  });
})();
