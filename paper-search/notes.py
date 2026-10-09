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

cfg = {}
_LOCK = threading.RLock()
_BG = {}            # 배경 조각의 캐시: gaps(인용 이웃, 12시간) · 공부 장 요지(파일 mtime) · 탐색 기록의 연구 그룹(id)
_STUDY_CACHE = {}
_HIST_LABS = {}

VERSION = "연구 노트 (2026-10-08)"
STATUS = ["idea", "review", "adopt", "hold"]
STATUS_KO = {"idea": "아이디어", "review": "검토 중", "adopt": "채택", "hold": "보류"}
_EFFORT = {"xhigh": ("opus", "xhigh"), "max": ("opus", "max"), "high": ("opus", None), "fast": ("sonnet", None)}
_LIB_BUDGET = 62000      # 서재 블록(글자) — 논문 287편이면 머리줄 ~35,000자 + 요약 한 줄 100자쯤 (재 봄 2026-10-08: 60,000 에 제목 100자로 두니 요약이 59자로 잘려 쓸모가 없었다)
CARD_KEYS = ["question", "hypothesis", "design", "expected", "risks", "papers", "groups", "journals", "next"]
CARD_KO = {"question": "연구 물음", "hypothesis": "가설", "design": "실험·해석 설계", "expected": "예상 결과와 그 뜻", "risks": "위험·약점·반론",
           "papers": "관련 논문", "groups": "비슷한 일을 하는 연구 그룹", "journals": "맞는 저널", "next": "다음에 할 일"}
_LIST_KEYS = ("papers", "groups", "journals", "next")


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
    for fn in (_lib_block, _study_block, _hist_block, _gap_block, _ms_block):
        try:
            text, st = fn()
        except Exception as e:
            text, st = "", {"error": str(e)[:100]}
        stats.update(st)
        if text:
            blocks.append(text)
    head = ("당신은 기계가공·정밀가공 분야 연구자의 연구 공저자이자 토의 상대다. 연구자가 연구 주제를 던지고 함께 따진다 — 이 주제가 설 만한가, 무엇이 이미 밝혀졌고 무엇이 비었는가, "
            "어떤 실험·해석으로 보일 수 있는가, 어디에 낼 것인가. 아래는 연구자가 가진 것·아는 것 전부다: 서재(모은 논문 모두), 그 서재로 쓴 공부 장, 지난 논문 탐색, 서재의 인용 이웃, 쓰고 있는 원고. "
            "토의는 이것을 바탕으로 한다 — 서재 안의 논문을 들어 말하고, 서재에 없는 것은 그렇다고 밝힌다.\n\n")
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
        return h._send(200, {"items": list_notes(), "version": VERSION, "status": STATUS, "status_ko": STATUS_KO, "card_keys": CARD_KEYS, "card_ko": CARD_KO})
    if p == "/api/notes/get":
        nt = _load(_q(url, "id"))
        return h._send(200, {"note": nt, "now": time.time()} if nt else {"error": "찾지 못했습니다"})
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
