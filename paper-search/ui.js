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
