# -*- coding: utf-8 -*-
"""연구 노트 — 연구 주제를 놓고 Claude 와 토의한다. Claude 는 서재 전체(논문마다 한 줄)·공부 장·지난 논문 탐색·서재의 인용 이웃·원고를 알고 답한다.
사용자(2026-10-08): '이런이런 연구주제는 어때? 하고 같이 토의도 할 수 있으면 좋겠는데 이런 기능은 어디에 넣지' → 홈의 「연구 노트」(따로 큰 창) → '일단 만들어보자 쓰면서 수정하고'.

주제 하나 = 연구노트\\<id>.json = {id, title, status(idea|review|adopt|hold), t, updated, seed{text, from}, thread[{role user|claude, text, t, model, tok, sec, lib, turns}],
  pending, error, opts, card{question, hypothesis, design, expected, risks, papers[], groups[], journals[], next[]}, card_t, card_model, card_pending, card_error}
배경(시스템 프롬프트 — 글자 하나까지 같아야 캐시가 맞는다, 1시간): _background() — 서재·공부·탐색 기록·인용 이웃·원고. 물음마다 달라지는 것(이 주제의 대화·카드·다른 주제 목록)은 물음에.
"""
import io
import json
import os
import re
import threading
import time

import manuscript as ms   # 본보기(서론 문단 구조) — 서재 논문의 절 나누기·문단 역할 (study.py 도 같은 식으로 쓴다)

cfg = {}
_LOCK = threading.RLock()
_BG = {}            # 배경 조각의 캐시: gaps(인용 이웃, 12시간) · 공부 장 요지(파일 mtime) · 탐색 기록의 연구 그룹(id)
_STUDY_CACHE = {}
_HIST_LABS = {}

VERSION = "연구 노트 (2026-10-25)"
STATUS = ["idea", "review", "adopt", "hold"]
STATUS_KO = {"idea": "아이디어", "review": "검토 중", "adopt": "채택", "hold": "보류"}
_EFFORT = {"xhigh": ("opus", "xhigh"), "max": ("opus", "max"), "high": ("opus", None), "fast": ("sonnet", None)}
_LIB_BUDGET = 62000      # 서재 블록(글자) — 논문 287편이면 머리줄 ~35,000자 + 요약 한 줄 100자쯤 (재 봄 2026-10-08: 60,000 에 제목 100자로 두니 요약이 59자로 잘려 쓸모가 없었다)
CARD_KEYS = ["question", "hypothesis", "design", "expected", "risks", "papers", "groups", "journals", "next"]
CARD_KO = {"question": "연구 물음", "hypothesis": "가설", "design": "실험·해석 설계", "expected": "예상 결과와 그 뜻", "risks": "위험·약점·반론",
           "papers": "관련 논문", "groups": "비슷한 일을 하는 연구 그룹", "journals": "맞는 저널", "next": "다음에 할 일"}
_LIST_KEYS = ("papers", "groups", "journals", "next")
PLAN_KEYS = [("background", "배경과 공백"), ("question", "연구 물음과 가설"), ("material", "재료·시편"), ("setup", "장비·측정"), ("design", "실험 설계 — 변수와 수준"),
             ("analysis", "해석·모델"), ("expected", "예상 결과와 판정 기준"), ("risks", "위험과 대안"), ("schedule", "일정"), ("venue", "저널과 독자")]   # 실험 설계서의 열 칸 (2026-10-19)
PLAN_KO = dict(PLAN_KEYS)
REVIEWERS = [("exp", "실험 전문가"), ("theory", "이론·모델 전문가"), ("editor", "저널 심사위원")]
REVIEWER_KO = dict(REVIEWERS)
_JOBKINDS = ("plan", "review", "draft", "intro", "matrix", "argmap", "gapcheck", "framing")   # matrix 식별력 표 · argmap 약속-답 지도 · gapcheck 공백 검증 · framing 연구의 틀 (2026-10-26)
INTRO_ROLES = list(ms.SECTION_KINDS["intro"]["roles"])   # 서론 문단의 역할 — 원고의 본보기·구성 보기와 같은 이름표(한 곳에만 둔다, 2026-10-25)
LAB_FILE = "_연구실.json"      # 우리 연구실 — 장비·역량·홈페이지 글 (2026-10-24)


def init(**kw):
    cfg.update(kw)
    cfg["DIR"] = os.path.join(os.path.dirname(cfg["ARCHIVE"]), "연구노트")
    os.makedirs(cfg["DIR"], exist_ok=True)


def _why():
    try:
        e = cfg["claude_error"]() if cfg.get("claude_error") else ""
        return (" — " + e) if e else ""
    except Exception:
        return ""


# ---------- 저장 ----------
def _path(nid):
    return os.path.join(cfg["DIR"], os.path.basename(str(nid)) + ".json")


def _load(nid):
    d = cfg["load_json"](_path(nid), None)
    return d if isinstance(d, dict) and d.get("id") else None


def _save(nt):
    with _LOCK:
        nt["updated"] = time.time()
        cfg["save_json"](_path(nt["id"]), nt)


def _brief(nt):
    th = nt.get("thread") or []
    last = next((m for m in reversed(th) if m.get("text")), None)
    return {"id": nt["id"], "title": nt.get("title") or "", "status": nt.get("status") or "idea", "t": nt.get("t", 0), "updated": nt.get("updated", 0),
            "asks": sum(1 for m in th if m.get("role") == "user"), "answers": sum(1 for m in th if m.get("role") == "claude"),
            "pending": bool(nt.get("pending")), "card": bool(nt.get("card")), "last": (last["text"][:90] if last else ""),
            "plan": bool(nt.get("plan")), "open": sum(1 for o in nt.get("objections") or [] if o.get("status") == "open"), "ms": bool(nt.get("ms")), "intro": bool(nt.get("intro")),
            "intro_drafts": sum(1 for q in (nt.get("intro") or {}).get("paras") or [] if (q.get("draft") or {}).get("text")),
            "matrix": bool(nt.get("matrix")), "unsplit": len((nt.get("matrix") or {}).get("unsplit") or []), "argmap": bool(nt.get("argmap")),
            "unanswered": sum(1 for x in (nt.get("argmap") or {}).get("issues") or [] if x.get("kind") == "unanswered"),
            "gapcheck": bool(nt.get("gapcheck")), "gap_done": sum(1 for r in (nt.get("gapcheck") or {}).get("claims") or [] if r.get("verdict") in ("done", "partial")),
            "framing": bool(nt.get("framing")), "framing_pick": (nt.get("framing") or {}).get("pick") or "",
            "busy": any(nt.get(k + "_pending") for k in _JOBKINDS) or bool((nt.get("intro") or {}).get("writing")),
            "from": ((nt.get("seed") or {}).get("from") or {}).get("kind", "")}


def list_notes():
    out = []
    try:
        files = [f for f in os.listdir(cfg["DIR"]) if f.endswith(".json") and not f.startswith("_")]
    except OSError:
        files = []
    for f in files:
        nt = cfg["load_json"](os.path.join(cfg["DIR"], f), None)
        if isinstance(nt, dict) and nt.get("id"):
            out.append(_brief(nt))
    out.sort(key=lambda x: -(x["updated"] or x["t"]))
    return out


def new_note(body):
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:120]
    seed_text = str(body.get("seed") or "").strip()[:4000]
    if not title and seed_text:
        title = seed_text.split("\n")[0][:80]
    if not title:
        return {"error": "주제를 적어 주세요"}
    nid = "nt_" + time.strftime("%Y%m%d_%H%M%S")
    k = 0
    while os.path.exists(_path(nid)):
        k += 1
        nid = "nt_" + time.strftime("%Y%m%d_%H%M%S") + "_%d" % k
    fr = body.get("from") if isinstance(body.get("from"), dict) else {}
    nt = {"id": nid, "title": title, "status": "idea", "t": time.time(), "thread": [], "card": None,
          "seed": {"text": seed_text, "from": {"kind": str(fr.get("kind") or "")[:20], "id": str(fr.get("id") or "")[:80], "title": str(fr.get("title") or "")[:160]}} if (seed_text or fr) else None}
    _save(nt)
    return {"note": nt}


def save_fields(nid, body):
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return {"error": "찾지 못했습니다"}
        if "title" in body:
            t = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:120]
            if t:
                nt["title"] = t
        if body.get("status") in STATUS:
            nt["status"] = body["status"]
        if isinstance(body.get("card"), dict):
            nt["card"] = _clean_card(body["card"], nt.get("card") or {})
            nt["card_edited"] = time.time()
        if isinstance(body.get("intro"), dict) and nt.get("intro"):   # 서론 구조의 요지를 손으로 고침 {"0": "요지", ...}
            for k, v in body["intro"].items():
                try:
                    i = int(k)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(nt["intro"].get("paras") or []):
                    nt["intro"]["paras"][i]["gist"] = str(v or "").strip()[:300]
            nt["intro_edited"] = time.time()
        if isinstance(body.get("plan"), dict) and nt.get("plan"):   # 설계서의 칸을 손으로 고침 (인용 검증은 그대로)
            for sec in nt["plan"].get("sections") or []:
                if sec.get("k") in body["plan"]:
                    sec["t"] = str(body["plan"][sec["k"]] or "").strip()[:2500]
            nt["plan_edited"] = time.time()
        _save(nt)
        return {"note": nt}


def _clean_card(d, base=None):
    out = dict(base or {})
    for k in CARD_KEYS:
        if k not in d:
            continue
        v = d.get(k)
        if k in _LIST_KEYS:
            if isinstance(v, str):
                v = [x.strip(" -·•") for x in v.split("\n")]
            out[k] = [str(x).strip()[:300] for x in (v or []) if str(x).strip()][:12]
        else:
            out[k] = str(v or "").strip()[:2000]
    return out


# ---------- 배경: Claude 가 아는 것 (시스템 프롬프트 → 캐시) ----------
def _lib_rows():
    """서재의 논문 전부 → [{year, file, head, line}] 최신순. head = '연도 ★저널 첫 저자 — 제목 [라벨]', line = 요약의 한 줄"""
    try:
        files = sorted(f for f in os.listdir(cfg["ARCHIVE"]) if f.lower().endswith(".pdf"))
    except OSError:
        files = []
    tags = cfg["load_json"](cfg["TAGS_PATH"], {}) if cfg.get("TAGS_PATH") else {}
    if not isinstance(tags, dict):
        tags = {}
    try:
        tops = set(str(x).upper() for x in (cfg["pref_abbrs"]() if cfg.get("pref_abbrs") else ()))
    except Exception:
        tops = set()
    rows = []
    for f in files:
        m = re.match(r"^(\d{4})_([A-Za-z0-9&-]+)_([^_]+)_(.*)\.pdf$", f)
        year, jr, au, title = (m.group(1), m.group(2), m.group(3), m.group(4)) if m else ("", "", "", f[:-4])
        t = tags.get(f) if isinstance(tags.get(f), dict) else {}
        title = re.sub(r"\s+", " ", str(t.get("title") or title)).strip()
        labels = [str(x) for x in (t.get("labels") or [])[:2] if str(x).strip()]
        try:
            line = cfg["paper_line"](f) if cfg.get("paper_line") else ""
        except Exception:
            line = ""
        rows.append({"year": year, "file": f, "line": re.sub(r"\s+", " ", line or "").strip(),
                     "head": "%s %s%s %s — %s%s" % (year or "????", "★" if jr.upper() in tops else "", jr or "?", au or "?", title[:84], (" [" + ", ".join(labels) + "]") if labels else "")})
    rows.sort(key=lambda r: (-(int(r["year"]) if r["year"].isdigit() else 0), r["file"]))
    return rows


def _lab_path():
    return os.path.join(cfg["DIR"], LAB_FILE)


def lab_get():
    d = cfg["load_json"](_lab_path(), None)
    return d if isinstance(d, dict) else {"text": "", "url": "", "page": "", "page_t": 0, "updated": 0}


def lab_save(body):
    """장비·역량 글과 홈페이지 주소 — 주소가 바뀌면 읽어 둔 글은 버린다(「홈페이지 읽기」 로 다시)"""
    with _LOCK:
        d = lab_get()
        if "text" in body:
            d["text"] = str(body.get("text") or "")[:6000]
        if "url" in body:
            u = str(body.get("url") or "").strip()[:300]
            if u != (d.get("url") or ""):
                d["url"] = u
                d["page"] = ""
                d["page_t"] = 0
                d.pop("page_err", None)
        d["updated"] = time.time()
        cfg["save_json"](_lab_path(), d)
        return {"lab": d}


class _HtmlText(object):
    """홈페이지의 글만 — script·style 은 버리고 블록 요소 경계에서 줄을 바꾼다 (표준 라이브러리만)"""
    _SKIP = ("script", "style", "noscript", "svg", "head")
    _BLOCK = ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "th", "dd", "dt", "ul", "ol", "table")

    def __init__(self):
        from html.parser import HTMLParser
        self.out, self.skip = [], 0
        parent = self

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag in parent._SKIP:
                    parent.skip += 1
                elif tag in parent._BLOCK:
                    parent.out.append("\n")

            def handle_endtag(self, tag):
                if tag in parent._SKIP and parent.skip:
                    parent.skip -= 1
                elif tag in parent._BLOCK:
                    parent.out.append("\n")

            def handle_data(self, data):
                if not parent.skip:
                    parent.out.append(data)
        self.p = P(convert_charrefs=True)

    def feed(self, html):
        self.p.feed(html)
        t = "".join(self.out)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\s*\n\s*", "\n", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def lab_fetch():
    """홈페이지를 읽어 글만 남겨 둔다(12,000자까지). 스크립트로 그리는 페이지는 글이 거의 없다 — 그때는 소개·장비 쪽 글을 손으로 붙이라고 알린다"""
    d = lab_get()
    u = str(d.get("url") or "").strip()
    if not re.match(r"^https?://", u):
        return {"error": "홈페이지 주소를 먼저 적어 주세요 (http:// 나 https:// 로 시작)"}
    try:
        import urllib.request
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Athenaeum research notes)"})
        resp = urllib.request.urlopen(req, timeout=20)
        raw = resp.read(1500000)
        enc = None
        ct = resp.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w\-]+)", ct) or re.search(rb"charset=[\"']?([\w\-]+)", raw[:4000]) and re.search(r"charset=[\"']?([\w\-]+)", raw[:4000].decode("ascii", "ignore"))
        if m:
            enc = m.group(1)
        html = None
        for e in ([enc] if enc else []) + ["utf-8", "cp949", "euc-kr"]:
            try:
                html = raw.decode(e)
                break
            except Exception:
                continue
        if html is None:
            html = raw.decode("utf-8", "replace")
        text = _HtmlText().feed(html)
        d["page"] = text[:12000]
        d["page_t"] = time.time()
        if len(text) < 200:
            d["page_err"] = "홈페이지에서 글을 %d자밖에 읽지 못했습니다 — 스크립트로 그리는 페이지인 듯합니다. 장비·연구 소개 쪽의 글을 위 칸에 직접 붙여 넣어 주세요" % len(text)
        else:
            d.pop("page_err", None)
    except Exception as e:
        d["page_err"] = "홈페이지를 읽지 못했습니다: " + str(e)[:160]
    with _LOCK:
        d["updated"] = time.time()
        cfg["save_json"](_lab_path(), d)
    return {"lab": d, "error": d.get("page_err")} if d.get("page_err") else {"lab": d}


def _lab_block():
    """배경의 첫 블록 — 연구자가 적어 둔 장비·역량과 홈페이지 글. 비어 있으면 넣지 않는다(그때는 규칙의 '[우리 연구실] 이 있으면' 이 비켜 간다)"""
    d = lab_get()
    text, page = (d.get("text") or "").strip(), (d.get("page") or "").strip()
    if not text and not page:
        return "", {"lab": 0}
    out = ["# 우리 연구실 — 연구자가 적어 둔 장비·측정 역량·할 수 없는 것. 실험·측정이 이 연구실에서 되는지 가를 때의 기준이다: 여기 없는 장비·측정은 외부(공동 장비 센터·협력 연구실·외주)에 맡겨야 하는 것으로 보고, 연구자가 '없다' 고 적은 것은 없는 것이다."]
    if text:
        out.append(text)
    if page:
        out.append("[연구실 홈페이지에서 읽은 글%s]\n%s" % ((" — " + d["url"]) if d.get("url") else "", page[:5000]))
    s = "\n".join(out)
    return s, {"lab": len(s)}


def _lib_block(budget=_LIB_BUDGET):
    rows = _lib_rows()
    n_sum = sum(1 for r in rows if r["line"])
    room = max(0, budget - sum(len(r["head"]) + 6 for r in rows))
    cut = min(170, room // n_sum) if n_sum else 0
    out = ["# 서재 — 연구자가 모은 논문 %d편(최신순; 요약이 있는 것 %d편). 줄마다: 연도 저널(★ = 우선 저널) 첫 저자 — 제목 [라벨] :: 그 논문이 무엇을 했나(요약의 한 줄). 파일 이름은 '연도_저널_저자_제목.pdf' 꼴이다." % (len(rows), n_sum)]
    for r in rows:
        s = r["line"][:cut] if cut >= 40 else ""
        if s and len(r["line"]) > cut:
            s = s.rstrip() + "…"
        out.append("- " + r["head"] + ((" :: " + s) if s else ""))
    return "\n".join(out), {"papers": len(rows), "summaries": n_sum, "cut": cut}


def _study_digest(it):
    """공부 장 하나의 요지(파일이 바뀔 때만 다시 읽는다 — 다 쓴 장은 근거 문장까지 들어 있어 크다)"""
    fp = os.path.join(os.path.dirname(cfg["ARCHIVE"]), "공부", os.path.basename(str(it.get("id") or "")) + ".json")
    try:
        mt = os.path.getmtime(fp)
    except OSError:
        return ""
    c = _STUDY_CACHE.get(fp)
    if c and c[0] == mt:
        return c[1]
    st = cfg["study_load"](it["id"]) if cfg.get("study_load") else None
    text = ""
    if isinstance(st, dict) and st.get("chapter"):
        ch, plan, P = st["chapter"], st.get("plan") or {}, st.get("papers") or []
        cited = sorted((p for p in P if p.get("n")), key=lambda p: p["n"])
        lines = ["## 「%s」 (%s, 논문 %d편: %s)" % (ch.get("title") or st.get("topic") or "", time.strftime("%Y-%m-%d", time.localtime(st.get("t") or 0)), len(cited),
                                                    ", ".join(("★" if p.get("top") else "") + str(p.get("short") or "") for p in cited)[:600])]
        if ch.get("scope"):
            lines.append("범위: " + str(ch["scope"])[:300])
        if plan.get("ask"):
            lines.append("이 장이 답하는 물음: " + " / ".join(str(x) for x in plan["ask"][:8])[:700])
        lines.append("절: " + " · ".join(str(s.get("title") or "") for s in (ch.get("sections") or []) if s.get("kind") != "summary")[:500])
        if ch.get("gaps"):
            lines.append("이 서재로는 답하지 못한 것:\n" + "\n".join("- " + str(g)[:300] for g in ch["gaps"][:8]))
        cm, tot = [], 0
        for s in ch.get("sections") or []:
            for p in (s.get("comment") or {}).get("parts") or []:
                if p.get("h") in ("큰 그림", "엇갈림의 까닭", "근거의 강약") and tot < 1500:
                    t = re.sub(r"\s+", " ", str(p.get("t") or ""))[:380]
                    cm.append("- [%s · %s] %s" % (s.get("title") or "", p["h"], t))
                    tot += len(t)
        if cm:
            lines.append("Claude 의 생각(그 장에 단 풀이 — 서재로 확인한 것이 아니다):\n" + "\n".join(cm))
        text = "\n".join(lines)
    _STUDY_CACHE[fp] = (mt, text)
    return text


def _study_block(limit=8, cap_each=2600):
    try:
        lst = [x for x in (cfg["study_list"]() if cfg.get("study_list") else []) if x.get("status") == "done"][:limit]
    except Exception:
        lst = []
    parts = [d[:cap_each] for d in (_study_digest(it) for it in lst) if d]
    if not parts:
        return "", {"studies": 0}
    return ("# 공부 장 — 연구자가 서재의 논문만으로 쓴 교과서 장 %d개(문장마다 원문과 대조한 것). '답하지 못한 것' = 서재에 없거나 한두 편에만 기대거나 논문끼리 엇갈리는 것\n\n" % len(parts) + "\n\n".join(parts)), {"studies": len(parts)}


def _hist_labs(hid, limit=6):
    if hid in _HIST_LABS:
        return _HIST_LABS[hid]
    s = ""
    try:
        d = cfg["load_json"](os.path.join(cfg["HIST_DIR"], os.path.basename(hid) + ".json"), None) if cfg.get("HIST_DIR") else None
        labs = ((d or {}).get("result") or {}).get("labs") or []
        s = ", ".join("%s(%s) %d편" % (l.get("pi") or "?", (l.get("inst") or "")[:30], l.get("n") or 0) for l in labs[:limit])
    except Exception:
        s = ""
    _HIST_LABS[hid] = s
    return s


def _hist_block(limit=20):
    try:
        idx = cfg["load_json"](os.path.join(cfg["HIST_DIR"], "_목록.json"), None) if cfg.get("HIST_DIR") else None
    except Exception:
        idx = None
    items = ((idx or {}).get("items") or [])[:limit]
    if not items:
        return "", {"searches": 0}
    out = ["# 지난 논문 탐색 %d건(최근순) — 검색어 (연도 범위) — 결과 편수·그중 보유 · 소주제 · 많이 나온 저널 · 결과에 많은 연구 그룹(교신저자, 기관)" % len(items)]
    for k, m in enumerate(items):
        labs = _hist_labs(m.get("id", "")) if k < 8 and m.get("id") else ""
        out.append("- %s (%s) — %d편·보유 %d · 소주제: %s · 저널: %s%s" % (
            str(m.get("q") or "")[:120], m.get("year") or "전체", m.get("n") or 0, m.get("owned") or 0,
            ", ".join(str(x) for x in (m.get("groups") or [])) or "-", ", ".join(str(x) for x in (m.get("journals") or [])) or "-", (" · 연구 그룹: " + labs) if labs else ""))
    return "\n".join(out), {"searches": len(items)}


def _gap_block(n=25):
    g = _BG.get("gaps")
    if not g or time.time() - g["t"] > 12 * 3600:
        rows = []
        try:
            rows = cfg["libnet_gaps"](n) if cfg.get("libnet_gaps") else []
        except Exception:
            rows = []
        if rows or not g:
            _BG["gaps"] = g = {"t": time.time(), "rows": rows}
    rows = g.get("rows") or []
    if not rows:
        return "", {"gaps": 0}
    out = ["# 서재가 많이 인용하는데 서재에 없는 논문 %d편 — 서재 논문들의 참고문헌·피인용에서 센 것(이 분야의 뿌리 문헌이거나 빠진 것)" % len(rows)]
    for r in rows:
        out.append("- %s %s — %s · %s · 서재와 %s가닥 · 피인용 %s" % (r.get("year") or "?", r.get("author") or "?", str(r.get("title") or "")[:110], r.get("abbr") or (r.get("venue") or "")[:30], r.get("links") or 0, r.get("cit") if r.get("cit") is not None else "?"))
    return "\n".join(out), {"gaps": len(rows)}


def _ms_block(limit=4, cap=2000):
    parts = []
    try:
        lst = cfg["ms_list"]() if cfg.get("ms_list") else []
    except Exception:
        lst = []
    for it in lst[:limit]:
        try:
            doc = cfg["ms_load"](it["id"])
        except Exception:
            doc = None
        if not isinstance(doc, dict):
            continue
        f = doc.get("front") or {}
        title = str(doc.get("title") or f.get("title") or "")[:200]
        heads = [str(n.get("heading") or "") for n in (doc.get("outline") or []) if (n.get("level") or 1) == 1 and n.get("heading")][:14]
        s = "## 「%s」 (%s)\n" % (title or "제목 없음", time.strftime("%Y-%m-%d", time.localtime(doc.get("updated") or 0)))
        if f.get("abstract"):
            s += "초록: " + re.sub(r"\s+", " ", str(f["abstract"]))[:1100] + "\n"
        if heads:
            s += "절: " + " · ".join(heads)[:500]
        parts.append(s.strip()[:cap])
    if not parts:
        return "", {"manuscripts": 0}
    return "# 연구자가 쓰고 있는 원고 %d편\n\n" % len(parts) + "\n\n".join(parts), {"manuscripts": len(parts)}


def _background():
    """시스템 프롬프트 — 물음마다 글자 하나까지 같아야 캐시가 맞는다(시각·주제 목록은 넣지 않는다). → (글, 통계)"""
    stats = {}
    blocks = []
    for fn in (_lab_block, _lib_block, _study_block, _hist_block, _gap_block, _ms_block):
        try:
            text, st = fn()
        except Exception as e:
            text, st = "", {"error": str(e)[:100]}
        stats.update(st)
        if text:
            blocks.append(text)
    head = ("당신은 기계가공·정밀가공 분야 연구자의 연구 공저자이자 토의 상대다. 연구자가 연구 주제를 던지고 함께 따진다 — 이 주제가 설 만한가, 무엇이 이미 밝혀졌고 무엇이 비었는가, "
            "어떤 실험·해석으로 보일 수 있는가, 어디에 낼 것인가. 아래는 연구자가 가진 것·아는 것 전부다: 우리 연구실(장비·역량 — 연구자가 적어 둔 것, 있을 때만), 서재(모은 논문 모두), 그 서재로 쓴 공부 장, 지난 논문 탐색, 서재의 인용 이웃, 쓰고 있는 원고. "
            "토의는 이것을 바탕으로 한다 — 서재 안의 논문을 들어 말하고, 서재에 없는 것은 그렇다고 밝히고, 실험·측정은 우리 연구실에서 되는지 가른다.\n\n")
    text = head + "\n\n".join(blocks)
    stats["chars"] = len(text)
    return text, stats


def background_view(full=False):
    text, stats = _background()
    return dict(stats, text=text) if full else stats


# ---------- 묻기 ----------
_RULE = (
    "토의하는 법:\n"
    "- 답은 사람이 읽는 한국어 글이다. 두괄식으로: 첫 문장에 판단(이 주제가 설 만한가, 무엇이 핵심인가, 무엇이 문제인가), 그 다음에 까닭, 마지막에 다음에 할 일. 짧은 문장으로 한 번에 한 가지씩. 인사말·칭찬·잘된 점의 나열·되묻기만 하는 답은 쓰지 않는다. 마크다운 제목·굵게 표시 없이 문단으로 쓴다. 따질 것이 여럿이면 번호 목록으로 — 항목마다 줄을 바꾸고 '① 혼동: …' 처럼 두세 낱말의 머리말과 쌍점으로 연다(화면이 항목마다 한 줄로 그린다).\n"
    "- 공저자처럼 따진다: 서재 안에 같은 것을 이미 한 연구가 있는가(있으면 그 논문을 들고, 어디까지 밝혀졌고 어디서 엇갈리는가 — 공부 장의 '답하지 못한 것'과 'Claude 의 생각'도 재료다), 새로움이 어디에 있는가, 어떤 실험·해석·측정으로 보일 수 있는가, 어느 저널·어느 연구 그룹의 일과 맞닿는가. 좋게만 말하지 말고 약점과 반론을 먼저 말한다.\n"
    "- 논문은 (첫 저자 연도) 로 가리킨다 — 서재의 논문은 그대로. 서재에 없는 논문이나 당신의 지식으로 보태는 것은 '서재 밖:' 으로 시작하는 문단에 따로 적어 갈라 둔다(연구자가 확인해야 한다). 지어낸 수치·인용은 절대 쓰지 않는다.\n"
    "- 세상의 문헌을 더 봐야 할 물음이 생기면 답 끝에 '탐색: <검색할 주제 한 줄(한국어나 영어)>' 줄을 1~3개 적는다 — 연구자가 그 줄을 눌러 논문 탐색을 돌린다. 서재의 논문을 깊이 읽어야 하면 '공부: <주제 한 줄>' 줄을 적어도 된다.\n"
    "- 실험·측정·장비를 말할 때는 [우리 연구실](있을 때)에 비추어 우리 장비로 되는지 가른다. 우리 연구실에 없는 측정·장비(예: EBSD·TEM·XRD·나노인덴터)는 '외부: <측정> — 어디서(공동 장비 센터·협력 연구실·외주)·대략의 비용과 기간' 으로 적고, 그 측정 없이도 가설을 가를 수 있는 대안이 있으면 같이 적는다. [우리 연구실] 이 없으면 가르지 않는다.\n"
    "- 연구자의 말에 결정이 보이면(이 주제로 간다, 이렇게 하자) 그것을 한 문장으로 짚어 두고 다음 물음으로 넘긴다. 내부 표시·규칙 이름은 쓰지 않는다.\n")


def _card_text(card):
    if not card:
        return ""
    out = []
    for k in CARD_KEYS:
        v = card.get(k)
        if not v:
            continue
        out.append("%s: %s" % (CARD_KO[k], " / ".join(str(x) for x in v) if isinstance(v, list) else str(v)))
    return "\n".join(out)


def _prompt(nt, question, lib, others):
    seed = nt.get("seed") or {}
    fr = seed.get("from") or {}
    out = "[이 주제] %s (%s)\n" % (nt.get("title") or "", STATUS_KO.get(nt.get("status"), "아이디어"))
    if seed.get("text"):
        src = {"study": "공부 장", "explore": "논문 탐색", "ms": "원고"}.get(fr.get("kind"), "")
        out += "[출발점]%s\n%s\n" % ((" (%s 「%s」 에서)" % (src, fr.get("title"))) if src and fr.get("title") else "", seed["text"][:3000])
    ct = _card_text(nt.get("card"))
    if ct:
        out += "\n[지금까지 정리한 주제 카드]\n" + ct + "\n"
    th = [m for m in nt.get("thread") or [] if m.get("text")]
    if len(th) > 1:
        past = "\n\n".join("%s: %s" % ("연구자" if m.get("role") == "user" else "Claude", m["text"][:2200 if m.get("role") == "user" else 3200]) for m in th[-11:-1])
        out += "\n[지난 대화]\n" + past + "\n"
    if nt.get("plan"):
        out += "\n[실험 설계서 — 지금까지 세운 것]\n" + _plan_text(nt["plan"], cap=700) + "\n"
    if nt.get("intro"):
        out += "\n[서론 구조 — 지금까지 세운 것]\n" + _intro_text(nt["intro"], brief=True) + "\n"
    opens = [o for o in nt.get("objections") or [] if o.get("status") == "open"]
    if opens:
        out += "\n[아직 답하지 않은 반론]\n" + "\n".join("- [%s] %s" % (REVIEWER_KO.get(o.get("who"), o.get("who")), o.get("t")) for o in opens[:10]) + "\n"
    if others:
        out += "\n[연구 노트의 다른 주제] " + " · ".join("%s(%s)" % (o["title"][:50], STATUS_KO.get(o["status"], "")) for o in others[:30]) + "\n"
    out += "\n[물음]\n" + question + "\n\n" + _RULE
    if lib:
        out += ("\n[서재를 찾아볼 수 있다]\n연구자가 모은 논문의 번역 폴더는 \"%s\" 다(지금 작업 폴더가 아니니 Grep·Glob·Read 에 이 경로를 주어라). 논문마다 \"<이름>.요약.md\"(한국어 요약)와 \"<이름>.번역.md\"(한국어 전문 번역, 문장마다 [sN] 표식)가 있고, "
                "원문 PDF 는 \"%s\" 에 같은 이름으로 있다(이름은 서재 목록의 '연도_저널_저자_제목' 꼴 — Glob 으로 찾는다).\n"
                "- 배경의 한 줄 요약만으로 답할 수 없는 물음일 때만 찾아라. Grep 으로 용어(영어·한국어)를 *.요약.md 에서 찾고, 필요한 논문의 요약 또는 .번역.md 의 그 부분만 Read 한다(통째로 읽지 마라). 도구는 많아야 10번.\n"
                "- 서재에서 확인한 것은 (저자 연도)로 가리키고, 확인하지 못한 것은 확인하지 못했다고 말하라.") % (cfg["GEN_DIR"], cfg["ARCHIVE"])
    else:
        out += "\n(너는 파일을 열 수 없다. 배경과 여기 준 것만 보고 답하라. 더 확인할 것이 있으면 무엇을 봐야 하는지 말하라 — 연구자가 「서재 찾아보기」 를 켜고 다시 물을 수 있다.)"
    return out


def ask(nid, body):
    question = str(body.get("question") or "").strip()
    if len(question) < 2:
        return {"error": "물음을 적어 주세요"}
    effort = body.get("effort") if body.get("effort") in _EFFORT else "xhigh"
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return {"error": "주제를 찾지 못했습니다"}
        if nt.get("pending"):
            return {"error": "아직 답을 기다리고 있습니다"}
        th = nt.setdefault("thread", [])
        if th and th[-1].get("role") == "user" and nt.get("error"):
            th[-1] = {"role": "user", "text": question, "t": time.time()}
        else:
            th.append({"role": "user", "text": question, "t": time.time()})
        nt["pending"] = time.time()
        nt.pop("error", None)
        nt["opts"] = {"effort": effort, "lib": bool(body.get("lib"))}
        _save(nt)
    threading.Thread(target=_ask_job, args=(nid,), name="notes-ask", daemon=True).start()
    return {"ok": True, "note": nt}


def _ask_jobkey(nid, nt):
    """돌고 있는 claude 호출의 이름 — 물음마다 다르게(pending 시각)."""
    return "notes:%s:%d" % (nid, int(nt.get("pending") or 0))


def ask_cancel(nid):
    """답을 기다리는 물음을 취소 — 돌고 있는 claude 를 끊고, _ask_job 의 마무리가 그 물음을 뺀다 (사용자 2026-10-09)"""
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return {"error": "주제를 찾지 못했습니다"}
        if not nt.get("pending"):
            return {"error": "이미 답이 왔습니다"}
        job = _ask_jobkey(nid, nt)
    cancel = cfg.get("claude_cancel")
    if not cancel:
        return {"error": "이 서버는 취소를 지원하지 않습니다"}
    cancel(job)
    return {"ok": True}


def _ask_job(nid):
    nt = _load(nid)
    if not nt or not nt.get("thread"):
        return
    question = nt["thread"][-1].get("text") or ""
    model, eff = _EFFORT.get((nt.get("opts") or {}).get("effort"), _EFFORT["xhigh"])
    lib = bool((nt.get("opts") or {}).get("lib"))
    t0 = time.time()
    res, err, cancelled = None, "", False
    try:
        others = [o for o in list_notes() if o["id"] != nid]
        system, _st = _background()
        run = cfg.get("claude_run")
        res = run(_prompt(nt, question, lib, others), timeout=1500, model=model, effort=eff, tools="Read,Grep,Glob" if lib else "",
                  add_dirs=[cfg["GEN_DIR"], cfg["ARCHIVE"]] if lib else (), system=system, job=_ask_jobkey(nid, nt)) if run else None
        cancelled = bool((res or {}).get("cancelled"))
        if not cancelled and (not res or not (res.get("text") or "").strip()):
            err = "Claude 응답이 없습니다" + _why()
    except Exception as e:
        err = "Claude 호출 실패: " + str(e)[:200]
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return
        nt.pop("pending", None)
        if cancelled:   # 「취소」 — 그 물음을 대화에서 뺀다 (오류로 적지 않는다)
            th = nt.get("thread") or []
            if th and th[-1].get("role") == "user":
                th.pop()
            nt.pop("error", None)
            _save(nt)
            return
        if err:
            nt["error"] = err
        else:
            nt.pop("error", None)
            nt.setdefault("thread", []).append({"role": "claude", "text": res["text"].strip(), "t": time.time(), "sec": round(time.time() - t0),
                                                "model": res.get("model", ""), "tok": res.get("tok"), "turns": res.get("turns", 1), "lib": lib})
        _save(nt)


# ---------- 주제 카드: 대화에서 정리 ----------
def _json_of(text):
    s = str(text or "")
    try:
        return json.loads(s[s.index("{"):s.rindex("}") + 1])
    except (ValueError, TypeError):
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    return None


def card_start(nid, body):
    effort = body.get("effort") if body.get("effort") in _EFFORT else "xhigh"
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return {"error": "주제를 찾지 못했습니다"}
        if not any(m.get("role") == "claude" for m in nt.get("thread") or []):
            return {"error": "먼저 한 번은 토의해야 카드를 정리할 수 있습니다"}
        if nt.get("card_pending"):
            return {"error": "카드를 정리하는 중입니다"}
        nt["card_pending"] = time.time()
        nt.pop("card_error", None)
        nt["card_opts"] = {"effort": effort}
        _save(nt)
    threading.Thread(target=_card_job, args=(nid,), name="notes-card", daemon=True).start()
    return {"ok": True}


def _card_job(nid):
    nt = _load(nid)
    if not nt:
        return
    model, eff = _EFFORT.get((nt.get("card_opts") or {}).get("effort"), _EFFORT["xhigh"])
    th = [m for m in nt.get("thread") or [] if m.get("text")]
    conv = "\n\n".join("%s: %s" % ("연구자" if m.get("role") == "user" else "Claude", m["text"][:5000]) for m in th[-20:])
    prompt = ("아래는 연구자와 나눈 연구 주제 토의다. 토의에서 정해진 것·거론된 것을 주제 카드 하나로 정리하라. 토의에 없는 것은 지어 넣지 말고 빈 문자열로 둔다. 연구자가 손으로 고친 카드가 있으면 그것을 바탕으로 고친다. 칸의 글은 한눈에 읽히게 — 따질 것이 여럿이면 항목마다 줄을 바꿔 '① 머리말: …' 꼴로(design 은 '재료: … / 장비: … / 조건: … / 측정: …' 처럼 줄마다 하나, expected 는 결과마다 한 줄), 한 문장에 사실 하나(화면이 항목마다 한 줄로 그린다).\n"
              "JSON 으로만 답하라(한국어, 마크다운 없이):\n"
              "{\"question\": \"연구 물음 한두 문장\", \"hypothesis\": \"가설 — 무엇이 어떻게 될 것이라 보는가\", \"design\": \"실험·해석 설계: 재료, 장비, 조건, 측정·관찰 (토의에서 나온 것만)\", "
              "\"expected\": \"예상 결과와 그것이 뜻하는 것\", \"risks\": \"위험·약점·반론과 그 대비\", \"papers\": [\"(첫 저자 연도) — 왜 관련되는가 (서재 밖이면 '서재 밖' 표시)\"], "
              "\"groups\": [\"연구 그룹 — 무엇을 하는가\"], \"journals\": [\"저널\"], \"next\": [\"다음에 할 일\"]}\n\n"
              "[주제] %s\n%s\n[토의]\n%s") % (nt.get("title") or "", ("[연구자가 손으로 고친 카드]\n" + _card_text(nt["card"]) + "\n") if nt.get("card") and nt.get("card_edited") else "", conv)
    t0 = time.time()
    res, err, card = None, "", None
    try:
        system, _st = _background()
        run = cfg.get("claude_run")
        res = run(prompt, timeout=900, model=model, effort=eff, tools="", system=system) if run else None
        card = _json_of(res.get("text")) if res else None
        if not isinstance(card, dict):
            err = "카드를 정리하지 못했습니다" + _why()
    except Exception as e:
        err = "Claude 호출 실패: " + str(e)[:200]
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return
        nt.pop("card_pending", None)
        if err:
            nt["card_error"] = err
        else:
            nt.pop("card_error", None)
            nt["card"] = _clean_card(card)
            nt["card_t"] = time.time()
            nt["card_model"] = res.get("model", "")
            nt["card_sec"] = round(time.time() - t0)
            nt["card_tok"] = res.get("tok")
            nt.pop("card_edited", None)
        _save(nt)


# ---------- 실험 설계서 · 심사위원 반론 · 원고 초안 (2026-10-19) ----------
def _lib_index():
    """서재 파일 → [(연도, 첫 저자 성(소문자 글자만), 파일)] — 설계서의 '(저자 연도)' 를 서재 파일에 맞출 때. 파일 수가 바뀌면 다시."""
    try:
        files = sorted(f for f in os.listdir(cfg["ARCHIVE"]) if f.lower().endswith(".pdf"))
    except OSError:
        files = []
    c = _BG.get("libidx")
    if c and c[0] == len(files):
        return c[1]
    idx = []
    for f in files:
        m = re.match(r"^(\d{4})_([^_]+)_([^_]+)_", f)
        if m:
            idx.append((m.group(1), re.sub(r"[^a-z]", "", m.group(3).lower()), f))
    _BG["libidx"] = (len(files), idx)
    return idx


def _cite_file(key):
    """'Zan 2026' · '(Zan et al., 2026)' · 'Kümmel 2015' → 서재 파일 또는 None (연도 같고 성이 앞부분부터 같으면)"""
    k = str(key or "")
    m = re.search(r"((?:19|20)\d{2})", k)
    if not m:
        return None
    yr = m.group(1)
    norm = lambda w: re.sub(r"[^a-z]", "", w.lower().replace("ü", "u").replace("ö", "o").replace("ä", "a").replace("é", "e"))
    words = [norm(w) for w in re.findall(r"[A-Za-zÀ-ž][A-Za-zÀ-ž'\-]*", k[:m.start()])]
    words = [w for w in words if w and w not in ("et", "al", "and", "the")]
    cands = []
    for i in range(len(words) - 1, -1, -1):   # 뒤에서부터 이어 붙인다: monaca → lamonaca → delamonaca (성이 두 낱말인 저자)
        cands.append("".join(words[i:]))
    idx = _lib_index()
    for au in cands[:3]:
        if len(au) < 2:
            continue
        for y, a, f in idx:
            if y == yr and (a == au or (len(au) >= 3 and (a.startswith(au) or au.startswith(a)))):
                return f
    return None


_FEAS_KO = {True: "우리", False: "외부", None: "모름"}


def _feas_line(f):
    return "%s(%s%s)" % (f.get("item"), _FEAS_KO.get(f.get("ours"), "모름"), (" — " + f["how"]) if f.get("how") else "")


def _plan_text(plan, cap=1200):
    out = []
    for sec in (plan or {}).get("sections") or []:
        if sec.get("t"):
            out.append("%s: %s" % (sec.get("h") or PLAN_KO.get(sec.get("k"), sec.get("k")), sec["t"][:cap]))
    for k, nm in (("gaps", "서재에 없어 확인할 것"), ("checks", "연구자가 정할 것")):
        if (plan or {}).get(k):
            out.append("%s: %s" % (nm, " / ".join(str(x) for x in plan[k][:8])))
    if (plan or {}).get("feasibility"):
        out.append("우리 연구실에서 할 수 있는가: " + " / ".join(_feas_line(f) for f in plan["feasibility"][:14]))
    return "\n".join(out)


def _clean_feas(v):
    out = []
    for it in (v if isinstance(v, list) else [])[:14]:
        if not isinstance(it, dict) or not str(it.get("item") or "").strip():
            continue
        o = it.get("ours")
        ours = True if (o is True or str(o).strip().lower() in ("true", "yes", "ours", "우리")) else (False if (o is False or str(o).strip().lower() in ("false", "no", "external", "외부")) else None)
        out.append({"item": str(it["item"]).strip()[:120], "ours": ours, "how": re.sub(r"\s+", " ", str(it.get("how") or "")).strip()[:300]})
    return out


def _thread_text(nt, n=24, cap=3000):
    th = [m for m in nt.get("thread") or [] if m.get("text")]
    return "\n\n".join("%s: %s" % ("연구자" if m.get("role") == "user" else "Claude", m["text"][:cap]) for m in th[-n:])


def _answered_text(nt):
    done = [o for o in nt.get("objections") or [] if o.get("status") in ("answered", "dropped")]
    return "\n".join("- [%s] %s%s" % (REVIEWER_KO.get(o.get("who"), o.get("who")), o.get("t"), (" → 답: " + o["reply"][:300]) if o.get("reply") else (" (보류)" if o.get("status") == "dropped" else " (답함)")) for o in done[:12])


_PLAN_RULE = (
    "연구자와 나눈 토의·주제 카드·(있으면) 손으로 고친 설계서를 바탕으로 실험 설계서를 쓴다. 토의에서 정해진 것은 그대로 쓰고, 정해지지 않은 칸은 배경(서재·공부 장·탐색)에 비추어 가장 그럴듯한 안을 '제안: ' 으로 시작해 적는다(연구자가 알아보게). "
    "지어낸 수치·인용은 쓰지 않는다. 서재의 논문을 들 때는 '(첫 저자 연도)' 로, 서재 밖의 지식으로 보태는 문장은 '서재 밖: ' 으로 시작한다. 답하지 않은 반론이 있으면 그것을 설계에 반영하거나 위험 칸에 적는다.\n"
    "JSON 으로만 답하라(한국어, 마크다운 없이): {\"sections\": {\"background\": {\"t\": \"글\", \"cites\": [\"첫 저자 연도\", ...]}, \"question\": {...}, \"material\": {...}, \"setup\": {...}, \"design\": {...}, "
    "\"analysis\": {...}, \"expected\": {...}, \"risks\": {...}, \"schedule\": {...}, \"venue\": {...}}, \"gaps\": [\"서재에 없어 연구자가 확인해야 할 것\"], \"checks\": [\"연구자가 정해야 할 것 — 선택지와 함께\"], "
    "\"feasibility\": [{\"item\": \"이 계획이 쓰는 측정·장비·공정 하나\", \"ours\": true|false|null, \"how\": \"우리 장비면 그 이름 / 외부면 어디서·어떻게(공동 장비 센터·협력 연구실·외주)·대략의 비용과 기간 / 모르면 무엇을 확인할지\"}]}\n"
    "칸마다 2~6문장(design 은 변수마다 한 줄 — '변수: 수준1 / 수준2 / 수준3 (고정: …), 반복 n'). 따질 것이 여럿인 칸(expected·risks·analysis·setup)은 항목마다 줄을 바꿔 '① 머리말: …' 로 적는다 — 화면이 항목마다 한 줄로 그린다. "
    "background 배경과 공백(무엇이 알려졌고 무엇이 비었나 — 서재 논문으로) · question 연구 물음과 가설(검증 가능한 꼴로, 맞으면/틀리면 무엇이 보이나) · material 재료·시편(규격·상태·수) · setup 장비·측정(무엇을 무엇으로 재나, 분해능·범위 — [우리 연구실] 이 있으면 우리 장비로 되는 것과 외부에 맡길 것을 갈라 적는다) · "
    "design 실험 설계(독립 변수와 수준, 고정 조건, 반복 수, 대조군) · analysis 해석·모델(측정값을 가설의 판정으로 바꾸는 길) · expected 예상 결과와 판정 기준 · risks 위험과 대안(실패할 자리와 대비) · schedule 일정(단계와 기간) · venue 저널과 독자(어느 저널, 심사자가 볼 것). "
    "cites 는 그 칸에서 든 서재 논문의 '첫 저자 연도' 목록(없으면 빈 목록). "
    "feasibility 는 이 계획이 쓰는 측정·장비·공정마다 [우리 연구실] 에 비추어 우리가 할 수 있는가 — ours 가 false 인 것은 어디에 맡길지, null 은 연구자가 확인할 것. [우리 연구실] 이 없으면 빈 목록. "
    "[유사 연구의 설계] 가 있으면: background 는 그 논문들이 무엇을 했고 어디서 멈췄는지를 그 논문으로 들어 공백을 세우고, material·setup·design 은 그들의 재료·장치·조건과 무엇이 같고 무엇이 다른지를 한 줄로 밝힌다(같은 설계면 그렇게 적는다 — 심사위원이 새로움을 물을 것이다). "
    "[… 방법을 이렇게 쓴다] 가 있으면 그 저널의 방법 절이 늘 적는 것(시편 상태·공구 기하·조건·측정 분해능 같은)을 설계서가 빠뜨리지 않게 한다. "
    "[택한 프레이밍] 이 있으면 question·background·venue 는 그 틀의 물음·공백·저널을 따르고 design·setup 은 그 틀이 '설계가 더 갖춰야 할 것' 으로 든 대조·측정을 채운다; 후보만 있고 고르지 않았으면 추천 틀을 따르되 checks 에 '틀을 고를 것' 을 적는다.")

_REVIEW_RULE = (
    "아래 연구 계획을 세 사람이 따로 심사한다 — exp 실험 전문가(장비·시편·측정의 실현 가능성 — [우리 연구실] 이 있으면 그 장비·역량으로 되는가와 외부에 맡길 측정의 비용·일정·접근, 교란 변수, 재현성, 측정 분해능이 효과 크기를 가를 수 있는가), theory 이론·모델 전문가(가설의 논리, 해석 모델의 가정, 같은 결과를 내는 대안 설명, 빠진 변수; [식별력 표] 가 있으면 못 가르는 쌍과 '~'·'?' 칸, [약속 ↔ 답 지도] 의 답 없는 약속을 먼저 공격한다), "
    "editor 저널 심사위원(새로움 — 서재의 어느 논문이 이미 비슷한 것을 했는가, 결과가 나와도 왜 중요한가, 그 저널의 독자가 묻는 것; [유사 연구의 설계] 가 있으면 그 논문들과 칸마다 견줘 이미 된 것과 다른 것을 짚고, [공백 검증] 에 '이미 됨' 으로 나온 공백을 계획이 그대로 쓰고 있으면 그것을 친다; [택한 프레이밍] 이 있으면 설계가 그 틀의 주장을 실제로 세울 수 있는가와 그 저널의 독자가 그 틀을 살 것인가를 묻는다). 각자 가장 아픈 반론 2~4개를 낸다 — 좋은 말·요약·되묻기만 하는 말은 쓰지 않는다. "
    "반론은 구체적으로(어느 칸의 무엇이 왜 문제인가), 서재 논문을 들 수 있으면 '(첫 저자 연도)' 로. 이미 답한 반론은 되풀이하지 않는다. 연구자가 답하면 풀릴 수 있게 물음을 한 줄 단다.\n"
    "JSON 으로만: {\"items\": [{\"who\": \"exp|theory|editor\", \"sec\": \"설계서 칸 이름(background·question·material·setup·design·analysis·expected·risks·schedule·venue) 또는 빈 문자열\", \"sev\": \"high|mid|low\", \"t\": \"반론 1~3문장\", \"ask\": \"연구자가 답해야 할 물음 한 줄\"}]}")

_DRAFT_RULE = (
    "아래 연구 계획으로 논문 원고의 뼈대를 세운다 — 제목·초록·키워드·절 차례. 절은 1단계 4~6개(서론·방법·결과·논의·결론 꼴을 이 연구에 맞게), 1단계마다 2단계 소절 2~5개. "
    "절·소절마다 요지(claim) 한 문장 = 그 절이 독자에게 세울 주장. 결과·논의의 요지는 예상 결과를 가정한 것이므로 앞에 '(예상) ' 을 붙인다. 서론의 요지는 배경 → 공백 → 목적의 논리로 서재 논문을 '(첫 저자 연도)' 로 든다. 지어낸 수치는 쓰지 않는다.\n"
    "JSON 으로만: {\"title\": \"제목(영어)\", \"title_ko\": \"제목(한국어)\", \"abstract\": \"초록 한국어 150~250자 — 예상 결과는 '…할 것으로 본다' 꼴\", \"keywords\": [\"영어 키워드 4~6개\"], \"journal\": \"저널 약어\", "
    "\"sections\": [{\"heading\": \"1. 서론\", \"claim\": \"요지\", \"children\": [{\"heading\": \"1.1 …\", \"claim\": \"요지\"}]}]}")


def _plan_prompt(nt):
    out = "[이 주제] %s (%s)\n" % (nt.get("title") or "", STATUS_KO.get(nt.get("status"), "아이디어"))
    seed = nt.get("seed") or {}
    if seed.get("text"):
        out += "[출발점]\n" + seed["text"][:3000] + "\n"
    ct = _card_text(nt.get("card"))
    if ct:
        out += "\n[주제 카드]\n" + ct + "\n"
    if nt.get("plan") and nt.get("plan_edited"):
        out += "\n[연구자가 손으로 고친 설계서 — 이것을 바탕으로 고친다]\n" + _plan_text(nt["plan"]) + "\n"
    opens = [o for o in nt.get("objections") or [] if o.get("status") == "open"]
    if opens:
        out += "\n[아직 답하지 않은 반론]\n" + "\n".join("- [%s] %s" % (REVIEWER_KO.get(o.get("who"), o.get("who")), o.get("t")) for o in opens[:10]) + "\n"
    ans = _answered_text(nt)
    if ans:
        out += "\n[답한 반론]\n" + ans + "\n"
    fr = _framing_text(nt.get("framing"))   # 연구의 틀 — 고른 것(없으면 후보와 추천) (2026-10-26)
    if fr:
        out += "\n" + fr
    dz, items = _designs(nt, k=8)   # 유사 연구의 설계 — 서재 주석에서 (2026-10-26)
    _DESIGNS[nt.get("id")] = [{"stem": x["stem"], "author": x["author"], "year": x["year"], "journal": x["journal"], "star": x.get("star")} for x in items]
    if dz:
        out += "\n" + dz
    mx = _matrix_text(nt.get("matrix"))
    if mx:
        out += "\n" + mx + "(설계서를 다시 쓸 때 못 가르는 쌍을 가를 관찰·대조를 design·setup 에 더하거나, 못 가른다고 risks 에 밝힌다)\n"
    am = _argmap_text(nt.get("argmap"))
    if am:
        out += "\n" + am
    gc = _gapcheck_text(nt.get("gapcheck"))
    if gc:
        out += "\n" + gc
    try:   # 그 저널의 방법 절은 무엇을 꼭 적는가 — 설계서의 재료·장비·설계 칸이 빠뜨리지 않게 (짧게)
        kn = (cfg.get("knowledge") or (lambda j, k, cap=0: ""))(_intro_journals(nt), "methods", cap=4500)
    except TypeError:
        kn = ""
    if kn:
        out += "\n" + kn
    out += "\n[토의]\n" + _thread_text(nt) + "\n\n" + _PLAN_RULE
    return out


def _review_prompt(nt):
    out = "[이 주제] %s\n" % (nt.get("title") or "")
    ct = _card_text(nt.get("card"))
    if ct:
        out += "\n[주제 카드]\n" + ct + "\n"
    if nt.get("plan"):
        out += "\n[실험 설계서]\n" + _plan_text(nt["plan"]) + "\n"
    else:
        out += "\n[토의 — 설계서가 아직 없어 토의를 놓고 심사한다]\n" + _thread_text(nt, n=12, cap=2500) + "\n"
    ans = _answered_text(nt)
    if ans:
        out += "\n[이미 답한 반론 — 되풀이하지 않는다]\n" + ans + "\n"
    fr = _framing_text(nt.get("framing"))
    if fr:
        out += "\n" + fr
    dz, _items = _designs(nt, k=6)   # 심사위원(editor)이 새로움을 실제 논문과 견주게 (2026-10-26)
    if dz:
        out += "\n" + dz
    mx = _matrix_text(nt.get("matrix"))
    if mx:
        out += "\n" + mx
    am = _argmap_text(nt.get("argmap"))
    if am:
        out += "\n" + am
    gc = _gapcheck_text(nt.get("gapcheck"))
    if gc:
        out += "\n" + gc
    return out + "\n" + _REVIEW_RULE


def _draft_prompt(nt):
    out = "[이 주제] %s\n" % (nt.get("title") or "")
    fr = _framing_text(nt.get("framing"), brief=True)   # 제목·초록은 고른 틀의 물음·주장으로
    if fr:
        out += "\n" + fr
    if nt.get("plan"):
        out += "\n[실험 설계서]\n" + _plan_text(nt["plan"], cap=2500) + "\n"
    if nt.get("intro"):
        out += "\n[서론 구조 — 서론의 소절·요지는 이 문단 차례를 따른다]\n" + _intro_text(nt["intro"]) + "\n"
    ct = _card_text(nt.get("card"))
    if ct:
        out += "\n[주제 카드]\n" + ct + "\n"
    ans = _answered_text(nt)
    if ans:
        out += "\n[심사위원의 반론과 답 — 서론·논의의 논리에 반영한다]\n" + ans + "\n"
    if not nt.get("plan"):
        out += "\n[토의]\n" + _thread_text(nt, n=12, cap=2500) + "\n"
    return out + "\n" + _DRAFT_RULE


_INTRO_RULE = (
    "이 연구의 논문 서론(Introduction)을 쓰기 전에 그 구조를 세운다 — 문단마다 역할·요지·그 문단이 할 말과 받칠 서재 논문. 글은 아직 쓰지 않는다. "
    "[본보기]가 있으면 그 저널 논문들이 서론을 몇 문단으로 어떤 차례(배경 → 선행연구 → 공백 → 목적)로 짜는지를 출발점으로 삼되 베끼지 말고 이 연구의 논리에 맞게 고친다. "
    "문단 수는 [이 저널 서론의 크기](있으면)의 범위 안에서 이 연구의 논리에 필요한 만큼(없으면 4~7개). 역할은 배경(분야·왜 중요한가) · 선행연구(무엇이 알려졌나 — 묶어서) · 공백(엇갈림·한계·남은 물음 — 설계서의 '배경과 공백'·답한 반론과 같은 말이어야 한다) · 목적·접근(이 연구의 물음과 방법 개요 — 설계서의 물음·가설 그대로) · 기여·구성(선택) 가운데 고른다. "
    "문단마다 points 는 그 문단이 차례로 세울 말 2~5개 — 사실·주장 하나에 그것을 받치는 서재 논문을 '(첫 저자 연도)' 로 cites 에 적는다(그 논문이 실제로 그 말을 하는지 요약 한 줄에 비추어; 지어낸 인용·수치 금지). "
    "[유사 연구의 설계] 가 있으면 선행연구·공백 문단의 points 는 그 논문들이 실제로 한 것(물음·기여·주장)과 멈춘 곳(공백)으로 세운다 — 요약 한 줄보다 믿을 만한 재료다. "
    "[택한 프레이밍] 이 있으면 공백·목적 문단은 그 틀의 공백·물음·주장과 같은 말이어야 하고, 그 틀의 '가장 가까운 연구' 는 선행연구 문단에 들어가 '다른 점' 이 공백을 세운다. "
    "서재에 없는 사실을 세워야 하면 t 를 '서재 밖: ' 으로 시작하고 cites 는 빈 목록 — 그런 자리는 gaps 에 탐색어와 함께 적는다(연구자가 논문을 찾아 넣는다). 연구자의 요청이 있으면 따른다.\n"
    "JSON 으로만 답하라(한국어, 마크다운 없이): {\"flow\": \"서론이 펴는 논리 한 문단 — 왜 이 차례인가\", \"paras\": [{\"role\": \"배경|선행연구|공백|목적·접근|기여·구성\", \"gist\": \"이 문단이 세울 말 한 문장\", "
    "\"points\": [{\"t\": \"할 말 한 문장\", \"cites\": [\"첫 저자 연도\", ...]}], \"len\": 문장 수 어림(정수)}], \"gaps\": [\"서재에 없어 채워야 할 근거 — 무엇을, 탐색어: ...\"]}")

_DESIGNS = {}   # 주제 id → 설계서를 만들 때 같이 본 유사 연구(stem·이름) — _apply_plan 이 plan.similar 에 옮긴다 (2026-10-26)


_KO_STOP = {"연구", "논문", "결과", "실험", "분석", "가능", "경우", "대한", "통해", "있다", "없다", "때문", "그리고", "하지만", "영향", "특성", "조건", "방법", "과정", "기존", "제안", "확인", "비교", "관찰", "측정", "모델", "설계", "가설", "물음", "수준", "변수", "우리", "연구실", "논의", "정리", "다음", "지금", "이런", "어떤", "무엇", "것은", "것이", "것을", "하나", "둘째", "첫째", "서재", "탐색", "공부", "주제", "카드", "토의"}
_KO_TAIL = re.compile(r"(으로써|으로는|에서는|에서도|으로|에서|부터|까지|에게|처럼|보다|이라|이다|들의|들이|들을|들은|들|을|를|이|가|은|는|의|에|로|과|와|도|만|나|고|며|서|면|적인|적)$")


def _ko_topic(nt, n=14):
    """한국어 주제 낱말 — 제목·카드(물음·가설)·설계서(배경·물음)·서론 구조 요지의 한글 낱말에서 조사를 떼고 자주 나온 것. 주석의 논문 층(물음·공백·기여·주장)이 한국어라 이것으로 맞춘다 (2026-10-26)"""
    parts = [nt.get("title") or ""]
    card = nt.get("card") or {}
    for k in ("question", "hypothesis", "design"):
        v = card.get(k)
        parts.append(" ".join(str(x) for x in v) if isinstance(v, list) else str(v or ""))
    for sec in (nt.get("plan") or {}).get("sections") or []:
        if sec.get("k") in ("background", "question") and sec.get("t"):
            parts.append(sec["t"])
    for q in (nt.get("intro") or {}).get("paras") or []:
        parts.append(q.get("gist") or "")
    import collections
    cnt = collections.Counter()
    for w in re.findall(r"[가-힣]{2,}", " ".join(parts)[:12000]):
        w2 = _KO_TAIL.sub("", w)
        if len(w2) >= 2 and w2 not in _KO_STOP and not w2.endswith(("다", "요", "데", "게", "까", "니", "면", "서", "지만", "는지", "라서", "하게", "히", "들")):   # 서술어·부사 꼴은 뺀다('않았다'·'없어보이는데')
            cnt[w2] += 1
    return [w for w, _ in cnt.most_common(n)]


def _designs(nt, k=8, brief=False):
    """[유사 연구의 설계] — 서재 주석에서 이 주제와 가까운 논문의 물음·공백·방법 문단·기여·주장 (corpus.similar_designs, Claude 없음). → (글, 항목들)"""
    fn = cfg.get("designs")
    if not fn:
        return "", []
    try:
        items = fn(topic=_intro_topic(nt), journals=_intro_journals(nt), cited=sorted(_intro_cited(nt)), k=k, ko=_ko_topic(nt)) or []
        text = (cfg.get("designs_text") or (lambda it, brief=False: ""))(items, brief=brief)
    except Exception:
        return "", []
    return text, items


def _intro_check(nt):
    """프로그램이 본 것 — 서론 구조를 서재의 분포(corpus.band_for, 그 저널 묶음의 서론)와 견준다: 문단 수·역할의 차례·첫 공백 문단의 자리 (원고의 절 계획과 같은 확인, 2026-10-26)"""
    intro = nt.get("intro") or {}
    paras = intro.get("paras") or []
    notes = []
    try:
        band = cfg["kband"](_intro_journals(nt), "intro") if cfg.get("kband") else None
    except Exception:
        band = None
    if band and band.get("nparas") and (band.get("n") or 0) >= 8 and paras:
        lo, md, hi = band["nparas"]
        if not lo <= len(paras) <= hi:
            notes.append("문단 %d개 — 서재의 그 저널 서론은 열에 아홉이 %d~%d개(가운데 %d)" % (len(paras), lo, hi, md))
        pats = [p for p, _ in (band.get("patterns") or [])]
        seq = []
        for p in paras:
            r = p.get("role") or "?"
            if not seq or seq[-1] != r:
                seq.append(r)
        mine = " → ".join(seq)
        if pats and mine not in pats:
            notes.append("역할의 차례 '%s' 는 서재에서 자주 보이는 차례(%s)와 다르다 — 그럴 까닭이 있으면 그대로" % (mine, " / ".join(pats[:3])))
        gp = band.get("gap_pos")
        idx = [i for i, p in enumerate(paras) if p.get("role") == "공백"]
        if gp and idx:
            pos = (idx[0] + 0.5) / len(paras)
            if not gp[0] - 0.1 <= pos <= gp[2] + 0.1:
                notes.append("첫 공백 문단이 %d문단(서론의 %.2f 지점) — 서재의 서론은 %.2f~%.2f 지점(가운데 %.2f)에 둔다" % (idx[0] + 1, pos, gp[0], gp[2], gp[1]))
        intro["band_n"] = band.get("n")
    intro["notes"] = notes
    return notes


# ---------- 식별력 표 — 가설 × 관찰 × 예측·판정 (2026-10-26, 사용자: '최종 목표는 연구 계획·서론 구조') ----------
# 교수가 계획서에서 맨 먼저 묻는 것: 이 실험이 경쟁하는 설명 가운데 어느 것이 맞는지 가를 수 있나. Claude 가 가설·관찰·칸마다의 예측(부호)을 세우고,
# 프로그램이 '두 가설의 예측 부호가 어느 관찰에서도 다르지 않은 쌍'(못 가르는 쌍)·아무 쌍도 못 가르는 관찰·아무것과도 안 갈리는 가설을 센다. 반론·설계서·서론 구조 물음에 들어간다.
_MATRIX_RULE = (
    "설계서(없으면 카드·토의)의 연구를 놓고, 이 실험이 경쟁하는 설명 가운데 어느 것이 맞는지를 실제로 가를 수 있는지 표로 세운다 — 심사위원이 아니라 공저자로서, 빠진 경쟁 설명을 찾아 넣는 것이 이 일의 반이다.\n"
    "hyps: 설계서의 가설(H1, H2 …)은 그대로 쓰고(kind main), 거기에 경쟁하는 설명을 더한다 — 영가설(kind null: 효과 없음·차이 없음), 장치·절차가 만드는 가짜 신호(kind artifact: 공구 정렬 오차·고정구 비대칭·동력계 혼선·열·마모 같은), "
    "다른 기전(kind rival: 같은 관찰을 다르게 설명하는 것 — [유사 연구의 설계]가 쓴 설명 포함). 모두 4~7개, 각각 한 문장. "
    "obs: 이 실험이 재는 양·비교 4~8개 — t(무엇을 어느 조건끼리 견주나), how(어떤 장치·분해능·표본 수로), when(예비|본). 설계서의 '장비·측정'·'해석'·'예상 결과' 칸에 있는 것만(새 장치를 지어내지 않는다). "
    "cells: 가설마다 관찰마다 예측 — dir 은 부호·경향 하나: '+' 커진다·있다·같은 쪽 / '-' 작아진다·반대쪽 / '0' 없다·차이 없음·대칭 / '~' 있지만 방향이 정해지지 않거나 다른 가설과 같은 꼴이라 못 가름 / '?' 이 가설은 이 관찰에 대해 예측하지 못함. t 는 한 구절(왜 그 부호인지). "
    "**두 가설의 dir 이 어느 관찰에서도 다르지 않으면 이 실험은 그 둘을 못 가른다 — 프로그램이 이것을 센다. 그러니 부호는 성실하게, 모르면 '?' 로.** "
    "rules: 판정 규칙 3~6개 — '관찰 Ok 가 어느 꼴이면 어느 가설을 접고 어느 것을 남긴다'. 설계서의 판정 기준과 어긋나지 않게. "
    "risks: 식별력을 깨는 것 0~4개 — 효과 크기가 분해능·표본 산포보다 작을 수 있는 자리, 두 가설이 같은 예측을 내는 자리, 가짜 신호가 주 가설의 부호와 같은 자리.\n"
    "JSON 으로만 답하라(한국어, 마크다운 없이): {\"hyps\": [{\"id\": \"H1\", \"t\": \"한 문장\", \"kind\": \"main|rival|null|artifact\"}], \"obs\": [{\"id\": \"O1\", \"t\": \"…\", \"how\": \"…\", \"when\": \"예비|본\"}], "
    "\"cells\": [{\"h\": \"H1\", \"o\": \"O1\", \"dir\": \"+|-|0|~|?\", \"t\": \"한 구절\"}], \"rules\": [\"…\"], \"risks\": [\"…\"]}")

_MX_KIND_KO = {"main": "주 가설", "rival": "경쟁 설명", "null": "영가설", "artifact": "가짜 신호"}


def _matrix_prompt(nt):
    out = "[이 주제] %s (%s)\n" % (nt.get("title") or "", STATUS_KO.get(nt.get("status"), "아이디어"))
    ct = _card_text(nt.get("card"))
    if ct:
        out += "\n[주제 카드]\n" + ct + "\n"
    if nt.get("plan"):
        out += "\n[실험 설계서]\n" + _plan_text(nt["plan"], cap=1800) + "\n"
    else:
        out += "\n[토의 — 설계서가 아직 없어 토의와 카드로 세운다]\n" + _thread_text(nt, n=12, cap=2500) + "\n"
    ans = _answered_text(nt)
    if ans:
        out += "\n[답한 반론]\n" + ans + "\n"
    opens = [o for o in nt.get("objections") or [] if o.get("status") == "open"]
    if opens:
        out += "\n[아직 답하지 않은 반론 — 경쟁 설명·가짜 신호의 후보]\n" + "\n".join("- [%s] %s" % (REVIEWER_KO.get(o.get("who"), o.get("who")), o.get("t")) for o in opens[:10]) + "\n"
    dz, _items = _designs(nt, k=6, brief=True)
    if dz:
        out += "\n" + dz
    fr = _framing_text(nt.get("framing"), brief=True)   # 주 가설은 고른 틀의 주장에서
    if fr:
        out += "\n" + fr
    if nt.get("matrix"):
        out += "\n[전에 세운 표 — 못 가른 쌍을 가를 관찰을 더하거나 예측을 고친다]\n" + _matrix_text(nt["matrix"]) + "\n"
    return out + "\n" + _MATRIX_RULE


def _matrix_check(m):
    """프로그램이 센다: 못 가르는 가설 쌍 · 아무 쌍도 못 가르는 관찰 · 아무것과도 안 갈리는 가설"""
    hyps = [h["id"] for h in m.get("hyps") or []]
    obs = [o["id"] for o in m.get("obs") or []]
    cells = m.get("cells") or {}
    d = lambda h, o: (cells.get(h + "|" + o) or {}).get("dir") or "?"
    kind = {h["id"]: h.get("kind") for h in m.get("hyps") or []}
    pairs, unsplit, coexist, used = [], [], [], set()
    for i in range(len(hyps)):
        for j in range(i + 1, len(hyps)):
            by = [o for o in obs if d(hyps[i], o) not in ("?", "~") and d(hyps[j], o) not in ("?", "~") and d(hyps[i], o) != d(hyps[j], o)]
            pairs.append({"a": hyps[i], "b": hyps[j], "by": by})
            used |= set(by)
            if not by:
                if kind.get(hyps[i]) == "main" and kind.get(hyps[j]) == "main":
                    coexist.append({"a": hyps[i], "b": hyps[j]})   # 주 가설끼리는 함께 설 수 있다 — 못 가르는 것이 흠이 아니다(첫 실전에서 H1↔H2·H1↔H3 가 경고로 잡혔다)
                else:
                    unsplit.append({"a": hyps[i], "b": hyps[j]})
    idle = [o for o in obs if o not in used]
    blind = [h for h in hyps if all((p["a"] != h and p["b"] != h) or not p["by"] for p in pairs)] if len(hyps) > 1 else []
    m["pairs"], m["unsplit"], m["coexist"], m["idle"], m["blind"] = pairs, unsplit, coexist, idle, blind
    return m


def _apply_matrix(nt, d, res, t0):
    S = lambda v, n=400: re.sub(r"\s+", " ", str(v or "")).strip()[:n]
    hyps, seen = [], set()
    for i, h in enumerate((d.get("hyps") if isinstance(d.get("hyps"), list) else [])[:8]):
        if not isinstance(h, dict) or not S(h.get("t")):
            continue
        hid = S(h.get("id"), 6) or "H%d" % (i + 1)
        if hid in seen:
            hid = "H%d" % (len(hyps) + 1)
        seen.add(hid)
        hyps.append({"id": hid, "t": S(h["t"]), "kind": h.get("kind") if h.get("kind") in _MX_KIND_KO else "rival"})
    obs, seen = [], set()
    for i, o in enumerate((d.get("obs") if isinstance(d.get("obs"), list) else [])[:10]):
        if not isinstance(o, dict) or not S(o.get("t")):
            continue
        oid = S(o.get("id"), 6) or "O%d" % (i + 1)
        if oid in seen:
            oid = "O%d" % (len(obs) + 1)
        seen.add(oid)
        obs.append({"id": oid, "t": S(o["t"], 300), "how": S(o.get("how"), 300), "when": "예비" if "예비" in str(o.get("when") or "") else "본"})
    if len(hyps) < 2 or len(obs) < 2:
        raise RuntimeError("표가 비어 왔습니다(가설 %d · 관찰 %d)" % (len(hyps), len(obs)))
    hid_set, oid_set = {h["id"] for h in hyps}, {o["id"] for o in obs}
    cells = {}
    for c in (d.get("cells") if isinstance(d.get("cells"), list) else []):
        if not isinstance(c, dict):
            continue
        h, o = S(c.get("h"), 6), S(c.get("o"), 6)
        if h in hid_set and o in oid_set:
            dr = str(c.get("dir") or "?").strip()
            cells[h + "|" + o] = {"dir": dr if dr in ("+", "-", "0", "~", "?") else "?", "t": S(c.get("t"), 200)}
    m = {"hyps": hyps, "obs": obs, "cells": cells, "rules": [S(x, 300) for x in (d.get("rules") or [])[:8] if S(x)], "risks": [S(x, 300) for x in (d.get("risks") or [])[:6] if S(x)],
         "t": time.time(), "model": res.get("model", ""), "sec": round(time.time() - t0), "tok": res.get("tok")}
    nt["matrix"] = _matrix_check(m)


def _matrix_text(m, brief=False):
    """지시문에 붙일 [식별력 표] — brief 면 가설 목록과 못 가르는 쌍만"""
    if not m or not m.get("hyps"):
        return ""
    L = ["[식별력 표 — 가설 × 관찰 × 예측 부호(+ 커짐·있음 / - 반대 / 0 없음 / ~ 못 가름 / ? 예측 없음). 프로그램이 센 것: 못 가르는 가설 쌍 %d]" % len(m.get("unsplit") or [])]
    for h in m["hyps"]:
        L.append("%s (%s): %s" % (h["id"], _MX_KIND_KO.get(h.get("kind"), h.get("kind")), h["t"]))
    if not brief:
        for o in m.get("obs") or []:
            L.append("%s: %s — %s%s" % (o["id"], o["t"], o.get("how") or "", (" [" + o["when"] + "]") if o.get("when") else ""))
        for h in m["hyps"]:
            L.append("  %s → " % h["id"] + " · ".join("%s %s" % (o["id"], (m["cells"].get(h["id"] + "|" + o["id"]) or {}).get("dir") or "?") for o in m.get("obs") or []))
        if m.get("rules"):
            L.append("판정: " + " / ".join(m["rules"]))
    if m.get("unsplit"):
        L.append("못 가르는 쌍: " + ", ".join("%s↔%s" % (p["a"], p["b"]) for p in m["unsplit"]) + " — 어느 관찰에서도 예측 부호가 다르지 않다(설계에 가를 관찰을 더하거나, 둘을 하나로 묶거나, 못 가른다고 밝혀야 한다)")
    if m.get("coexist") and not brief:
        L.append("주 가설끼리 서로 가르지 않는 쌍(함께 설 수 있다): " + ", ".join("%s↔%s" % (p["a"], p["b"]) for p in m["coexist"]))
    if m.get("idle") and not brief:
        L.append("아무 쌍도 못 가르는 관찰: " + ", ".join(m["idle"]))
    if m.get("risks") and not brief:
        L.append("식별력을 깨는 것: " + " / ".join(m["risks"]))
    return "\n".join(L) + "\n"


# ---------- 연구의 틀(프레이밍 후보) — 같은 실험을 무엇으로 세울 것인가 (2026-10-26, 승인된 차례의 ④) ----------
# 지도교수가 설계서 앞에서 묻는 것: 이 연구는 기전을 밝히는 연구인가, 측정법을 세우는 연구인가, 재료·공정의 지침인가 — 틀이 정해져야 물음·공백·저널·대조군이 정해진다.
# Claude 가 2~4개를 내고(틀마다 물음·주장·공백·가장 가까운 연구·다른 점·설계가 더 갖출 것·저널·약점·맞음) 연구자가 하나를 고른다(op framing_pick). 고른 틀은 설계서·서론 구조·반론·식별력 표·원고 초안 물음에 [택한 프레이밍] 으로.
_FRAMING_RULE = (
    "아래 주제·토의·카드를 놓고, 이 연구를 세울 수 있는 틀(프레이밍)을 2~4개 낸다 — 같은 실험이라도 무엇을 주장으로 세우느냐에 따라 물음·공백·저널·꼭 필요한 대조군이 달라진다. 지도교수가 설계서 앞에서 묻는 것이다. "
    "kind: mechanism 기전을 밝힘 · method 측정·관찰법을 세움 · material 재료·공정의 거동과 지침 · model 모델·이론의 검증 · scale 크기·척도 효과 · application 적용·설계 지침. 서로 다른 kind 로 2~4개, 억지로 채우지 않는다. "
    "틀마다: name 짧은 이름(제목처럼) · question 연구 물음 한 문장 · claim 결과가 나오면 세울 주장 한 문장(검증 가능한 꼴) · gap 이 틀이 메우는 공백 — 서재 논문으로(cites) · near 가장 가까운 선행 연구 1~3편('첫 저자 연도', [유사 연구의 설계]·서재에서) · "
    "differs 그 연구와 다른 점 한 문장(없으면 '다르지 않다' 고 적는다 — 심사위원이 새로움을 물을 것이다) · needs 이 틀이 설계에 더 요구하는 것 1~3개(대조군·측정·시편·분해능) · journal 어울리는 저널 약어와 그 독자가 묻는 것 · risk 이 틀의 가장 큰 약점 한 문장 · "
    "fit high|mid|low 토의·[우리 연구실]·서재로 보아 이 연구가 그 틀을 감당할 수 있는가. "
    "서재의 논문은 '(첫 저자 연도)' 로, 지어낸 수치·인용은 쓰지 않는다. [공백 검증] 에 '이미 됨' 으로 나온 공백을 틀의 gap 으로 쓰지 않는다. recommend 는 하나, why 는 왜 그것인가 2~3문장(다른 틀을 접는 까닭도).\n"
    "JSON 으로만 답하라(한국어, 마크다운 없이): {\"framings\": [{\"id\": \"F1\", \"name\": \"…\", \"kind\": \"mechanism|method|material|model|scale|application\", \"question\": \"…\", \"claim\": \"…\", \"gap\": \"…\", \"cites\": [\"첫 저자 연도\"], "
    "\"near\": [\"첫 저자 연도\"], \"differs\": \"…\", \"needs\": [\"…\"], \"journal\": \"…\", \"risk\": \"…\", \"fit\": \"high|mid|low\"}], \"recommend\": \"F1\", \"why\": \"…\"}")

_FR_KIND_KO = {"mechanism": "기전", "method": "측정·관찰법", "material": "재료·공정", "model": "모델 검증", "scale": "척도 효과", "application": "적용 지침"}


def _framing_prompt(nt):
    out = "[이 주제] %s (%s)\n" % (nt.get("title") or "", STATUS_KO.get(nt.get("status"), "아이디어"))
    seed = nt.get("seed") or {}
    if seed.get("text"):
        out += "[출발점]\n" + seed["text"][:3000] + "\n"
    ct = _card_text(nt.get("card"))
    if ct:
        out += "\n[주제 카드]\n" + ct + "\n"
    if nt.get("plan"):
        out += "\n[실험 설계서 — 이미 있다; 틀을 고르면 다시 만든다]\n" + _plan_text(nt["plan"], cap=1500) + "\n"
    ans = _answered_text(nt)
    if ans:
        out += "\n[답한 반론]\n" + ans + "\n"
    dz, _items = _designs(nt, k=8)   # 가장 가까운 연구와 다른 점은 실제 논문의 설계로 가른다
    if dz:
        out += "\n" + dz
    gc = _gapcheck_text(nt.get("gapcheck"))
    if gc:
        out += "\n" + gc
    fr = nt.get("framing") or {}
    if fr.get("items"):
        out += "\n[전에 낸 틀 — 연구자가 %s]\n" % (("'%s' 를 골랐다: 그 틀은 지키되 다듬고, 나머지는 다시 생각한다" % fr["pick"]) if fr.get("pick") else "아직 고르지 않았다") + _framing_text(fr, all_items=True) + "\n"
    req = str((nt.get("framing_opts") or {}).get("req") or "").strip()
    if req:
        out += "\n[연구자의 요청]\n" + req + "\n"
    out += "\n[토의]\n" + _thread_text(nt, n=16, cap=3000) + "\n\n" + _FRAMING_RULE
    return out


def _apply_framing(nt, d, res, t0):
    S = lambda v, n=400: re.sub(r"\s+", " ", str(v or "")).strip()[:n]
    cites_of = lambda v, n: [{"key": S(c, 60), "file": _cite_file(S(c, 60))} for c in (v if isinstance(v, list) else [])[:n] if S(c)]
    items, seen = [], set()
    for i, f in enumerate((d.get("framings") if isinstance(d.get("framings"), list) else [])[:5]):
        if not isinstance(f, dict) or not S(f.get("question")):
            continue
        fid = S(f.get("id"), 5)
        if fid in seen or not re.match(r"^F\d$", fid):
            fid = "F%d" % (len(items) + 1)
        seen.add(fid)
        items.append({"id": fid, "name": S(f.get("name"), 80), "kind": f.get("kind") if f.get("kind") in _FR_KIND_KO else "mechanism", "question": S(f.get("question")), "claim": S(f.get("claim")),
                      "gap": S(f.get("gap"), 600), "cites": cites_of(f.get("cites"), 8), "near": cites_of(f.get("near"), 4), "differs": S(f.get("differs")),
                      "needs": [S(x, 200) for x in (f.get("needs") if isinstance(f.get("needs"), list) else [])[:4] if S(x)],
                      "journal": S(f.get("journal"), 200), "risk": S(f.get("risk")), "fit": f.get("fit") if f.get("fit") in ("high", "mid", "low") else "mid"})
    if len(items) < 2:
        raise RuntimeError("틀이 %d개만 왔습니다" % len(items))
    ids = {x["id"] for x in items}
    rec = S(d.get("recommend"), 5)
    if rec not in ids:
        rec = items[0]["id"]
    old = nt.get("framing") or {}
    pick = old.get("pick") if old.get("pick") in ids else ""   # 다시 뽑아도 고른 번호가 남아 있으면 지킨다(지시문이 그 틀을 지키라고 한다)
    nt["framing"] = {"items": items, "recommend": rec, "why": S(d.get("why"), 800), "pick": pick, "pick_t": old.get("pick_t") if pick else None,
                     "t": time.time(), "model": res.get("model", ""), "sec": round(time.time() - t0), "tok": res.get("tok")}


def framing_pick(nid, body):
    """연구자가 틀을 고른다(fid 가 비면 푼다) — 그 뒤의 설계서·서론 구조·반론이 그 틀을 따른다"""
    with _LOCK:
        nt = _load(nid)
        if not nt or not (nt.get("framing") or {}).get("items"):
            return {"error": "프레이밍 후보가 없습니다"}
        fid = str(body.get("fid") or "").strip()
        if fid and fid not in {x["id"] for x in nt["framing"]["items"]}:
            return {"error": "그 틀을 찾지 못했습니다"}
        nt["framing"]["pick"] = fid
        nt["framing"]["pick_t"] = time.time() if fid else None
        _save(nt)
        return {"note": nt}


def _framing_text(fr, brief=False, all_items=False):
    """지시문에 붙일 [택한 프레이밍] — 골랐으면 그 틀을 자세히, 안 골랐으면 후보 목록(추천 표시). all_items 는 다시 뽑을 때·md 에"""
    if not fr or not fr.get("items"):
        return ""
    by = {x["id"]: x for x in fr["items"]}
    pick = by.get(fr.get("pick"))

    def full(f):
        L = ["%s %s (%s · 맞음 %s)" % (f["id"], f.get("name") or "", _FR_KIND_KO.get(f.get("kind"), f.get("kind")), f.get("fit")), "물음: " + (f.get("question") or ""), "주장: " + (f.get("claim") or "")]
        if f.get("gap"):
            L.append("공백: " + f["gap"] + ((" (" + ", ".join(c["key"] for c in f.get("cites") or []) + ")") if f.get("cites") else ""))
        if f.get("near"):
            L.append("가장 가까운 연구: " + ", ".join(c["key"] for c in f["near"]) + ((" — 다른 점: " + f["differs"]) if f.get("differs") else ""))
        if f.get("needs"):
            L.append("설계가 더 갖춰야 할 것: " + " / ".join(f["needs"]))
        if f.get("journal"):
            L.append("저널·독자: " + f["journal"])
        if f.get("risk"):
            L.append("약점: " + f["risk"])
        return "\n".join(L)

    line = lambda f: "%s %s (%s): %s" % (f["id"], f.get("name") or "", _FR_KIND_KO.get(f.get("kind"), f.get("kind")), f.get("question") or "")
    if pick and not all_items:
        return "[택한 프레이밍 — 연구자가 고른 틀. 설계서의 물음·배경·저널, 서론의 공백·목적, 가설과 주장은 이 틀을 따른다]\n" + (line(pick) if brief else full(pick)) + "\n"
    if all_items:
        head = "[프레이밍 후보%s]" % ((" — 연구자가 고른 것 " + fr["pick"]) if fr.get("pick") else "")
    else:
        head = "[프레이밍 후보 — 연구자가 아직 고르지 않았다; 추천 %s. 추천 틀을 따르되 '틀을 고를 것' 을 연구자가 정할 것에 적는다]" % (fr.get("recommend") or "")
    body = "\n".join((line(f) if brief else full(f)) for f in fr["items"])
    return head + "\n" + body + (("\n추천 까닭: " + fr["why"]) if fr.get("why") and not brief else "") + "\n"


# ---------- 약속 ↔ 답 지도 — 원고의 논증 모형(ms.argument_build)을 노트의 가짜 원고에 (2026-10-26) ----------
_ARGMAP_NOTE = ("\n[주의] 위 원고는 실험 전의 연구 계획(연구 노트의 서론 구조·설계서·식별력 표)을 원고의 꼴로 늘어놓은 것이다 — 결과·주장은 '예상' 이다. 그래도 같은 틀로 본다: "
                "서론(구조)이 세운 공백·목적 문장(약속)마다 예상 결과·판정 규칙이 답하는지, 답이 없는 약속은 unanswered 로, 서론의 공백과 기여(예상)가 맞물리지 않으면 gap_mismatch 로, 예상 결과가 받치지 않는 주장은 unsupported 로. "
                "수치는 없는 것이 정상이니 number 문제는 세우지 않는다. results 는 '예상 결과·판정' 절의 항목(예상), claims 는 설계서가 결과가 나오면 세우겠다는 주장이다.\n")


def _note_doc(nt):
    """연구 노트를 원고의 꼴로 — 논증 모형 기계가 그대로 읽게. 서론 = 서론 구조(쓴 문단이 있으면 그 글, 없으면 요지와 할 말) · 방법 = 설계서 재료·장비·설계 · 결과(예상) = 예상 결과 + 식별력 표 · 논의 = 해석 · 결론(예상) = 판정 규칙"""
    plan = nt.get("plan") or {}
    sec = {s.get("k"): s.get("t") or "" for s in plan.get("sections") or []}
    card = nt.get("card") or {}
    intro = nt.get("intro") or {}
    L = []
    for i, q in enumerate(intro.get("paras") or []):
        dr = (q.get("draft") or {}).get("text")
        if dr:
            L.append(dr)
        else:
            L.append("(%d문단 · 역할 %s · 요지: %s)\n" % (i + 1, q.get("role") or "", q.get("gist") or "") + " ".join(
                (pt.get("t") or "") + ((" (" + ", ".join(c.get("key") for c in pt.get("cites") or []) + ")") if pt.get("cites") else "") for pt in q.get("points") or []))
    m = nt.get("matrix") or {}
    results = (sec.get("expected") or "")
    if m.get("hyps"):
        results += "\n\n(식별력 표의 예측) " + _matrix_text(m)
    concl = ("판정 규칙: " + " / ".join(m.get("rules") or [])) if m.get("rules") else ""
    for k in ("expected",):
        if card.get(k):
            concl += ("\n" if concl else "") + "카드의 예상 결과: " + (" / ".join(str(x) for x in card[k]) if isinstance(card[k], list) else str(card[k]))
    out = [("1. Introduction", "\n\n".join(L)), ("2. Materials and methods", "\n\n".join(x for x in (sec.get("material"), sec.get("setup"), sec.get("design")) if x)),
           ("3. Results (expected)", results), ("4. Discussion (planned analysis)", sec.get("analysis") or ""), ("5. Conclusions (expected)", concl)]
    outline = [{"id": "n%d" % (i + 1), "level": 1, "heading": h, "draft": t, "claim": ""} for i, (h, t) in enumerate(out)]
    q = card.get("question") or sec.get("question") or ""
    hyp = card.get("hypothesis") or ""
    abstract = (" ".join(str(x) for x in q) if isinstance(q, list) else str(q)) + ("\n" + (" ".join(str(x) for x in hyp) if isinstance(hyp, list) else str(hyp)) if hyp else "")
    return {"id": "nt:" + str(nt.get("id") or ""), "title": nt.get("title") or "", "front": {"abstract": abstract[:3000]}, "outline": outline, "refs_text": [], "sources": [], "source_dirs": [], "meta": {}}


def _argmap_job(nid):
    nt = _load(nid)
    if not nt:
        return
    eff = (nt.get("argmap_opts") or {}).get("effort") or "xhigh"
    t0 = time.time()
    arg, err = None, ""
    try:
        doc = _note_doc(nt)
        if not any(n.get("draft") for n in doc["outline"]):
            err = "서론 구조·설계서가 없어 지도를 그릴 글이 없습니다"
        else:
            r = ms.argument_build(doc, {"effort": eff if eff in getattr(ms, "_SEL_EFFORT", {}) else "xhigh"}, save=False, extra=_ARGMAP_NOTE)
            if isinstance(r, dict) and r.get("error"):
                err = r["error"]
            elif not isinstance(r, dict):
                err = "지도를 받지 못했습니다"
            else:
                arg = r
    except Exception as e:
        err = "논증 모형 실패: " + str(e)[:200]
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return
        nt.pop("argmap_pending", None)
        if err:
            nt["argmap_error"] = err
        else:
            nt.pop("argmap_error", None)
            arg["sec"] = round(time.time() - t0)
            nt["argmap"] = arg
        _save(nt)


def _argmap_text(a, cap=3500):
    """지시문에 붙일 [약속 ↔ 답 지도] — 약속마다 답하는 주장, 답 없는 약속·받침 없는 주장·문제"""
    if not a or not (a.get("promises") or a.get("issues")):
        return ""
    L = ["[약속 ↔ 답 지도 — 서론 구조의 공백·목적(약속)과 설계서의 예상 결과·주장을 논증 모형으로 맞댄 것(계획 단계라 결과는 예상). 답 없는 약속은 서론에서 빼거나 설계에 답을 더한다]"]
    for pm in a.get("promises") or []:
        L.append("- 약속: %s → %s" % (pm.get("t", "")[:160], (", ".join(pm.get("answered_by") or []) or "답 없음")))
    weak = [c for c in a.get("claims") or [] if c.get("strength") == "weak" or not c.get("results")]
    if weak:
        L.append("받침이 약한 주장: " + " / ".join("%s %s" % (c["id"], c["t"][:100]) for c in weak[:5]))
    for x in (a.get("issues") or [])[:8]:
        L.append("- 문제(%s): %s" % (ms._ARG_KINDS.get(x.get("kind"), x.get("kind")), x.get("note", "")[:200]))
    out = "\n".join(L) + "\n"
    return out if len(out) <= cap else out[:cap] + "\n(…)\n"


# ---------- 공백 검증 — 서론 구조의 공백 문장을 세상의 문헌에서 (2026-10-26, 사용자 'ㄱㄱ': 서론 구조를 뽑으면 자동으로, 탐색 둘까지가 아니라 공백 문장마다 하나씩·넷까지) ----------
# 서재는 세상의 일부다. 공백 문장("…을 잰 연구가 없다")마다 탐색의 검색 계획(Claude Sonnet) → OpenAlex 불리언(관련도 40 + 2022년 뒤 최신 20) → 서재 보유 표시 → Claude(노트의 모델)가 초록만으로 '이미 한 논문 / 일부 / 열림' 을 가른다.
_GAP_RULE = (
    "아래는 한 연구 계획의 서론이 세운 공백 문장들과, 공백마다 학술 DB 에서 찾아온 후보 논문(제목·연도·저널·초록)이다. 공백마다 후보 가운데 **그 공백이 '없다'고 한 바로 그것을 이미 한 논문**이 있는지 가려라 — 초록에 적힌 것만으로 판단하고, 제목만 비슷한 것·주제가 겹칠 뿐인 것은 넣지 않는다. "
    "level: done = 그 공백이 없다고 한 그 측정·그 조건·그 재료를 이미 했다 / partial = 일부만(다른 재료·다른 척도·간접 측정·모사만). verdict: done 이 하나라도 있으면 done, partial 만 있으면 partial, 없으면 open. "
    "note 는 연구자가 공백 문장을 어떻게 고쳐 말해야 하는가 한 줄(한국어; open 이면 빈 문자열). papers 의 how 는 그 논문이 무엇을 이미 했나 한 줄(한국어, 초록에 있는 말로).\n"
    "JSON 으로만: {\"claims\": [{\"i\": 1, \"verdict\": \"done|partial|open\", \"note\": \"…\", \"papers\": [{\"k\": 3, \"level\": \"done|partial\", \"how\": \"…\"}]}]}")

_GAP_VERDICT_KO = {"done": "이미 됨", "partial": "일부 됨", "open": "열림", "fail": "검색 실패"}


def _gap_claims(nt, cap=4):
    """서론 구조의 공백 문단에서 검증할 주장 — 문단마다 points(구체적인 것) 둘까지, 없으면 요지. '서재 밖:' 머리말은 뗀다"""
    out = []
    for i, q in enumerate((nt.get("intro") or {}).get("paras") or []):
        if q.get("role") != "공백":
            continue
        pts = [re.sub(r"^서재 밖:\s*", "", str(pt.get("t") or "")).strip() for pt in q.get("points") or []]
        pts = [t for t in pts if len(t) > 15][:2] or ([str(q.get("gist") or "").strip()] if q.get("gist") else [])
        for t in pts:
            out.append({"para": i + 1, "t": t[:400]})
    return out[:cap]


def _gapcheck_job(nid):
    nt = _load(nid)
    if not nt:
        return
    eff_key = (nt.get("gapcheck_opts") or {}).get("effort") or "xhigh"
    model, eff = _EFFORT.get(eff_key, _EFFORT["xhigh"])
    t0 = time.time()
    claims = _gap_claims(nt)
    err, result, tok = "", None, None
    try:
        if not claims:
            err = "서론 구조에 공백 문단이 없습니다"
        elif not (cfg.get("plan_search") and cfg.get("build_boolean") and cfg.get("openalex_boolean")):
            err = "이 서버는 탐색 기계를 주지 않았습니다"
        else:
            rows = []
            for c in claims:
                row = dict(c, expr="", n=0, papers=[], verdict="fail", note="")
                try:
                    plan = cfg["plan_search"](c["t"] + " — 이것을 실제로 재거나 한 논문을 찾는다") or {}
                    fallback = lambda p: len(p.get("concepts") or []) == 1 and (p["concepts"][0].get("terms") or [None])[0] and len(str(p["concepts"][0]["terms"][0])) > 60   # 계획을 못 받아 원문 그대로 한 개념이 된 것
                    if fallback(plan):
                        plan = cfg["plan_search"]("다음 공백 문장이 '없다·안 됐다' 고 하는 측정·관찰·조건을 찾을 영어 검색어(개념 2~3개, 필수): " + c["t"][:300]) or {}
                    if fallback(plan):
                        row["err"] = "검색 계획을 받지 못했다"
                        rows.append(row)
                        continue
                    cs = plan.get("concepts") or []
                    req = [x for x in cs if x.get("required")]
                    expr = cfg["build_boolean"](req if len(req) >= 2 else cs, plan.get("exclude"))   # 필수 개념이 하나뿐이면 전체 개념으로 — 하나짜리 OR 묶음은 13만 편에 걸렸다
                    row["expr"], row["plan"] = expr, plan.get("intent", "")
                    if expr:
                        found, cnt = cfg["openalex_boolean"](expr, year="2008", per_page=40)
                        if cnt == 0 and len(cs) > 2:   # 개념을 다 AND 하니 0편(따옴표 구절이 많을 때) → 앞의 두 개념으로 넓혀 한 번 더 (2026-10-26 재시험에서 5문단 하나가 0편)
                            expr2 = cfg["build_boolean"](cs[:2], plan.get("exclude"))
                            if expr2 and expr2 != expr:
                                found, cnt = cfg["openalex_boolean"](expr2, year="2008", per_page=40)
                                if cnt:
                                    expr = expr2
                                    row["expr"] = expr
                        recent, _c = cfg["openalex_boolean"](expr, year="2022", per_page=20, sort="publication_date:desc")
                        seen, merged = set(), []
                        for it in list(found) + list(recent):
                            if it.get("id") and it["id"] not in seen and it.get("title"):
                                seen.add(it["id"])
                                merged.append(it)
                        row["n"] = cnt
                        row["papers"] = [{"id": it["id"], "title": it.get("title", "")[:200], "year": it.get("year") or 0, "venue": (it.get("venue") or "")[:60], "doi": it.get("doi") or "",
                                          "abstract": (it.get("abstract") or "")[:450], "rel": it.get("rel") or 0} for it in merged[:18]]
                        row["verdict"] = "open"
                except Exception as e:
                    row["err"] = str(e)[:160]
                rows.append(row)
            try:
                om = cfg["owned_map"]([{"id": p["id"], "title": p["title"]} for r in rows for p in r["papers"]]) if cfg.get("owned_map") else {}
            except Exception:
                om = {}
            for r in rows:
                for p in r["papers"]:
                    p["owned"] = om.get(p["id"], "")
            # Claude 가 가른다 — 배경 없이(시스템 프롬프트 없음: 공백 문장과 초록만 보면 된다)
            L = ["[연구 계획의 서론이 세운 공백 문장들]"]
            for i, r in enumerate(rows):
                L.append("%d) %s" % (i + 1, r["t"]))
            for i, r in enumerate(rows):
                if not r["papers"]:
                    continue
                L.append("\n[공백 %d 의 후보 논문 — 검색식: %s · 전체 %d편 가운데 %d편]" % (i + 1, r["expr"][:300], r["n"], len(r["papers"])))
                for k, p in enumerate(r["papers"]):
                    L.append("%d) %s (%s, %s)%s — %s" % (k + 1, p["title"], p["year"], p["venue"], " ★서재" if p.get("owned") else "", p["abstract"] or "(초록 없음)"))
            run = cfg.get("claude_run")
            res = run("\n".join(L) + "\n\n" + _GAP_RULE, timeout=900, model=model, effort=eff, tools="") if (run and any(r["papers"] for r in rows)) else None
            d = _json_of((res or {}).get("text")) if res else None
            tok = (res or {}).get("tok")
            if isinstance(d, dict) and isinstance(d.get("claims"), list):
                for x in d["claims"]:
                    try:
                        i = int(x.get("i") or 0) - 1
                    except Exception:
                        continue
                    if not 0 <= i < len(rows) or not rows[i]["papers"]:
                        continue
                    r = rows[i]
                    r["verdict"] = x.get("verdict") if x.get("verdict") in ("done", "partial", "open") else r["verdict"]
                    r["note"] = re.sub(r"\s+", " ", str(x.get("note") or "")).strip()[:300]
                    for y in (x.get("papers") if isinstance(x.get("papers"), list) else [])[:6]:
                        try:
                            k = int(y.get("k") or 0) - 1
                        except Exception:
                            continue
                        if 0 <= k < len(r["papers"]):
                            r["papers"][k]["level"] = y.get("level") if y.get("level") in ("done", "partial") else "partial"
                            r["papers"][k]["how"] = re.sub(r"\s+", " ", str(y.get("how") or "")).strip()[:300]
                    if r["verdict"] == "open" and any(p.get("level") == "done" for p in r["papers"]):
                        r["verdict"] = "done"
            elif any(r["papers"] for r in rows):
                err = "후보는 찾았지만 판정을 받지 못했습니다" + _why()
            for r in rows:   # 화면·지시문에는 가린 논문만 — 초록은 뺀다
                r["papers"] = [{k: v for k, v in p.items() if k != "abstract"} for p in r["papers"] if p.get("level")]
            result = {"claims": rows, "t": time.time(), "sec": round(time.time() - t0), "tok": tok, "model": (res or {}).get("model") or model}
    except Exception as e:
        err = "공백 검증 실패: " + str(e)[:200]
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return
        nt.pop("gapcheck_pending", None)
        if err and not result:
            nt["gapcheck_error"] = err
        else:
            nt.pop("gapcheck_error", None)
            if err:
                result["warn"] = err
            nt["gapcheck"] = result
        _save(nt)


def _gapcheck_text(g, cap=3000):
    """지시문에 붙일 [공백 검증] — 공백마다 판정과 이미 한 논문"""
    if not g or not g.get("claims"):
        return ""
    L = ["[공백 검증 — 서론 구조의 공백 문장을 학술 DB(OpenAlex)에서 찾아 Claude 가 초록으로 가린 것. '이미 됨' 인 공백은 그대로 쓰지 말고 그 논문을 선행연구에 넣어 공백을 좁힌다; '열림' 은 아직 세상에도 없는 것으로 보되 단정은 피한다]"]
    for r in g["claims"]:
        L.append("- (%s) %s%s" % (_GAP_VERDICT_KO.get(r.get("verdict"), r.get("verdict")), r["t"][:200], (" → " + r["note"]) if r.get("note") else ""))
        for p in (r.get("papers") or [])[:4]:
            L.append("    · %s (%s, %s)%s — %s" % (p.get("title", "")[:120], p.get("year"), p.get("venue"), " ★서재" if p.get("owned") else "", p.get("how", "")))
    out = "\n".join(L) + "\n"
    return out if len(out) <= cap else out[:cap] + "\n(…)\n"


_INTRO_MODELS = {}   # 주제 id → 본보기 논문 이름들 (물음을 만들 때 적고, 결과를 담을 때 꺼낸다 — _bg_job 이 주제 파일을 다시 읽어 nt 에 적어 두면 사라진다)
_JABBR = re.compile(r"\b(IJMTM|JMPT|CIRPA|CIRP|PE|JMP|IJEM|JMSE|IJPEM|IJPEMGT|JMST|AMT|WEAR|TI|PCIRP|MD|JAC|JMPS|IJMS|NM)\b")


def _intro_journals(nt):
    """서론 본보기를 고를 저널 약어 — 설계서의 '저널과 독자' 칸 → 카드의 저널 → 기본 IJMTM·JMPT"""
    texts = []
    for sec in (nt.get("plan") or {}).get("sections") or []:
        if sec.get("k") == "venue" and sec.get("t"):
            texts.append(sec["t"])
    texts += [str(x) for x in (nt.get("card") or {}).get("journals") or []]
    found = []
    for t in texts:
        for m in _JABBR.finditer(t):
            a = "CIRPA" if m.group(1) == "CIRP" else m.group(1)
            if a not in found:
                found.append(a)
    return found[:3] or ["IJMTM", "JMPT"]


def _intro_topic(nt):
    """본보기를 고를 주제 낱말 — 제목·카드·설계서·서론 구조에 든 영어 낱말(원고의 _topic_words 와 같은 잣대: 자주 나온 열 개)"""
    parts = [nt.get("title") or ""]
    card = nt.get("card") or {}
    for k in ("question", "hypothesis", "papers"):
        v = card.get(k)
        parts.append(" ".join(str(x) for x in v) if isinstance(v, list) else str(v or ""))
    for sec in (nt.get("plan") or {}).get("sections") or []:
        if sec.get("k") in ("background", "question", "venue") and sec.get("t"):
            parts.append(sec["t"])
    skip = {w.lower() for w in _JABBR.pattern.strip("\\b()").split("|")}   # 저널 약어
    for q in (nt.get("intro") or {}).get("paras") or []:
        parts.append(q.get("gist") or "")
        parts += [pt.get("t") or "" for pt in q.get("points") or []]
        for pt in q.get("points") or []:
            for c in pt.get("cites") or []:
                skip |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(c.get("key") or ""))}   # 인용 열쇠의 저자 성
    for sec in (nt.get("plan") or {}).get("sections") or []:
        for c in sec.get("cites") or []:
            skip |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", str(c.get("key") or ""))}
    for x in card.get("papers") or []:
        skip |= {w.lower() for w in re.findall(r"^[A-Za-z][A-Za-z'\-]+", str(x).strip())}
    import collections
    words = [w for w in re.findall(r"[a-z][a-z\-]{3,}", " ".join(parts)[:20000].lower()) if w not in ms._STOP and w not in skip]
    return [w for w, _ in collections.Counter(words).most_common(10)]


def _intro_cited(nt):
    """카드·설계서·서론 구조가 든 서재 논문 — 본보기 고르기에서 앞으로(원고의 '인용한 논문 +3' 자리)"""
    files = set()
    for x in (nt.get("card") or {}).get("papers") or []:
        f = _cite_file(str(x))
        if f:
            files.add(f)
    for sec in (nt.get("plan") or {}).get("sections") or []:
        files |= {c["file"] for c in sec.get("cites") or [] if c.get("file")}
    for q in (nt.get("intro") or {}).get("paras") or []:
        for pt in q.get("points") or []:
            files |= {c["file"] for c in pt.get("cites") or [] if c.get("file")}
    return files


def _intro_models(nt, n=3, diag=None):
    """[본보기] = ms.intro_skeletons — 원고의 구성 보기와 같은 고르기(저널 · 주제 낱말 · 최근 순 · 이미 든 논문 앞으로)·문단 역할·요지·대목. → (글, [논문 이름들]). diag(dict) 를 주면 고르는 과정을 적는다"""
    abbrs = _intro_journals(nt)
    try:
        items = ms.intro_skeletons(abbrs, topic=_intro_topic(nt), cited=_intro_cited(nt), n=n, diag=diag)
    except Exception as e:
        if diag is not None:
            diag["error"] = str(e)[:200]
        items = []
    out, names = [], []
    for it in items:
        names.append(it["name"])
        rows = ["%s — 서론 문단 %d개 · 차례: %s" % (it["name"], len(it["paras"]), it["pattern"])]
        for g in it.get("groups") or []:
            rows.append("  대목 [문단 %s–%s] %s: %s" % (g.get("a"), g.get("b"), g.get("head") or "", g.get("what") or ""))
        for j, q in enumerate(it["paras"]):
            rows.append("  %d. %s%s (%d낱말)" % (j + 1, q.get("role") or "?", (" — " + str(q["gist"])[:90]) if q.get("gist") else "", q.get("words") or 0))
        out.append("\n".join(rows))
    return "\n".join(out), names


def _intro_prompt(nt):
    out = "[이 주제] %s (%s)\n" % (nt.get("title") or "", STATUS_KO.get(nt.get("status"), "아이디어"))
    ct = _card_text(nt.get("card"))
    if ct:
        out += "\n[주제 카드]\n" + ct + "\n"
    if nt.get("plan"):
        out += "\n[실험 설계서]\n" + _plan_text(nt["plan"], cap=1500) + "\n"
    ans = _answered_text(nt)
    if ans:
        out += "\n[답한 반론 — 공백·목적 문단에 반영한다]\n" + ans + "\n"
    fr = _framing_text(nt.get("framing"))
    if fr:
        out += "\n" + fr
    out += "\n[토의]\n" + _thread_text(nt, n=12, cap=2000) + "\n"
    models, names = _intro_models(nt)
    _INTRO_MODELS[nt.get("id")] = names   # _apply_intro 가 기록에 옮긴다
    if models:
        out += "\n[본보기 — 서재의 그 저널 논문(주제가 가까운 것부터) 서론이 실제로 짜인 차례: 대목(같은 것을 말하는 이어진 문단들)과 문단마다 역할 — 요지]\n" + models + "\n"
    dz, _items = _designs(nt, k=6, brief=True)   # 선행연구·공백 문단의 재료 — 그 논문들이 실제로 한 것과 멈춘 곳 (2026-10-26)
    if dz:
        out += "\n" + dz
    mx = _matrix_text(nt.get("matrix"), brief=True)
    if mx:
        out += "\n" + mx + "(목적 문단은 이 가설들 가운데 무엇을 어떻게 가르는지를 말한다)\n"
    am = _argmap_text(nt.get("argmap"))
    if am:
        out += "\n" + am + "(답 없는 약속은 공백 문단에서 빼거나 설계가 답하는 것으로 좁힌다)\n"
    gc = _gapcheck_text(nt.get("gapcheck"))
    if gc:
        out += "\n" + gc
    shape = ms.intro_shape_text(ms.intro_shape(tuple(_intro_journals(nt))) or ms.intro_shape())   # 그 저널 서론의 크기 — 서재에서 잰 것(내가 정한 '4~7개' 대신)
    if shape:
        out += "\n" + shape
    kn = (cfg.get("knowledge") or (lambda j, k: ""))(_intro_journals(nt), "intro")   # 서재 학습의 바탕 — 그 저널의 서론은 이렇게 쓴다 (주석이 8편 넘게 쌓인 뒤에 생긴다)
    if kn:
        out += "\n" + kn
    req = str((nt.get("intro_opts") or {}).get("req") or "").strip()
    if req:
        out += "\n[연구자의 요청]\n" + req + "\n"
    if nt.get("intro") and nt.get("intro_edited"):
        out += "\n[연구자가 손으로 고친 서론 구조 — 이것을 바탕으로 고친다]\n" + _intro_text(nt["intro"]) + "\n"
    return out + "\n" + _INTRO_RULE


def _intro_text(intro, brief=False):
    out = []
    if intro.get("flow") and not brief:
        out.append("흐름: " + intro["flow"])
    for i, p in enumerate(intro.get("paras") or []):
        line = "%d문단 [%s] %s%s" % (i + 1, p.get("role") or "", p.get("gist") or "", " (글을 썼음)" if (p.get("draft") or {}).get("text") else "")
        if not brief:
            for pt in p.get("points") or []:
                line += "\n  - %s%s" % (pt.get("t") or "", (" (" + ", ".join(c.get("key") for c in pt.get("cites") or []) + ")") if pt.get("cites") else "")
        out.append(line)
    if intro.get("gaps") and not brief:
        out.append("채워야 할 근거: " + " / ".join(str(x) for x in intro["gaps"][:8]))
    return "\n".join(out)


# ---------- 서론 문단 쓰기 — 원고의 문단 쓰기(서재에서 뽑기 → Stylus 로 쓰기 → 근거 대조 → 고쳐 쓰기)를 그대로 쓴다 (2026-10-25, 사용자: '다 유기적으로 해야 해') ----------
def _intro_doc(nt):
    """연구 노트의 서론 구조를 원고 꼴로 — manuscript.lib_use 는 원고를 받는다. 「1. Introduction」 절 하나: 쓴 문단은 그 글, 아직 안 쓴 문단은 '[k문단 자리 — 역할: 요지]'(앞뒤를 알고 겹치지 않게 쓰게).
    참고문헌 목록은 intro.refs — 문단을 쓸수록 늘고 번호가 이어진다. 원고로 넘길 때(_apply_draft) 그대로 간다."""
    intro = nt.get("intro") or {}
    parts = []
    for i, q in enumerate(intro.get("paras") or []):
        d = q.get("draft") or {}
        parts.append(d["text"] if d.get("text") else "[%d문단 자리 — %s: %s]" % (i + 1, q.get("role") or "", q.get("gist") or ""))
    jr = _intro_journals(nt)
    return {"id": "nt_intro_" + str(nt.get("id")), "title": nt.get("title") or "", "front": {"title": "", "abstract": "", "keywords": []},
            "outline": [{"id": "intro", "level": 1, "heading": "1. Introduction", "claim": intro.get("flow") or "", "cards": [], "draft": "", "draft_en": "\n\n".join(parts), "status": "todo"}],
            "refs_text": list(intro.get("refs") or []), "meta": {"journal": jr[0] if jr else ""}, "sources": [], "source_dirs": [], "figures": []}


def _intro_order(nt, i):
    """문단 쓰기의 주문(lib_use 의 '저자의 주문' 자리, 1500자 안) — 이 문단의 역할·요지·차례로 세울 말과 받칠 논문, 앞뒤 문단의 요지, 서론의 논리"""
    intro = nt["intro"]
    paras = intro["paras"]
    q = paras[i]
    lines = ["서론(Introduction)의 %d문단(모두 %d문단) — 역할: %s. 이 글은 「1. Introduction」 절의 '[%d문단 자리]' 에 들어간다. 그 앞뒤에 이미 쓴 글과 다른 문단의 자리 표시(요지)를 읽고, 겹치지 않고 이어지게 쓴다." % (i + 1, len(paras), q.get("role") or "", i + 1),
             "요지: " + (q.get("gist") or ""), "차례로 세울 말:"]
    for k, pt in enumerate((q.get("points") or [])[:6]):
        keys = [c.get("key") for c in pt.get("cites") or [] if c.get("key")]
        lines.append("%d) %s%s" % (k + 1, pt.get("t") or "", (" (" + ", ".join(keys) + ")") if keys else ""))
    if i > 0:
        lines.append("앞 문단(%s): %s" % (paras[i - 1].get("role") or "", paras[i - 1].get("gist") or ""))
    if i + 1 < len(paras):
        lines.append("다음 문단(%s): %s" % (paras[i + 1].get("role") or "", paras[i + 1].get("gist") or ""))
    if intro.get("flow"):
        lines.append("서론 전체의 논리: " + intro["flow"][:300])
    jr = _intro_journals(nt)
    lines.append("'서재 밖:' 으로 적힌 말은 서재에 근거가 없는 사실 — 그 자리에는 [@?] 를 단다. 영어로 쓴다. 저널: %s" % (jr[0] if jr else "IJMTM"))
    return "\n".join(lines)[:1500]


def intro_write_start(nid, body):
    try:
        i = int(body.get("i"))
    except (TypeError, ValueError):
        return {"error": "문단 번호가 없습니다"}
    effort = body.get("effort") if body.get("effort") in ms._SEL_EFFORT else "xhigh"
    with _LOCK:
        nt = _load(nid)
        if not nt or not nt.get("intro"):
            return {"error": "서론 구조가 없습니다 — 먼저 「서론 구조 뽑기」"}
        paras = nt["intro"].get("paras") or []
        if not 0 <= i < len(paras):
            return {"error": "그 문단이 없습니다"}
        if nt["intro"].get("writing"):
            return {"error": "이미 쓰는 중인 문단이 있습니다 — 끝난 뒤에 (참고문헌 번호가 이어지도록 한 번에 하나씩)"}
        if nt.get("intro_pending"):
            return {"error": "서론 구조를 뽑는 중입니다"}
        nt["intro"]["writing"] = {"i": i, "t": time.time(), "effort": effort, "stage": "extract"}
        paras[i].pop("draft_error", None)
        _save(nt)
    threading.Thread(target=_intro_write_job, args=(nid, i, effort), name="notes-intro-write", daemon=True).start()
    return {"ok": True}


def _intro_write_job(nid, i, effort):
    """① ms.lib_extract — 이 문단의 요지·할 말로 서재에서 근거 문장을 뽑는다(원고의 「서재에서 뽑기」와 같은 결과, _서재뽑기.json 에 남아 원고 창에도 보인다)
    ② ms.lib_use(mode write) — 가짜 원고(_intro_doc)를 놓고 Stylus 규칙·서재 논문의 분포로 쓰고 → 다른 호출이 근거와 대조 → 걸리면 고쳐 쓰고 다시 대조(원고의 문단 쓰기 그대로)
    ③ [@열쇠] → 참고문헌 번호(ms.cites_apply — 화면의 citeApply 와 같은 규칙), 새 문헌은 intro.refs 끝에."""
    t0 = time.time()
    err, use, item = "", None, None
    try:
        nt = _load(nid)
        q = ((nt or {}).get("intro") or {}).get("paras")[i]
        topic = ((q.get("gist") or "") + " — " + "; ".join(re.sub(r"^서재 밖:\s*", "", pt.get("t") or "") for pt in (q.get("points") or [])[:5]))[:600]
        item = ms.lib_extract(topic, effort)
        if not item or item.get("error"):
            raise RuntimeError((item or {}).get("error") or "서재에서 뽑지 못했습니다")
        with _LOCK:
            nt = _load(nid)
            w = ((nt or {}).get("intro") or {}).get("writing")
            if nt and w:
                w.update(stage="write", xid=item["id"])
                _save(nt)
        use = ms.lib_use(_intro_doc(nt), {"xid": item["id"], "mode": "write", "note": _intro_order(nt, i), "node": "intro", "effort": effort, "kind": "intro", "role": q.get("role")})   # kind·role → 서재 학습의 바탕·같은 자리의 실제 문단이 지시문에 붙는다
        if not use or use.get("error"):
            raise RuntimeError((use or {}).get("error") or "문단을 받지 못했습니다")
    except Exception as e:
        err = str(e)[:300]
    with _LOCK:
        nt = _load(nid)
        if not nt or not nt.get("intro"):
            return
        intro = nt["intro"]
        intro.pop("writing", None)
        paras = intro.get("paras") or []
        if not 0 <= i < len(paras):
            _save(nt)
            return
        if err:
            paras[i]["draft_error"] = err
            _save(nt)
            return
        refs = list(intro.get("refs") or [])
        text, num = ms.cites_apply(use.get("text") or "", use.get("cites") or [], refs)
        intro["refs"] = refs
        chk = use.get("check") or {}
        sents = chk.get("sentences") or []
        bad = [{"s": str(x.get("s") or "")[:240], "verdict": x.get("verdict"), "type": x.get("type") or "", "abrupt": bool(x.get("abrupt")), "why": str(x.get("why") or "")[:300]}
               for x in sents if x.get("verdict") in ("over", "none") or (x.get("verdict") == "own" and x.get("type") == "needs") or x.get("abrupt")]
        paras[i]["draft"] = {"text": text, "raw": use.get("text") or "", "xid": item["id"], "uid": use.get("id"), "t": time.time(), "model": use.get("model") or "", "sec": round(time.time() - t0), "tok": use.get("tok"),
                             "stylus": use.get("stylus") or ms.STYLUS, "answer": use.get("answer") or "", "changes": use.get("changes") or "", "rounds": use.get("rounds") or 1, "reads": chk.get("reads") or "",
                             "nsent": len(sents), "flagged": bad, "second": sum(1 for x in sents if x.get("second")), "todo": use.get("todo") or 0, "unknown": use.get("unknown") or [],
                             "flow": [str(x) for x in ((use.get("flow") or {}).get("marks") or [])[:6]], "plan": [str(m.get("move") or "")[:200] for m in use.get("plan") or []],
                             "cites": [{"key": c.get("key"), "n": num.get(c.get("key"), c.get("n") or ""), "label": c.get("label") or "", "file": c.get("file") or "", "unread": bool(c.get("unread"))} for c in use.get("cites") or []],
                             "extract": {"papers": len(item.get("papers") or []), "sents": sum(len(z.get("sents") or []) for z in item.get("papers") or []), "sec": item.get("sec")}, "steps": use.get("steps") or []}
        paras[i].pop("draft_error", None)
        _save(nt)


def intro_draft_clear(nid, body):
    try:
        i = int(body.get("i"))
    except (TypeError, ValueError):
        return {"error": "문단 번호가 없습니다"}
    with _LOCK:
        nt = _load(nid)
        paras = ((nt or {}).get("intro") or {}).get("paras") or []
        if not 0 <= i < len(paras):
            return {"error": "그 문단이 없습니다"}
        paras[i].pop("draft", None)
        paras[i].pop("draft_error", None)
        _save(nt)
        return {"note": nt}


def _apply_intro(nt, d, res, t0):
    paras_in = d.get("paras") if isinstance(d.get("paras"), list) else []
    paras = []
    for p in paras_in[:8]:
        if not isinstance(p, dict) or not str(p.get("gist") or "").strip():
            continue
        role = str(p.get("role") or "").strip()
        role = next((r for r in INTRO_ROLES if r == role or role in r or r in role), role[:12])
        pts = []
        for pt in (p.get("points") if isinstance(p.get("points"), list) else [])[:6]:
            if isinstance(pt, str):
                pt = {"t": pt}
            if not isinstance(pt, dict) or not str(pt.get("t") or "").strip():
                continue
            cites = [{"key": str(c).strip()[:60], "file": _cite_file(str(c))} for c in (pt.get("cites") if isinstance(pt.get("cites"), list) else [])[:6] if str(c).strip()]
            pts.append({"t": re.sub(r"\s+", " ", str(pt["t"])).strip()[:400], "cites": cites})
        try:
            ln = int(p.get("len") or 0)
        except (TypeError, ValueError):
            ln = 0
        paras.append({"role": role, "gist": re.sub(r"\s+", " ", str(p["gist"])).strip()[:300], "points": pts, "len": max(0, min(ln, 30))})
    if len(paras) < 3:
        raise RuntimeError("서론 구조가 비어 왔습니다")
    old = nt.get("intro") or {}
    for i, q in enumerate(paras):   # 다시 뽑아도 이미 쓴 문단은 같은 자리·같은 역할이면 지킨다 — 참고문헌 목록도 (2026-10-25)
        prev = (old.get("paras") or [])[i] if i < len(old.get("paras") or []) else None
        if prev and prev.get("draft") and prev.get("role") == q["role"]:
            q["draft"] = prev["draft"]
    nt["intro"] = {"flow": re.sub(r"\s+", " ", str(d.get("flow") or "")).strip()[:1500], "paras": paras,
                   "gaps": [str(x).strip()[:300] for x in (d.get("gaps") or [])[:10] if str(x).strip()], "refs": list(old.get("refs") or []),
                   "models": _INTRO_MODELS.pop(nt.get("id"), None) or [], "t": time.time(), "model": res.get("model", ""), "sec": round(time.time() - t0), "tok": res.get("tok")}
    nt.pop("intro_edited", None)


def _apply_plan(nt, d, res, t0):
    secs = d.get("sections") if isinstance(d.get("sections"), dict) else {}
    out = []
    for k, h in PLAN_KEYS:
        v = secs.get(k)
        if isinstance(v, str):
            v = {"t": v}
        if not isinstance(v, dict):
            v = {}
        t = re.sub(r"[ \t]+", " ", str(v.get("t") or "")).strip()[:2500]
        cites = []
        for c in (v.get("cites") if isinstance(v.get("cites"), list) else [])[:12]:
            c = str(c).strip()[:60]
            if c:
                cites.append({"key": c, "file": _cite_file(c)})
        out.append({"k": k, "h": h, "t": t, "cites": cites})
    if sum(1 for x in out if x["t"]) < 4:
        raise RuntimeError("설계서가 비어 왔습니다")
    nt["plan"] = {"sections": out, "gaps": [str(x).strip()[:300] for x in (d.get("gaps") or [])[:10] if str(x).strip()],
                  "checks": [str(x).strip()[:300] for x in (d.get("checks") or [])[:10] if str(x).strip()],
                  "feasibility": _clean_feas(d.get("feasibility")),
                  "similar": _DESIGNS.pop(nt.get("id"), None) or [],
                  "t": time.time(), "model": res.get("model", ""), "sec": round(time.time() - t0), "tok": res.get("tok")}
    nt.pop("plan_edited", None)


def _apply_review(nt, d, res, t0):
    items = d.get("items") if isinstance(d.get("items"), list) else []
    keep = [o for o in nt.get("objections") or [] if o.get("status") in ("answered", "dropped")]   # 답한 것·보류한 것은 남기고 열린 것은 새 반론으로 바꾼다
    n = 0
    for o in nt.get("objections") or []:
        m = re.match(r"^r(\d+)$", str(o.get("id") or ""))
        if m:
            n = max(n, int(m.group(1)))
    new = []
    for it in items[:14]:
        if not isinstance(it, dict) or not str(it.get("t") or "").strip():
            continue
        n += 1
        who = it.get("who") if it.get("who") in REVIEWER_KO else "editor"
        new.append({"id": "r%d" % n, "who": who, "sec": it.get("sec") if it.get("sec") in PLAN_KO else "", "sev": it.get("sev") if it.get("sev") in ("high", "mid", "low") else "mid",
                    "t": str(it["t"]).strip()[:800], "ask": str(it.get("ask") or "").strip()[:300], "status": "open", "t0": time.time()})
    if not new:
        raise RuntimeError("반론이 비어 왔습니다")
    nt["objections"] = keep + new
    nt["review_t"] = time.time()
    nt["review_model"] = res.get("model", "")
    nt["review_sec"] = round(time.time() - t0)


def _apply_draft(nt, d, res, t0):
    secs = d.get("sections") if isinstance(d.get("sections"), list) else []
    if not secs or not cfg.get("ms_new") or not cfg.get("ms_save"):
        raise RuntimeError("절 차례가 비어 왔습니다" if secs else "원고를 만들 수 없는 서버입니다")
    title_ko = re.sub(r"\s+", " ", str(d.get("title_ko") or d.get("title") or nt.get("title") or "")).strip()[:200]
    doc = cfg["ms_new"](title_ko)
    outline, n = [], 0
    for sct in secs[:8]:
        if not isinstance(sct, dict) or not str(sct.get("heading") or "").strip():
            continue
        n += 1
        outline.append({"id": "n%d" % n, "level": 1, "heading": str(sct["heading"]).strip()[:120], "claim": str(sct.get("claim") or "").strip()[:600], "cards": [], "draft": "", "draft_en": "", "status": "todo"})
        for c in (sct.get("children") if isinstance(sct.get("children"), list) else [])[:6]:
            if not isinstance(c, dict) or not str(c.get("heading") or "").strip():
                continue
            n += 1
            outline.append({"id": "n%d" % n, "level": 2, "heading": str(c["heading"]).strip()[:120], "claim": str(c.get("claim") or "").strip()[:600], "cards": [], "draft": "", "draft_en": "", "status": "todo"})
    if not outline:
        raise RuntimeError("절 차례가 비어 왔습니다")
    doc["outline"] = outline
    intro = nt.get("intro") or {}
    texts = [q["draft"]["text"] for q in intro.get("paras") or [] if (q.get("draft") or {}).get("text")]
    if texts:   # 연구 노트에서 쓴 서론 문단은 서론 절의 영문 글로, 그 참고문헌은 원고의 목록으로 — 노트 → 원고가 한 흐름이 된다 (2026-10-25)
        node = next((x for x in outline if x["level"] == 1 and re.search(r"introduc|서론", x["heading"], re.I)), outline[0])
        node["draft_en"] = "\n\n".join(texts)
        doc["refs_text"] = list(intro.get("refs") or [])
    doc["front"] = {"title": str(d.get("title") or title_ko).strip()[:300], "abstract": re.sub(r"[ \t]+", " ", str(d.get("abstract") or "")).strip()[:3000],
                    "keywords": [str(x).strip()[:40] for x in (d.get("keywords") if isinstance(d.get("keywords"), list) else [])[:8] if str(x).strip()]}
    doc.setdefault("meta", {})["journal"] = str(d.get("journal") or "").strip()[:40]
    q = next((x["t"] for x in (nt.get("plan") or {}).get("sections") or [] if x.get("k") == "question" and x.get("t")), "") or (nt.get("card") or {}).get("question") or ""
    doc.setdefault("idea", {})["question"] = str(q)[:1000]
    doc["note_id"] = nt["id"]
    cfg["ms_save"](doc)
    nt["ms"] = {"id": doc["id"], "title": doc["title"], "t": time.time(), "model": res.get("model", ""), "sec": round(time.time() - t0), "nodes": len(outline), "intro_paras": len(texts)}


def _job_key(nid, kind, t):
    return "notes:%s:%s:%d" % (nid, kind, int(t or 0))


def job_start(nid, kind, body):
    """설계서(plan) · 반론(review) · 원고 초안(draft) — 배경 스레드 하나, 상태는 <kind>_pending·<kind>_error"""
    if kind not in _JOBKINDS:
        return {"error": "unknown"}
    effort = body.get("effort") if body.get("effort") in _EFFORT else "xhigh"
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return {"error": "주제를 찾지 못했습니다"}
        if nt.get(kind + "_pending"):
            return {"error": "이미 만드는 중입니다"}
        if kind != "draft" and not any(m.get("role") == "claude" for m in nt.get("thread") or []):
            return {"error": "먼저 한 번은 토의해야 합니다"}
        if kind == "draft" and not (nt.get("plan") or nt.get("card")):
            return {"error": "먼저 설계서(또는 카드)를 만들어야 원고 초안을 세울 수 있습니다"}
        if kind == "matrix" and not (nt.get("plan") or nt.get("card")):
            return {"error": "먼저 설계서(또는 카드)를 만들어야 식별력 표를 세울 수 있습니다"}
        if kind == "argmap" and not ((nt.get("intro") or {}).get("paras") and (nt.get("plan") or nt.get("card"))):
            return {"error": "서론 구조와 설계서(또는 카드)가 있어야 약속-답 지도를 그릴 수 있습니다"}
        if kind == "gapcheck" and not any(q.get("role") == "공백" for q in (nt.get("intro") or {}).get("paras") or []):
            return {"error": "서론 구조에 공백 문단이 있어야 검증할 수 있습니다"}
        nt[kind + "_pending"] = time.time()
        nt.pop(kind + "_error", None)
        nt[kind + "_opts"] = {"effort": effort, "req": str(body.get("req") or "").strip()[:600]}
        _save(nt)
    threading.Thread(target=_bg_job, args=(nid, kind), name="notes-" + kind, daemon=True).start()
    return {"ok": True}


def job_cancel(nid, kind):
    if kind not in _JOBKINDS:
        return {"error": "unknown"}
    with _LOCK:
        nt = _load(nid)
        if not nt or not nt.get(kind + "_pending"):
            return {"error": "만드는 중이 아닙니다"}
        job = _job_key(nid, kind, nt.get(kind + "_pending"))
    cancel = cfg.get("claude_cancel")
    if not cancel:
        return {"error": "이 서버는 취소를 지원하지 않습니다"}
    cancel(job)
    return {"ok": True}


def _bg_job(nid, kind):
    nt = _load(nid)
    if not nt:
        return
    if kind == "argmap":   # 원고의 논증 모형 기계를 그대로 — 물음·답 꼴이 달라 따로 간다
        return _argmap_job(nid)
    if kind == "gapcheck":   # 탐색 기계 + 판정 — 따로 간다
        return _gapcheck_job(nid)
    model, eff = _EFFORT.get((nt.get(kind + "_opts") or {}).get("effort"), _EFFORT["xhigh"])
    prompt = {"plan": _plan_prompt, "review": _review_prompt, "draft": _draft_prompt, "intro": _intro_prompt, "matrix": _matrix_prompt, "framing": _framing_prompt}[kind](nt)
    t0 = time.time()
    res, err, d, cancelled = None, "", None, False
    try:
        system, _st = _background()
        run = cfg.get("claude_run")
        res = run(prompt, timeout=1500, model=model, effort=eff, tools="", system=system, job=_job_key(nid, kind, nt.get(kind + "_pending"))) if run else None
        cancelled = bool((res or {}).get("cancelled"))
        if not cancelled:
            d = _json_of((res or {}).get("text"))
            if not isinstance(d, dict):
                err = {"plan": "설계서를", "review": "반론을", "draft": "원고 초안을", "intro": "서론 구조를", "matrix": "식별력 표를", "framing": "틀 후보를"}[kind] + " 받지 못했습니다" + _why()
    except Exception as e:
        err = "Claude 호출 실패: " + str(e)[:200]
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return
        nt.pop(kind + "_pending", None)
        if cancelled:
            nt.pop(kind + "_error", None)
            _save(nt)
            return
        if not err:
            try:
                {"plan": _apply_plan, "review": _apply_review, "draft": _apply_draft, "intro": _apply_intro, "matrix": _apply_matrix, "framing": _apply_framing}[kind](nt, d, res, t0)
                if kind == "intro":
                    _intro_check(nt)   # 프로그램이 본 것 — 서재 분포와 견줌 (비평 물음에도 들어간다)
            except Exception as e:
                err = str(e)[:300]
        if err:
            nt[kind + "_error"] = err
        else:
            nt.pop(kind + "_error", None)
        if kind == "intro" and not err:
            nt["intro_pending"] = time.time()   # 이어서 비평 — 화면은 같은 기다림 막대를 보인다
        _save(nt)
    if kind == "intro" and not err:
        try:
            _intro_critique(nid, model, eff)
        finally:
            with _LOCK:
                nt = _load(nid)
                if nt:
                    nt.pop("intro_pending", None)
                    try:
                        _intro_check(nt)   # 비평이 설계도를 고쳤으면 다시 견준다
                    except Exception:
                        pass
                    _save(nt)
        try:   # 서론 구조가 서면 약속 ↔ 답 지도를 이어서 그린다(Opus 한 번 — 서론의 공백·목적에 설계서가 답하는가, 2026-10-26)
            job_start(nid, "argmap", {"effort": (nt.get("intro_opts") or {}).get("effort") or "xhigh"})
            job_start(nid, "gapcheck", {"effort": (nt.get("intro_opts") or {}).get("effort") or "xhigh"})   # 공백 문장을 세상의 문헌에서 — 나란히
        except Exception:
            pass


def _intro_critique(nid, model, eff):
    """서론 구조(설계도)를 심사위원이 공격하고 고친다 — 원고의 절 계획(ms.plan_section)과 같은 규칙. 고친 문단은 cites 를 서재 파일에 다시 맞추고, 이미 쓴 문단(draft)은 같은 자리·같은 역할이면 지킨다."""
    nt = _load(nid)
    intro = (nt or {}).get("intro") or {}
    if not intro.get("paras"):
        return
    plan_json = json.dumps({"flow": intro.get("flow", ""), "paras": [{"role": q.get("role"), "gist": q.get("gist"), "points": [{"t": pt.get("t"), "src": [c.get("key") for c in pt.get("cites") or []]} for pt in q.get("points") or []], "len": q.get("len")} for q in intro["paras"]]}, ensure_ascii=False)
    ctx = "[이 주제] %s\n" % (nt.get("title") or "")
    ct = _card_text(nt.get("card"))
    if ct:
        ctx += "\n[주제 카드]\n" + ct + "\n"
    if nt.get("plan"):
        ctx += "\n[실험 설계서]\n" + _plan_text(nt["plan"], cap=1200) + "\n"
    kn = (cfg.get("knowledge") or (lambda j, k: ""))(_intro_journals(nt), "intro")
    dz, _items = _designs(nt, k=6, brief=True)
    am = _argmap_text(nt.get("argmap"))
    if am:
        dz = (dz or "") + "\n" + am
    gc = _gapcheck_text(nt.get("gapcheck"))
    if gc:
        dz = (dz or "") + "\n" + gc
    notes_ = intro.get("notes") or []
    prompt = ctx + ("\n" + kn if kn else "") + ("\n" + dz if dz else "") + (("\n[프로그램이 본 것 — 서재 분포와 견줌]\n" + "\n".join("- " + x for x in notes_) + "\n") if notes_ else "") + "\n[계획]\n" + plan_json + "\n\n" + ms._PLAN_CRIT % "서론(Introduction)" + "(이 계획의 src 는 '첫 저자 연도' 열쇠다 — 고친 설계도에서도 그 꼴로 적는다. 서재에 없는 것은 '서재 밖: ' 으로.)"
    system, _st = _background()
    run = cfg.get("claude_run")
    res = run(prompt, timeout=1200, model=model, effort=None if eff in ("xhigh", "max") else eff, tools="", system=system) if run else None
    c = _json_of((res or {}).get("text")) if res else None
    with _LOCK:
        nt = _load(nid)
        intro = (nt or {}).get("intro") or {}
        if not nt or not intro.get("paras"):
            return
        if not isinstance(c, dict):
            intro["critique"] = {"verdict": "?", "issues": [], "err": "비평을 받지 못했다"}
            _save(nt)
            return
        issues = []
        for x in (c.get("issues") or [])[:8]:
            if isinstance(x, dict) and str(x.get("note") or "").strip():
                try:
                    pi = int(x.get("para") or 0)
                except Exception:
                    pi = 0
                issues.append({"para": pi, "note": re.sub(r"\s+", " ", str(x["note"])).strip()[:400], "fix": re.sub(r"\s+", " ", str(x.get("fix") or "")).strip()[:300]})
        intro["critique"] = {"verdict": "revise" if (c.get("verdict") == "revise" or issues) else "ok", "issues": issues, "t": time.time(), "tok": (res or {}).get("tok")}
        if issues and isinstance(c.get("paras"), list) and c["paras"]:
            fake = {"paras": [], "flow": intro.get("flow", ""), "gaps": intro.get("gaps") or []}
            for p in c["paras"]:
                if not isinstance(p, dict):
                    continue
                pts = []
                for pt in (p.get("points") if isinstance(p.get("points"), list) else [])[:6]:
                    if isinstance(pt, str):
                        pt = {"t": pt, "src": []}
                    if isinstance(pt, dict) and str(pt.get("t") or "").strip():
                        pts.append({"t": pt["t"], "cites": [v for v in (pt.get("src") or []) if str(v).strip() and not re.match(r"^(R|C|E)\d", str(v))]})
                fake["paras"].append({"role": p.get("role"), "gist": p.get("gist"), "points": pts, "len": p.get("len")})
            try:
                old_models = intro.get("models")
                before = [{"role": q.get("role"), "gist": q.get("gist")} for q in intro["paras"]]
                crit = intro["critique"]
                _apply_intro(nt, fake, res or {}, time.time())   # cites 를 서재 파일에 맞추고 쓴 문단을 지키는 길 그대로
                for k in ("sec", "tok", "model", "t"):   # 뽑기의 시간·토큰·모델은 지킨다 — 비평의 것은 critique.tok 에 (전에는 sec 0 으로 덮였다, 2026-10-26)
                    if intro.get(k) is not None:
                        nt["intro"][k] = intro[k]
                nt["intro"]["critique"] = crit
                nt["intro"]["before"] = before
                nt["intro"]["models"] = old_models or []
            except Exception as e:
                intro["critique"]["err"] = str(e)[:200]
        _save(nt)


def objection_set(nid, body):
    with _LOCK:
        nt = _load(nid)
        if not nt:
            return {"error": "주제를 찾지 못했습니다"}
        rid, status = str(body.get("rid") or ""), body.get("status")
        for o in nt.get("objections") or []:
            if o.get("id") == rid:
                if status in ("open", "answered", "dropped"):
                    o["status"] = status
                if "reply" in body:
                    o["reply"] = str(body.get("reply") or "").strip()[:1500]
                break
        else:
            return {"error": "그 반론을 찾지 못했습니다"}
        _save(nt)
        return {"note": nt}


# ---------- 내보내기 ----------
def to_md(nt):
    out = ["# " + (nt.get("title") or ""), "상태: %s · %s" % (STATUS_KO.get(nt.get("status"), ""), time.strftime("%Y-%m-%d", time.localtime(nt.get("t") or 0))), ""]
    seed = nt.get("seed") or {}
    if seed.get("text"):
        out += ["## 출발점", seed["text"], ""]
    if nt.get("card"):
        out += ["## 주제 카드"]
        for k in CARD_KEYS:
            v = nt["card"].get(k)
            if not v:
                continue
            out.append("**%s**" % CARD_KO[k])
            out += (["- " + str(x) for x in v] if isinstance(v, list) else [str(v)]) + [""]
    if nt.get("framing"):
        out += ["## 연구의 틀 (프레이밍 후보)", _framing_text(nt["framing"], all_items=True), ""]
    if nt.get("plan"):
        out += ["## 실험 설계서"]
        for sec in nt["plan"].get("sections") or []:
            if sec.get("t"):
                out += ["**%s**" % sec.get("h"), sec["t"], ""]
        for k, nm in (("gaps", "서재에 없어 확인할 것"), ("checks", "연구자가 정할 것")):
            if nt["plan"].get(k):
                out += ["**%s**" % nm] + ["- " + str(x) for x in nt["plan"][k]] + [""]
        if nt["plan"].get("feasibility"):
            out += ["**우리 연구실에서 할 수 있는가**"] + ["- " + _feas_line(f) for f in nt["plan"]["feasibility"]] + [""]
    if nt.get("matrix"):
        out += ["## 식별력 표 — 가설 × 관찰 × 예측", _matrix_text(nt["matrix"]), ""]
    if nt.get("argmap"):
        out += ["## 약속 ↔ 답 지도", _argmap_text(nt["argmap"]), ""]
    if nt.get("gapcheck"):
        out += ["## 공백 검증 — 세상의 문헌에서", _gapcheck_text(nt["gapcheck"]), ""]
    if nt.get("intro"):
        out += ["## 서론 구조", _intro_text(nt["intro"]), ""]
        drafts = [q["draft"]["text"] for q in nt["intro"].get("paras") or [] if (q.get("draft") or {}).get("text")]
        if drafts:
            out += ["## 서론 초안 (쓴 문단)"] + [t + "\n" for t in drafts]
        if nt["intro"].get("refs"):
            out += ["## 참고문헌 (서론)"] + [str(r) for r in nt["intro"]["refs"]] + [""]
    if nt.get("objections"):
        out += ["## 심사위원의 반론"]
        for o in nt["objections"]:
            out += ["- [%s · %s] %s%s%s" % (REVIEWER_KO.get(o.get("who"), ""), {"open": "열림", "answered": "답함", "dropped": "보류"}.get(o.get("status"), ""), o.get("t"), (" — 물음: " + o["ask"]) if o.get("ask") else "", (" — 답: " + o["reply"]) if o.get("reply") else "")]
        out.append("")
    th = [m for m in nt.get("thread") or [] if m.get("text")]
    if th:
        out += ["## 토의"]
        for m in th:
            out += ["**%s** (%s)" % ("나" if m.get("role") == "user" else "Claude", time.strftime("%m-%d %H:%M", time.localtime(m.get("t") or 0))), m["text"], ""]
    return "\n".join(out)


# ---------- 라우트 ----------
def _q(url, k):
    from urllib.parse import parse_qs
    return parse_qs(url.query).get(k, [""])[0]


def handle_get(h, url):
    p = url.path
    if p == "/notes":
        with open(os.path.join(cfg["BASE"], "notes.html"), "rb") as f:
            return h._send(200, f.read(), "text/html; charset=utf-8")
    if p == "/api/notes":
        return h._send(200, {"items": list_notes(), "version": VERSION, "status": STATUS, "status_ko": STATUS_KO, "card_keys": CARD_KEYS, "card_ko": CARD_KO,
                             "plan_keys": PLAN_KEYS, "reviewers": REVIEWERS})
    if p == "/api/notes/get":
        nt = _load(_q(url, "id"))
        w = ((nt or {}).get("intro") or {}).get("writing")
        if w and w.get("xid"):   # 문단 쓰기의 지금 단계(쓰기 → 대조 → 고쳐 쓰기)는 manuscript 가 메모리에 둔다 — 응답에만 얹는다
            st = ms._LIBX_STAGE.get(w["xid"]) or {}
            if st.get("stage"):
                w["stage"] = st["stage"]
        return h._send(200, {"note": nt, "now": time.time()} if nt else {"error": "찾지 못했습니다"})
    if p == "/api/notes/designs":            # 유사 연구의 설계 — 서재 주석에서 (토큰 0, 2026-10-26)
        nt = _load(_q(url, "id")) or {}
        try:
            k = max(1, min(20, int(_q(url, "k") or 10)))
        except ValueError:
            k = 10
        text, items = _designs(nt, k=k)
        return h._send(200, {"items": items, "topic": _intro_topic(nt), "ko": _ko_topic(nt), "journals": _intro_journals(nt), "chars": len(text)})
    if p == "/api/notes/intro_models":       # 서론 본보기 진단 — 어느 저널·어느 논문의 서론을 고르나
        nt = _load(_q(url, "id")) or {}
        diag = {}
        try:
            text, names = _intro_models(nt, diag=diag)
            return h._send(200, dict(diag, names=names, text=text))
        except Exception as e:
            import traceback
            return h._send(200, dict(diag, error=str(e)[:300], tb=traceback.format_exc()[-1500:]))
    if p == "/api/notes/lab":                # 우리 연구실 (2026-10-24); ?text=1 은 홈페이지에서 읽은 글 그대로
        d = lab_get()
        if _q(url, "text") == "1":
            return h._send(200, (d.get("page") or "").encode("utf-8"), "text/plain; charset=utf-8")
        return h._send(200, d)
    if p == "/api/notes/bg":
        full = _q(url, "text") == "1"
        d = background_view(full)
        if full:
            return h._send(200, d["text"].encode("utf-8"), "text/plain; charset=utf-8")
        return h._send(200, d)
    return h._send(404, {"error": "unknown"})


def handle_post(h, body):
    try:
        op, nid = body.get("op"), str(body.get("id") or "")
        if op == "new":
            return h._send(200, new_note(body))
        if op == "ask":
            return h._send(200, ask(nid, body))
        if op == "cancel":
            return h._send(200, ask_cancel(nid))
        if op == "card":
            return h._send(200, card_start(nid, body))
        if op in _JOBKINDS:                      # plan · review · draft (2026-10-19)
            return h._send(200, job_start(nid, op, body))
        if op == "cancel_job":
            return h._send(200, job_cancel(nid, str(body.get("kind") or "")))
        if op == "framing_pick":                 # 연구의 틀 고르기 (2026-10-26)
            return h._send(200, framing_pick(nid, body))
        if op == "objection":
            return h._send(200, objection_set(nid, body))
        if op == "intro_write":                  # 서론 문단 쓰기 — 원고의 문단 쓰기 그대로 (2026-10-25)
            return h._send(200, intro_write_start(nid, body))
        if op == "intro_draft_clear":
            return h._send(200, intro_draft_clear(nid, body))
        if op == "lab_save":
            return h._send(200, lab_save(body))
        if op == "lab_fetch":
            return h._send(200, lab_fetch())
        if op == "save":
            return h._send(200, save_fields(nid, body))
        if op == "delete":
            fp = _path(nid)
            if os.path.isfile(fp) and os.path.dirname(os.path.abspath(fp)) == os.path.abspath(cfg["DIR"]):
                os.remove(fp)
            return h._send(200, {"ok": True})
        if op == "md":
            nt = _load(nid)
            if not nt:
                return h._send(200, {"error": "찾지 못했습니다"})
            return h._send(200, {"md": to_md(nt), "name": re.sub(r'[\\/:*?"<>|]', " ", nt.get("title") or "연구 노트").strip()[:60] + ".md"})
        return h._send(404, {"error": "unknown"})
    except Exception as e:
        return h._send(200, {"error": str(e)[:300]})
