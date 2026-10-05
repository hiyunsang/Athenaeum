/* Paperdesk 공통 스크립트: 진행 막대 (기다리는 모든 곳에 같은 모양) */
// 서버 작업 상태(elapsed, done, total, eta, stage, waiting)로 채움 비율(0~1)과 남은 시간 문구를 추정
// defaultTotalSec: 진척 정보가 없을 때 가정하는 전체 소요 시간(초)
function progressInfo(s, defaultTotalSec) {
  s = s || {};
  const el = s.elapsed || 0;
  let frac, eta;
  if (s.waiting) return { frac: 0.02, eta: null, text: "대기 중" };
  if (s.total && s.done > 0) {
    frac = s.done / s.total;
    eta = (s.eta != null) ? s.eta : Math.round(el / s.done * (s.total - s.done));
  } else {
    let T = defaultTotalSec || 90;
    if (s.total && !s.done) T = Math.max(T, Math.ceil(s.total / 4) * 25 + 40);  // 구간 4개 동시 처리 가정
    frac = Math.min(0.85, el / T);
    eta = Math.max(5, Math.round(T - el));
  }
  if (s.phase === "wrap") { frac = 0.93; eta = 25; }
  // 다음 폴링까지 자연스럽게 차오르도록 5초 뒤 예상 진척을 목표치로
  const step = eta > 0 ? Math.min(0.12, 5 / (eta + 5) * (1 - frac)) : 0;
  return { frac: Math.min(0.97, frac + step), eta: eta, text: fmtEta(eta) };
}
function fmtEta(sec) {
  if (sec == null) return "";
  if (sec < 55) return "약 " + Math.max(5, Math.round(sec / 5) * 5) + "초";
  return "약 " + Math.max(1, Math.round(sec / 60)) + "분";
}
// 막대 HTML. prev: 직전 채움 비율(다시 그릴 때 0에서 튀지 않게 그 자리에서 시작)
function pbarHtml(frac, prev, cls) {
  return "<span class='pbar" + (cls ? " " + cls : "") + "' data-to='" + Math.round(frac * 100) + "'><i style='width:" +
    Math.round((prev == null ? frac : prev) * 100) + "%'></i></span>";
}
// 그려진 막대를 목표치까지 미끄러지게 (CSS transition)
function animateBars(root) {
  (root || document).querySelectorAll(".pbar[data-to]").forEach(el => {
    const to = el.getAttribute("data-to"); el.removeAttribute("data-to");
    requestAnimationFrame(() => requestAnimationFrame(() => { const i = el.querySelector("i"); if (i) i.style.width = to + "%"; }));
  });
}

// ---------- 환경설정 (모든 화면 공통, localStorage 에 기억) ----------
// 항목: 테마(시스템/밝게/어둡게) · 읽기 글꼴 배율 · 원고 글꼴 배율 · 읽기 폭 · 줄 간격 · 원고 자동 검토
const UI_DEFAULTS = { ui_theme: "system", ui_scale_read: 1, ui_scale_ms: 1, ui_width: 760, ui_lh: 1.8, ms_autorev: "0", fig_model: "opus", ui_font_ms: "", ui_font_read: "", ui_pdf_zoom: 1,
  ms_sel_effort: "xhigh", ms_sel_whole: "1", ms_sel_lib: "1", ms_chat_view: "side", ms_alt_view: "clean" };   // 원고에서 Claude 에게 물을 때: 모델 · 원고 전체를 같이 · 서재 찾아보기 · 대화를 글 옆에/목록으로
// 글꼴: 기본 목록(이 PC 에 깔린 것만 보임) + 사용자가 추가한 것(글꼴 폴더에 넣은 파일, 직접 적은 설치 글꼴 이름)
const UI_FONTS = [["Malgun Gothic", "맑은 고딕"], ["Batang", "바탕"], ["Gulim", "굴림"], ["Dotum", "돋움"], ["NanumGothic", "나눔고딕"], ["NanumMyeongjo", "나눔명조"],
  ["NanumSquare", "나눔스퀘어"], ["Noto Sans KR", "Noto Sans KR"], ["Noto Serif KR", "Noto Serif KR"], ["HCR Batang", "함초롬바탕"], ["HCR Dotum", "함초롬돋움"], ["KoPubWorldBatang", "KoPub 바탕"], ["KoPubWorldDotum", "KoPub 돋움"],
  ["Times New Roman", "Times New Roman"], ["Georgia", "Georgia"], ["Cambria", "Cambria"], ["Palatino Linotype", "Palatino"], ["Arial", "Arial"], ["Calibri", "Calibri"], ["Segoe UI", "Segoe UI"]];
let uiUserFonts = [];   // 글꼴 폴더의 파일 [{name, file}]
function uiFontInstalled(name) {   // 같은 글을 그 글꼴로 쟀을 때 폭이 기본 글꼴과 다르면 깔려 있는 것
  try {
    const c = uiFontInstalled.c || (uiFontInstalled.c = document.createElement("canvas").getContext("2d"));
    const probe = "mmmmmmmmlliWW 가나다라마바사 0123456789";
    return ["monospace", "serif"].some(base => { c.font = "64px " + base; const w0 = c.measureText(probe).width; c.font = "64px \"" + name + "\", " + base; return Math.abs(c.measureText(probe).width - w0) > 0.5; });
  } catch (e) { return true; }
}
function uiCustomFonts() { try { return JSON.parse(localStorage.getItem("ui_fonts_custom") || "[]"); } catch (e) { return []; } }
function uiFontOptions(cur) {
  const opt = (v, t) => "<option value='" + v.replace(/'/g, "&#39;") + "'" + (v === cur ? " selected" : "") + ">" + t.replace(/</g, "&lt;") + "</option>";
  const mine = uiUserFonts.map(f => f.name).concat(uiCustomFonts());
  let h = opt("", "기본 (Pretendard)") + opt("serif", "명조 (제목용 세리프)");
  if (mine.length) h += "<optgroup label='내가 추가한 글꼴'>" + mine.map(n => opt(n, n)).join("") + "</optgroup>";
  h += "<optgroup label='이 컴퓨터의 글꼴'>" + UI_FONTS.filter(f => uiFontInstalled(f[0])).map(f => opt(f[0], f[1])).join("") + "</optgroup>";
  if (cur && cur !== "serif" && !mine.includes(cur) && !UI_FONTS.some(f => f[0] === cur)) h += opt(cur, cur);
  return h;
}
async function uiLoadUserFonts() {   // 글꼴 폴더의 파일을 웹 글꼴로 등록 (쓸 때만 실제로 읽힌다)
  try {
    const r = await (await fetch("/api/fonts")).json();
    uiUserFonts = r.fonts || [];
    let st = document.getElementById("uiUserFonts");
    if (!st) { st = document.createElement("style"); st.id = "uiUserFonts"; document.head.appendChild(st); }
    st.textContent = uiUserFonts.map(f => "@font-face { font-family: \"" + f.name.replace(/"/g, "") + "\"; src: url(\"/userfont/" + encodeURIComponent(f.file) + "\"); font-display: swap; }").join("\n");
  } catch (e) {}
}
uiLoadUserFonts();
function uiGet(k) {
  try {
    let v = localStorage.getItem(k);
    if (v === null && k === "ui_scale_read") v = localStorage.getItem("ui_scale");   // 옛 키 이어받기
    if (v === null) return UI_DEFAULTS[k];
    return (typeof UI_DEFAULTS[k] === "number") ? (parseFloat(v) || UI_DEFAULTS[k]) : v;
  } catch (e) { return UI_DEFAULTS[k]; }
}
function uiSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} applyUi(); }
function applyUi() {
  const root = document.documentElement;
  const t = uiGet("ui_theme");
  if (t === "dark" || t === "light") root.dataset.theme = t; else delete root.dataset.theme;
  root.style.setProperty("--read-scale", uiGet("ui_scale_read"));
  root.style.setProperty("--ms-scale", uiGet("ui_scale_ms"));
  const w = uiGet("ui_width"); root.style.setProperty("--read-width", w >= 9999 ? "100%" : w + "px");
  root.style.setProperty("--read-lh", uiGet("ui_lh"));
  [["ui_font_ms", "--ms-font"], ["ui_font_read", "--read-font"]].forEach(([k, css]) => {   // 고른 글꼴이 없으면 변수를 지워 화면 기본을 쓴다
    const v = uiGet(k);
    if (v) root.style.setProperty(css, v === "serif" ? "var(--font-serif)" : "\"" + String(v).replace(/"/g, "") + "\", var(--font)"); else root.style.removeProperty(css);
  });
  if (typeof window.onUiChange === "function") { try { window.onUiChange(); } catch (e) {} }   // 화면마다 따로 반영할 것 (읽기 화면의 PDF 크기)
  document.querySelectorAll("#uiSettings [data-k]").forEach(el => {
    const k = el.dataset.k, v = uiGet(k);
    if (el.type === "checkbox") el.checked = String(v) === "1"; else el.value = v;
    const out = document.querySelector("#uiSettings [data-out='" + k + "']");
    if (out) out.textContent = (k.startsWith("ui_scale") || k === "ui_pdf_zoom") ? Math.round(v * 100) + "%" : (k === "ui_width" ? (v >= 9999 ? "전체" : v + "px") : v);
  });
}
applyUi();
// 옛 함수 이름 호환 (다른 화면 코드가 부를 수 있음)
function uiTheme() { const t = document.documentElement.dataset.theme; return t || ((window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light"); }
function setTheme(t) { uiSet("ui_theme", t); }
function toggleTheme() { setTheme(uiTheme() === "dark" ? "light" : "dark"); }
function uiScale() { return uiGet("ui_scale_read"); }
function setScale(s) { uiSet("ui_scale_read", Math.min(1.8, Math.max(0.8, Math.round(s * 20) / 20))); }

// 환경설정 창: 왼쪽에 갈래, 오른쪽에 그 갈래의 항목 묶음 (항목이 늘어도 아래로만 길어지지 않게). 위의 찾기는 모든 갈래에서 찾는다.
// 새 항목은 UI_CATS 의 알맞은 갈래·묶음에 한 줄 넣으면 된다. 값은 data-k(localStorage 키)로 바로 적용·기억된다.
function openSettings(cat) {
  if (document.getElementById("uiSettings")) { closeSettings(); return; }
  const row = (label, inner, hint, wide) => "<div class='urow" + (wide ? " wide" : "") + "'><div class='ulab'>" + label + (hint ? "<div class='uhint'>" + hint + "</div>" : "") + "</div><div class='uctl'>" + inner + "</div></div>";
  const range = (k, min, max, step) => "<input type='range' data-k='" + k + "' min='" + min + "' max='" + max + "' step='" + step + "'><span class='uout' data-out='" + k + "'></span>";
  const sw = k => "<label class='usw'><input type='checkbox' data-k='" + k + "'><i></i></label>";
  const sel = (k, opts, cls) => "<select data-k='" + k + "'" + (cls ? " class='" + cls + "'" : "") + ">" + opts.map(o => "<option value='" + o[0] + "'>" + o[1] + "</option>").join("") + "</select>";
  const key = (id, st) => "<input type='password' id='" + id + "' placeholder='없으면 비워 둠' autocomplete='off'><span class='uout' id='" + st + "'></span>";
  const UI_CATS = [
    { id: "general", name: "일반", groups: [
      { rows: [row("테마", sel("ui_theme", [["system", "시스템 설정 따르기"], ["light", "밝게"], ["dark", "어둡게"]]))] },
      { rows: [row("기본값으로 되돌리기", "<button class='ureset' onclick='resetSettings()'>되돌리기</button>", "화면·읽기·원고·Claude 설정을 처음 값으로. 추가한 글꼴과 연결(키)은 그대로 둡니다"),
               row("판", "<span class='uout' id='uiVer' style='min-width:0'></span>", "지금 돌고 있는 Athenaeum")] }] },
    { id: "read", name: "읽기", groups: [
      { title: "글", rows: [row("글꼴 크기", range("ui_scale_read", 0.8, 1.8, 0.05), "요약·번역 본문. 누워서 볼 때 크게"),
               row("글꼴 모양", "<select data-k='ui_font_read' class='ufont'>" + uiFontOptions(uiGet("ui_font_read")) + "</select>", "요약·번역 본문"),
               row("줄 간격", range("ui_lh", 1.4, 2.4, 0.1)),
               row("읽기 폭", range("ui_width", 560, 9999, 40), "본문 한 줄 길이. 오른쪽 끝 = 전체 폭")] },
      { title: "원문", rows: [row("영문(PDF) 크기", range("ui_pdf_zoom", 0.5, 2.4, 0.05), "읽기 화면 오른쪽의 PDF. PDF 위의 − + · 폭 맞춤과 같은 값")] }] },
    { id: "ms", name: "원고", groups: [
      { title: "글", rows: [row("글꼴 크기", range("ui_scale_ms", 0.8, 1.8, 0.05), "초안·영문·초록·카드"),
               row("글꼴 모양", "<select data-k='ui_font_ms' class='ufont'>" + uiFontOptions(uiGet("ui_font_ms")) + "</select>", "초안·초록 글 칸")] },
      { title: "대화", rows: [row("대화를 놓는 자리", sel("ms_chat_view", [["side", "글 옆에 (워드의 메모처럼)"], ["list", "목록으로"]]), "글 옆에 = 그 글의 높이에 놓이고 글을 따라 움직입니다"),
               row("고친 글을 보이는 방식", sel("ms_alt_view", [["clean", "새 글만 (바뀐 곳은 옅은 바탕)"], ["diff", "지운 말과 넣은 말을 같이"]]), "Claude 가 낸 대안과 다시 쓴 글")] }] },
    { id: "claude", name: "Claude", groups: [
      { title: "원고에서 물을 때", rows: [
        row("모델", sel("ms_sel_effort", [["xhigh", "Opus · 엑스트라"], ["max", "Opus · 최대"], ["fable", "Fable · 엑스트라"], ["high", "Opus · 보통"], ["fast", "Sonnet · 빠름"]]), "대화와 서재에서 뽑기에 같이 쓰입니다. 최대·Fable 은 더 오래 걸리고 사용량이 많습니다"),
        row("원고 전체를 같이 보내기", sw("ms_sel_whole"), "고른 문단만이 아니라 논문 전체를 읽고 답합니다"),
        row("서재 찾아보기", sw("ms_sel_lib"), "내 논문모음을 직접 찾아 읽고 답합니다. 문헌이 필요 없는 물음에는 끄는 것이 가장 큰 절약입니다")] },
      { title: "그 밖에", rows: [
        row("쓰다 멈추면 자동 검토", sw("ms_autorev"), "30초 멈추면 그 문단을 검토합니다. 사용량이 듭니다"),
        row("도식 코드 모델", sel("fig_model", [["opus", "Opus (꼼꼼함, 5~7분)"], ["sonnet", "Sonnet (빠름, 약 2분)"]]), "말로 만드는 도식의 그리기 코드를 쓰는 모델")] }] },
    { id: "fonts", name: "글꼴", groups: [
      { title: "글꼴 추가", rows: [
        row("글꼴 파일 넣기", "<button class='ureset' id='fontFileBtn'>파일 고르기…</button><input type='file' id='fontFile' accept='.ttf,.otf,.woff,.woff2' style='display:none'>", ".ttf · .otf · .woff2"),
        row("설치된 글꼴 이름으로", "<input type='text' id='fontName' placeholder='예: 함초롬바탕'><button class='ureset' id='fontNameBtn'>추가</button>", "이 컴퓨터에 깔린 글꼴의 이름을 그대로 적습니다"),
        "<div class='urow'><div class='uhint' id='fontState'>추가한 글꼴은 읽기·원고의 「글꼴 모양」 목록에 나옵니다</div></div>"] }] },
    { id: "keys", name: "연결", groups: [
      { title: "논문 탐색 · 관련맵", rows: [
        row("OpenAlex API 키", key("oaKey", "oaKeyState"), "키가 없으면 같은 네트워크(학교)가 나눠 쓰는 무료 일일 한도에 걸릴 수 있습니다. 무료 키: <a href='https://help.openalex.org/api/authentication/' target='_blank'>help.openalex.org</a>", true),
        row("Elsevier API 키", key("elsKey", "elsKeyState"), "초록이 없는 논문을 Scopus 로 채웁니다(모든 출판사, 주 1만 회). 무료 키: <a href='https://dev.elsevier.com/' target='_blank'>dev.elsevier.com</a>", true)] }] }];
  const groupHtml = g => (g.title ? "<div class='ugt'>" + g.title + "</div>" : "") + "<div class='ugrp'>" + g.rows.join("") + "</div>";
  let cur = UI_CATS.some(c => c.id === cat) ? cat : (localStorage.getItem("ui_set_cat") || "general");
  if (!UI_CATS.some(c => c.id === cur)) cur = "general";
  const html = "<div id='uiSettings'><div class='ubox'><div class='uside'><input type='search' id='uiFind' placeholder='찾기' autocomplete='off'>" +
    UI_CATS.map(c => "<a href='#' class='ucat' data-cat='" + c.id + "'>" + c.name + "</a>").join("") + "</div>" +
    "<div class='umain'><div class='uhead'><b id='uiCatName'></b><button class='uclose' onclick='closeSettings()' title='닫기 (Esc)'>✕</button></div>" +
    UI_CATS.map(c => "<div class='upane' data-pane='" + c.id + "'>" + c.groups.map(groupHtml).join("") + "</div>").join("") +
    "<div class='uhint' id='uiNone' style='display:none;padding:var(--s4) 0'>맞는 항목이 없습니다</div></div></div></div>";
  document.body.insertAdjacentHTML("beforeend", html);
  const box = document.getElementById("uiSettings"), find = document.getElementById("uiFind");
  const show = () => {   // 갈래 하나를 보이거나, 찾는 말이 있으면 모든 갈래에서 맞는 항목만
    const q = find.value.trim().toLowerCase();
    let any = false;
    box.querySelectorAll(".upane").forEach(p => {
      const mine = p.dataset.pane === cur;
      p.style.display = q || mine ? "" : "none";
      p.querySelectorAll(".urow").forEach(r => { const hit = !q || r.textContent.toLowerCase().includes(q); r.style.display = hit ? "" : "none"; if (hit && (q || mine)) any = true; });
      p.querySelectorAll(".ugrp").forEach(g => { const vis = [...g.children].some(r => r.style.display !== "none"); g.style.display = vis ? "" : "none"; if (g.previousElementSibling && g.previousElementSibling.classList.contains("ugt")) g.previousElementSibling.style.display = vis && !q ? "" : "none"; });
    });
    box.querySelectorAll(".ucat").forEach(a => a.classList.toggle("on", !q && a.dataset.cat === cur));
    document.getElementById("uiCatName").textContent = q ? "「" + find.value.trim() + "」 찾기" : UI_CATS.find(c => c.id === cur).name;
    document.getElementById("uiNone").style.display = any ? "none" : "";
  };
  box.querySelectorAll(".ucat").forEach(a => a.onclick = e => { e.preventDefault(); cur = a.dataset.cat; find.value = ""; try { localStorage.setItem("ui_set_cat", cur); } catch (err) {} show(); });
  find.oninput = show;
  show();
  fetch("/api/version").then(r => r.json()).then(v => { const h = document.getElementById("uiVer"); if (h && v.version) h.textContent = v.version; }).catch(() => {});   // 어느 판이 도는지
  box.addEventListener("mousedown", e => { if (e.target === box) closeSettings(); });
  box.querySelectorAll("[data-k]").forEach(el => {
    el.addEventListener("input", () => uiSet(el.dataset.k, el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value));
    el.addEventListener("change", () => uiSet(el.dataset.k, el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value));
  });
  applyUi();
  // 글꼴 추가: 파일을 글꼴 폴더에 넣거나(서버), 설치된 글꼴 이름을 목록에 더한다
  const refill = pick => box.querySelectorAll("select.ufont").forEach(sel => { sel.innerHTML = uiFontOptions(uiGet(sel.dataset.k)); });
  const fst = document.getElementById("fontState");
  uiLoadUserFonts().then(() => refill());
  document.getElementById("fontFileBtn").onclick = () => document.getElementById("fontFile").click();
  document.getElementById("fontFile").onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    fst.textContent = "넣는 중…";
    const b64 = await new Promise(res => { const rd = new FileReader(); rd.onload = () => res(String(rd.result).split(",")[1]); rd.readAsDataURL(f); });
    try {
      const r = await (await fetch("/api/fonts", { method: "POST", body: JSON.stringify({ name: f.name, b64 }) })).json();
      if (r.error) { fst.textContent = "⚠ " + r.error; return; }
      await uiLoadUserFonts(); refill(); fst.textContent = "「" + r.added + "」 추가됨 — 읽기·원고의 「글꼴 모양」 에서 고르세요";
    } catch (err) { fst.textContent = "⚠ 넣지 못했습니다"; }
    e.target.value = "";
  };
  const addName = () => {
    const inp = document.getElementById("fontName"), name = inp.value.trim().replace(/["']/g, ""); if (!name) return;
    if (!uiFontInstalled(name)) { fst.textContent = "⚠ 「" + name + "」 글꼴을 이 컴퓨터에서 찾지 못했습니다 (이름을 정확히)"; return; }
    const list = uiCustomFonts(); if (!list.includes(name)) list.push(name);
    try { localStorage.setItem("ui_fonts_custom", JSON.stringify(list)); } catch (e) {}
    inp.value = ""; refill(); fst.textContent = "「" + name + "」 추가됨 — 읽기·원고의 「글꼴 모양」 에서 고르세요";
  };
  document.getElementById("fontNameBtn").onclick = addName;
  document.getElementById("fontName").onkeydown = e => { if (e.key === "Enter") addName(); };
  // API 키는 서버 설정 (설정.json) — 열 때 상태를 받고, 바꾸면 저장
  [["oaKey", "oaKeyState", "openalex_api_key"], ["elsKey", "elsKeyState", "elsevier_api_key"]].forEach(([id, stId, name]) => {
    const inp = document.getElementById(id), st = document.getElementById(stId); if (!inp) return;
    const shown = s => { if (s[name + "_set"]) { inp.placeholder = "저장됨 (…" + s[name + "_tail"] + ") — 바꾸려면 입력"; st.textContent = "저장됨"; } else { inp.placeholder = "없으면 비워 둠"; } };
    fetch("/api/settings").then(r => r.json()).then(shown).catch(() => {});
    inp.addEventListener("change", async () => {
      st.textContent = "저장 중…";
      try {
        const body = {}; body[name] = inp.value.trim();
        const s = await (await fetch("/api/settings", { method: "POST", body: JSON.stringify(body) })).json();
        inp.value = ""; shown(s); st.textContent = s[name + "_set"] ? "저장됨" : "지움";
      } catch (e) { st.textContent = "저장 실패"; }
    });
  });
  document.addEventListener("keydown", escClose);
}
function escClose(e) { if (e.key === "Escape") closeSettings(); }
function closeSettings() {
  const b = document.getElementById("uiSettings"); if (b) b.remove(); document.removeEventListener("keydown", escClose);
  if (typeof window.onUiClose === "function") { try { window.onUiClose(); } catch (e) {} }   // 화면이 설정을 다시 읽게 (원고의 모델 고르기 등)
}
function resetSettings() { Object.keys(UI_DEFAULTS).forEach(k => { try { localStorage.removeItem(k); } catch (e) {} }); try { localStorage.removeItem("ui_scale"); } catch (e) {} applyUi(); }
// 헤더에 넣는 버튼: ⚙ 하나 (안에서 전부 조절)
function uiControlsHtml() { return "<span class='uictl'><button onclick='openSettings()' title='환경설정: 화면·읽기·원고·Claude·글꼴·연결'>⚙ 설정</button></span>"; }
function mountUiControls(sel) {
  const host = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (host && !host.querySelector(".uictl")) host.insertAdjacentHTML("beforeend", uiControlsHtml());
}

// ---------- 이 창이 옛 화면인지 알리기 ----------
// 프로그램을 고친 뒤에도 이미 열어 둔 창은 옛 화면(옛 스크립트) 그대로다 → 서버의 화면 파일이 이 창을 연 뒤에 바뀌었으면 알린다. 저절로 새로 고치지는 않는다(쓰던 글·열어 둔 것을 잃지 않게).
(function () {
  let mine = null, told = false;
  function tell() {
    if (told || document.getElementById("uiStale")) return; told = true;
    const d = document.createElement("div"); d.id = "uiStale";
    d.innerHTML = "<span>프로그램이 업데이트되었습니다 — 이 창은 옛 화면입니다.</span><button class='act ready' id='uiStaleGo'>새로 고침</button><button class='act' id='uiStaleNo'>나중에</button>";
    document.body.appendChild(d);
    document.getElementById("uiStaleGo").onclick = () => location.reload();
    document.getElementById("uiStaleNo").onclick = () => d.remove();
  }
  function check() {
    fetch("/api/version").then(r => r.json()).then(v => { if (!v.ui) return; if (mine === null) mine = v.ui; else if (v.ui !== mine) tell(); }).catch(() => {});
  }
  check();
  window.addEventListener("focus", check);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
  setInterval(check, 180000);
})();
