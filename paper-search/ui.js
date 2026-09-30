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
const UI_DEFAULTS = { ui_theme: "system", ui_scale_read: 1, ui_scale_ms: 1, ui_width: 760, ui_lh: 1.8, ms_autorev: "0", fig_model: "opus", ui_font_ms: "", ui_font_read: "", ui_pdf_zoom: 1 };
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

function openSettings() {
  if (document.getElementById("uiSettings")) { closeSettings(); return; }
  const row = (label, inner, hint) => "<div class='urow'><div class='ulab'>" + label + (hint ? "<div class='uhint'>" + hint + "</div>" : "") + "</div><div class='uctl'>" + inner + "</div></div>";
  const range = (k, min, max, step) => "<input type='range' data-k='" + k + "' min='" + min + "' max='" + max + "' step='" + step + "'><span class='uout' data-out='" + k + "'></span>";
  const html =
    "<div id='uiSettings'><div class='ubox'>" +
    "<div class='uhead'><b>환경설정</b><span class='uhint'>바꾸면 바로 적용되고 이 컴퓨터에 기억됩니다</span><button class='uclose' onclick='closeSettings()'>✕</button></div>" +
    row("테마", "<select data-k='ui_theme'><option value='system'>시스템 설정 따르기</option><option value='light'>밝게</option><option value='dark'>어둡게</option></select>") +
    row("읽기 글꼴", range("ui_scale_read", 0.8, 1.8, 0.05), "요약·번역 본문. 누워서 볼 때 크게") +
    row("원고 글꼴", range("ui_scale_ms", 0.8, 1.8, 0.05), "원고의 초안·영문·초록·카드") +
    row("영문(PDF) 크기", range("ui_pdf_zoom", 0.5, 2.4, 0.05), "읽기 화면 오른쪽의 PDF 원문. PDF 위의 − + · 폭 맞춤과 같고, 다음에 열어도 기억합니다") +
    row("읽기 글꼴 모양", "<select data-k='ui_font_read' class='ufont'>" + uiFontOptions(uiGet("ui_font_read")) + "</select>", "요약·번역 본문") +
    row("원고 글꼴 모양", "<select data-k='ui_font_ms' class='ufont'>" + uiFontOptions(uiGet("ui_font_ms")) + "</select>", "원고의 초안·초록 글 칸") +
    row("글꼴 추가", "<div style='display:flex;flex-direction:column;gap:6px;width:100%'><div style='display:flex;gap:6px;align-items:center'><button class='ureset' id='fontFileBtn' style='color:var(--accent)'>글꼴 파일 넣기…</button><input type='file' id='fontFile' accept='.ttf,.otf,.woff,.woff2' style='display:none'><span class='uout' id='fontState' style='text-align:left;min-width:0'></span></div>" +
        "<div style='display:flex;gap:6px'><input type='text' id='fontName' placeholder='또는 설치된 글꼴 이름 (예: 함초롬바탕)' style='flex:1;border:1px solid var(--line);border-radius:var(--r);padding:4px 8px;font:inherit;background:var(--surface);color:var(--text)'><button class='ureset' id='fontNameBtn' style='color:var(--accent)'>추가</button></div></div>",
        "글꼴 파일(.ttf · .otf · .woff2)을 넣거나, 이 컴퓨터에 설치된 글꼴의 이름을 그대로 적습니다. 추가한 글꼴은 위 두 목록에 나옵니다") +
    row("읽기 폭", range("ui_width", 560, 9999, 40), "본문 한 줄 길이. 오른쪽 끝 = 전체 폭") +
    row("줄 간격", range("ui_lh", 1.4, 2.4, 0.1), "요약·번역 본문") +
    row("원고 자동 검토", "<label class='uswitch'><input type='checkbox' data-k='ms_autorev'> 쓰다가 30초 멈추면 Claude가 그 문단을 검토</label>", "토큰이 듭니다. 기본은 꺼짐") +
    row("도식 코드 모델", "<select data-k='fig_model'><option value='opus'>Opus (꼼꼼함, 5~7분)</option><option value='sonnet'>Sonnet (빠름, 약 2분)</option></select>", "말로 만드는 도식의 그리기 코드를 쓰는 모델. 요약·번역·검토는 항상 Opus") +
    row("OpenAlex API 키", "<input type='password' id='oaKey' placeholder='없으면 비워 둠' autocomplete='off' style='width:100%'><span class='uout' id='oaKeyState'></span>",
        "논문 탐색·관련맵이 쓰는 OpenAlex. 키가 없으면 같은 네트워크(학교)가 나눠 쓰는 무료 일일 한도에 걸릴 수 있습니다. 무료 키: <a href='https://help.openalex.org/api/authentication/' target='_blank'>help.openalex.org/api/authentication</a>") +
    row("Elsevier API 키", "<input type='password' id='elsKey' placeholder='없으면 비워 둠' autocomplete='off' style='width:100%'><span class='uout' id='elsKeyState'></span>",
        "논문 탐색에서 초록이 없는 논문(Elsevier 등은 OpenAlex 가 초록을 안 줌)을 Scopus 초록 API 로 채웁니다 — 모든 출판사, 주 1만 회. 검색 결과의 선별 정확도와 정보 패널의 요약·번역에 쓰입니다. 무료 키: <a href='https://dev.elsevier.com/' target='_blank'>dev.elsevier.com</a>") +
    "<div class='ufoot'><button class='ureset' onclick='resetSettings()'>기본값으로</button></div>" +
    "</div></div>";
  document.body.insertAdjacentHTML("beforeend", html);
  const box = document.getElementById("uiSettings");
  fetch("/api/version").then(r => r.json()).then(v => { const h = box.querySelector(".uhint"); if (h && v.version) h.textContent = "Athenaeum " + v.version + " · " + h.textContent; }).catch(() => {});   // 어느 판이 도는지
  box.addEventListener("click", e => { if (e.target === box) closeSettings(); });
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
      await uiLoadUserFonts(); refill(); fst.textContent = "「" + r.added + "」 추가됨 — 위 목록에서 고르세요";
    } catch (err) { fst.textContent = "⚠ 넣지 못했습니다"; }
    e.target.value = "";
  };
  const addName = () => {
    const inp = document.getElementById("fontName"), name = inp.value.trim().replace(/["']/g, ""); if (!name) return;
    if (!uiFontInstalled(name)) { fst.textContent = "⚠ 「" + name + "」 글꼴을 이 컴퓨터에서 찾지 못했습니다 (이름을 정확히)"; return; }
    const list = uiCustomFonts(); if (!list.includes(name)) list.push(name);
    try { localStorage.setItem("ui_fonts_custom", JSON.stringify(list)); } catch (e) {}
    inp.value = ""; refill(); fst.textContent = "「" + name + "」 추가됨 — 위 목록에서 고르세요";
  };
  document.getElementById("fontNameBtn").onclick = addName;
  document.getElementById("fontName").onkeydown = e => { if (e.key === "Enter") addName(); };
  // OpenAlex API 키는 서버 설정 (설정.json) — 열 때 상태를 받고, 바꾸면 저장
  const oa = document.getElementById("oaKey"), oaSt = document.getElementById("oaKeyState");
  if (oa) {
    fetch("/api/settings").then(r => r.json()).then(s => { if (s.openalex_api_key_set) { oa.placeholder = "저장됨 (…" + s.openalex_api_key_tail + ") — 바꾸려면 입력"; oaSt.textContent = "저장됨"; } }).catch(() => {});
    oa.addEventListener("change", async () => {
      oaSt.textContent = "저장 중…";
      try {
        const s = await (await fetch("/api/settings", { method: "POST", body: JSON.stringify({ openalex_api_key: oa.value.trim() }) })).json();
        oaSt.textContent = s.openalex_api_key_set ? "저장됨 (…" + s.openalex_api_key_tail + ")" : "지움";
        oa.value = ""; oa.placeholder = s.openalex_api_key_set ? "저장됨 (…" + s.openalex_api_key_tail + ") — 바꾸려면 입력" : "없으면 비워 둠";
      } catch (e) { oaSt.textContent = "저장 실패"; }
    });
  }
  const els = document.getElementById("elsKey"), elsSt = document.getElementById("elsKeyState");
  if (els) {
    fetch("/api/settings").then(r => r.json()).then(s => { if (s.elsevier_api_key_set) { els.placeholder = "저장됨 (…" + s.elsevier_api_key_tail + ") — 바꾸려면 입력"; elsSt.textContent = "저장됨"; } }).catch(() => {});
    els.addEventListener("change", async () => {
      elsSt.textContent = "저장 중…";
      try {
        const s = await (await fetch("/api/settings", { method: "POST", body: JSON.stringify({ elsevier_api_key: els.value.trim() }) })).json();
        elsSt.textContent = s.elsevier_api_key_set ? "저장됨 (…" + s.elsevier_api_key_tail + ")" : "지움";
        els.value = ""; els.placeholder = s.elsevier_api_key_set ? "저장됨 (…" + s.elsevier_api_key_tail + ") — 바꾸려면 입력" : "없으면 비워 둠";
      } catch (e) { elsSt.textContent = "저장 실패"; }
    });
  }
  document.addEventListener("keydown", escClose);
}
function escClose(e) { if (e.key === "Escape") closeSettings(); }
function closeSettings() { const b = document.getElementById("uiSettings"); if (b) b.remove(); document.removeEventListener("keydown", escClose); }
function resetSettings() { Object.keys(UI_DEFAULTS).forEach(k => { try { localStorage.removeItem(k); } catch (e) {} }); try { localStorage.removeItem("ui_scale"); } catch (e) {} applyUi(); }
// 헤더에 넣는 버튼: ⚙ 하나 (안에서 전부 조절)
function uiControlsHtml() { return "<span class='uictl'><button onclick='openSettings()' title='환경설정: 테마·글꼴 크기·읽기 폭·줄 간격'>⚙ 설정</button></span>"; }
function mountUiControls(sel) {
  const host = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (host && !host.querySelector(".uictl")) host.insertAdjacentHTML("beforeend", uiControlsHtml());
}
