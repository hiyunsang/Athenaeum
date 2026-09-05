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
const UI_DEFAULTS = { ui_theme: "system", ui_scale_read: 1, ui_scale_ms: 1, ui_width: 760, ui_lh: 1.8, ms_autorev: "0" };
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
  document.querySelectorAll("#uiSettings [data-k]").forEach(el => {
    const k = el.dataset.k, v = uiGet(k);
    if (el.type === "checkbox") el.checked = String(v) === "1"; else el.value = v;
    const out = document.querySelector("#uiSettings [data-out='" + k + "']");
    if (out) out.textContent = k.startsWith("ui_scale") ? Math.round(v * 100) + "%" : (k === "ui_width" ? (v >= 9999 ? "전체" : v + "px") : v);
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
    row("읽기 폭", range("ui_width", 560, 9999, 40), "본문 한 줄 길이. 오른쪽 끝 = 전체 폭") +
    row("줄 간격", range("ui_lh", 1.4, 2.4, 0.1), "요약·번역 본문") +
    row("원고 자동 검토", "<label class='uswitch'><input type='checkbox' data-k='ms_autorev'> 쓰다가 30초 멈추면 Claude가 그 문단을 검토</label>", "토큰이 듭니다. 기본은 꺼짐") +
    "<div class='ufoot'><button class='ureset' onclick='resetSettings()'>기본값으로</button></div>" +
    "</div></div>";
  document.body.insertAdjacentHTML("beforeend", html);
  const box = document.getElementById("uiSettings");
  box.addEventListener("click", e => { if (e.target === box) closeSettings(); });
  box.querySelectorAll("[data-k]").forEach(el => {
    el.addEventListener("input", () => uiSet(el.dataset.k, el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value));
    el.addEventListener("change", () => uiSet(el.dataset.k, el.type === "checkbox" ? (el.checked ? "1" : "0") : el.value));
  });
  applyUi();
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
