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

VERSION = "연구 노트 (2026-10-24b)"
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
_JOBKINDS = ("plan", "review", "draft", "intro")
INTRO_ROLES = ["배경", "선행연구", "공백", "목적·접근", "기여·구성"]   # 서론 문단의 역할 (manuscript SECTION_KINDS["intro"] 와 같은 이름)
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
            "busy": any(nt.get(k + "_pending") for k in _JOBKINDS),
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
    "- 답은 사람이 읽는 한국어 글이다. 두괄식으로: 첫 문장에 판단(이 주제가 설 만한가, 무엇이 핵심인가, 무엇이 문제인가), 그 다음에 까닭, 마지막에 다음에 할 일. 짧은 문장으로 한 번에 한 가지씩. 인사말·칭찬·잘된 점의 나열·되묻기만 하는 답은 쓰지 않는다. 마크다운 제목·굵게 표시 없이 문단으로 쓴다(필요하면 번호 목록).\n"
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
    prompt = ("아래는 연구자와 나눈 연구 주제 토의다. 토의에서 정해진 것·거론된 것을 주제 카드 하나로 정리하라. 토의에 없는 것은 지어 넣지 말고 빈 문자열로 둔다. 연구자가 손으로 고친 카드가 있으면 그것을 바탕으로 고친다.\n"
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
    "칸마다 2~6문장(design 은 변수마다 한 줄 — '변수: 수준1 / 수준2 / 수준3 (고정: …), 반복 n'). "
    "background 배경과 공백(무엇이 알려졌고 무엇이 비었나 — 서재 논문으로) · question 연구 물음과 가설(검증 가능한 꼴로, 맞으면/틀리면 무엇이 보이나) · material 재료·시편(규격·상태·수) · setup 장비·측정(무엇을 무엇으로 재나, 분해능·범위 — [우리 연구실] 이 있으면 우리 장비로 되는 것과 외부에 맡길 것을 갈라 적는다) · "
    "design 실험 설계(독립 변수와 수준, 고정 조건, 반복 수, 대조군) · analysis 해석·모델(측정값을 가설의 판정으로 바꾸는 길) · expected 예상 결과와 판정 기준 · risks 위험과 대안(실패할 자리와 대비) · schedule 일정(단계와 기간) · venue 저널과 독자(어느 저널, 심사자가 볼 것). "
    "cites 는 그 칸에서 든 서재 논문의 '첫 저자 연도' 목록(없으면 빈 목록). "
    "feasibility 는 이 계획이 쓰는 측정·장비·공정마다 [우리 연구실] 에 비추어 우리가 할 수 있는가 — ours 가 false 인 것은 어디에 맡길지, null 은 연구자가 확인할 것. [우리 연구실] 이 없으면 빈 목록.")

_REVIEW_RULE = (
    "아래 연구 계획을 세 사람이 따로 심사한다 — exp 실험 전문가(장비·시편·측정의 실현 가능성 — [우리 연구실] 이 있으면 그 장비·역량으로 되는가와 외부에 맡길 측정의 비용·일정·접근, 교란 변수, 재현성, 측정 분해능이 효과 크기를 가를 수 있는가), theory 이론·모델 전문가(가설의 논리, 해석 모델의 가정, 같은 결과를 내는 대안 설명, 빠진 변수), "
    "editor 저널 심사위원(새로움 — 서재의 어느 논문이 이미 비슷한 것을 했는가, 결과가 나와도 왜 중요한가, 그 저널의 독자가 묻는 것). 각자 가장 아픈 반론 2~4개를 낸다 — 좋은 말·요약·되묻기만 하는 말은 쓰지 않는다. "
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
    return out + "\n" + _REVIEW_RULE


def _draft_prompt(nt):
    out = "[이 주제] %s\n" % (nt.get("title") or "")
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
    "문단은 4~7개. 역할은 배경(분야·왜 중요한가) · 선행연구(무엇이 알려졌나 — 묶어서) · 공백(엇갈림·한계·남은 물음 — 설계서의 '배경과 공백'·답한 반론과 같은 말이어야 한다) · 목적·접근(이 연구의 물음과 방법 개요 — 설계서의 물음·가설 그대로) · 기여·구성(선택) 가운데 고른다. "
    "문단마다 points 는 그 문단이 차례로 세울 말 2~5개 — 사실·주장 하나에 그것을 받치는 서재 논문을 '(첫 저자 연도)' 로 cites 에 적는다(그 논문이 실제로 그 말을 하는지 요약 한 줄에 비추어; 지어낸 인용·수치 금지). "
    "서재에 없는 사실을 세워야 하면 t 를 '서재 밖: ' 으로 시작하고 cites 는 빈 목록 — 그런 자리는 gaps 에 탐색어와 함께 적는다(연구자가 논문을 찾아 넣는다). 연구자의 요청이 있으면 따른다.\n"
    "JSON 으로만 답하라(한국어, 마크다운 없이): {\"flow\": \"서론이 펴는 논리 한 문단 — 왜 이 차례인가\", \"paras\": [{\"role\": \"배경|선행연구|공백|목적·접근|기여·구성\", \"gist\": \"이 문단이 세울 말 한 문장\", "
    "\"points\": [{\"t\": \"할 말 한 문장\", \"cites\": [\"첫 저자 연도\", ...]}], \"len\": 문장 수 어림(정수)}], \"gaps\": [\"서재에 없어 채워야 할 근거 — 무엇을, 탐색어: ...\"]}")

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


def _intro_models(nt, n=3, diag=None):
    """[본보기] 그 저널 서재 논문 n편의 서론 문단 구조 — 역할·요지(ms._label_chunked 'intro', 본보기 캐시). → (글, [논문 이름들]). diag(dict) 를 주면 고르는 과정을 적는다"""
    abbrs = _intro_journals(nt)
    if diag is not None:
        diag["journals"] = abbrs
    try:
        files = [f for f in os.listdir(cfg["ARCHIVE"]) if f.lower().endswith(".pdf") and len(f.split("_")) > 2 and f.split("_")[1] in abbrs]
    except OSError:
        files = []
    files.sort(reverse=True)   # 연도가 앞이라 최근순
    if diag is not None:
        diag["candidates"] = len(files)
        diag["tried"] = []
    picked, texts = [], {}
    for f in files[:40]:
        if len(picked) >= n:
            break
        try:
            secs = ms._paper_secs(f, ["intro"])
        except Exception as e:
            if diag is not None:
                diag["tried"].append((f[:50], "ERR " + str(e)[:80]))
            continue
        sec = next((x for x in secs if x.get("kind") == "intro" and 3 <= len(x.get("paras") or []) <= 24), None)
        if diag is not None:
            diag["tried"].append((f[:50], [(x.get("kind"), len(x.get("paras") or [])) for x in secs][:3]))
        if not sec:
            continue
        picked.append((f, sec))
        texts["M%d" % len(picked)] = [p.get("text", "") for p in sec["paras"]]
    if not picked:
        return "", []
    try:
        labels = ms._label_chunked("intro", texts)
    except Exception:
        labels = {}
    out, names = [], []
    for i, (f, sec) in enumerate(picked):
        m = re.match(r"^(\d{4})_([^_]+)_([^_]+)_", f)
        name = "%s %s (%s)" % (m.group(3), m.group(1), m.group(2)) if m else f[:40]
        names.append(name)
        lab = labels.get("M%d" % (i + 1)) or []
        rows = []
        for j, p in enumerate(sec["paras"]):
            role, gist = (lab[j][0], lab[j][1]) if j < len(lab) and isinstance(lab[j], list) and len(lab[j]) >= 2 else ("", "")
            rows.append("  %d. %s%s" % (j + 1, role or "?", (" — " + str(gist)[:90]) if gist else ""))
        out.append("%s — 서론 문단 %d개:\n%s" % (name, len(sec["paras"]), "\n".join(rows)))
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
    out += "\n[토의]\n" + _thread_text(nt, n=12, cap=2000) + "\n"
    models, names = _intro_models(nt)
    _INTRO_MODELS[nt.get("id")] = names   # _apply_intro 가 기록에 옮긴다
    if models:
        out += "\n[본보기 — 서재에 있는 그 저널 논문의 서론이 실제로 짜인 차례(문단마다 역할 — 요지)]\n" + models + "\n"
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
        line = "%d문단 [%s] %s" % (i + 1, p.get("role") or "", p.get("gist") or "")
        if not brief:
            for pt in p.get("points") or []:
                line += "\n  - %s%s" % (pt.get("t") or "", (" (" + ", ".join(c.get("key") for c in pt.get("cites") or []) + ")") if pt.get("cites") else "")
        out.append(line)
    if intro.get("gaps") and not brief:
        out.append("채워야 할 근거: " + " / ".join(str(x) for x in intro["gaps"][:8]))
    return "\n".join(out)


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
    nt["intro"] = {"flow": re.sub(r"\s+", " ", str(d.get("flow") or "")).strip()[:1500], "paras": paras,
                   "gaps": [str(x).strip()[:300] for x in (d.get("gaps") or [])[:10] if str(x).strip()],
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
    doc["front"] = {"title": str(d.get("title") or title_ko).strip()[:300], "abstract": re.sub(r"[ \t]+", " ", str(d.get("abstract") or "")).strip()[:3000],
                    "keywords": [str(x).strip()[:40] for x in (d.get("keywords") if isinstance(d.get("keywords"), list) else [])[:8] if str(x).strip()]}
    doc.setdefault("meta", {})["journal"] = str(d.get("journal") or "").strip()[:40]
    q = next((x["t"] for x in (nt.get("plan") or {}).get("sections") or [] if x.get("k") == "question" and x.get("t")), "") or (nt.get("card") or {}).get("question") or ""
    doc.setdefault("idea", {})["question"] = str(q)[:1000]
    doc["note_id"] = nt["id"]
    cfg["ms_save"](doc)
    nt["ms"] = {"id": doc["id"], "title": doc["title"], "t": time.time(), "model": res.get("model", ""), "sec": round(time.time() - t0), "nodes": len(outline)}


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
    model, eff = _EFFORT.get((nt.get(kind + "_opts") or {}).get("effort"), _EFFORT["xhigh"])
    prompt = {"plan": _plan_prompt, "review": _review_prompt, "draft": _draft_prompt, "intro": _intro_prompt}[kind](nt)
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
                err = {"plan": "설계서를", "review": "반론을", "draft": "원고 초안을", "intro": "서론 구조를"}[kind] + " 받지 못했습니다" + _why()
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
                {"plan": _apply_plan, "review": _apply_review, "draft": _apply_draft, "intro": _apply_intro}[kind](nt, d, res, t0)
            except Exception as e:
                err = str(e)[:300]
        if err:
            nt[kind + "_error"] = err
        else:
            nt.pop(kind + "_error", None)
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
    if nt.get("intro"):
        out += ["## 서론 구조", _intro_text(nt["intro"]), ""]
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
        return h._send(200, {"note": nt, "now": time.time()} if nt else {"error": "찾지 못했습니다"})
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
        if op == "objection":
            return h._send(200, objection_set(nid, body))
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
