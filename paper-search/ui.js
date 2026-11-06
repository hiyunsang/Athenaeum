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
  ms_sel_effort: "xhigh", ms_sel_whole: "1", ms_sel_lib: "1", ms_chat_view: "side", ms_alt_view: "clean", ui_update_check: "1" };   // 원고에서 Claude 에게 물을 때: 모델 · 원고 전체를 같이 · 서재 찾아보기 · 대화를 글 옆에/목록으로
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
               row("판", "<span class='uout' id='uiVer' style='min-width:0'></span>", "지금 돌고 있는 Athenaeum"),
               row("새 판 알림", sw("ui_update_check") + "<span class='uout' id='uiUpd' style='min-width:0'></span>", "하루 한 번 GitHub Releases 에 새 판이 나왔는지 봅니다. 보내는 것은 없습니다"),
               row("처음 설정", "<a class='act ready' href='/start'>열기</a>", "Claude Code · 수집 폴더 · 라벨 체계 · 학습 같은 처음 할 일을 한눈에")] }] },
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
    { id: "claude", name: "AI", groups: [
      { title: "엔진", rows: [
        row("쓰는 AI", "<select id='aiEngine'><option value='claude'>Claude Code (Claude 구독)</option><option value='codex'>Codex CLI (ChatGPT 구독)</option></select><span class='uout' id='aiEngineState'></span>",
            "요약·번역·공부·원고가 어느 AI 를 부르는지. 둘 다 그 회사의 공식 명령줄 도구를 통해 내 구독 사용량을 씁니다(API 키 없음). 바꾸면 그다음 호출부터", true),
        row("Codex CLI", "<span class='uout' id='codexState'>확인 중…</span><div><button class='ureset' id='codexInstallBtn'>설치 창 열기</button> <button class='ureset' id='codexLoginBtn'>로그인 창 열기</button> <button class='ureset' id='codexCheck'>다시 확인</button></div>",
            "설치 창은 PowerShell 에 winget install OpenAI.Codex 를, 로그인 창은 codex login 을 띄워 브라우저에서 ChatGPT 계정(Plus 이상)으로 로그인합니다. 한도(5시간·주)에 걸리면 홈에 안내가 뜹니다", true),
        row("GPT 모델 이름", "<input type='text' id='codexModel' placeholder='gpt-6.1-sol' style='width:150px'> <input type='text' id='codexModelFast' placeholder='gpt-6-luna' style='width:130px'><span class='uout' id='codexModelState'></span>",
            "앞은 큰 모델(글쓰기·공부·대화), 뒤는 가벼운 호출(라벨·개념 뽑기). Codex 가 받는 이름이 바뀌면 여기서 고칩니다. 비우면 기본값", true),
        row("Athenaeum 전용 ChatGPT 계정", "<label class='usw'><input type='checkbox' id='codexOwn'><i></i></label><span class='uout' id='codexOwnState'></span>",
            "켜면 Athenaeum 만 다른 ChatGPT 계정으로 돕니다(로그인 폴더를 따로 둠). 켠 뒤 위의 「로그인 창 열기」 에서 그 계정으로 한 번 로그인", true)] },
      { title: "원고에서 물을 때", rows: [
        row("모델", sel("ms_sel_effort", [["xhigh", "Opus · 엑스트라"], ["max", "Opus · 최대"], ["fable", "Fable · 엑스트라"], ["high", "Opus · 보통"], ["fast", "Sonnet · 빠름"]]), "대화와 서재에서 뽑기에 같이 쓰입니다. 최대·Fable 은 더 오래 걸리고 사용량이 많습니다"),
        row("원고 전체를 같이 보내기", sw("ms_sel_whole"), "고른 문단만이 아니라 논문 전체를 읽고 답합니다"),
        row("서재 찾아보기", sw("ms_sel_lib"), "내 논문모음을 직접 찾아 읽고 답합니다. 문헌이 필요 없는 물음에는 끄는 것이 가장 큰 절약입니다")] },
      { title: "그 밖에", rows: [
        row("쓰다 멈추면 자동 검토", sw("ms_autorev"), "30초 멈추면 그 문단을 검토합니다. 사용량이 듭니다"),
        row("도식 코드 모델", sel("fig_model", [["opus", "Opus (꼼꼼함, 5~7분)"], ["sonnet", "Sonnet (빠름, 약 2분)"]]), "말로 만드는 도식의 그리기 코드를 쓰는 모델")] },
      { title: "Claude 채팅이 보는 폴더", rows: [
        row("서재 사본 만들어 두기", "<label class='usw'><input type='checkbox' id='shOn'><i></i></label><span class='uout' id='shState' style='min-width:0;white-space:normal;text-align:left;flex:1'></span>",
            "연구 노트·공부·논문 요약·번역·원고를 Claude 채팅(데스크톱 앱)이 읽기 좋은 md 파일로 한 폴더에 만들어 둡니다. 바뀐 것만 30초마다 맞추고, 토큰은 들지 않고, 원본은 건드리지 않습니다", true),
        row("폴더", "<input type='text' id='shDir' placeholder='비우면 Athenaeum 폴더 안의 Claude용' style='width:250px'><button class='ureset' id='shDirSave'>저장</button> <button class='ureset' id='shOpen' data-local='1'>열기</button>",
            "Claude 의 프로젝트에 이 폴더를 연결합니다. 빈 폴더이거나 Athenaeum 이 만든 폴더여야 합니다", true),
        row("Claude 프로젝트에 붙일 지침", "<button class='ureset' id='shCopy'>지침 복사</button> <button class='ureset' id='shSync'>지금 맞추기</button><span class='uout' id='shInbox' style='min-width:0;white-space:normal;text-align:left;flex:1'></span>",
            "복사한 글을 프로젝트의 지침 칸에 붙이면 Claude 가 폴더의 「읽어보기.md」 부터 읽습니다. 채팅에서 정리한 것을 \"Athenaeum 에 남겨 줘\" 라고 하면 폴더의 「Athenaeum에 남기기」 에 글을 쓰고, 그 글이 연구 노트의 대화에 붙습니다", true)] },
      { title: "계정", rows: [
        row("Athenaeum 전용 Claude 계정", "<label class='usw'><input type='checkbox' id='claudeOwn'><i></i></label><span class='uout' id='claudeOwnState'></span>",
            "켜면 요약·번역·공부·연구 노트가 터미널의 claude 와 다른 계정으로 돕니다(로그인 폴더를 따로 둠). 켠 뒤 아래 「로그인 창 열기」 에서 그 계정으로 한 번 로그인. 두 계정 모두 Pro 나 Max 여야 합니다", true),
        row("로그인", "<button class='ureset' id='claudeLoginBtn'>로그인 창 열기</button> <button class='ureset' id='claudeOwnCheck'>다시 확인</button>",
            "검은 창이 뜨면 거기서 테마를 고르고 로그인(안 물으면 /login). 브라우저가 주 계정으로 열리면 시크릿 창에서. 끝나면 /exit 로 닫습니다", true)] }] },
    { id: "fonts", name: "글꼴", groups: [
      { title: "글꼴 추가", rows: [
        row("글꼴 파일 넣기", "<button class='ureset' id='fontFileBtn'>파일 고르기…</button><input type='file' id='fontFile' accept='.ttf,.otf,.woff,.woff2' style='display:none'>", ".ttf · .otf · .woff2"),
        row("설치된 글꼴 이름으로", "<input type='text' id='fontName' placeholder='예: 함초롬바탕'><button class='ureset' id='fontNameBtn'>추가</button>", "이 컴퓨터에 깔린 글꼴의 이름을 그대로 적습니다"),
        "<div class='urow'><div class='uhint' id='fontState'>추가한 글꼴은 읽기·원고의 「글꼴 모양」 목록에 나옵니다</div></div>"] }] },
    { id: "devices", name: "기기", groups: [
      { title: "다른 기기에서 쓰기", rows: [
        row("다른 기기에서 열기", "<label class='usw'><input type='checkbox' id='rmOn'><i></i></label><span class='uout' id='rmState' style='min-width:0;white-space:normal;text-align:left;flex:1'></span>",
            "집 PC·노트북·휴대폰의 브라우저에서 이 PC 의 Athenaeum 을 엽니다. 이 PC 밖에서 온 요청은 비밀번호 로그인을 거치고, 꺼 두면 밖에서는 열리지 않습니다", true),
        row("비밀번호", "<input type='password' id='rmPw' placeholder='8자 이상' autocomplete='new-password'><button class='ureset' id='rmPwSet'>정하기</button><span class='uout' id='rmPwState' style='min-width:0'></span>",
            "다른 기기에서 로그인할 때 씁니다. 비밀번호 자체는 저장하지 않고 확인용 값만 둡니다. 바꾸면 로그인돼 있던 기기는 모두 풀립니다", true),
        row("연결", "<button class='ureset' id='rmServe'>연결 설정</button><span class='uout' id='rmTs' style='min-width:0;white-space:normal;text-align:left;flex:1'></span>",
            "Tailscale(내 기기끼리만 통하는 사설망)을 이 PC 와 쓸 기기에 깔고 같은 계정으로 로그인한 뒤 「연결 설정」 을 누르면 주소가 나옵니다. 인터넷에 공개하지 않습니다. 휴대폰은 그 주소를 열어 홈 화면에 추가하면 앱처럼 씁니다", true),
        row("로그인된 기기", "<button class='ureset' id='rmOut'>모두 로그아웃</button><span class='uout' id='rmSess' style='min-width:0;white-space:normal;text-align:left;flex:1'></span>", "", true),
        row("서버 지킴이", "<label class='usw'><input type='checkbox' id='kpOn'><i></i></label><span class='uout' id='kpState' style='min-width:0;white-space:normal;text-align:left;flex:1'></span>",
            "Athenaeum 서버가 꺼져 있으면 5분 안에 다시 켭니다(윈도우 작업 스케줄러에 등록). 다른 기기에서 쓸 때는 켜 두세요. 켜 둔 동안은 작업 관리자로 서버를 꺼도 다시 켜지므로, 서버를 끄려면 이 스위치부터 끕니다", true)] },
      { title: "백업", rows: [
        row("백업 폴더", "<input type='text' id='bkDir' placeholder='폴더의 전체 경로' style='width:260px'><button class='ureset' id='bkSave'>저장</button>",
            "논문·번역·메모·원고·공부·연구 노트를 이 폴더에 복사해 둡니다(바뀐 파일만, 지운 파일은 백업에서 지우지 않음). Google Drive 같은 클라우드 폴더를 고르면 PC 가 고장 나도 남고 다른 PC 에서도 파일이 보입니다", true),
        row("매일 자동으로", "<label class='usw'><input type='checkbox' id='bkAuto'><i></i></label>", "새벽에 한 번"),
        row("지금 백업", "<button class='ureset' id='bkNow'>백업</button><span class='uout' id='bkState' style='min-width:0;white-space:normal;text-align:left;flex:1'></span>", "", true)] }] },
    { id: "keys", name: "연결", groups: [
      { title: "논문 탐색 · 관련맵", rows: [
        row("OpenAlex API 키", key("oaKey", "oaKeyState"), "키가 없으면 같은 네트워크(학교)가 나눠 쓰는 무료 일일 한도에 걸릴 수 있습니다. 무료 키: <a href='https://help.openalex.org/api/authentication/' target='_blank'>help.openalex.org</a>", true),
        row("Elsevier API 키", key("elsKey", "elsKeyState"), "초록이 없는 논문을 Scopus 로 채웁니다(모든 출판사, 주 1만 회). 무료 키: <a href='https://dev.elsevier.com/' target='_blank'>dev.elsevier.com</a>", true),
        row("논문 탐색의 우선 저널", "<textarea id='prefJ' rows='4' style='width:100%;font-size:12px;line-height:1.45' placeholder='줄마다 저널 하나 (영어 정식 이름)'></textarea><span class='uout' id='prefJState'></span>",
            "탐색이 이 저널 안에서 한 번 더 찾고, 같은 검색 단계에서는 이 저널을 앞에 둡니다(결과에 ★). 비우면 기본값: IJMTM · JMPT · CIRP Annals · Precision Engineering · JMSE", true)] }] }];
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
  if (uiGet("ui_update_check") !== "0") uiUpdateInfo().then(u => {   // 새 판이 있나 (서버가 하루 캐시)
    const h = document.getElementById("uiUpd"); if (!h || !u) return;
    if (!u.ok) h.textContent = "확인하지 못함"; else if (u.newer) h.innerHTML = "새 판 " + uiEsc(u.latest) + " — <a class='act ready' href='" + uiEsc(u.url) + "' target='_blank'>내려받기 ↗</a>"; else h.textContent = "최신입니다";
  });
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
  // 탐색의 우선 저널 (서버 설정) — 줄마다 하나, 비우면 기본
  const pj = document.getElementById("prefJ"), pjs = document.getElementById("prefJState");
  if (pj) {
    const showJ = s => { pj.value = (s.preferred_journals || []).join("\n"); pjs.textContent = s.preferred_journals_custom ? "저장됨" : "기본값"; };
    fetch("/api/settings").then(r => r.json()).then(showJ).catch(() => {});
    pj.addEventListener("change", async () => {
      pjs.textContent = "저장 중…";
      try { showJ(await (await fetch("/api/settings", { method: "POST", body: JSON.stringify({ preferred_journals: pj.value }) })).json()); }
      catch (e) { pjs.textContent = "저장 실패"; }
    });
  }
  // Athenaeum 전용 Claude 계정 (서버 설정 claude_config_dir = CLAUDE_CONFIG_DIR) — 사용자 2026-10-08: 'Athenaeum 만 따로 다른 계정으로'
  const co = document.getElementById("claudeOwn"), cos = document.getElementById("claudeOwnState");
  if (co) {
    const showC = s => { co.checked = !!s.claude_own_account; cos.textContent = s.claude_own_account ? (s.claude_logged_in ? "켜짐 · 로그인됨 — " + s.claude_config_dir : "켜짐 · 아직 로그인 안 됨 — 아래 「로그인 창 열기」") : "꺼짐 — 이 PC 의 기본 claude 로그인을 씁니다"; };
    const loadC = () => fetch("/api/settings").then(r => r.json()).then(showC).catch(() => {});
    loadC();
    co.addEventListener("change", async () => { cos.textContent = "저장 중…"; try { showC(await (await fetch("/api/settings", { method: "POST", body: JSON.stringify({ claude_own_account: co.checked }) })).json()); } catch (e) { cos.textContent = "저장 실패"; } });
    document.getElementById("claudeLoginBtn").onclick = async () => {
      if (!co.checked) { cos.textContent = "먼저 위의 전용 계정을 켜세요"; return; }
      try { const r = await (await fetch("/api/claude_login", { method: "POST", body: "{}" })).json(); cos.textContent = r.error ? "⚠ " + r.error : "검은 창이 열렸습니다 — 거기서 로그인한 뒤 「다시 확인」"; } catch (e) { cos.textContent = "창을 열지 못했습니다"; }
    };
    document.getElementById("claudeOwnCheck").onclick = loadC;
  }
  // AI 엔진(서버 설정 engine)과 Codex CLI 의 상태 — 2026-11-02 사용자: '지피티 구독만 있는 사람들도 쓸 수 있게'. 값은 서버(설정.json)에 있다
  const ae = document.getElementById("aiEngine");
  if (ae) {
    const aes = document.getElementById("aiEngineState"), cs = document.getElementById("codexState"), cm = document.getElementById("codexModel"), cmf = document.getElementById("codexModelFast"),
          cms = document.getElementById("codexModelState"), cxo = document.getElementById("codexOwn"), cxos = document.getElementById("codexOwnState");
    const showE = s => {
      ae.value = s.engine || "claude";
      aes.textContent = s.engine === "codex" ? (s.codex_found ? (s.codex_logged_in ? "GPT 로 돕니다" : "Codex 는 있지만 로그인이 안 됐습니다") : "Codex CLI 가 없습니다 — 아래에서 설치") : (s.claude_found ? "Claude 로 돕니다" : "Claude Code 가 없습니다");
      cs.textContent = s.codex_found ? ((s.codex_logged_in ? "설치됨 · 로그인됨" : "설치됨 · 로그인 안 됨") + (s.codex_version ? " · " + s.codex_version : "") + " — " + s.codex_path) : "설치되어 있지 않습니다";
      const def = s.codex_defaults || [];
      if (document.activeElement !== cm) cm.value = s.codex_model === def[0] ? "" : (s.codex_model || "");
      if (document.activeElement !== cmf) cmf.value = s.codex_model_fast === def[1] ? "" : (s.codex_model_fast || "");
      cms.textContent = "지금 " + (s.codex_model || "") + " · " + (s.codex_model_fast || "");
      cxo.checked = !!s.codex_own_account;
      cxos.textContent = s.codex_own_account ? ("켜짐 — " + s.codex_home + (s.codex_logged_in ? " · 로그인됨" : " · 아직 로그인 안 됨")) : "꺼짐 — 이 PC 의 기본 codex 로그인을 씁니다";
      window.UI_ENGINE = s.engine || "claude";
    };
    const post = body => fetch("/api/settings", { method: "POST", body: JSON.stringify(body) }).then(r => r.json()).then(showE).catch(() => { aes.textContent = "저장 실패"; });
    fetch("/api/settings").then(r => r.json()).then(showE).catch(() => {});
    ae.addEventListener("change", () => { aes.textContent = "저장 중…"; post({ engine: ae.value }); });
    cm.addEventListener("change", () => post({ codex_model: cm.value.trim() }));
    cmf.addEventListener("change", () => post({ codex_model_fast: cmf.value.trim() }));
    cxo.addEventListener("change", () => { cxos.textContent = "저장 중…"; post({ codex_own_account: cxo.checked }); });
    document.getElementById("codexInstallBtn").onclick = async () => {
      try { const r = await (await fetch("/api/claude_install", { method: "POST", body: JSON.stringify({ engine: "codex" }) })).json(); cs.textContent = r.error ? "⚠ " + r.error : "PowerShell 창이 열렸습니다 — 끝나면 「다시 확인」"; } catch (e) { cs.textContent = "창을 열지 못했습니다"; }
    };
    document.getElementById("codexLoginBtn").onclick = async () => {
      try { const r = await (await fetch("/api/claude_login", { method: "POST", body: JSON.stringify({ engine: "codex", default: !cxo.checked }) })).json(); cs.textContent = r.error ? "⚠ " + r.error : "검은 창이 열렸습니다 — 브라우저에서 로그인한 뒤 「다시 확인」"; } catch (e) { cs.textContent = "창을 열지 못했습니다"; }
    };
    document.getElementById("codexCheck").onclick = () => { cs.textContent = "확인 중…"; post({ codex_recheck: true }); };
  }
  {   /* AI 갈래: Claude 채팅이 보는 폴더 (2026-11-07) — 서재의 md 사본을 만들어 두고, 채팅에서 정리한 글을 연구 노트로 되가져온다 */
    const $s = id => document.getElementById(id), on = $s("shOn");
    if (on) {
      const st = $s("shState"), dir = $s("shDir"), ib = $s("shInbox"); let prompt = "", tm = null;
      const fmtT = t => { if (!t) return ""; const d = new Date(t * 1000); return (d.getMonth() + 1) + "/" + d.getDate() + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
      const showS = v => {
        if (!v || !("dir" in v)) { st.textContent = (v && v.error) ? "⚠ " + v.error : ""; return; }
        on.checked = !!v.on; prompt = v.prompt || "";
        if (document.activeElement !== dir) dir.value = v.custom ? v.dir : "";
        dir.placeholder = v.custom ? "" : v.dir;
        const c = v.counts || {}, r = v.result || {}, errs = ((v.last || {}).errors || []).length;
        st.textContent = r.error ? "⚠ " + r.error : v.err ? "⚠ " + v.err : v.running ? "맞추는 중…"
          : !v.on ? (v.exists ? "꺼짐 — 폴더는 그대로 있습니다" : "꺼짐")
          : !v.synced ? "켜짐 — 처음 만드는 중입니다 (10초쯤)"
          : "켜짐 — 논문 " + (c.papers || 0) + " · 번역 " + (c.translations || 0) + " · 연구 노트 " + (c.notes || 0) + " · 공부 " + (c.studies || 0) + " · 원고 " + (c.ms || 0) + " · 파일 " + (c.files || 0).toLocaleString() + "개 " + Math.round((c.bytes || 0) / 1e6) + " MB · 마지막으로 바뀐 때 " + fmtT(v.synced) + (errs ? " · 못 만든 것 " + errs : "");
        const held = v.held || [];
        ib.textContent = (v.inbox_n ? "가져온 글 " + v.inbox_n + "개" : "") + (held.length ? (v.inbox_n ? " · " : "") + "넣지 못한 글: " + held.map(x => x.name + " — " + x.why).join(" / ") : "");
        clearTimeout(tm); if ((v.running || (v.on && !v.synced)) && $s("uiSettings")) tm = setTimeout(loadS, 2000);
      };
      const loadS = () => fetch("/api/shelf").then(r => r.json()).then(showS).catch(() => {});
      const postS = body => fetch("/api/shelf", { method: "POST", body: JSON.stringify(body) }).then(r => r.json()).then(v => { showS(v); return v; }).catch(() => { st.textContent = "저장하지 못했습니다"; });
      loadS();
      on.addEventListener("change", () => { st.textContent = "설정 중…"; postS({ op: "on", on: on.checked }).then(() => setTimeout(loadS, 2500)); });
      $s("shDirSave").onclick = () => postS({ op: "dir", dir: dir.value.trim() }).then(() => setTimeout(loadS, 2500));
      $s("shOpen").onclick = () => postS({ op: "open" });
      $s("shSync").onclick = () => { st.textContent = "맞추는 중…"; postS({ op: "sync" }); };
      $s("shCopy").onclick = async () => {
        const b = $s("shCopy");
        try { await navigator.clipboard.writeText(prompt); b.textContent = "복사했습니다"; } catch (e) { window.prompt("이 글을 복사해 Claude 프로젝트의 지침 칸에 붙입니다", prompt); }
        setTimeout(() => { b.textContent = "지침 복사"; }, 1800);
      };
    }
  }
  {   /* 기기 갈래: 다른 기기에서 열기(로그인) · Tailscale 연결 · 백업 (2026-11-06) */
    const $i = id => document.getElementById(id), on = $i("rmOn");
    if (on) {
      const st = $i("rmState"), pw = $i("rmPw"), pws = $i("rmPwState"), ts = $i("rmTs"), ss = $i("rmSess");
      const fmtT = t => { if (!t) return ""; const d = new Date(t * 1000); return (d.getMonth() + 1) + "/" + d.getDate() + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
      const showR = v => {
        if (v.remote) {   /* 다른 기기에서 연 설정 창 — 바꾸는 것은 Athenaeum 이 깔린 PC 에서만 */
          ["rmOn", "rmPw", "rmPwSet", "rmServe", "rmOut", "kpOn", "bkDir", "bkSave", "bkAuto", "bkNow"].forEach(id => { const e = $i(id); if (e) e.disabled = true; });
          on.checked = true; st.innerHTML = "이 설정은 Athenaeum 이 깔린 PC 에서만 바꿉니다 · <a href='#' id='rmMeOut'>이 기기 로그아웃</a>";
          $i("rmMeOut").onclick = async e => { e.preventDefault(); try { await fetch("/api/logout", { method: "POST", body: "{}" }); } catch (e2) {} location.href = "/login"; };
          return;
        }
        on.checked = !!v.on;
        st.textContent = v.on ? "켜짐" : (v.has_password ? "꺼짐" : "꺼짐 — 먼저 비밀번호를 정합니다");
        pws.textContent = v.has_password ? "정해져 있음" : "아직 없음";
        const t = v.tailscale || {};
        ts.innerHTML = !t.found ? "Tailscale 이 이 PC 에 없습니다 — <a href='https://tailscale.com/download' target='_blank' rel='noopener'>tailscale.com/download</a> 에서 받아 설치하고 로그인합니다"
          : !t.running ? "Tailscale 이 로그인되어 있지 않습니다 (작업 표시줄의 아이콘에서 로그인)"
          : t.serving ? "연결됨 — 다른 기기에서 <b>" + (t.urls || []).map(uiEsc).join("</b> 또는 <b>") + "</b>"
          : "Tailscale 켜짐 (" + uiEsc(t.name || "") + ") — 「연결 설정」 을 누르면 주소가 열립니다";
        const list = v.sessions || [];
        ss.textContent = list.length ? list.length + "대 — " + list.slice(0, 4).map(x => x.dev + " " + fmtT(x.t)).join(", ") : "없음";
        const kp = v.keeper || {}, kpOn = $i("kpOn"), kpSt = $i("kpState");
        if (kpOn) kpOn.checked = !!kp.on;
        if (kpSt) kpSt.textContent = !kp.on ? "꺼짐" : !kp.mine ? "켜짐 — 다른 폴더의 Athenaeum 을 지키고 있습니다 (껐다 켜면 이 폴더로 바뀝니다)" : "켜짐 — 5분마다 확인" + (kp.last ? " · 마지막 기록: " + kp.last : "");
        if (v.result && v.result.error) st.textContent = "⚠ " + v.result.error;
      };
      const postR = body => fetch("/api/remote", { method: "POST", body: JSON.stringify(body) }).then(r => r.json()).then(v => { showR(v); return v; }).catch(() => { st.textContent = "저장하지 못했습니다"; });
      fetch("/api/remote?check=1").then(r => r.json()).then(showR).catch(() => {});
      on.addEventListener("change", () => postR({ op: "on", on: on.checked }));
      const kpOnEl = $i("kpOn");
      if (kpOnEl) kpOnEl.addEventListener("change", async () => { $i("kpState").textContent = "설정 중…"; const v = await postR({ op: "keeper", on: kpOnEl.checked }); if (v && v.result && v.result.error) $i("kpState").textContent = "⚠ " + v.result.error; });
      $i("rmPwSet").onclick = async () => {
        if ((pw.value || "").length < 8) { pws.textContent = "8자 이상으로 적어 주세요"; pw.focus(); return; }
        const v = await postR({ op: "password", password: pw.value });
        if (v && v.result && v.result.ok) { pw.value = ""; pws.textContent = "정했습니다"; } else if (v && v.result) pws.textContent = "⚠ " + (v.result.error || "");
      };
      $i("rmServe").onclick = async () => { ts.textContent = "설정 중… (길면 40초)"; const v = await postR({ op: "serve", on: true }); if (v && v.result && v.result.error) ts.innerHTML = "⚠ " + uiEsc(v.result.error).replace(/https?:\/\/[^\s<]+/g, u => "<a href='" + u + "' target='_blank' rel='noopener'>" + u + "</a>"); };
      $i("rmOut").onclick = () => postR({ op: "logout_all" });
      const bd = $i("bkDir"), ba = $i("bkAuto"), bs = $i("bkState"); let bt = null;
      const mb = n => n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : Math.round(n / 1e6) + " MB";
      const showB = v => {
        if (!v || !("dir" in v)) return;
        if (document.activeElement !== bd) bd.value = v.dir || "";
        ba.checked = !!v.auto;
        const l = v.last;
        bs.textContent = v.running ? "백업 중 " + v.done + " / " + v.total + " · 복사 " + v.copied + "개 (" + mb(v.bytes) + ")"
          : (v.result && v.result.error) ? "⚠ " + v.result.error
          : l ? "마지막 " + fmtT(l.t) + " — 파일 " + l.files + "개 가운데 " + l.copied + "개 복사 (" + mb(l.bytes) + ")" + (l.errors ? " · 실패 " + l.errors : "") + (l.stopped ? " · 중단됨" : "")
          : (v.dir ? "아직 백업한 적이 없습니다" : "폴더를 정하면 백업할 수 있습니다");
        clearTimeout(bt); if (v.running && $i("uiSettings")) bt = setTimeout(loadB, 2000);
      };
      const loadB = () => fetch("/api/backup").then(r => r.json()).then(showB).catch(() => {});
      const postB = body => fetch("/api/backup", { method: "POST", body: JSON.stringify(body) }).then(r => r.json()).then(showB).catch(() => { bs.textContent = "저장하지 못했습니다"; });
      loadB();
      $i("bkSave").onclick = () => postB({ op: "set", dir: bd.value.trim() });
      ba.addEventListener("change", () => postB({ op: "set", auto: ba.checked }));
      $i("bkNow").onclick = () => { bs.textContent = "시작하는 중…"; postB({ op: "run" }); };
    }
  }
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

// ---------- 새 판 알림 ----------
// 홈을 열 때 GitHub Releases 의 최신 판을 묻는다(서버가 하루 캐시 — /api/update_check). 새 판이 있으면 왼쪽 아래에 한 줄, 「나중에」 는 그 판에 대해 다시 보이지 않는다. ⚙ 일반의 「새 판 알림」 으로 끈다.
function uiEsc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function uiUpdateInfo() { return fetch("/api/update_check").then(r => r.json()).catch(() => null); }
(function () {
  if (location.pathname !== "/" && location.pathname !== "/index.html") return;
  if (uiGet("ui_update_check") === "0") return;
  let seen = ""; try { seen = localStorage.getItem("ui_upd_seen") || ""; } catch (e) {}
  uiUpdateInfo().then(u => {
    if (!u || !u.ok || !u.newer || seen === u.latest || document.getElementById("uiUpdate")) return;
    const add = () => {
      const d = document.createElement("div"); d.id = "uiUpdate";
      d.innerHTML = "<span>새 판 " + uiEsc(u.latest) + " 이 나왔습니다 (지금 " + uiEsc(u.current) + ").</span><a class='act ready' href='" + uiEsc(u.url) + "' target='_blank'>내려받기 ↗</a><button class='act' id='uiUpdNo'>나중에</button>";
      document.body.appendChild(d);
      document.getElementById("uiUpdNo").onclick = () => { try { localStorage.setItem("ui_upd_seen", u.latest); } catch (e) {} d.remove(); };
    };
    if (document.body) add(); else document.addEventListener("DOMContentLoaded", add);
  });
})();

// 엔진이 Codex(GPT) 면 화면의 모델 이름표를 바꿔 보인다 — 화면들은 'Opus · 엑스트라' 같은 Claude 이름으로 고르기를 그리고, 서버가 그 노력 단계를 GPT 모델·추론 노력으로 옮긴다(2026-11-02).
// 고르기의 값(xhigh·max·high·fast)은 그대로라 저장된 설정도 그대로 쓰인다. Fable 은 GPT 에 짝이 없어 숨긴다.
(function () {
  const MAP = [["Opus · 최대", "GPT · 최대"], ["Opus · 엑스트라", "GPT · 높음"], ["Opus · 보통", "GPT · 보통"], ["Sonnet · 빠름", "GPT 가벼운 모델 · 빠름"],
               ["Opus (꼼꼼함, 5~7분)", "GPT (꼼꼼함)"], ["Sonnet (빠름, 약 2분)", "GPT 가벼운 모델 (빠름)"], ["(대조는 Opus)", "(대조는 큰 모델)"]];
  function fix(root) {
    const opts = root.querySelectorAll ? root.querySelectorAll("option") : [];
    opts.forEach(o => {
      let t = o.textContent;
      if (/Fable/.test(t)) { o.hidden = true; o.disabled = true; return; }
      for (const [a, b] of MAP) if (t.includes(a)) t = t.replace(a, b);
      if (t !== o.textContent) o.textContent = t;
    });
  }
  function start() {
    fix(document);
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => { if (n.nodeType === 1) fix(n); }))).observe(document.documentElement, { childList: true, subtree: true });
  }
  const go = () => fetch("/api/version").then(r => r.json()).then(v => { window.UI_ENGINE = v.engine || "claude"; if (v.remote) document.documentElement.dataset.remote = "1"; if (v.engine === "codex") start(); }).catch(() => {});
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go); else go();
})();
