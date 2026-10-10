# -*- coding: utf-8 -*-
"""
Athenaeum 공부 — 내 서재의 논문만으로 쓰는 교과서 한 장.

주제를 적으면(예: '단결정 실리콘 절삭') 서재의 논문을 읽어 전공책의 한 장처럼 정리해 준다.
다른 점은 근거다. 장의 사실 문장은 모두 서재 논문의 실제 문장(문장 번호)에 묶여 있고,
화면에 보이는 그 원문은 Claude 가 쓴 것이 아니라 번역 문장표(파일)에서 꺼낸 것이다.

흐름 (_run):
  1 계획   주제 → 장 제목·범위·물음, 찾을 낱말(개념)
  2 훑기   서재의 모든 문장을 낱말로 훑어 논문마다 점수 (프로그램)
  3 고르기 후보 논문의 제목·한줄 요약·점수 → 읽을 논문(핵심 = 통째로 / 관련 = 맞은 대목만)
  4 읽기   논문마다 따로: 원문(문장 번호 붙임)을 읽고 주제에 관한 사실을 메모로 — 메모마다 근거 문장 번호
  5 짜기   모든 메모 → 장의 절과 차례, 용어표 (메모를 절에 나눈다) → 절마다 따로 논지(여러 메모가 함께 받치는 문장)를 세우고,
           어느 논지에도 들지 않는 메모는 '번외'로 남긴다 (2026-10-07: 전에는 절 = 메모 목록이라 글이 논문마다의 결과 나열이 됐다)
  6 쓰기   절마다 따로: 논지마다 한 문단 — 논지 문장(◆) → 받치는 연구를 메모 하나에 한 문장씩 → 연구들의 관계(같다·조건이 달라 다르다·엇갈린다).
           수치가 견줄 만하면 표(행마다 메모 번호), 논지를 눈으로 보여 주는 논문의 그림을 캡션 목록에서 골라 '그림:' 한 줄. 절 끝 '### 번외'.
  7 대조   절마다 따로(쓴 것과 다른 호출): 문장(표의 행·그림 설명도)을 근거 원문과 하나씩 견준다 → 근거를 넘는 문장은 고치고,
           고친 문장을 다시 견줘 통과하지 못하면 뺀다. 수치는 프로그램이 근거 문장의 글자와 따로 대조한다
  8 마무리 참고문헌(서재의 논문 → Crossref 서지), 근거 문장 모음, 그림은 논문 PDF 에서 잘라 공부\\그림\\<id>\\ 에 (figcrop)

지어내기를 막는 장치: ① 근거로 보이는 원문은 파일에서 꺼낸다(Claude 는 번호만 준다) ② 없는 문장 번호를 든 메모는 버린다
③ 메모의 수치가 근거 문장에 없으면 버린다 ④ 쓴 문장은 다른 호출이 원문과 대조한다 ⑤ 통과하지 못한 문장은 싣지 않고 '뺀 문장'에 남긴다
⑥ 그림은 Claude 가 그리는 것이 아니라 논문의 PDF 에서 그대로 잘라 오고, 캡션 목록에 없는 그림은 쓸 수 없다. 그림 설명 문장은 캡션·근거와 대조한다.

데이터: MAENG_paper\\공부\\<id>.json  (단계마다 저장 — 끊겨도 「이어서」 로 남은 단계부터)
server.py 가 init() 으로 주입하고 /study, /api/study* 를 이 모듈에 넘긴다. 서재를 읽는 부품은 manuscript 의 것을 쓴다.
"""

import io
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import manuscript as ms
import figcrop

cfg = {}
_LOCK = threading.RLock()
_JOBS = {}          # 공부 id → {"stop": bool, "t0": 시작 시각}
_SENT_CACHE = {}    # 파일 → ((정렬표 mtime, 번역 mtime), 문장들)
_LIST_CACHE = {}    # 공부 파일 이름 → (mtime, 목록에 보일 것)
_CAP_CACHE = {}     # 파일 → 그림·표 캡션 목록 [{kind, n, page, text, ko}]

VERSION = "공부 (2026-10-09)"   # 이름에 판 번호를 붙이지 않는다(사용자). 날짜 = 그림 색인·도식 후보·교과서식 배치(저녁)와 Claude 의 생각(해설 층)·절 사이 잇기·묶어 쓰기를 넣은 날. 그 전: 논지 층·번외·표·그림(10-07), 그 전: Stylus 적용(10-06) · 절마다 보기 · 마인드맵 · 드래그해 묻기
_DEPTH = {"small": (6, 10), "mid": (12, 20), "wide": (20, 36)}   # (통째로 읽는 논문, 읽는 논문 전체)
_STAGES = ["plan", "scan", "select", "read", "figs", "outline", "write", "join", "verify", "wrap", "comment"]
_PART_CHARS = 95000     # 한 번에 읽히는 원문 글자 수 (넘으면 나눠 읽는다)
_HIT_CHARS = 16000      # 관련 논문: 맞은 대목만


def init(**kw):
    cfg.update(kw)
    cfg["DIR"] = os.path.join(os.path.dirname(cfg["ARCHIVE"]), "공부")
    os.makedirs(cfg["DIR"], exist_ok=True)


class _Stop(Exception):
    pass


# ---------- 저장 ----------
def _path(sid):
    return os.path.join(cfg["DIR"], os.path.basename(str(sid)) + ".json")


def _load(sid):
    d = cfg["load_json"](_path(sid), None)
    return d if isinstance(d, dict) and d.get("id") else None


def _save(st):
    with _LOCK:
        st["updated"] = time.time()
        cfg["save_json"](_path(st["id"]), st)


def _live(sid):
    return sid in _JOBS


def _status(st):
    """파일에는 running 인데 서버가 다시 켜져 도는 작업이 없으면 '멈춤'."""
    s = st.get("status") or ""
    return "stopped" if s == "running" and not _live(st.get("id")) else s


def _chat_brief(c):
    """장 목록에 싣는 대화 요약 — 「대화 찾기」(사용자 2026-10-09: '기존에 했던 대화 찾기가 너무 빡세다') 가 장을 열지 않고 모든 장의 대화를 뒤지게."""
    th = c.get("thread") or []
    qs = [m.get("text") or "" for m in th if m.get("role") == "user"]
    return {"id": c.get("id"), "t": c.get("t"), "sec": c.get("sec") or "", "quote": (c.get("quote") or "")[:120], "q": " / ".join(q[:120] for q in qs)[:400],
            "n": sum(1 for m in th if m.get("role") == "claude"), "pending": bool(c.get("pending")), "key": (c.get("keys") or [""])[0]}


def list_studies():
    out = []
    try:
        files = [f for f in os.listdir(cfg["DIR"]) if f.endswith(".json") and not f.startswith("_")]
    except OSError:
        files = []
    for f in files:
        fp = os.path.join(cfg["DIR"], f)
        try:
            mt = os.path.getmtime(fp)
        except OSError:
            continue
        c = _LIST_CACHE.get(f)
        if not c or c[0] != mt:        # 파일이 바뀌었을 때만 다시 읽는다 (다 쓴 장은 근거 문장까지 들어 있어 크다 — 목록을 볼 때마다 다 읽지 않게)
            st = cfg["load_json"](fp, None)
            if not isinstance(st, dict) or not st.get("id"):
                continue
            c = (mt, {"id": st["id"], "topic": st.get("topic", ""), "title": (st.get("chapter") or {}).get("title") or (st.get("plan") or {}).get("title") or "",
                      "t": st.get("t", 0), "status": st.get("status") or "", "stage": st.get("stage", ""), "error": st.get("error", ""),
                      "papers": len([p for p in st.get("papers") or [] if p.get("read")]), "stats": st.get("stats"),
                      "chats": [_chat_brief(c) for c in st.get("chats") or []]})
            _LIST_CACHE[f] = c
        out.append(dict(c[1], status=_status(c[1])))
    for f in [f for f in _LIST_CACHE if f not in files]:
        _LIST_CACHE.pop(f, None)
    out.sort(key=lambda x: -x["t"])
    return out


# ---------- 서재의 문장 ----------
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f­￾￿]")
_TAIL_RX = re.compile(r"참고\s*문헌|^references?\b|bibliograph|literature cited|그림\s*(및|과|·|,)\s*표|figures?\s+and\s+tables?", re.I)
_ACK_RX = re.compile(r"감사의\s*글|사사|acknowledg|declaration|credit author|저자\s*기여|이해\s*(상충|관계)|conflicts? of interest|competing interest|data availab|데이터\s*가용|funding|윤리", re.I)
_REF_LIKE = re.compile(r"^\s*\[\d{1,3}\]\s+\S|\bpp?\.\s*\d+|\bvol\.\s*\d+|\(\d{4}\)\s*\d+\s*[–\-]\s*\d+|\bdoi\b|https?://", re.I)
_CAP_RX = re.compile(r"^\s*(fig\.?|figure|table|scheme)\s*\.?\s*\d", re.I)
_AUTHOR_LIKE = re.compile(r"\b[A-Z]\.\s?(?:[A-Z]\.\s?)*[A-Z][a-z]+|\b[A-Z][a-z]+,?\s(?:[A-Z]\.\s?){1,3}[,;]")   # 'T.H.C. Childs' · 'Childs T.H.C.,' — 참고문헌 줄의 저자


def _clean(t):
    return re.sub(r"\s+", " ", _CTRL.sub(" ", t or "")).strip()


def _paper_sents(fname):
    """논문 하나의 문장들: [{sid 's12', n, en 원문, ko 번역, page(0부터), para, sec 절 제목, zone body|ack|tail, head}] — 번역 정렬표 + 번역문.
    번역을 만들지 않은 논문은 PDF 본문에서(문장 번호 'x1'…, 쪽·번역 없음)."""
    stem = os.path.splitext(fname)[0]
    ap, mp = os.path.join(cfg["GEN_DIR"], stem + ".번역.정렬.json"), os.path.join(cfg["GEN_DIR"], stem + ".번역.md")
    try:
        key = (os.path.getmtime(ap), os.path.getmtime(mp) if os.path.isfile(mp) else 0)
    except OSError:
        key = None
    if key is None:
        return [{"sid": "x%d" % (i + 1), "n": i + 1, "en": _clean(x[1]), "ko": "", "page": None, "para": None, "sec": "", "zone": "body", "head": False}
                for i, x in enumerate(ms._pdf_sentences(fname))]
    c = _SENT_CACHE.get(fname)
    if c and c[0] == key:
        return c[1]
    tb = cfg["load_json"](ap, None) or {}
    ko, sec_of, heads, cur = {}, {}, set(), ""
    try:
        md = io.open(mp, encoding="utf-8").read()
    except Exception:
        md = ""
    for ln in md.split("\n"):
        hm = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if hm:
            if len(hm.group(1)) == 1:   # 맨 위 제목 줄
                continue
            cur = re.sub(r"\s+", " ", re.sub(r"\[s\d+\]", "", hm.group(2))).strip()
            for m in re.finditer(r"\[s(\d+)\]", ln):
                sec_of[m.group(1)] = cur
                heads.add(m.group(1))
            continue
        parts = re.split(r"\[s(\d+)\]", ln)   # 표식으로 나눈다 — 문장 안의 '[' (인용 번호)에서 끊기지 않게
        for i in range(1, len(parts) - 1, 2):
            sec_of.setdefault(parts[i], cur)
            t = parts[i + 1].strip()
            if len(t) >= 2:
                ko.setdefault(parts[i], t)
    out, sec, zone = [], "", "body"
    keys = sorted((k for k in tb if str(k).isdigit()), key=int)
    for pos, k in enumerate(keys):
        v = tb[k] or {}
        en = _clean(v.get("t") or "")
        if k in sec_of and sec_of[k] != sec:
            sec = sec_of[k]
            if _TAIL_RX.search(sec):
                zone = "tail"            # 참고문헌·그림과 표부터는 끝까지 꼬리 (그 뒤의 제목 줄은 추출이 흩어진 조각이다)
            elif zone != "tail":
                zone = "ack" if _ACK_RX.search(sec) else "body"
        if zone != "tail" and len(keys) > 30 and pos > len(keys) * 0.4 and (   # 번역문에 제목 줄이 없는 논문: 원문의 'References' 줄이나 '[1] …' 로 시작하는 목록에서
                re.match(r"^(references?|bibliography|literature cited)\s*$", en, re.I) or (pos > len(keys) * 0.5 and re.match(r"^\[1\]\s+\S", en))):
            zone = "tail"
        out.append({"sid": "s" + str(k), "n": int(k), "en": en, "ko": _clean(ko.get(k, "")), "page": v.get("p"), "para": v.get("para"),
                    "sec": sec, "zone": zone, "head": k in heads})
    _SENT_CACHE[fname] = (key, out)
    return out


def _readable(sents):
    """읽힐 문장만: 제목 줄·짧은 조각·감사의 글·참고문헌 줄·표의 숫자 줄은 뺀다."""
    out = []
    for s in sents:
        t = s["en"]
        if s.get("head") or len(t) < 25 or s["zone"] == "ack":
            continue
        if re.match(r"^\s*\[\d{1,3}\]\s+[A-Z]", t) and re.search(r"(19|20)\d{2}", t):   # 번호 붙은 참고문헌 줄 (제목 줄이 없어 본문으로 남은 것도)
            continue
        if s["zone"] == "tail" and not _CAP_RX.match(t) and (     # 꼬리에서는 그림·표의 캡션과 흩어진 본문 문장만 남긴다
                _REF_LIKE.search(t) or len(t) < 60 or sum(ch.isdigit() for ch in t) > len(t) * 0.2 or re.search(r"\b(19|20)\d{2}\b", t) or _AUTHOR_LIKE.search(t)):
            continue
        out.append(s)
    return out


def _unit_garbled(sents):
    """PDF 에서 μ 글자가 깨져 μm 가 'mm'·'lm' 로 꺼내진 논문으로 보이는가 (μm 가 한 번도 없는데 lm 이 여럿이거나, nm 를 쓰는 논문에 mm 가 여럿)."""
    t = " ".join(s["en"] for s in sents)
    if re.search(r"\d\s?[µμ]m\b", t):
        return False
    return len(re.findall(r"\d\s?lm\b", t)) >= 3 or (len(re.findall(r"\d\s?mm\b", t)) >= 5 and len(re.findall(r"\d\s?nm\b", t)) >= 3)


def _is_korean_doc(sents):
    """원문이 한국어다 — 논문 원본이 아니라 번역본 PDF 가 논문 이름으로 들어온 것 (서지가 맞지 않는다)."""
    han = lambda t: sum(1 for ch in t if "가" <= ch <= "힣") > len(t) * 0.2
    return bool(sents) and sum(1 for s in sents if han(s["en"])) > len(sents) * 0.15


def _concept_rx(concepts):
    rx = [(ms._term_rx(c.get("en")), ms._term_rx(c.get("ko"), True)) for c in concepts]
    must = {i for i, c in enumerate(concepts) if c.get("must") and (rx[i][0] or rx[i][1])}
    if not must:
        must = {i for i in range(len(rx)) if rx[i][0] or rx[i][1]}
        must = set(sorted(must)[:2])
    return rx, must


def _hits(sents, rx, must):
    """문장마다 맞은 개념 → (hit[문장] = {개념}, idx = 주제 문장의 자리: 필수 개념이 앞 두 문장~다음 문장 안에 모두 있고 그 문장에 하나는 있다)."""
    hit = []
    for s in sents:
        low = s["en"].lower()
        hit.append({k for k, (re_en, re_ko) in enumerate(rx) if (re_en and re_en.search(low)) or (re_ko and s["ko"] and re_ko.search(s["ko"]))})
    idx = []
    for i, h in enumerate(hit):
        if h & must and must <= set().union(*hit[max(0, i - 2):i + 2]):
            idx.append(i)
    return hit, idx


_REVIEW_RX = re.compile(r"\b(review|survey|overview|advances in|state[- ]of[- ]the[- ]art|perspectives?|progress in)\b", re.I)


def _looks_review(title):
    return bool(_REVIEW_RX.search(str(title or "")))


# 우선 저널(사용자 2026-10-07: '출처가 AMT 가 너무 많다 — 안에서 우선순위나 가중치를. AMT 의 리뷰·서론은 괜찮은데 결론이 좀, 수치는 쓸모 있다').
# 서버가 탐색과 같은 우선 저널(설정 → 연결)의 파일 약어를 준다(pref_abbrs). ★ 논문 = 고를 때 먼저, 읽을 때 결론 메모를 덜 뽑지 않는 쪽, 논지·설명의 기둥.
def _top_set():
    try:
        fn = cfg.get("pref_abbrs")
        return set(str(x).upper() for x in (fn() if fn else ()))
    except Exception:
        return set()


def _is_top(journal, tops=None):
    return str(journal or "").upper() in (tops if tops is not None else _top_set())


def _pname(st, pi):
    """물음에 적는 논문 이름: 'P5 ★ Fang 2005 (IJMTM)' — ★ 는 우선 저널"""
    p = st["papers"][pi]
    return "P%d %s%s (%s)" % (pi + 1, "★ " if p.get("top") else "", p["short"], p.get("journal") or "")


def _journal_stats(st, order):
    """장에 인용된 논문의 저널별 편수 → [{abbr, n, top}] 편수 순(화면의 「이 장의 기록」)"""
    cnt = {}
    for pi in order:
        p = st["papers"][pi]
        j = p.get("journal") or "?"
        cnt.setdefault(j, {"abbr": j, "n": 0, "top": bool(p.get("top"))})["n"] += 1
    return sorted(cnt.values(), key=lambda x: (-x["n"], x["abbr"]))


def _scan(concepts):
    """서재의 모든 논문을 훑어 주제 문장이 있는 논문을 점수와 함께. → (후보들, 훑은 논문 수, 문장 수, 뺀 문서)"""
    rx, must = _concept_rx(concepts)
    if not must:
        return [], 0, 0, []
    try:
        files = sorted(f for f in os.listdir(cfg["ARCHIVE"]) if f.lower().endswith(".pdf"))
    except OSError:
        files = []
    out, nfiles, nsent, odd = [], 0, 0, []
    tops = _top_set()
    for f in files:
        sents = _readable(_paper_sents(f))
        if len(sents) < 8:
            continue
        if _is_korean_doc(sents):
            odd.append(f)
            continue
        nfiles += 1
        nsent += len(sents)
        hit, idx = _hits(sents, rx, must)
        meta = ms.paper_meta(f)
        title = str(meta.get("title") or "").lower()
        tit = sum(1 for i in must if rx[i][0] and rx[i][0].search(title))
        if len(idx) >= 2 or tit == len(must):
            out.append({"file": f, "co": len(idx), "tit": tit, "n": len(sents), "score": len(idx) + (10 if tit == len(must) else 2 * tit), "top": _is_top(meta.get("journal", ""), tops)})
    out.sort(key=lambda x: -(x["score"] * (1.25 if x["top"] else 1.0)))   # 우선 저널은 같은 점수대에서 앞에(후보 70편 안에 들게)
    return out, nfiles, nsent, odd


def _paper_text(sents):
    """읽힐 글: 절 제목 줄 + 문단마다 한 줄, 문장 앞에 [s12]."""
    lines, cur, sec, para = [], [], None, object()
    for s in sents:
        if s["sec"] != sec:
            if cur:
                lines.append(" ".join(cur)); cur = []
            sec = s["sec"]
            if sec:
                lines.append("\n## " + sec)
        if s["para"] != para and cur:
            lines.append(" ".join(cur)); cur = []
        para = s["para"]
        if s.get("gap"):
            if cur:
                lines.append(" ".join(cur)); cur = []
            lines.append("(…)")
        cur.append("[%s] %s" % (s["sid"], s["en"]))
    if cur:
        lines.append(" ".join(cur))
    return "\n".join(lines).strip()


def _parts(sents, size=_PART_CHARS):
    """긴 논문은 문단 경계에서 나눈다 (많아야 3부분)."""
    total = sum(len(s["en"]) + 8 for s in sents)
    if total <= size:
        return [sents]
    n = min(3, -(-total // size))
    per, out, cur, acc = total / float(n), [], [], 0
    for i, s in enumerate(sents):
        cur.append(s)
        acc += len(s["en"]) + 8
        if acc >= per and len(out) < n - 1 and (i + 1 == len(sents) or sents[i + 1]["para"] != s["para"]):
            out.append(cur); cur, acc = [], 0
    if cur:
        out.append(cur)
    return out


def _windows(sents, idx, pad=4, cap=_HIT_CHARS):
    """주제 문장의 앞뒤만 (관련 논문). 끊긴 곳에는 gap 표시."""
    keep = set()
    for i in idx:
        keep.update(range(max(0, i - pad), min(len(sents), i + pad + 1)))
    out, acc, last = [], 0, None
    for i in sorted(keep):
        s = dict(sents[i])
        if last is not None and i != last + 1:
            s["gap"] = True
        acc += len(s["en"]) + 8
        if acc > cap:
            break
        out.append(s)
        last = i
    return out


# ---------- Claude ----------
def _ask(st, prompt, model, effort=None, timeout=1500):
    run = cfg.get("claude_run")
    res = run(prompt, timeout=timeout, model=model, effort=effort, tools="") if run else None
    with _LOCK:
        st["calls"] = st.get("calls", 0) + 1
        tok = (res or {}).get("tok") or {}
        if tok:
            t = st.setdefault("tok", {"in": 0, "cached": 0})
            t["in"] += int(tok.get("in") or 0)
            t["cached"] += int(tok.get("cached") or 0)
        if res and res.get("model"):
            st.setdefault("models", {})[res["model"]] = st.get("models", {}).get(res["model"], 0) + 1
    return (res or {}).get("text") or ""


def _json_of(text):
    """답에서 JSON 객체 하나. 못 읽으면 None"""
    t = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", (text or "").strip())
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        d = json.loads(t[a:b + 1])
    except ValueError:
        try:
            d = json.loads(re.sub(r",\s*([\]}])", r"\1", t[a:b + 1]))   # 끝에 남은 쉼표
        except ValueError:
            return None
    return d if isinstance(d, dict) else None


def _ask_json(st, prompt, model, effort=None, timeout=1500, need=None, tries=2):
    for _ in range(tries):
        _check_stop(st)
        d = _json_of(_ask(st, prompt, model, effort, timeout))
        if d is not None and (need is None or need in d):
            return d
    return None


def _why():
    try:
        return ms._why()
    except Exception:
        return ""


def _check_stop(st):
    j = _JOBS.get(st["id"])
    if j and j.get("stop"):
        raise _Stop()


def _stage(st, name, **prog):
    with _LOCK:
        st["stage"] = name
        st["stage_t"] = time.time()
        st["prog"] = dict(prog)
    _save(st)


def _log(st, msg):
    with _LOCK:
        st.setdefault("log", []).append({"t": time.time(), "m": msg})
        st["log"] = st["log"][-60:]


# ---------- 수치 대조 (프로그램) ----------
def _nums(text):
    """글 속의 수(두 자리 이상이거나 소수). 연도와 한 자리 수는 뺀다. '1,000' = '1000', '0.50' = '0.5'"""
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text or "")
    out = set()
    for m in re.finditer(r"\d+(?:\.\d+)?", t):
        x = m.group(0)
        if "." in x:
            x = x.rstrip("0").rstrip(".") or "0"
        if not re.fullmatch(r"(19|20)\d{2}|\d", x):
            out.add(x)
    return out


def _nums_missing(text, sources):
    """text 의 수 가운데 sources(근거 문장들)에 그 수로 적혀 있지 않은 것. 낱말 전체로 견준다('10' 은 '100' 에 맞지 않는다)."""
    have = set()
    for s in sources:
        have |= _nums(s)
        have |= _nums(re.sub(r"(\d)\s*\.\s+(\d)", r"\1.\2", s))   # PDF 추출이 벌려 놓은 소수 '0. 5'
        for m in re.finditer(r"(?<![\d.])\d(?:\s+[¯̄\-]?\s*\d){1,3}(?![\d.])", s):   # 띄어 적은 결정면·방위 '(1 1 1)', '⟨1 1 ¯2⟩' → 111, 112
            have.add(re.sub(r"\D", "", m.group(0)))
    return sorted(_nums(text) - have)


# ---------- 1 계획 ----------
def _plan(st, nlib):
    prompt = (
        "연구자가 자기 서재(영어 논문 %d편)의 논문만으로 아래 [주제]를 공부하려 한다. 서재에서 그 주제를 다룬 논문과 문장을 낱말로 찾고, 찾은 것으로 전공 교과서의 한 장을 쓸 것이다. 그 준비를 하라.\n"
        "JSON 으로만 답하라:\n"
        "{\"title\": \"장 제목(한국어, 교과서의 장 제목처럼 짧게)\", \"scope\": \"이 장이 다룰 범위 한 문장(한국어)\",\n"
        " \"ask\": [\"이 장을 읽고 나면 답할 수 있어야 하는 물음 5~8개(한국어). 개념 → 현상 → 기전 → 영향 인자 → 방법 → 한계의 차례로\"],\n"
        " \"concepts\": [{\"name\": \"개념 이름(한국어)\", \"must\": true, \"en\": [\"...\"], \"ko\": [\"...\"]}]}\n"
        "규칙:\n"
        "- concepts 는 주제를 이루는 개념 1~3개 (예: '단결정 실리콘 절삭' → 대상 '단결정 실리콘' + 행위 '절삭'). must 는 그 개념이 빠지면 이 주제가 아닌 것에 true.\n"
        "- 개념마다 논문 본문에 실제로 쓰이는 영어 표현 4~14개(동의어·약어·다른 철자, 소문자)와 한국어 번역문에 쓰일 표현 2~8개.\n"
        "- 영어 표현은 낱말의 첫머리에서 맞춘다. 활용형을 한 번에 잡으려면 어간으로 적되 5자 이상으로(machin, cutting, turning, silicon). 4자 이하는 약어로 보아 낱말 전체가 같을 때만 맞는다(bue, spdt).\n"
        "- 너무 흔해서 아무 문장에나 걸리는 낱말(surface, tool, process 같은)은 그 개념의 핵심일 때만 넣는다.\n\n"
        "[주제]\n%s") % (nlib, st["topic"][:800])
    d = _ask_json(st, prompt, "sonnet", None, 240, need="concepts")
    if not d:
        raise RuntimeError("주제를 풀지 못했습니다" + _why())
    concepts = []
    for c in (d.get("concepts") or [])[:3]:
        if not isinstance(c, dict):
            continue
        en = [str(x).strip().lower()[:40] for x in (c.get("en") or [])[:16] if str(x).strip()]
        ko = [str(x).strip()[:20] for x in (c.get("ko") or [])[:10] if str(x).strip()]
        if en or ko:
            concepts.append({"name": str(c.get("name") or "")[:30], "must": bool(c.get("must")), "en": en, "ko": ko})
    if not concepts:
        raise RuntimeError("찾을 낱말을 정하지 못했습니다" + _why())
    st["plan"] = {"title": str(d.get("title") or st["topic"]).strip()[:80], "scope": str(d.get("scope") or "").strip()[:300],
                  "ask": [str(x).strip()[:200] for x in (d.get("ask") or [])[:8] if str(x).strip()], "concepts": concepts}


# ---------- 3 고르기 ----------
def _select(st, cands, caps):
    ncore, nall = caps
    cands = cands[:70]
    rows, tops = [], _top_set()
    limit = max(2, ncore // 3)     # 통째로 읽는 자리에 두는 ★ 아닌 연구 논문의 상한(아래 자리 바꾸기와 같은 값)
    for k, c in enumerate(cands):
        m = ms.paper_meta(c["file"])
        line = ms._paper_line(c["file"])
        rows.append("K%d | %s · %s | %s | 주제 문장 %d개%s%s" % (k + 1, ms.paper_short(c["file"]), ("★ " if _is_top(m.get("journal", ""), tops) else "") + m.get("journal", ""), str(m.get("title") or "")[:150], c["co"],
                                                           " · 제목에 주제" if c["tit"] else "", (" | " + line[:220]) if line else ""))
    p = st["plan"]
    prompt = (
        "연구자의 서재에서 아래 [주제]의 교과서 한 장을 쓰는 데 읽을 논문을 고른다. 낱말로 추린 후보를 준다(K번호 | 저자 연도 · 저널 | 제목 | 주제 문장 수 | 그 논문의 한줄 요약). 저널 앞의 ★ = 연구자가 정한 우선 저널.\n"
        "JSON 으로만: {\"papers\": [{\"k\": 3, \"role\": \"core|related\", \"why\": \"이 장에서 이 논문이 맡을 것 한 구절(한국어)\"}]}\n"
        "규칙:\n"
        "- core = 이 주제를 자기 실험·모델·시뮬레이션·리뷰로 직접 다룬 논문. 통째로 읽는다. 많아야 %d편.\n"
        "- related = 주제의 일부만 다루거나, 비교·배경으로 쓸 만한 논문. 주제 문장의 앞뒤만 읽는다.\n"
        "- 낱말만 걸렸을 뿐 주제를 다루지 않는 논문은 넣지 않는다. 전체 많아야 %d편. 이 장에 중요한 순서로 적는다.\n"
        "- 리뷰 논문과 기초가 되는 옛 논문, 서로 다른 방법(실험·시뮬레이션·모델)의 논문이 고루 들어가게 한다.\n"
        "- 저널의 무게: 주제를 다루는 정도가 비슷하면 ★ 논문을 core 로 먼저 고른다. ★ 가 아닌 저널의 연구 논문은 이 주제를 자기 실험·모델로 직접 다룬 것만 core 로 하되 많아야 %d편(나머지는 related — "
        "그 논문의 수치·조건은 related 로도 교과서에 들어간다). 리뷰 논문과 기초가 되는 옛 논문은 저널과 무관하게 core 여도 된다.\n\n"
        "[주제] %s\n[장의 범위] %s\n[답할 물음]\n%s\n\n[후보]\n%s") % (ncore, nall, limit, st["topic"][:400], p["scope"], "\n".join("- " + a for a in p["ask"]), "\n".join(rows))
    d = _ask_json(st, prompt, "sonnet", None, 300, need="papers")
    picked, seen = [], set()
    for x in ((d or {}).get("papers") or []):
        try:
            c = cands[int(str(x.get("k")).lstrip("Kk")) - 1]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        if c["file"] in seen:
            continue
        seen.add(c["file"])
        picked.append((c, "core" if x.get("role") == "core" else "related", str(x.get("why") or "").strip()[:160]))
    if not picked:   # Claude 가 고르지 못하면 점수 순서로
        picked = [(c, "core" if k < ncore else "related", "") for k, c in enumerate(cands[:nall])]
    out, nc = [], 0
    for c, role, why in picked[:nall]:
        if role == "core":
            nc += 1
            if nc > ncore:
                role = "related"
        m = ms.paper_meta(c["file"])
        out.append({"file": c["file"], "short": ms.paper_short(c["file"]), "title": str(m.get("title") or ""), "year": m.get("year", ""), "journal": m.get("journal", ""),
                    "top": _is_top(m.get("journal", ""), tops), "role": role, "why": why, "co": c["co"], "nsent": c["n"], "notes": [], "read": False})
    # 우선 저널이 아닌 연구 논문이 통째로 읽는 자리의 1/3 을 넘으면, ★ 논문 가운데 related 로 밀린 것과 자리를 바꾼다(★ 후보가 없으면 그대로 — 서재에 ★ 가 없는 주제도 있다).
    # 시험(실리콘, 2026-10-07): 절반으로 두니 Sonnet 이 AMT 5편을 core 에 두고 ★ Fang 1998(CIRP)을 related 로 밀었는데 바뀌는 것이 없었다
    weak_core = [p for p in out if p["role"] == "core" and not p["top"] and not _looks_review(p["title"])]
    top_rel = [p for p in out if p["role"] == "related" and p["top"]]
    while len(weak_core) > limit and top_rel:
        weak_core.pop()["role"] = "related"
        top_rel.pop(0)["role"] = "core"
    seen = {}
    for p in out:      # 같은 저자·연도가 둘이면 a, b 를 붙인다 (글에서 'Liu 등(2018)' 이 어느 논문인지 갈리게)
        seen.setdefault(p["short"], []).append(p)
    for short, ps in seen.items():
        if len(ps) > 1:
            for k, p in enumerate(ps):
                p["short"] = short + chr(97 + k)
    st["papers"] = out


# ---------- 4 읽기 ----------
_KINDS = {"def": "정의·개념", "bg": "배경", "method": "방법", "result": "결과", "mech": "기전", "factor": "영향 인자", "model": "모델", "limit": "한계·조건", "open": "쟁점"}


def _read_prompt(st, paper, text, part, nparts, nmax, mode):
    p = st["plan"]
    return (
        "연구자의 서재에 있는 논문 한 편을 읽고, 아래 [주제]에 관해 이 논문이 말하는 사실을 메모로 뽑는다. 이 메모들을 모아 전공 교과서의 한 장을 쓴다.\n"
        "교과서의 모든 문장은 메모의 근거 문장으로 되짚어 대조하므로, 메모는 논문의 문장이 실제로 말하는 것만 담아야 한다. 당신이 아는 지식을 보태지 않는다.\n\n"
        "JSON 으로만 답하라:\n"
        "{\"about\": \"이 논문이 무엇을 어떤 재료·조건·방법으로 했는지 한국어 한 문장\", \"type\": \"research|review|model|simulation\",\n"
        " \"notes\": [{\"pt\": \"한국어 한두 문장으로 적은 사실\", \"s\": [12, 13], \"kind\": \"def|bg|method|result|mech|factor|model|limit|open\", \"own\": true}]}\n"
        "규칙:\n"
        "- 메모 하나에는 사실 하나(한두 문장). 여러 사실은 메모를 나눈다 — 실험 조건을 한 메모에 길게 늘어놓지 않는다.\n"
        "- pt 는 s 에 적은 문장들이 말하는 것만. 그 문장에 없는 조건·수치·원인·해석을 보태지 않는다. 다른 문장에서 읽은 조건을 넣으려면 그 문장 번호도 s 에 넣는다(s 는 1~6개, [s12] 의 12). "
        "pt 의 수치는 프로그램이 s 의 문장과 글자로 대조해, 거기에 없는 수치가 하나라도 있으면 그 메모를 버린다.\n"
        "- 누가·어떤 재료·어떤 조건(결정 방위·공구·절삭 깊이·속도·온도·스케일, 실험인지 시뮬레이션인지)에서의 말인지가 pt 에 드러나게 쓴다 — 조건이 떨어진 사실은 교과서에서 일반 법칙처럼 잘못 쓰인다.\n"
        "- 장치 설정값·계산값(해상도·노출 시간·촬영 주파수·배율·장면 크기·화소 크기 같은 method 의 수치)은 그 값을 정한 전제(배율·절삭속도·요구 정확도·가정)가 같은 메모에 들어갈 때만 수치로 적고 그 전제의 문장 번호도 s 에 넣는다. "
        "전제가 글에 없으면 수치 없이 원리(무엇이 무엇을 정하는가, 무엇과 무엇 사이의 절충인가)만 적는다 — 전제 없는 설정값은 교과서에서 따라갈 수 없는 수의 나열이 된다.\n"
        "- 수치·단위·기호는 그 문장에 적힌 그대로(단위를 바꾸거나 반올림하지 않는다). 문장이 말로 적은 양(twice, half)은 말로(두 배, 절반).\n"
        "- PDF 에서 글을 꺼낼 때 μ 글자가 깨져 μm 가 'mm' 나 'lm' 로 적힌 논문이 있다. 문맥으로 보아 그렇게 깨진 것이 분명한 수치(나노·마이크로 절삭의 깊이가 'mm', 연삭 손상 깊이가 '수 mm' 같은 것)는 "
        "메모에 넣지 않는다 — 그대로 옮기면 틀린 단위가 실리고, 고쳐 적으면 원문과 달라진다. 수치 없이 사실만 적는다.\n"
        "- own: 이 논문이 자기 실험·해석·모델로 말한 것이면 true. 남의 연구를 전한 것(서론·리뷰의 문헌 소개, 인용 번호가 달린 문장)이면 false.\n"
        "- kind: def 정의·개념 / bg 배경·필요성 / method 실험·측정·해석 방법 / result 관찰·측정 결과 / mech 기전·원인 설명 / factor 영향 인자와 그 방향 / model 모델·식·예측 / limit 한계·성립 조건 / open 아직 모르는 것·논쟁\n"
        "- 주제에 관한 것만, 교과서에 실을 만한 것부터. 이 글의 길이면 %d개 안팎이 알맞고 많아야 %d개 — 긴 논문·리뷰는 절마다 빠짐없이 뽑는다. "
        "정의·개념·기전·성립 조건·방향성(극성)·한계 같은 설명 메모는 결과 수치만큼 중요하다(교과서의 설명 절은 이 메모로 쓴다). 같은 말을 되풀이하는 메모는 하나로 합친다. 그림·표를 가리키기만 하는 말, 논문의 구성 안내, 감사·서지 정보는 뺀다.\n"
        "- 이 글이 주제를 다루지 않으면 notes 를 비운다.\n%s"
        "- 전문 용어는 한국어로 쓰고 처음 나올 때 영어를 괄호에: 연성 영역 절삭(ductile-regime cutting).\n\n"
        "[주제] %s\n[장의 범위] %s\n[이 장이 답할 물음]\n%s\n\n"
        "[논문] %s — %s (%s %s)%s%s\n\n%s") % (
            max(12, int(nmax * 0.8)), nmax, ("" if paper.get("top") or _looks_review(paper.get("title")) else
                   "- 이 논문은 연구자가 정한 우선 저널의 것이 아니다. 배경·정의·방법·수치가 든 결과는 그대로 뽑되, 결론·해석·일반화(mech·factor·model·limit 의 단정)는 이 주제에 꼭 필요한 것만 뽑는다 — 교과서의 기둥은 우선 저널의 논문이 세운다.\n"),
            st["topic"][:400], p["scope"], "\n".join("- " + a for a in p["ask"]),
            paper["short"], paper["title"][:200], paper["journal"], paper["year"],
            (" · 전체 %d부분 가운데 %d번째" % (nparts, part + 1)) if nparts > 1 else "",
            (" · 주제 문장의 앞뒤만 발췌한 것이다((…) = 건너뜀)" if mode == "hits" else "") +
            (" · 이 논문은 μ 글자가 깨져 꺼내진 것으로 보인다(μm 가 mm·lm 로 적혀 있다) — 그런 수치는 메모에 넣지 않는다" if paper.get("unitwarn") else ""), text)


def _read_paper(st, pi, model, effort):
    """논문 하나 → {about, type, notes: [{pt, s[sid], kind, own}], parts, mode, dropped, fail}"""
    sents = _readable(_paper_sents(st["papers"][pi]["file"]))
    paper = dict(st["papers"][pi], unitwarn=_unit_garbled(sents))
    rx, must = _concept_rx(st["plan"]["concepts"])
    mode = "full" if paper["role"] == "core" else "hits"
    if mode == "hits":
        _hit, idx = _hits(sents, rx, must)
        chunks = [_windows(sents, idx)] if idx else []
        if not chunks or len(chunks[0]) < 3:
            chunks, mode = _parts(sents)[:1], "full"
    else:
        chunks = _parts(sents)
    is_rev = _looks_review(paper.get("title")) or paper.get("type") == "review"
    if mode == "full":
        # 메모 상한은 글의 길이를 따른다 — 부분마다 2,600자에 하나꼴로 24~60, 리뷰는 1/4 더. 전에는 32(한 부분)/20 고정이었고 Beyerlein 2014(리뷰 11만 자)가 24개만 돌아와
        # 극성 같은 개념 메모가 빠졌다(사용자 2026-10-09: '리뷰 논문은 특히 긴데 24개에 걸려 뭐가 없다 — 상한을 늘리자')
        per = sum(len(s["en"]) for ch in chunks for s in ch) / float(max(1, len(chunks)))
        nmax = max(24, min(60, int(per // 2600)))
        if is_rev:
            nmax = min(64, int(nmax * 1.25))
        elif not paper.get("top"):   # 우선 저널이 아닌 연구 논문 — 결론·해석 메모를 덜 뽑는다(수치·배경은 그대로)
            nmax = max(8, int(nmax * 0.75))
    else:
        nmax = 10
    about, typ, notes, dropped, fail = "", "", [], 0, 0
    for k, ch in enumerate(chunks):
        by = {s["sid"]: s for s in ch}
        d = _ask_json(st, _read_prompt(st, paper, _paper_text(ch), k, len(chunks), nmax, mode), model, effort, 1500, need="notes")
        if d is None:
            fail += 1
            continue
        about = about or str(d.get("about") or "").strip()[:300]
        typ = typ or str(d.get("type") or "").strip()[:12]
        for n in (d.get("notes") or [])[:nmax + 4]:
            if not isinstance(n, dict):
                continue
            pt = _clean(str(n.get("pt") or ""))
            sids = []
            for x in (n.get("s") if isinstance(n.get("s"), list) else [n.get("s")])[:8]:
                x = str(x).strip()
                sid = x if x in by else ("s" + x if ("s" + x) in by else ("x" + x if ("x" + x) in by else ""))
                if sid and sid not in sids:
                    sids.append(sid)
            if len(pt) < 8 or not sids:        # 근거 문장 번호가 이 글에 없는 메모는 버린다
                dropped += 1
                continue
            if _nums_missing(pt, [by[s]["en"] for s in sids]):   # 메모의 수치가 근거 문장에 없다
                dropped += 1
                continue
            notes.append({"pt": pt[:600], "s": sids[:6], "kind": n.get("kind") if n.get("kind") in _KINDS else "result", "own": n.get("own") is not False})
    return {"about": about, "type": typ, "notes": notes, "parts": len(chunks), "mode": mode, "dropped": dropped, "fail": fail, "all_fail": bool(chunks) and fail == len(chunks),
            "unitwarn": paper["unitwarn"]}


def _read_all(st, model):
    todo = [i for i, p in enumerate(st["papers"]) if not p.get("read")]
    total = len(st["papers"])
    _stage(st, "read", done=total - len(todo), total=total)
    if not todo:
        return
    failed = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(_read_paper, st, i, model, None): i for i in todo}
        for f in as_completed(futs):
            i = futs[f]
            try:
                r = f.result()
            except _Stop:
                r = None
            except Exception:
                r = {"all_fail": True, "notes": [], "about": "", "type": "", "parts": 0, "mode": "", "dropped": 0}
            if r is None:
                continue
            with _LOCK:
                p = st["papers"][i]
                if r.get("all_fail"):
                    failed += 1
                    p["fail"] = True
                else:
                    p.update(about=r["about"], type=r["type"], parts=r["parts"], mode=r["mode"], dropped=r["dropped"], read=True, unitwarn=bool(r.get("unitwarn")))
                    p.pop("fail", None)
                    p["notes"] = [dict(n, id="N%d.%d" % (i + 1, k + 1)) for k, n in enumerate(r["notes"])]
                st["prog"] = {"done": sum(1 for x in st["papers"] if x.get("read")), "total": total, "last": p["short"]}
            _save(st)
    _check_stop(st)
    if failed and failed >= max(2, len(todo) // 2):
        raise RuntimeError("논문 %d편을 읽지 못했습니다%s — 「이어서」 로 남은 논문부터 다시 읽습니다" % (failed, _why()))


def _all_notes(st):
    """→ {메모 id: (논문 순번, 메모)}"""
    return {n["id"]: (i, n) for i, p in enumerate(st["papers"]) if p.get("read") for n in p.get("notes") or []}


# ---------- 5 짜기 ----------
def _review_skeleton(st):
    """서재의 리뷰(읽은 논문 가운데 리뷰로 판정된 것)의 짜임 — 절 제목의 차례와 절마다 그 리뷰에서 뽑은 메모 → 짜기 물음의 [리뷰의 짜임]. 리뷰가 없으면 ''.
    사용자(2026-10-09): '너무 많은 내용을 하나에 우겨넣어 조리가 없다 — 차라리 리뷰 논문을 읽는 게 낫겠다' → 이 분야를 정리한 사람의 차례를 뼈대의 출발점으로 빌린다."""
    out = []
    for pi, p in enumerate(st["papers"]):
        if not p.get("read") or not p.get("notes"):
            continue
        if not (p.get("type") == "review" or _looks_review(p.get("title"))):
            continue
        try:
            sents = _paper_sents(p["file"])
        except Exception:
            continue
        by_sid, order = {}, []
        for x in sents:
            if x.get("zone") == "tail":
                continue
            sec = (x.get("sec") or "").strip()
            if sec and sec not in order:
                order.append(sec)
            by_sid[x["sid"]] = sec
        per = {}
        for n in p["notes"]:
            sec = next((by_sid[sid] for sid in (n.get("s") or []) if sid in by_sid and by_sid[sid]), "")
            per.setdefault(sec, []).append(n["id"])
        lines = ["## %s — %s" % (_pname(st, pi), str(p.get("title") or "")[:160])]
        line = ""
        try:
            line = ms._paper_line(p["file"])
        except Exception:
            line = ""
        if line:
            lines.append("요지: " + line[:400])
        for sec in order[:40]:
            ids = per.get(sec) or []
            lines.append("- %s%s" % (sec[:90], (" — 메모 " + ", ".join(ids[:10])) if ids else ""))
        if per.get(""):
            lines.append("- (절을 모르는 메모) " + ", ".join(per[""][:10]))
        out.append("\n".join(lines))
    return "\n\n".join(out[:2])


def _outline(st, model, effort):
    notes = _all_notes(st)
    p = st["plan"]
    plist = "\n".join("%s (%s) — %s" % (_pname(st, i), {"review": "리뷰", "model": "모델", "simulation": "시뮬레이션"}.get(x.get("type"), "연구"), x.get("about") or x["title"][:120])
                      for i, x in enumerate(st["papers"]) if x.get("read") and x.get("notes"))
    nlist = "\n".join("%s [%s%s] %s" % (nid, _KINDS.get(n["kind"], ""), "" if n.get("own") else " · 재인용", n["pt"]) for nid, (_i, n) in notes.items())
    try:
        review = _review_skeleton(st)
    except Exception:
        review = ""
    prompt = (
        "전공 교과서의 한 장을 짠다. 연구자의 서재 논문에서 뽑은 [메모]가 이 장의 재료 전부다(N번호의 앞 숫자 = 논문 P번호). 먼저 이 장의 논증 줄기를 세우고, 그 줄기의 단계로 절을 나눈 뒤, 절마다 쓸 메모를 고르라.\n"
        "JSON 으로만 답하라:\n"
        "{\"title\": \"장 제목(한국어)\",\n"
        " \"spine\": \"이 장의 논증 한 문단(한국어, 5~8문장): 무엇이 물음인가 → 그것을 설명하는 경쟁하는 설명(이론·기전)은 무엇인가 → 무엇이 그 설명들을 가르는가(어떤 관찰·조건·측정) → 무엇이 합의되고 무엇이 엇갈리는가 → 무엇이 남는가. 읽는 사람은 P·N 번호를 모른다 — 논문은 '저자 연도' 로.\",\n"
        " \"sections\": [{\"title\": \"절 제목(번호 없이)\", \"aim\": \"이 절이 답하는 물음 한 문장\", \"role\": \"줄기에서 이 절이 맡는 단계 한 구절(예: '경쟁하는 두 설명을 세운다', '둘을 가르는 관찰을 모은다')\", "
        "\"owns\": [\"이 절이 집인 개념·변수 (예: '절삭속도의 영향', '단열 전단띠의 정의') — 다른 절은 이것을 다시 설명하지 않고 가리키기만 한다\"], \"notes\": [\"N3.2\", \"N5.1\"]}],\n"
        " \"summary\": [\"N3.2\"],\n"
        " \"terms\": [{\"en\": \"ductile-regime cutting\", \"ko\": \"연성 영역 절삭\"}],\n"
        " \"gaps\": [\"[이 장이 답할 물음] 가운데 이 서재의 논문으로는 답할 수 없는 것, 한두 편에만 기대는 것, 논문끼리 엇갈리는 것(한국어 한 문장씩. 읽는 사람은 P·N 번호를 모른다 — 논문은 '저자 연도' 로 적고 '메모' 라는 말은 쓰지 않는다)\"]}\n"
        "짜는 법:\n"
        "- 줄기 먼저: spine 은 이 장이 배우는 사람에게 펴 보일 하나의 논증이다. 절은 그 줄기의 단계이지 주제의 서랍(구조·조건·결과·방법…)이 아니다 — 절마다 role 에 줄기의 어느 단계를 맡는지 적는다. 절은 4~7개.\n"
        "- 물음은 겹치지 않게: 한 물음은 한 절이 답한다. 같은 개념·변수가 여러 절에서 다시 설명되지 않도록, 개념마다 집이 되는 절을 하나 정해 owns 에 적는다(예: '절삭속도의 영향' 은 한 절에만). 다른 절은 그 개념을 한 구절로 가리키고 넘어간다.\n"
        "- 메모는 그 절의 물음에 꼭 필요한 것만 고른다. 모든 메모를 어느 절엔가 넣으려 하지 않는다 — 교과서는 서재의 색인이 아니다. 쓰지 않은 메모는 그냥 둔다. 한 메모는 한 절에만.\n"
        "- 절의 차례는 배우는 사람이 따라갈 논리다(무엇인가 → 무슨 일이 일어나는가 → 왜 → 무엇이 바꾸는가 → 어떻게 아는가 → 어디까지 아는가)를 줄기에 맞게 쓰되, 메모가 셋이 안 되는 절은 만들지 말고 가까운 절에 합친다.\n"
        "- [리뷰의 짜임]이 있으면 그것은 이 분야를 정리한 사람의 차례다. 절의 차례와 범위를 거기서 빌려 출발점으로 삼고(리뷰의 절 제목을 그대로 베끼지는 않는다), 이 장의 물음에 맞게 고친 뒤 리뷰 뒤의 논문과 리뷰가 다루지 않은 메모를 그 틀에 얹는다.\n"
        "- 절 제목은 내용을 말하는 명사구로(예: '임계 절삭 깊이와 취성–연성 전이'). '서론'·'결론'·'기타' 같은 제목은 쓰지 않는다.\n"
        "- summary 는 장 끝의 '핵심 정리'에 쓸 메모 6~10개(이 장에서 가장 중요한 사실). [논문]의 ★ 는 연구자가 정한 우선 저널 — summary 는 ★ 논문과 리뷰의 메모를 먼저, ★ 가 아닌 저널의 연구 논문 결론은 그것뿐일 때만.\n"
        "- terms 는 메모에 나온 전문 용어 15~40개의 영어와 한국어. 같은 영어 용어에 메모마다 다른 한국어가 쓰였으면 이 분야에서 가장 굳은 것 하나로 정한다. 굳은 한국어가 없는 말은 ko 를 영어 그대로 둔다.\n"
        "- 메모에 없는 것을 지어내지 않는다.\n\n"
        "[주제] %s\n[장의 범위] %s\n[이 장이 답할 물음]\n%s\n\n%s[논문]\n%s\n\n[메모]\n%s") % (
            st["topic"][:400], p["scope"], "\n".join("- " + a for a in p["ask"]), ("[리뷰의 짜임] (이 서재의 리뷰 논문의 절 차례와, 절마다 그 리뷰에서 뽑은 메모)\n" + review + "\n\n") if review else "", plist, nlist)
    d = _ask_json(st, prompt, model, effort, 1800, need="sections")
    if not d:
        raise RuntimeError("장의 짜임을 정하지 못했습니다" + _why())
    used, secs = set(), []
    for s in (d.get("sections") or [])[:10]:
        if not isinstance(s, dict):
            continue
        ids = []
        for x in s.get("notes") or []:
            x = str(x).strip()
            if x in notes and x not in used:
                used.add(x)
                ids.append(x)
        if len(ids) >= 2 and str(s.get("title") or "").strip():
            secs.append({"title": re.sub(r"^\s*\d+(\.\d+)*[.)]?\s*", "", str(s["title"]).strip())[:80], "aim": str(s.get("aim") or "").strip()[:240],
                         "role": str(s.get("role") or "").strip()[:160], "owns": [str(o).strip()[:40] for o in (s.get("owns") if isinstance(s.get("owns"), list) else [])[:8] if str(o).strip()], "notes": ids})
    if not secs:
        raise RuntimeError("장의 짜임을 정하지 못했습니다 (메모 %d개)" % len(notes))
    summ = [x for x in (str(y).strip() for y in d.get("summary") or []) if x in notes][:12]
    if len(summ) >= 3:
        secs.append({"title": "핵심 정리", "aim": "", "notes": summ, "kind": "summary"})
    terms, seen = [], set()
    for t in (d.get("terms") or [])[:60]:
        if isinstance(t, dict) and str(t.get("en") or "").strip() and str(t.get("ko") or "").strip():
            en = str(t["en"]).strip()[:60]
            if en.lower() not in seen:
                seen.add(en.lower())
                terms.append({"en": en, "ko": str(t["ko"]).strip()[:40]})
    def names(t):      # 빈 곳의 글에 남은 속 번호(P3)는 논문 이름으로, 메모 번호는 뗀다
        def one(m):
            i = int(m.group(1)) - 1
            return st["papers"][i]["short"] if 0 <= i < len(st["papers"]) else m.group(0)
        return re.sub(r"\bP(\d{1,2})\b", one, re.sub(r"\s*\(?N\d+\.\d+(?:\s*,\s*N\d+\.\d+)*\)?", "", t))
    st["outline"] = {"title": str(d.get("title") or p["title"]).strip()[:80], "spine": names(_clean(str(d.get("spine") or "")))[:1500], "sections": secs, "terms": terms,
                     "gaps": [names(str(x).strip())[:300] for x in (d.get("gaps") or [])[:8] if str(x).strip()], "unused": len(notes) - len(used), "review_based": bool(review)}


# 절의 논지 — 사용자(2026-10-07): 장이 '결과 나열' 로 읽힌다. 재 보니 두 논문 이상이 받치는 문장이 4~5%, 한 논문만의 문단이 41~64% 였다.
# 짜기가 '메모를 절에 나누기' 로 끝나 쓰는 쪽이 메모 목록을 차례로 풀어 썼기 때문 → 절마다 '여러 메모가 함께 말하는 것'(논지)을 먼저 세우고 문단은 논지마다.
# 어느 논지에도 들지 않는 메모는 버리지 않고 번외로(사용자: '연구에서 가장 중요한 건 의견이 합치되지 않는 부분').
_REL = {"agree": "여러 연구가 같은 것을 본다", "cond": "조건이 달라 값이나 양상이 다르다", "conflict": "연구끼리 엇갈린다", "extend": "한 연구가 다른 연구를 넓히거나 기전을 더한다", "single": "한 연구만 말한다"}


def _claims(st, k, model, effort):
    """절 하나의 논지와 번외 → outline.sections[k] 에 claims·extra·dropped·claimed 를 적는다. Claude 가 답하지 않으면 False.
    사용자(2026-10-09): 메모를 다 싣는 규칙이 절을 리뷰 한 장 분량으로 만들었다 → 논지 3~5개, 논지마다 대표 메모 하나둘(anchor)을 깊게, 나머지는 보기, 안 쓰는 메모는 버린다."""
    ol = st["outline"]
    sec = ol["sections"][k]
    plist, nlist = _note_block(st, sec["notes"], with_src=False)
    toc = "\n".join("%s %d. %s%s" % ("▶" if i == k else "  ", i + 1, s["title"], (" — " + s["aim"]) if s.get("aim") else "") for i, s in enumerate(ol["sections"]))
    prompt = (
        "전공 교과서의 한 절을 쓰기 전에 그 절의 논지를 세운다. 절의 재료는 연구자의 서재 논문에서 뽑은 [메모]뿐이다(N번호의 앞 숫자 = 논문 P번호, ★ = 우선 저널).\n"
        "JSON 으로만 답하라:\n"
        "{\"claims\": [{\"t\": \"논지 한 문장(한국어)\", \"anchor\": [\"이 논지를 가장 잘 받치는 대표 연구의 메모 1~2개\"], \"notes\": [\"대표를 포함해 이 논지에 쓸 메모 전부 — 보기로 한 문장이나 표의 행이 될 것까지\"], "
        "\"rel\": \"agree|cond|conflict|extend|single\", \"how\": \"메모들 사이의 관계 한 구절 — 무엇이 같고 무엇이(어떤 조건이) 다른지. 엇갈리면 어느 쪽이 널리 받아들여지는지(정설)와 반론\"}],\n"
        " \"extra\": [\"논지와 어긋나거나 특이해서 따로 적을 메모 번호 (5개까지)\"]}\n"
        "규칙:\n"
        "- 논지 = 이 절의 물음에 답하는 한 문장으로 적은 사실. 3~5개, 배우는 사람이 따라갈 차례로. 첫 논지는 물음에 대한 답이다 — 정설이 있으면 정설을 먼저 세우고 반론은 뒤의 논지나 how 에 둔다.\n"
        "- 고른다: 이 절의 물음에 꼭 필요한 메모만 notes 에 넣는다. 모든 메모를 어느 논지엔가 넣으려 하지 않는다 — 교과서는 서재의 색인이 아니다. 같은 사실을 되풀이하는 메모는 하나만. 한 메모는 한 논지에만.\n"
        "- anchor = 그 논지를 가장 잘 받치는 대표 연구의 메모 1~2개(★·리뷰·직접 실험·기전을 말하는 것 먼저). 글은 대표 메모를 깊게 펴고 나머지 notes 는 한 문장이나 표의 행으로만 쓴다.\n"
        "- rel: agree 여러 연구가 같은 것을 본다 / cond 조건(재료·방위·공구·깊이·속도·방법)이 달라 값이나 양상이 다르다 — how 에 무엇이 다른지 / "
        "conflict 연구끼리 엇갈린다 — 양쪽을 다 두되 how 에 정설과 반론을 가른다(엇갈리는 것은 배우는 사람에게 가장 중요한 정보다) / extend 한 연구가 다른 연구를 넓히거나 기전을 더한다 / single.\n"
        "- 논지 문장은 메모들이 말하는 것을 묶은 것이다. 메모에 없는 사실·수치·원인을 넣지 않고, 조건을 떼고 일반 법칙처럼 넓히지 않는다. 수치는 메모 그대로. 교과서적 상식을 보태지 않는다.\n"
        "- 저널의 무게: [논문]의 ★ 는 연구자가 정한 우선 저널이다. 논지의 받침은 ★ 논문과 리뷰의 결과·기전 메모로 먼저 세운다. ★ 가 아닌 저널의 연구 논문이 혼자 받치는 결과·기전·일반화는 논지로 세우지 않는다 — "
        "그 수치와 조건은 ★ 논문의 논지를 받치는 보기(agree·cond)나 표의 행으로 쓰고, 붙을 데가 없으면 쓰지 않는다. ★ 가 아닌 논문의 배경·정의·방법·재인용 메모는 저널과 무관하게 쓴다.\n"
        "- method 메모의 장치 설정값·계산값은 논지의 받침이 아니라 보기다 — 그 값을 정한 전제(배율·속도·가정)가 메모에 든 것만 보기로 쓰고, 설정값만 늘어놓은 메모는 쓰지 않는다(글에서 전제 없는 수의 나열이 된다).\n"
        "- 일반 개념(정의·기전·극성·성립 조건)의 논지를 한 재료·한 조건의 논문 메모 하나로만 받치지 않는다. 리뷰나 일반 논문의 메모가 없으면 그 개념은 그 재료의 절(차례)에 맡기고 이 절에서는 세우지 않는다 — "
        "특정 재료의 말('육방정 능면체 결정에서는 …')이 일반 설명의 자리에 들어가면 안 된다.\n"
        "- extra 는 논지와 어긋나거나 특이해서 따로 적을 가치가 있는 메모만(5개까지, 절 끝의 '번외'에 한 문장씩 실린다). 나머지 안 쓴 메모는 버린다.\n\n"
        "[장] %s — %s\n[장의 줄기] %s\n[절의 차례] (▶ = 이 절)\n%s\n\n[이 절] %s%s%s\n\n[논문]\n%s\n\n[메모]\n%s") % (
            ol["title"], st["plan"]["scope"], ol.get("spine") or "(없음)", toc, sec["title"], (" — 이 절이 답하는 물음: " + sec["aim"]) if sec.get("aim") else "",
            (" — 줄기에서 맡은 단계: " + sec["role"]) if sec.get("role") else "", plist, nlist)
    d = _ask_json(st, prompt, model, effort, 900, need="claims")
    claims, used = [], set()
    for c in ((d or {}).get("claims") or [])[:8]:
        if not isinstance(c, dict):
            continue
        ids = [x for x in (str(y).strip() for y in (c.get("notes") if isinstance(c.get("notes"), list) else [])) if x in sec["notes"] and x not in used]
        anchor = [x for x in (str(y).strip() for y in (c.get("anchor") if isinstance(c.get("anchor"), list) else [])) if x in sec["notes"] and x not in used]
        for a in anchor:
            if a not in ids:
                ids.insert(0, a)
        t = _clean(str(c.get("t") or "")).strip()
        if len(t) < 8 or not ids:
            continue
        used.update(ids)
        rel = c.get("rel") if c.get("rel") in _REL else ("single" if len({x.split(".")[0] for x in ids}) < 2 else "agree")
        claims.append({"t": t[:300], "notes": ids[:12], "anchor": [a for a in anchor if a in ids][:2] or ids[:1], "rel": rel, "how": str(c.get("how") or "").strip()[:240]})
    extra = [x for x in (str(y).strip() for y in ((d or {}).get("extra") if isinstance((d or {}).get("extra"), list) else [])) if x in sec["notes"] and x not in used][:5]
    with _LOCK:
        sec["claims"] = claims
        sec["extra"] = extra                                                  # 번외는 Claude 가 고른 것만(어긋나거나 특이한 것)
        sec["dropped"] = [x for x in sec["notes"] if x not in used and x not in extra]   # 쓰지 않는 메모 — 버린다(통계에만)
        sec["claimed"] = True
    return d is not None


def _claims_all(st, model, effort):
    todo = [k for k, s in enumerate(st["outline"]["sections"]) if not s.get("claimed") and s.get("kind") != "summary"]
    nsec = len(st["outline"]["sections"])
    _stage(st, "outline", done=nsec - len(todo), total=nsec)
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_claims, st, k, model, effort): k for k in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except _Stop:
                continue
            except Exception:
                pass
            with _LOCK:
                st["prog"] = {"done": sum(1 for s in st["outline"]["sections"] if s.get("claimed") or s.get("kind") == "summary"), "total": nsec}
            _save(st)
    _check_stop(st)


# ---------- 6 쓰기 ----------
def _note_block(st, ids, with_src=True):
    notes, lines, pset = _all_notes(st), [], []
    for nid in ids:
        if nid not in notes:
            continue
        pi, n = notes[nid]
        if pi not in pset:
            pset.append(pi)
        lines.append("%s [%s · %s] (P%d %s) %s" % (nid, _KINDS.get(n["kind"], ""), "이 논문의 결과·해석" if n.get("own") else "재인용 — 이 논문이 남의 연구를 전한 말", pi + 1, st["papers"][pi]["short"], n["pt"]))
        if with_src:
            by = {s["sid"]: s for s in _paper_sents(st["papers"][pi]["file"])}
            for sid in n["s"]:
                if sid in by:
                    lines.append("   %s: \"%s\"" % (sid, by[sid]["en"][:700]))
    plist = "\n".join("%s — %s" % (_pname(st, pi), st["papers"][pi].get("about") or st["papers"][pi]["title"][:120]) for pi in sorted(pset))
    return plist, "\n".join(lines)


def _claim_block(st, sec):
    """쓰기 물음의 재료: 논지마다 그 메모와 근거 원문, 끝에 번외 메모 → (논문 목록, 글, 논문 순번들)"""
    notes, lines, pset = _all_notes(st), [], []

    def memo(nid, tag=""):
        pi, n = notes[nid]
        if pi not in pset:
            pset.append(pi)
        out = ["  %s%s [%s · %s] (P%d %s) %s" % (nid, tag, _KINDS.get(n["kind"], ""), "이 논문의 결과·해석" if n.get("own") else "재인용 — 이 논문이 남의 연구를 전한 말", pi + 1, st["papers"][pi]["short"], n["pt"])]
        by = {s["sid"]: s for s in _paper_sents(st["papers"][pi]["file"])}
        for sid in n["s"]:
            if sid in by:
                out.append("     %s: \"%s\"" % (sid, by[sid]["en"][:700]))
        return out
    for i, c in enumerate(sec.get("claims") or [], 1):
        lines.append("논지 %d: %s" % (i, c["t"]))
        lines.append("  메모 사이: %s%s" % (_REL.get(c.get("rel"), ""), (" — " + c["how"]) if c.get("how") else ""))
        for nid in c["notes"]:
            if nid in notes:
                lines += memo(nid, " [대표 — 깊게]" if nid in (c.get("anchor") or []) else " [보기 — 한 문장이나 표의 행]")
        lines.append("")
    extra = [x for x in sec.get("extra") or [] if x in notes]
    if extra:
        lines.append("[번외] 논지와 어긋나거나 특이해서 따로 적는 메모 — 절 끝의 '### 번외' 아래에 메모마다 한 문장으로")
        for nid in extra:
            lines += memo(nid)
    plist = "\n".join("%s — %s" % (_pname(st, pi), st["papers"][pi].get("about") or st["papers"][pi]["title"][:120]) for pi in sorted(pset))
    return plist, "\n".join(lines), pset


def _fi():
    """그림 색인 모듈(figindex) — 공부의 cfg 로 한 번 초기화(서버든 시험 틀이든). 사용자(2026-10-08): '기존 연구들이 그려 놓은 스케메틱을 잘 써야 이해가 된다'"""
    import figindex
    if not figindex.cfg.get("DIR"):
        figindex.init(GEN_DIR=cfg["GEN_DIR"], ARCHIVE=cfg["ARCHIVE"], load_json=cfg["load_json"], save_json=cfg["save_json"], claude_run=cfg.get("claude_run"))
    return figindex


def _fig_index_all(st):
    """읽은 논문마다 그림 색인(없는 것만, Sonnet 4편 동시) — 쓰기가 도식을 고르고 풀어 쓰는 재료. 결과는 st.figidx 통계."""
    fi = _fi()
    items = [(p["file"], p.get("title") or "") for p in st["papers"] if p.get("read")]
    t0, done = time.time(), {"n": 0}
    with _LOCK:
        st["prog"] = {"done": 0, "total": len(items)}

    def tick(f, d):
        done["n"] += 1
        with _LOCK:
            st["prog"] = {"done": done["n"], "total": len(items)}
            tok = (d or {}).get("tok") or {}
            if tok and isinstance(st.get("tok"), dict):
                st["tok"]["in"] = st["tok"].get("in", 0) + int(tok.get("in") or 0)
                st["tok"]["cached"] = st["tok"].get("cached", 0) + int(tok.get("cached") or 0)
                st["calls"] = (st.get("calls") or 0) + 1
    res = fi.ensure(items, model="sonnet", workers=4, on_done=tick)
    nfig = nsch = 0
    for d in res.values():
        a, b = fi.summary(d)
        nfig += a
        nsch += b
    st["figidx"] = {"papers": len(items), "figs": nfig, "schem": nsch, "sec": round(time.time() - t0), "fail": sum(1 for d in res.values() if not (d or {}).get("ok"))}
    _log(st, "논문 %d편의 그림 %d장을 읽어 두었습니다 (도식·장치 %d장, %d초)" % (len(items), nfig, nsch, round(time.time() - t0)))


def _caps_of(f):
    """논문 하나의 그림·표 캡션(PDF 에서 읽은 원문 + 캡션.json 의 번역이 있으면), 메모리 캐시"""
    if f not in _CAP_CACHE:
        try:
            caps = figcrop.captions(os.path.join(cfg["ARCHIVE"], f))
        except Exception:
            caps = []
        ko = {}
        d = cfg["load_json"](os.path.join(cfg["GEN_DIR"], os.path.splitext(f)[0] + ".캡션.json"), None)
        for c in ((d or {}).get("captions") or []) if isinstance(d, dict) else []:
            if isinstance(c, dict) and c.get("ko"):
                ko[(c.get("kind"), c.get("n"))] = str(c["ko"])
        _CAP_CACHE[f] = [dict(c, ko=ko.get((c["kind"], c["n"]), "")) for c in caps]
    return _CAP_CACHE[f]


_FIGREF = re.compile(r"\b(Fig\.?|Figure|Table)\s*(\d{1,3})(?!\d)", re.I)


def _fig_list(st, sec):
    """[그림 목록] — 두 갈래: (가) 논지의 근거 문장(±2문장)이 'Fig. N' 으로 가리키는 그림·표(사진·그래프·도식), (나) 그 절 논문들의 도식·장치 그림(그림 색인의 type schematic·setup — 근거 문장이 가리키지 않아도 후보).
    → (목록 글, {(논문 순번, kind, n): {cap, src: ['pi:sid', …], desc, ftype}}). 모든 후보에 '그림 읽기'(figindex: 그림을 본 Sonnet 의 설명)를 붙여 쓰는 Claude 가 그림을 보지 않고도 고르게 한다.
    사용자(2026-10-07): '그림이 본문과 잘 맞지 않는다' → 근거 문장이 가리키는 것만으로 좁혔더니(16장 중 8장) 기전을 그린 도식이 거의 못 들었다(결과 문장은 도식을 가리키지 않는다).
    사용자(2026-10-08): '기존 연구들이 그려 놓은 스케메틱을 잘 써야 이해가 된다' → 도식·장치 그림은 (나) 로 더한다(절에 12장까지, ★·통째로 읽은 논문부터)."""
    fi = _fi()
    notes = _all_notes(st)
    lines, allow, pset, idx = [], {}, [], {}

    def index(pi):
        if pi not in idx:
            try:
                idx[pi] = fi.load(st["papers"][pi]["file"])
            except Exception:
                idx[pi] = None
        return idx[pi]

    def desc_of(pi, kind, n):
        d = index(pi) if kind == "fig" else None
        return (next((x for x in (d or {}).get("figs") or [] if x.get("n") == n), None) if d else None) or {}

    def reading(x):
        return ("    그림 읽기: %s%s" % (x["what"][:300], (" — " + x["shows"][:160]) if x.get("shows") else "")) if x.get("what") else ""
    for ci, c in enumerate(sec.get("claims") or [], 1):
        found = {}
        for nid in c.get("notes") or []:
            if nid not in notes:
                continue
            pi, n = notes[nid]
            if pi not in pset:
                pset.append(pi)
            ss = _paper_sents(st["papers"][pi]["file"])
            order = {s["sid"]: i for i, s in enumerate(ss)}
            for sid in n["s"]:
                if sid not in order:
                    continue
                i = order[sid]
                for j in range(max(0, i - 2), min(len(ss), i + 3)):
                    for m in _FIGREF.finditer(ss[j]["en"]):
                        k = (pi, "table" if m.group(1).lower().startswith("t") else "fig", int(m.group(2)))
                        if ss[j]["sid"] not in found.setdefault(k, []):
                            found[k].append(ss[j]["sid"])
        for k, sids in list(found.items())[:4]:
            pi, kind, n = k
            cap = next((x for x in _caps_of(st["papers"][pi]["file"]) if x["kind"] == kind and x["n"] == n), None)
            if not cap:
                continue
            label = "P%d %s %d" % (pi + 1, "Fig." if kind == "fig" else "Table", n)
            x = desc_of(pi, kind, n)
            allow.setdefault(k, {"cap": "%s: %s" % (label, cap["text"][:400]), "src": ["%d:%s" % (pi, s) for s in sids[:4]], "desc": x.get("what", ""), "ftype": x.get("type", "")})
            by = {s["sid"]: s for s in _paper_sents(st["papers"][pi]["file"])}
            lines.append("논지 %d 의 근거 문장이 가리키는 그림 — %s (p.%d)%s: %s%s" % (ci, label, cap["page"], (" [%s]" % fi.TYPES.get(x["type"], x["type"])) if x.get("type") else "", cap["text"][:200], (" — " + cap["ko"][:160]) if cap.get("ko") else ""))
            if reading(x):
                lines.append(reading(x))
            for s in sids[:3]:
                lines.append("    %s: \"%s\"" % (s, by[s]["en"][:300]))
    for nid in sec.get("extra") or []:        # 번외 메모의 논문도 이 절의 논문이다
        if nid in notes and notes[nid][0] not in pset:
            pset.append(notes[nid][0])
    # (나) 도식·장치 그림: 이 절 논문들의 그림 색인에서 — 근거 문장이 가리키지 않아도 후보. ★·통째로 읽은 논문부터, 절에 12장까지
    # (나) 후보는 이 절의 대표(anchor) 메모를 낸 논문이나 이 절에 메모 둘 이상을 댄 논문의 것만 — 메모 하나로 스친 논문의 도식(쌍정 장 1절에 끼어든 사파이어 결정면 그림, 2026-10-09)이 후보에 들지 않게
    cnt, strong = {}, set()
    for c in sec.get("claims") or []:
        for nid in c.get("notes") or []:
            if nid in notes:
                cnt[notes[nid][0]] = cnt.get(notes[nid][0], 0) + 1
        for nid in c.get("anchor") or []:
            if nid in notes:
                strong.add(notes[nid][0])
    strong |= {pi for pi, k in cnt.items() if k >= 2}
    schem = []
    for pi in sorted([p for p in pset if p in strong], key=lambda i: (0 if st["papers"][i].get("top") else 1, 0 if st["papers"][i].get("role") == "core" else 1, i)):
        for x in (index(pi) or {}).get("figs") or []:
            k = (pi, "fig", x.get("n"))
            if x.get("type") in ("schematic", "setup") and x.get("quality") != "poor" and x.get("what") and k not in allow:
                schem.append((pi, x))
    for pi, x in schem[:12]:
        k = (pi, "fig", x["n"])
        label = "P%d Fig. %d" % (pi + 1, x["n"])
        allow[k] = {"cap": "%s: %s" % (label, x["cap"][:400]), "src": [], "desc": x.get("what", ""), "ftype": x.get("type", "")}
        lines.append("[%s — 근거 문장이 가리키지 않는 그림: 개념·기전·장치를 처음 설명하는 자리에만] %s (p.%s): %s" % (fi.TYPES.get(x["type"], "도식"), label, x.get("page", "?"), x["cap"][:200]))
        lines.append(reading(x))
    return "\n".join(lines[:110]), allow


_WRITE_RULES = (
    "쓰는 법:\n"
    "1. 교과서답게 쓴다 — 한 절은 한 편의 글이다(유체역학·고체역학 교과서의 한 절처럼). [설계도]는 이 절이 펴는 생각의 차례와 그 받침일 뿐, 문단의 틀이 아니다. "
    "문단은 앞 문단이 끝낸 데서 시작하고(앞 문단의 마지막 생각을 받는 말이나 그 대상을 주어로), 끝에서 다음 문단으로 넘긴다. 생각(논지)을 머리 문장으로 세우지 않고 설명하면서 드러나게 한다. "
    "연구는 설명의 근거로 문장 안에 녹인다 — '…조건에서는 …가 관찰된다' 처럼 사실과 조건을 주어로. 누구의 연구인지는 줄 끝의 메모 번호(인용)가 알려 주므로 연구자 이름을 문장에 넣지 않아도 된다. "
    "논문을 하나씩 소개하는 글('A 는 …했다. B 는 …했다.')도, 주장 한 문장 뒤에 연구를 줄 세우는 글도 아니다. "
    "메모는 글의 재료이지 문장의 단위가 아니다 — 메모를 하나씩 문장으로 옮겨 늘어놓으면 정보의 나열이 된다. 서로 이어진 사실(같은 대상의 성질들, 조건과 결과, 원인과 결과, 견줌)은 한 문장이나 한 호흡의 두 문장으로 묶고 "
    "그 관계를 말로 드러낸다('…이면서도', '…이어서', '다만', '그래서'). 메모의 근거 원문을 문장 단위로 되풀이해 옮기지 않는다.\n"
    "1-1. 잇는 문장을 쓴다: 사실이 없는 이음 문장 — 앞을 받아 다음을 여는 말, 개념과 개념을 잇는 말, 왜 이것을 보는지 말하는 말 — 을 ⟦-⟧ 로 자유롭게 쓴다. "
    "모든 문장이 논문에서 오면 글이 되지 않는다. 다만 잇는 문장에 새 사실·수치·원인을 담지 않는다(그런 것은 메모가 있어야 한다). "
    "잇는 문장은 평서문이 기본이다 — 앞 문단의 결론을 받아 다음 대상을 주어로 세운다. 물음 꼴('…인가?', '남는 물음은 …이다', '…따져 보아야 한다')은 절에 한둘까지, 문단마다 되풀이하지 않는다.\n"
    "2. 사실 문장은 메모와 그 근거 문장이 말하는 데까지만. 조건(재료·결정 방위·공구·절삭 깊이·속도·온도·스케일, 실험인지 시뮬레이션인지)을 떼어 일반 법칙처럼 쓰지 않는다 — "
    "'…에서 …가 관찰되었다', '… 조건에서는 …' 처럼 조건과 함께 쓴다. 한 연구의 결과를 '일반적으로'·'항상' 으로 넓히지 않고, 근거가 조심스럽게 말한 것(may, suggest)을 단정으로 바꾸지 않는다. 뜻만 지키면 표현은 자유다 — 원문이나 메모의 문장 구조를 따라 옮겨 쓰지 않고, 원문이 '곧 …' 하고 풀어 준 뜻은 같이 옮긴다.\n"
    "3. 수치·단위·기호는 근거 문장에 적힌 그대로. 근거가 말로 적은 양(twice, half)은 말로(두 배, 절반). 메모에 없는 수치를 만들지 않는다.\n"
    "4. 메모에 없는 내용은 쓰지 않는다 — 당신이 아는 교과서적 상식이라도. 설명에 빈 곳이 있으면 비워 둔다(이 책은 서재가 말하는 것만 싣는다).\n"
    "5. '재인용' 메모(그 논문이 남의 연구를 전한 말)는 그 논문의 저자가 한 일처럼 쓰지 않는다 — 그 논문의 저자를 주어로 세우지 않고 사실을 주어로 쓴다. "
    "문장마다 '…로 알려져 있다'·'…라고 보고되어 있다'를 붙이지는 않는다(교과서는 사실을 평서문으로 말하고, 누가 한 말인지는 인용이 알려 준다). 근거 문장에 원래 연구자의 이름이 있으면 그 이름은 써도 된다.\n"
    "6. 연구자 이름은 문장의 주어가 아니라 인용이다. 사실·현상·기전·수치는 사실을 주어로 쓴다('절삭 깊이가 커지면 가공면은 … 을 차례로 거친다'). 연구자를 주어로 세우는 문장('Yan 등(2003)은 …')과 이름을 조건 자리에 끼운 꼴('Liu 등(2019)의 테이퍼 절삭에서는')은 "
    "그 연구만의 장치·방법을 말해야 할 때만, 절에 두셋까지. 그때는 [논문]에 적힌 이름과 연도만 쓴다. 인용 번호는 쓰지 않는다(프로그램이 붙인다).\n"
    "7. 논문끼리 결과나 설명이 다르면 한쪽으로 뭉개지 말고 둘 다 조건과 함께 적는다. 연구들이 같은 것을 보았는지, 어디서 갈리는지는 설명의 흐름 속에서 말하고, 그 문장에는 관계된 메모를 모두 ⟦ ⟧ 에 적는다 — "
    "근거 문장들이 실제로 그 관계를 보일 때만. 앞 문장들을 되풀이하는 정리 문장은 문단마다 두지 않는다(절 끝에 한 번 — 규칙 16).\n"
    "8. 한국어 문장: 평서문(~다). 번역투와 명사 나열을 피하고 한 문장에 한 가지를 말한다. 전문 용어는 [용어]의 한국어를 쓰고 이 절에서 처음 나올 때 영어를 괄호에 넣는다. 수식은 말로 풀어 쓴다.\n"
    "9. 문장의 크기: 문장은 생각의 단위다. 한 생각을 받치는 사실이 여럿이면 한 문장에 묶고(메모 번호를 모두 적는다), 한 메모가 조건·해석을 길게 담으면 둘로 나눈다. "
    "다만 실험의 세부(장치·속도·날끝 반경·관찰 수단)를 한 문장에 다 싣지 않는다 — 그 사실이 언제 참인지를 정하는 조건 하나둘만 두고 나머지는 뺀다. 괄호 삽입은 처음 나온 영어 용어에만. "
    "읽는 사람은 메모가 몇 개였는지 모른다 — 문단을 읽고 나면 사실의 목록이 아니라 하나의 설명이어야 한다. 잇는 문장은 필요한 만큼. 설계도의 메모를 다 쓰지 않는다: 논지마다 [대표] 메모 하나둘을 두세 문장으로 깊게(무엇을 어떤 조건에서 어떻게 보았나), [보기] 메모는 한 문장이나 표의 행으로, 붙을 자리가 없으면 뺀다. 교과서는 서재의 색인이 아니다.\n"
    "10. 절의 첫 문단은 [앞 절]이 끝낸 생각을 받아 시작한다(잇는 문장 한둘로 — '이 절은 …를 다룬다' 같은 안내문이 아니라 내용을 잇는 말). 절의 끝은 [다음 절]의 물음으로 자연스럽게 넘긴다(한 문장, 예고가 아니라 이음). [장의 줄기]와 [이 절의 자리]를 따른다 — 이 절은 줄기의 한 단계이지 주제의 서랍이 아니다. [다른 절이 맡은 개념]은 여기서 다시 설명하지 않고 한 구절로 가리키고 넘어간다('…는 6절에서 본다' 는 괜찮다). "
    "소제목(###)은 절이 길 때만 둘에서 넷, 문단마다 달지 않는다.\n"
    "11. 단위가 'mm'·'lm' 로 적혀 있지만 문맥으로 보아 μm 의 글자가 깨진 것이 분명한 수치는 쓰지 않는다(수치 없이 말하거나 그 사실을 뺀다).\n"
    "12. 표: 셋 이상의 연구가 견줄 만한 수치(값과 조건)를 주면 표로 묶는다 — '표: 캡션' 한 줄 다음에 | 로 칸을 나눈 머리 행과 자료 행, 자료 행마다 끝에 그 행의 근거인 메모 번호. "
    "칸의 수치·단위·조건은 근거 그대로(표의 행도 본문 문장과 똑같이 원문과 대조된다). 표에 넣은 수치를 본문 문장에 되풀이하지 않는다. 절마다 많아야 둘.\n"
    "13. 그림: [그림 목록]에는 두 갈래가 있다 — (가) 설계도의 근거 문장이 가리키는 그림(사진·그래프·도식) (나) 이 절 논문들의 도식·장치 그림(근거 문장이 가리키지 않아도 된다). 교과서처럼 놓는다: "
    "개념·기전·장치를 처음 설명하는 문단에는 그것을 그린 도식 하나(나), 관찰을 말하는 대목 옆에 사진, 경향·수치를 말하는 대목 옆에 그래프(가). 절에 둘에서 넷, 같은 그림을 두 번 싣지 않는다. 그림을 채우려고 고르지 않는다 — 그 그림 없이도 설명이 서면 넣지 않는다. "
    "(나)의 도식은 그 문단이 그 개념·장치를 실제로 설명할 때만 — 뒤의 절에서 쓸 그림을 미리 싣거나 '다른 절에서 다룬다' 며 거는 것, 이 절이 다루지 않는 재료의 그림(금속의 결정학 절에 사파이어 결정면 그림 같은)은 넣지 않는다. "
    "그림을 넣을 때는 그 그림을 걸고 풀어 쓴다: 앞 문단의 한 문장이 ⟨그림⟩ 으로 가리키고('…가 ⟨그림⟩에 보인다' — 번호는 프로그램이 붙인다), 바로 뒤에 '그림: P5 Fig. 4 — 설명. ⟦메모 번호⟧' 한 줄. "
    "설명은 한두 문장으로 무엇이 무엇인지(부분·화살표·영역·축이 가리키는 것)와 독자가 봐야 할 곳을 말한다 — 캡션·그림 읽기·그 그림을 가리키는 원문 문장이 말하는 것만, 그 밖의 수치·결론은 보태지 않는다. "
    "도식(나)의 설명은 메모 번호가 없어도 된다(⟦-⟧ — 근거는 캡션과 그림 읽기). 표도 같은 식으로 ⟨표⟩. 목록에 없는 그림은 쓸 수 없다(프로그램이 논문 PDF 에서 그 그림을 잘라 싣는다).\n"
    "14. 번외: 설계도에 [번외]가 있을 때만, 절 끝 '### 번외' 아래에 그 메모를 메모마다 한 문장으로(조건과 함께). 논지와 어긋나는 메모는 어긋난다고 적는다.\n"
    "15. 저널의 무게: [논문]의 ★ 는 연구자가 정한 우선 저널이다. 설명의 기둥(현상·기전·법칙을 말하는 문장)은 ★ 논문과 리뷰의 메모로 세운다. ★ 가 아닌 저널의 연구 논문의 결과는 그 조건과 수치를 보기로 덧붙이는 자리('…에서도 …가 관찰되었다', 표의 행)에 두고, "
    "그 결론을 일반화하는 문장의 받침으로 삼지 않는다. 배경·정의·방법·재인용 메모는 저널과 무관하게 쓴다.\n"
    "16. 분량과 수치: 한 절은 20~35문장이다(핵심 정리 제외). 리뷰 논문의 한 소절처럼 — 많이 싣는 것이 아니라 고른 것을 깊게 설명하는 것이 교과서다. 수치는 본문에 논지를 세우는 대표값 하나만 두고, 견줄 수치가 셋 이상이면 표로 옮긴다(표의 수치는 본문에 되풀이하지 않는다). 절의 끝은 두세 문장으로 이 절이 세운 것을 묶고 [다음 절]로 넘긴다(⟦-⟧ 로, 새 사실 없이).\n"
    "17. 수치는 전제와 함께: 한 연구의 장치 설정값·계산값(해상도·노출 시간·주파수·장면 크기·화소 크기 같은 것)은 그것을 정한 전제(배율·절삭속도·요구 정확도·가정)가 같은 문장에 있을 때만 싣는다. "
    "'해상도를 512×512 로 두면 장면이 0.58 mm 가 된다' 처럼 전제 없이 결과만 적으면 읽는 사람은 그 수가 어디서 나오는지 따라갈 수 없다 — 그런 수의 나열(짜깁기)은 쓰지 않는다. "
    "방법의 세부는 교과서의 설명이 아니다: 원리(무엇과 무엇 사이의 절충인가, 무엇이 무엇을 정하는가)를 말로 설명하고, 한 연구의 값은 전제가 든 보기 한 문장('예컨대 ×15 배율·절삭속도 3 m/min 에서는 노출 50 μs 가 번짐을 두 화소 안에 묶었다')으로만, 그것도 논지에 필요할 때만. "
    "전제가 메모·근거에 없으면 수치를 빼고 원리만 쓴다.\n")


# Stylus — 원고의 글쓰기 지능과 같은 규칙을 공부의 글에도 (사용자 2026-10-06: '공부 기능에 글 쓸 때 기존에 쓰던 글 지능인 Stylus 적용'). 규칙은 manuscript._LIBX_STYLE 한 곳에만 두고 여기서 고른다.
_STYLUS_PICK = ("문단은 사실을 한 문장씩", "넓게 스치지 말고 깊게 편다", "모든 문장은 앞의 어느 문장을", "새 대목을 여는 문장", "주어에 앞에서 쓰지 않은 한정어", "문장의 크기는",
                "문장의 첫머리(주어)는", "같은 것을 다시 가리킬 때는 줄인다", "조건·범위의 부사구", "덧붙일 말", "여러 논문이 같은 점을 받치면", "같은 사실을 두 번 말하지 마라", "마지막 문장은 앞을 요약하거나")


def _stylus_block():
    """쓰기 물음에 붙는 Stylus 규칙 + 이 분야 논문의 짜임·관계 사례(서재에서 잰 것). 물음의 % 형식에 끼우지 말고 값으로 넣는다(사례 글에 % 가 있다)."""
    try:
        rules = ms._style_pick(_STYLUS_PICK)
    except Exception:
        rules = ""
    try:
        norms = ms._norms_text(ms._style_norms(), None)
    except Exception:
        norms = ""
    return ("[Stylus — 문장과 문단의 규칙] 이 연구자의 서재에 있는 논문(IJMTM·JMPT)의 글을 재서 만든 규칙이다. 영어 논문에서 잰 것이라 보기가 영어지만 한국어 글에도 그대로 적용한다 — "
            "It·They 는 '이는'·'이것'·'그것' 이나 주어 생략으로, [@A][@B] 인용 묶음은 줄 끝의 메모 번호 묶음(⟦N3.2, N7.1⟧)으로, '인용'은 메모 번호로 읽는다. "
            "이 글은 논문의 서론이 아니라 교과서다 — 인용 자리·인용 비율·'인용 나열'에 관한 줄은 적용하지 않고, 문장의 이음·새 대목을 여는 법·문장의 크기와 첫머리에 관한 줄만 적용한다. "
            "낱말 수·문장 수의 숫자는 이 절에 적용하지 않는다(절의 분량은 메모가 받치는 만큼). 위 '쓰는 법'과 어긋나면 위 '쓰는 법'이 우선이다.\n"
            + rules + ("\n" + norms if norms else ""))


def _write_prompt(st, k):
    """→ (물음, 고를 수 있는 그림 {(논문 순번, kind, n): 캡션})"""
    ol = st["outline"]
    sec = ol["sections"][k]
    claimed = bool(sec.get("claims")) and sec.get("kind") != "summary"
    figs = {}
    if claimed:
        plist, nlist, pset = _claim_block(st, sec)
        figtxt, figs = _fig_list(st, sec)
        body_secs = [s for s in ol["sections"] if s.get("kind") != "summary"]
        pos = body_secs.index(sec) if sec in body_secs else -1
        prev_s = body_secs[pos - 1] if pos > 0 else None
        next_s = body_secs[pos + 1] if 0 <= pos < len(body_secs) - 1 else None
        near = ("[앞 절] %s — 끝낸 생각: %s\n" % (prev_s["title"], " / ".join(c["t"] for c in (prev_s.get("claims") or [])[-2:]) or prev_s.get("aim", "")) if prev_s else "[앞 절] 없음 — 이 절이 장의 첫 절이다. 장의 범위를 여는 잇는 문장으로 시작한다.\n") + \
               ("[다음 절] %s — %s\n" % (next_s["title"], next_s.get("aim", "")) if next_s else "[다음 절] 없음 — 이 절이 본문의 마지막 절이다.\n")
        material = ("[설계도] 이 절이 펴는 생각의 차례와 그 받침 — 문단의 틀이 아니라 설계도다. 생각 아래가 그것을 받치는 메모이고, 메모 아래는 그 근거인 논문의 원문 문장이다. 글은 이 차례를 따라 한 편으로 이어 쓴다.\n%s\n\n%s\n"
                    "[그림 목록] (두 갈래 — 설계도의 근거 문장이 'Fig. N' 으로 가리키는 논문의 그림·표(그 그림을 가리키는 원문 문장과 함께), 그리고 이 절 논문들의 도식·장치 그림. '그림 읽기' 는 그림을 본 Claude 가 적은 것 — 어느 그림이 무엇을 보여 주는지 그것으로 안다. 여기 있는 것만 고를 수 있다. 비어 있으면 이 절에는 그림을 넣지 않는다)\n%s") % (nlist, near, figtxt or "(없음)")
    else:
        plist, nlist = _note_block(st, sec["notes"])
        material = "[메모] (메모 아래는 그 근거인 논문의 원문 문장)\n%s" % nlist
    terms = ", ".join("%s = %s" % (t["en"], t["ko"]) for t in ol.get("terms") or []) or "(없음)"
    toc = "\n".join("%s %d. %s%s" % ("▶" if i == k else "  ", i + 1, s["title"], (" — " + s["aim"]) if s.get("aim") else "") for i, s in enumerate(ol["sections"]))
    head = ("당신은 기계가공·재료 분야의 전공 교과서를 쓰는 저자다. 연구자의 서재에 있는 논문에서 뽑은 [메모]만으로 아래 장의 한 절을 쓴다.\n"
            "이 책의 약속: 모든 사실은 서재 논문의 실제 문장으로 되짚어 확인된다. 쓴 뒤 문장마다 근거 문장과 대조해, 근거가 말하지 않는 것은 지운다. 그러니 메모와 그 근거 문장이 말하는 데까지만 쓴다.\n\n")
    if sec.get("kind") == "summary":
        form = ("출력 형식 — 이 형식만 쓴다:\n<<<SEC>>>\n핵심 사실 하나를 조건과 함께 적은 문장. ⟦N3.2⟧\n핵심 사실 하나. ⟦N3.4, N7.1⟧\n<<<END>>>\n"
                "- 이 절은 장 끝의 '핵심 정리'다. 6~10줄, 줄마다 이 장의 핵심 사실 하나를 한 문장으로. 줄 끝의 ⟦ ⟧ 안에 근거인 메모 번호. 소제목·이음 문장·문단 나눔 없이.\n\n")
    elif claimed:
        form = ("출력 형식 — 이 형식만 쓴다:\n<<<SEC>>>\n앞 절을 받아 이 절을 여는 문장(사실 없음). ⟦-⟧\n사실을 말하는 문장(조건과 함께). ⟦N3.2⟧\n이어진 두 사실을 관계를 드러내며 한 문장에. ⟦N5.1, N3.2⟧\n그 사실들이 뜻하는 것을 받아 다음 사실로 넘어가는 문장. ⟦N7.1⟧\n다음 문단으로 넘기는 문장(사실 없음). ⟦-⟧\n¶\n"
                "앞 문단을 받아 이어 가는 문장. ⟦N7.1⟧\n…가 ⟨그림⟩에 보인다 하고 그림을 가리키는 문장. ⟦N5.1⟧\n그림: P5 Fig. 4 — 그림을 풀어 쓰는 한두 문장(무엇이 무엇이고 독자가 어디를 봐야 하는지). ⟦N5.1⟧\n¶\n…\n표: 보고된 임계 절삭 두께\n| 논문 | 조건 | 임계 두께 | 판정 방법 |\n| Fang 1998 | (100) Si, 0° 공구 | 236 nm | 홈 표면의 균열 | ⟦N4.3⟧\n¶\n### 번외\n번외 메모 하나를 조건과 함께 한 문장으로. ⟦N7.4⟧\n<<<END>>>\n"
                "- 한 줄에 한 문장. 줄 끝의 ⟦ ⟧ 안에 그 문장의 근거인 메모 번호. ¶ 한 줄은 문단을 나눈다. 표는 '표:' 줄로 시작하고 행마다 끝에 메모 번호. 그림은 '그림:' 한 줄(그 줄의 설명은 한두 문장이어도 된다).\n"
                "- ⟦-⟧ = 사실이 없는 잇는 문장(앞을 받아 다음을 여는 말, 개념을 잇는 말, 왜 이것을 보는지 말하는 말). 새 사실·수치·원인을 담지 않는다. 사실을 말하는 문장이면 메모 번호를 적는다.\n\n")
    else:
        form = ("출력 형식 — 이 형식만 쓴다:\n<<<SEC>>>\n문장 하나. ⟦N3.2⟧\n문장 하나. ⟦N3.4, N7.1⟧\n¶\n### 소제목 (필요할 때만)\n앞의 사실들을 묶는 문장. ⟦-⟧\n<<<END>>>\n"
                "- 한 줄에 한 문장. 줄 끝의 ⟦ ⟧ 안에 그 문장의 근거인 메모 번호를 적는다. ¶ 한 줄은 문단을 나눈다.\n"
                "- ⟦-⟧ 는 이음 문장(이 절이 무엇을 다루는지 알리거나, 바로 앞에 쓴 사실들을 묶거나, 다음으로 넘기는 말)에만 쓴다. 이음 문장에는 새 사실·수치·원인을 담지 않는다. 사실을 말하는 문장이면 주제문이어도 메모 번호를 적는다.\n\n")
    spine = ""
    if ol.get("spine"):
        others = [(x["title"], x.get("owns") or []) for x in ol["sections"] if x is not sec and x.get("owns")]
        spine = ("[장의 줄기] %s\n[이 절의 자리] %s\n[이 절이 맡은 개념 — 여기서 설명한다] %s\n[다른 절이 맡은 개념 — 여기서는 가리키기만] %s\n" % (
            ol["spine"], sec.get("role") or "(없음)", ", ".join(sec.get("owns") or []) or "(없음)", " / ".join("%s: %s" % (t, ", ".join(o)) for t, o in others) or "(없음)"))
    prompt = (head + "[장] %s — %s\n[절의 차례] (▶ = 지금 쓸 절)\n%s\n\n[쓸 절] %s%s\n%s\n" + form + _WRITE_RULES + "\n%s\n[논문]\n%s\n\n[용어]\n%s\n\n%s") % (
        ol["title"], st["plan"]["scope"], toc, sec["title"], (" — 이 절이 답하는 물음: " + sec["aim"]) if sec.get("aim") else "", spine,
        _stylus_block() if sec.get("kind") != "summary" else "", plist, terms, material)
    return prompt, figs


_REF_OPEN, _REF_CLOSE = r"[⟦⟨〈《〚\[]{1,2}", r"[⟧⟩〉》〛\]]{1,2}"   # 닫는 괄호를 다른 글자로 쓴 것(⟦N8.5⟩)도 읽는다
_REF_END = re.compile(r"\s*" + _REF_OPEN + r"\s*((?:N\d+\.\d+|-|–|—)(?:\s*[,;]\s*N\d+\.\d+)*)\s*" + _REF_CLOSE + r"\s*[.]?\s*$")
_REF_ANY = re.compile(r"\s*" + _REF_OPEN + r"\s*(?:N\d+\.\d+|-)(?:\s*[,;]\s*N\d+\.\d+)*\s*" + _REF_CLOSE)


_FIG_LINE = re.compile(r"^(?:그림|figure|fig\.?)\s*[:：]\s*P\s*(\d{1,2})\s*(fig\.?|figure|table|표|그림)\s*(\d{1,3})\s*[—\-–:.]*\s*(.*)$", re.I)
_CLAIM_MARK = re.compile(r"^(?:[◆◇■●▶►]|논지\s*\d*\s*[:：])\s*")


def _parse_units(text, valid, figs=None):
    """쓴 글 → 블록 목록: {h: 소제목[, kind: extra]} | {units: [{t, notes[], role?}][, kind]} | {table: {cap, cols}, units: [{t, cells, notes}]} | {fig: {pi, kind, n}, units: [{t, notes, cap}]}
    ◆ 로 시작하는 문장은 논지(role: claim). '### 번외' 뒤의 블록은 kind: extra. 그림은 figs(허용 목록)에 있는 것만."""
    m = re.search(r"<<<SEC>>>(.*?)(?:<<<END>>>|\Z)", text or "", re.S)
    body = m.group(1) if m else (text or "")
    blocks, cur, kind, tab = [], None, "", None

    def refs_of(mm):
        return [r for r in re.findall(r"N\d+\.\d+", mm.group(1)) if r in valid] if mm else []
    for ln in body.split("\n"):
        ln = ln.strip()
        if not ln or ln in ("¶", "¶"):
            cur = tab = None
            continue
        if ln.startswith("#"):
            h = ln.lstrip("#").strip()
            if h:
                if re.match(r"^번외", h):
                    kind = "extra"
                    blocks.append({"h": "번외", "kind": "extra"})
                else:
                    blocks.append({"h": h[:80]})
            cur = tab = None
            continue
        if re.match(r"^표\s*[:：]", ln):
            tab = {"table": {"cap": _REF_ANY.sub("", re.sub(r"^표\s*[:：]\s*", "", ln)).strip()[:200], "cols": []}, "units": []}
            if kind:
                tab["kind"] = kind
            blocks.append(tab)
            cur = None
            continue
        if ln.startswith("|") and tab is not None:
            mm = _REF_END.search(ln)
            refs = refs_of(mm)
            row = ln[:mm.start()] if mm else ln
            cells = [_REF_ANY.sub("", c).strip() for c in row.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue                                    # 머리 행 아래의 구분선
            if not tab["table"]["cols"] and not refs:
                tab["table"]["cols"] = [c[:60] for c in cells[:8]]
                continue
            if not any(cells):
                continue
            tab["units"].append({"t": " | ".join(c for c in cells if c)[:400], "cells": [c[:120] for c in cells[:8]], "notes": refs[:6]})
            continue
        tab = None
        mf = _FIG_LINE.match(ln)
        if mf:
            pi, n = int(mf.group(1)) - 1, int(mf.group(3))
            fk = "table" if mf.group(2).lower().startswith(("t", "표")) else "fig"
            mm = _REF_END.search(mf.group(4))
            t = _REF_ANY.sub("", (mf.group(4)[:mm.start()] if mm else mf.group(4))).strip()
            if figs and (pi, fk, n) in figs and len(t) >= 4:
                fg = figs[(pi, fk, n)]
                fb = {"fig": {"pi": pi, "kind": fk, "n": n, "ftype": fg.get("ftype") or ""}, "units": [{"t": t[:700], "notes": refs_of(mm)[:6], "cap": fg["cap"][:500], "fsrc": list(fg.get("src") or [])[:4], "fdesc": (fg.get("desc") or "")[:400]}]}
                if kind:
                    fb["kind"] = kind
                blocks.append(fb)
            cur = None
            continue
        mm = _REF_END.search(ln)
        refs = refs_of(mm)
        t = (ln[:mm.start()] if mm else ln).strip()
        role = ""
        if _CLAIM_MARK.match(t):
            role = "claim"
            t = _CLAIM_MARK.sub("", t)
        t = re.sub(r"^[-•*]\s+", "", t.strip())
        t = _REF_ANY.sub("", t).strip()   # 문장 가운데 끼운 표시는 뗀다
        if len(t) < 4:
            continue
        if cur is None:
            cur = {"units": []}
            if kind:
                cur["kind"] = kind
            blocks.append(cur)
        u = {"t": t, "notes": refs[:6]}
        if role:
            u["role"] = role
        cur["units"].append(u)
    return blocks


def _write_section(st, k, model, effort):
    """절 하나를 쓴다 → blocks. Claude 가 답하지 않았으면 None(「이어서」 에서 다시), 답은 했는데 쓸 문장이 없으면 [](빈 절)."""
    valid = set(st["outline"]["sections"][k]["notes"])
    blocks, answered = [], False
    for _ in range(2):
        _check_stop(st)
        prompt, figs = _write_prompt(st, k)
        text = _ask(st, prompt, model, effort, 1800)
        answered = answered or bool(text.strip())
        blocks = _parse_units(text, valid, figs)
        if sum(len(b.get("units") or []) for b in blocks) >= 2:
            return blocks
    return blocks if answered else None


# ---------- 7 대조 ----------
_VERDICT = {"over": "근거보다 나아감", "none": "근거가 그 말을 하지 않음", "attr": "누가 한 말인지 다름", "num": "수치가 근거와 다름", "fact": "근거 없는 사실"}


def _unit_src(st, u):
    """문장의 근거: 메모들의 근거 문장을 차례로(겹치면 한 번), 그림 설명이면 그 그림을 가리키는 원문 문장도. → [(논문 순번, sid, own)]"""
    notes, out, seen = _all_notes(st), [], set()
    for nid in u.get("notes") or []:
        if nid not in notes:
            continue
        pi, n = notes[nid]
        for sid in n["s"]:
            if (pi, sid) not in seen:
                seen.add((pi, sid))
                out.append((pi, sid, bool(n.get("own"))))
    for key in u.get("fsrc") or []:
        try:
            pi, sid = key.split(":")
            pi = int(pi)
        except ValueError:
            continue
        if (pi, sid) not in seen and 0 <= pi < len(st["papers"]):
            seen.add((pi, sid))
            out.append((pi, sid, True))
    return out[:10]


def _src_text(st, src):
    by = {}
    out = []
    for pi, sid, own in src:
        if pi not in by:
            by[pi] = {s["sid"]: s for s in _paper_sents(st["papers"][pi]["file"])}
        s = by[pi].get(sid)
        if s:
            out.append((pi, sid, own, s))
    return out


def _verify_prompt(st, units, terms):
    lines = []
    for i, u in units:
        src = _src_text(st, _unit_src(st, u))
        miss = u.get("nummiss") or []
        tag = "(논지 문장 — 여러 근거를 묶어 말한 것) " if u.get("role") == "claim" else "(표의 행 — 칸은 | 로 나눔) " if u.get("cells") else "(그림 설명 — 캡션·그림 읽기·그 그림을 가리키는 원문 문장이 말하는 것만) " if u.get("cap") else ""
        lines.append("u%d: %s%s%s" % (i, tag, u["t"], ("   (프로그램: 수치 %s 가 근거에 보이지 않음)" % ", ".join(miss)) if miss else ""))
        if not src and not u.get("cap"):
            lines.append("   (근거 표시 없음 — 이음 문장)")
        if u.get("cap"):
            lines.append("   캡션 — %s" % u["cap"][:500])
        if u.get("fdesc"):
            lines.append("   그림 읽기(그림을 본 Claude 가 적은 것) — %s" % u["fdesc"][:400])
        for pi, sid, own, s in src:
            p = st["papers"][pi]
            lines.append("   근거 — %s (%s %s)%s %s: \"%s\"" % (p["short"], p["journal"], p["year"], "" if own else " · 이 논문이 남의 연구를 전한 문장", sid, s["en"][:900]))
    return (
        "전공 교과서의 한 절을 논문 원문과 대조한다. 절의 문장(u번호)마다 그 문장이 근거로 든 논문의 원문 문장을 아래에 붙였다. "
        "문장이 말하는 사실을 근거 문장이 실제로 말하는지 하나씩 확인하라. 당신이 아는 지식으로 판단하지 말고, 붙여 준 근거 문장만 본다.\n\n"
        "판정:\n"
        "- ok: 문장의 모든 사실(대상·조건·방향·수치·원인)을 근거 문장이 말한다. 한국어로 옮겨 적었거나 여러 근거를 묶은 것은 ok.\n"
        "  논지 문장(여러 근거를 묶어 말한 것)은 각 부분이 근거 가운데 어느 하나에든 있으면 ok. 근거들이 보이지 않는 관계(같다·다르다·더 크다·엇갈린다)를 말하거나 조건을 떼고 일반 법칙으로 넓혔으면 over.\n"
        "  표의 행은 칸마다 값·조건이 근거에 있는지 본다. 그림 설명은 캡션·그림 읽기·근거가 말하는 것만 담았으면 ok(그림 읽기는 그림에 보이는 것을 적은 글이다 — 거기 적힌 부분·화살표·축·글자는 근거로 친다).\n"
        "- over: 근거보다 나아갔다 — 조건을 떼고 일반화했다, 근거에 없는 원인·결론을 보탰다, 조심스러운 말(may, suggest)을 단정으로 높였다. "
        "근거의 수치를 그대로 옮겼어도 그 값을 정한 전제(배율·속도·장치·가정)를 떼어 '…로 두면 …가 된다' 처럼 일반 법칙으로 읽히면 over — fix 는 전제를 붙이거나, 전제가 근거에 없으면 수치를 뺀 원리 문장으로.\n"
        "- none: 근거 문장이 그 말을 하지 않는다(다른 이야기다).\n"
        "- attr: 누가 한 말인지가 틀렸다 — 그 논문이 남의 연구를 전한 문장인데 '그 논문의 저자가 그것을 했다·보였다'고 썼다, 또는 문장 속 저자 이름·연도가 근거의 논문과 다르다. "
        "(남의 연구를 전한 문장을 근거로 사실을 주어 없이 평서문으로 적은 것은 attr 가 아니다 — 내용이 근거와 맞으면 ok.)\n"
        "- num: 수치·단위·기호가 근거와 다르다.\n"
        "- fact: 근거 표시가 없는 이음 문장인데 새 사실·수치·원인을 담았다. 앞뒤 문장에 이미 있는 내용을 받아 잇는 말, 왜 이것을 보는지 안내하는 말, 바로 앞 문장들을 묶는 말, 다음으로 넘기는 말은 ok(교과서의 잇는 문장이다).\n\n"
        "JSON 으로만 답하라: {\"units\": [{\"u\": 1, \"v\": \"ok\"}, {\"u\": 2, \"v\": \"over|none|attr|num|fact\", \"why\": \"무엇이 근거와 다른지 한국어 한 문장\", \"fix\": \"같은 근거로, 근거가 말하는 데까지만 고쳐 쓴 문장. 고칠 수 없으면 빈 문자열\"}]}\n"
        "- 모든 문장을 번호 순서대로 빠짐없이 하나씩 적는다. ok 인 문장은 u 와 v 만 적는다(why·fix 없이). 판정이 망설여져도 ok 와 나머지 가운데 하나로 정한다 — '문제는 없지만' 하고 걸지 않는다.\n"
        "- '(프로그램: 수치 … 가 근거에 보이지 않음)' 이 붙은 문장은 ok 로 두지 말고 num 으로 적고, fix 에서 그 수치를 근거 문장에 적힌 그대로 고치거나 뺀다.\n"
        "- 단위가 'mm'·'lm' 인데 문맥으로 보아 μm 의 글자가 깨진 것이 분명한 수치(PDF 추출 오류)는 num 으로 넣고 fix 에서 그 수치를 뺀다.\n"
        "- fix 는 한 문장, 평서문(~다), 원래 문장의 용어를 그대로. 근거에 없는 것을 보태지 않는다. fact 의 fix 는 사실을 뺀 이음말만 남기거나 빈 문자열.\n"
        "- 문체·표현의 좋고 나쁨은 판정하지 않는다. 사실이 근거와 맞는지만 본다.\n\n"
        "[용어] %s\n\n[절의 문장과 근거]\n%s") % (terms, "\n".join(lines))


def _verdicts(d):
    """대조의 답 → {문장 번호: {v, why, fix}}. 답이 없으면 None"""
    if d is None:
        return None
    out = {}
    for x in d.get("units") or []:
        if not isinstance(x, dict):
            continue
        try:
            i = int(str(x.get("u")).lstrip("u"))
        except (TypeError, ValueError):
            continue
        out[i] = {"v": str(x.get("v") or "").strip().lower(), "why": str(x.get("why") or "").strip()[:300], "fix": _clean(str(x.get("fix") or ""))}
    return out


def _mark_nums(st, u):
    src = [s["en"] for _pi, _sid, _own, s in _src_text(st, _unit_src(st, u))]
    if u.get("cap"):
        src.append(u["cap"])      # 그림 설명의 수치는 캡션에 있어도 된다
    if u.get("fdesc"):
        src.append(u["fdesc"])
    u["nummiss"] = _nums_missing(u["t"], src)
    return u["nummiss"]


def _verify_section(st, k, model):
    """절 하나를 대조한다 → (남긴 blocks, 뺀 문장들, 고친 수). 한 번 고칠 기회를 주고, 고친 문장을 다시 대조해 통과하지 못하면 뺀다."""
    sec = json.loads(json.dumps(st["sections"][k]))     # 사본에서 일한다 — 다른 절이 끝나 저장하는 동안 이 절의 문장을 고치면 저장이 깨진다
    terms = ", ".join("%s = %s" % (t["en"], t["ko"]) for t in st["outline"].get("terms") or []) or "(없음)"
    units = [u for b in sec["blocks"] for u in b.get("units") or []]
    if not units:
        return [], [], 0
    for u in units:
        _mark_nums(st, u)
    verd = _verdicts(_ask_json(st, _verify_prompt(st, list(enumerate(units, 1)), terms), model, None, 1500, need="units"))
    if verd is None:
        raise RuntimeError("원문과 대조하지 못했습니다" + _why())
    nomiss = lambda u: "수치 %s 가 근거 문장에 없음" % ", ".join(u.get("nummiss") or [])
    removed, redo = [], []
    for i, u in enumerate(units, 1):
        x = verd.get(i)
        if x is None:                          # 판정이 오지 않은 문장 — 통과로 치지 않고 한 번 더 묻는다
            u["unseen"] = True
            redo.append((i, u))
            continue
        if x["v"] == "ok" and not u.get("nummiss"):
            u["ok"] = True
            continue
        v = x["v"] if x["v"] in _VERDICT else ("num" if u.get("nummiss") else "over")
        why = x["why"] or (nomiss(u) if u.get("nummiss") else "")
        if len(x["fix"]) < 6:
            removed.append({"t": u["t"], "v": v, "why": why, "sec": sec["title"]})
            u["drop"] = True
            continue
        u.update(was=u["t"], t=x["fix"], v=v, why=why)
        redo.append((i, u))
    if redo:                                   # 고친 문장(과 판정이 빠진 문장)을 다시 대조한다 — 여기서 ok 가 아니면 싣지 않는다
        for _i, u in redo:
            _mark_nums(st, u)
        verd2 = _verdicts(_ask_json(st, _verify_prompt(st, redo, terms), model, None, 1200, need="units")) or {}
        for i, u in redo:
            x = verd2.get(i)
            if x is not None and x["v"] == "ok" and not u.get("nummiss"):
                u["ok"] = True
                u["fixed"] = "was" in u
                continue
            why2 = (nomiss(u) if u.get("nummiss") else "") or (x["why"] if x else "") or "다시 대조하지 못함"
            if "was" in u:
                removed.append({"t": u["was"], "v": u["v"], "why": u["why"], "sec": sec["title"], "tried": u["t"], "why2": why2})
            else:
                removed.append({"t": u["t"], "v": (x["v"] if x and x["v"] in _VERDICT else "none"), "why": why2, "sec": sec["title"]})
            u["drop"] = True
    blocks = []
    for b in sec["blocks"]:
        if "h" in b:
            blocks.append(b)
            continue
        us = [u for u in b["units"] if u.get("ok") and not u.get("drop")]
        if us:
            nb = {k2: v for k2, v in b.items() if k2 != "units"}     # 표·그림·번외 표시는 그대로
            nb["units"] = us
            blocks.append(nb)
    while blocks and "h" in blocks[-1]:      # 글이 따라오지 않는 소제목
        blocks.pop()
    blocks = [b for i, b in enumerate(blocks) if not ("h" in b and i + 1 < len(blocks) and "h" in blocks[i + 1])]
    return blocks, removed, len([1 for _i, u in redo if u.get("fixed") and not u.get("drop")])


# ---------- 8 마무리 ----------
def _fig_dir(sid):
    return os.path.join(cfg["DIR"], "그림", os.path.basename(str(sid)))


def _fig_file(st, fig):
    """논문 PDF 에서 그림(표)을 잘라 공부\\그림\\<id>\\P5_fig4.png 로 → {img, page, w, h, cap_en, cap_ko}. 못 자르면 None(그 그림은 싣지 않는다)"""
    try:
        p = st["papers"][fig["pi"]]
        name = "P%d_%s%d.png" % (fig["pi"] + 1, fig["kind"], fig["n"])
        r = figcrop.crop(os.path.join(cfg["ARCHIVE"], p["file"]), fig["kind"], fig["n"], os.path.join(_fig_dir(st["id"]), name))
    except Exception as e:
        r = {"err": str(e)[:100]}
    if "err" in r:
        return None
    cap = next((c for c in _CAP_CACHE.get(p["file"]) or [] if c["kind"] == fig["kind"] and c["n"] == fig["n"]), None)
    x = {}
    if fig["kind"] == "fig":
        try:
            x = _fi().fig(p["file"], fig["n"]) or {}
        except Exception:
            x = {}
    return {"img": name, "page": r["page"], "w": r["w"], "h": r["h"], "cap_en": (r.get("cap") or "")[:600], "cap_ko": ((cap or {}).get("ko") or "")[:400],
            "ftype": x.get("type") or fig.get("ftype") or "", "desc": (x.get("what") or "")[:400]}


def _wrap(st):
    """남은 문장으로 장을 묶는다: 인용 번호(처음 나온 순서), 근거 문장 모음, 참고문헌, 그림 자르기."""
    order, src, secs, seen_figs = [], {}, [], {}
    nunit = nfact = nfixed = nclaim = nmulti = ntab = nfig = nextra = nschem = 0
    by = {}
    for sec in st["sections"]:
        blocks = []
        for b in sec.get("blocks") or []:
            if "h" in b:
                blocks.append({k2: v for k2, v in b.items()})
                continue
            nb = {k2: v for k2, v in b.items() if k2 != "units"}
            if nb.get("fig"):
                fkey = (nb["fig"]["pi"], nb["fig"]["kind"], nb["fig"]["n"])
                if fkey in seen_figs:      # 같은 그림이 다른 절에 또 — 두 번 싣지 않고, 본문의 ⟨그림⟩ 은 먼저 실린 번호를 가리킨다
                    blocks.append({"figref": seen_figs[fkey]})
                    continue
                info = _fig_file(st, nb["fig"])
                if not info:          # 논문에서 그 그림을 잘라 내지 못하면 그림도 설명도 싣지 않는다
                    continue
                nb["fig"] = dict(nb["fig"], **info)
            us = []
            for u in b["units"]:
                ss = _src_text(st, _unit_src(st, u))
                refs = []
                for pi, sid, own, s in ss:
                    if pi not in order:
                        order.append(pi)
                    key = "%d:%s" % (pi, sid)
                    if key not in src:
                        item = {"en": s["en"], "ko": s["ko"], "page": (s["page"] + 1) if isinstance(s["page"], int) else None, "sec": s["sec"], "own": own}
                        if not own:      # 남의 연구를 전한 문장 → 그 문장이 인용한 문헌을 그 논문의 참고문헌 목록에서
                            if pi not in by:
                                try:
                                    by[pi] = ms._pdf_refs(st["papers"][pi]["file"])
                                except Exception:
                                    by[pi] = {}
                            cited = [{"n": n, "line": by[pi][int(n)][:400]} for n in ms._cite_numbers(s["en"])[:5] if int(n) in by[pi]]
                            if cited:
                                item["cited"] = cited
                        src[key] = item
                    elif own:
                        src[key]["own"] = True
                    refs.append(key)
                nunit += 1
                nfact += 1 if refs else 0
                nfixed += 1 if u.get("fixed") else 0
                nmulti += 1 if len({r.split(":")[0] for r in refs}) >= 2 else 0
                item = {"t": u["t"], "src": refs, "fixed": bool(u.get("fixed"))}
                if u.get("role") == "claim":
                    item["role"] = "claim"
                if u.get("cells"):
                    item["cells"] = u["cells"]
                if nb.get("kind") == "extra":
                    nextra += 1
                us.append(item)
            if us:
                nb["units"] = us
                blocks.append(nb)
                if nb.get("table"):
                    ntab += 1
                    nb["table"]["no"] = ntab
                if nb.get("fig"):
                    nfig += 1
                    nb["fig"]["no"] = nfig
                    seen_figs[(nb["fig"]["pi"], nb["fig"]["kind"], nb["fig"]["n"])] = nfig
                    nschem += 1 if nb["fig"].get("ftype") in ("schematic", "setup") else 0
        # 본문의 ⟨그림⟩·⟨표⟩ 는 그 뒤에 오는 그림·표의 번호로 (교과서처럼 '그림 3에 보인다'). 뒤에 그림이 없으면 표시만 뗀다
        for i, b in enumerate(blocks):
            if "h" in b or b.get("fig") or b.get("table") or b.get("figref"):
                continue
            nxt = {"fig": None, "table": None}
            for b2 in blocks[i + 1:]:
                if b2.get("fig") and nxt["fig"] is None:
                    nxt["fig"] = b2["fig"]["no"]
                if b2.get("figref") and nxt["fig"] is None:
                    nxt["fig"] = b2["figref"]
                if b2.get("table") and nxt["table"] is None:
                    nxt["table"] = b2["table"]["no"]
            for u in b["units"]:
                t = re.sub(r"[⟨〈<]그림[⟩〉>]", ("그림 %d" % nxt["fig"]) if nxt["fig"] else "", u["t"])
                t = re.sub(r"[⟨〈<]표[⟩〉>]", ("표 %d" % nxt["table"]) if nxt["table"] else "", t)
                t = re.sub(r"(?<=[가-힣A-Za-z0-9,.])(그림|표) (\d+)", r" \1 \2", t)      # 낱말에 붙었으면 띄우고
                t = re.sub(r"\(\s+(그림|표) ", r"(\1 ", t)                               # 괄호 안에서는 붙인다
                u["t"] = re.sub(r"\s{2,}", " ", t).strip()
        blocks = [b for b in blocks if not b.get("figref")]
        if blocks:
            secs.append({"title": sec["title"], "aim": sec.get("aim", ""), "kind": sec.get("kind", ""), "blocks": blocks})
    nclaim = sum(len(s.get("claims") or []) for s in st["outline"].get("sections") or [])   # 설계도의 생각 수
    for n, pi in enumerate(order, 1):
        st["papers"][pi]["n"] = n

    def ref(pi):
        try:
            return pi, ms.library_ref(st["papers"][pi]["file"])
        except Exception:
            return pi, ""
    with ThreadPoolExecutor(max_workers=4) as ex:
        for pi, line in ex.map(ref, order):
            p = st["papers"][pi]
            p["ref"] = line or ("%s. %s. %s %s." % (p["short"].rsplit(" ", 1)[0], p["title"].rstrip("."), p["journal"], p["year"]))
    ol = st["outline"]
    st["chapter"] = {"title": ol["title"], "scope": st["plan"]["scope"], "sections": secs, "terms": ol.get("terms") or [], "gaps": ol.get("gaps") or []}
    st["src"] = src
    st["stylus"] = getattr(ms, "STYLUS", "")
    st["stats"] = {"units": nunit, "fact": nfact, "bridge": nunit - nfact, "fixed": nfixed, "removed": len(st.get("removed") or []), "cited": len(order),
                   "read": sum(1 for p in st["papers"] if p.get("read")), "notes": len(_all_notes(st)), "srcs": len(src),
                   "sents_read": sum(p.get("nsent", 0) for p in st["papers"] if p.get("read") and p.get("mode") == "full"),
                   "claims": nclaim, "multi": nmulti, "tables": ntab, "figs": nfig, "schem": nschem, "extra": nextra, "dropped": sum(len(x.get("dropped") or []) for x in st["outline"].get("sections") or []), "journals": _journal_stats(st, order), "joined": st.get("joined_n", 0)}


# ---------- 6-1 절 사이 잇기 (2026-10-07) ----------
# 절은 셋씩 동시에 쓰므로 여는 문장이 앞 절의 설계도만 보고 쓰인다. 재 보니 경계 14곳 중 9곳은 그래도 맞았고 2곳은 어긋났다(앞 절이 긴 사실 문장 묶음으로 끝난 자리).
# 절을 차례로 쓰면 +30분이라, 쓴 뒤에 경계마다 작은 호출 하나로 잇는 문장(근거 없는 문장)만 고쳐 쓴다 — 사실 문장은 한 글자도 바꾸지 않고, 고친 잇는 문장은 뒤의 대조가 다시 본다(fact).
def _para_blocks(sec):
    return [b for b in (sec.get("blocks") or []) if b.get("units") and "h" not in b and not b.get("fig") and not b.get("table") and b.get("kind") != "extra"]


def _join_pair(st, a, k, model):
    """절 a 의 실제 마지막 문단과 절 k 의 첫 문단을 놓고 잇는 문장만 고쳐 쓴다 → 고친 문장 수"""
    secs = st["sections"]
    pb, cb = _para_blocks(secs[a]), _para_blocks(secs[k])
    if not pb or not cb:
        return 0
    tail, head = pb[-1], cb[0]

    def lines(block, tag):
        return "\n".join("%s%d %s %s" % (tag, i + 1, "[잇는 문장]" if not u.get("notes") else "[사실 — 고치지 않는다]", u["t"]) for i, u in enumerate(block["units"]))
    orig_tail, orig_head = [u["t"] for u in tail["units"]], [u["t"] for u in head["units"]]
    prompt = (
        "전공 교과서의 두 절이 만나는 자리를 다듬는다. 두 절은 따로 쓰였기 때문에, 다음 절의 여는 문장이 앞 절의 실제 마지막 문장을 받지 못했거나 앞 절의 닫는 문장이 다음 절의 실제 첫 문장으로 넘어가지 못했을 수 있다.\n"
        "할 일: '[잇는 문장]' 으로 표시된 문장만 고쳐 쓴다 — 다음 절의 여는 잇는 문장은 앞 절의 마지막 문장들이 실제로 말한 것을 받아 이 절의 첫 사실 문장으로 자연스럽게 넘어가게, 앞 절의 닫는 잇는 문장은 다음 절의 첫 사실 문장으로 넘어가게. "
        "'[사실 — 고치지 않는다]' 문장은 한 글자도 바꾸지 않는다.\n"
        "규칙: 고치지 않는 것이 기본이다 — 여는 문장이 앞 절의 실제 마지막 문장들과 어긋날 때(앞 절이 말하지 않은 것을 받거나, 앞 절의 끝과 상관없는 말로 열 때), 또는 닫는 문장이 다음 절의 실제 첫 사실과 어긋날 때만 고친다. "
        "잇는 문장에는 근거가 없다 — 사실을 말하지 않는다. 앞 문단의 결과·수치·재료·방법을 되풀이해 요약하지 말고(그 문장들은 이미 있다), 앞의 무엇이 다음의 무엇으로 넘어가는지를 대상의 이름 정도로만 짚는다. "
        "고칠 때는 원래 문장의 말투·길이·결을 지키고 그 자리만 바꾼다 — 경계마다 같은 꼴의 문장을 만들지 않는다. '이 절은 …를 다룬다·살펴본다' 같은 안내문, 물음 꼴('…인가?', '남는 물음은 …'), '앞 절에서 보았듯이' 같은 상투어는 쓰지 않는다. "
        "다음 절의 첫 문단에 잇는 문장이 없는데 앞 절과 끊겨 보이면 add_head 에 여는 문장 하나를 새로 적어도 된다(아니면 빈 문자열).\n"
        "JSON 으로만: {\"fits\": 이미 잘 이어져 있으면 true(그러면 나머지는 비운다), \"tail\": {\"T4\": \"고친 문장\"}, \"head\": {\"H1\": \"고친 문장\"}, \"add_head\": \"\", \"ok\": true}\n\n"
        "[장] %s\n[앞 절] %d. %s\n[앞 절의 마지막 문단]\n%s\n\n[다음 절] %d. %s\n[다음 절의 첫 문단]\n%s") % (
        st["outline"]["title"], a + 1, secs[a]["title"], lines(tail, "T"), k + 1, secs[k]["title"], lines(head, "H"))
    d = _ask_json(st, prompt, model, "high", 300, need="ok")
    if not d or d.get("fits") is True:   # 잘 이어져 있으면 손대지 않는다 — 처음엔 모든 경계를 고쳐 '…로 이어진다' 꼴로 통일해 버렸다(2026-10-07)
        return 0

    def clean(t):   # 잇는 문장의 검사: 수치가 들었거나(사실을 끌어옴), 안내문이거나, 길면 버린다 — 시험에서 '노치 마모·코팅 박리가 남았다' 같은 결과 요약이 잇는 문장으로 들어왔다(2026-10-07)
        t = _clean(str(t or "")).strip()
        if not (8 <= len(t) <= 160) or _REF_ANY.search(t) or re.search(r"\d", t) or re.search(r"이 절(은|에서는)\s|살펴본다[.]?$|다룬다[.]?$|보았듯이", t):
            return ""
        return t
    n = 0
    with _LOCK:
        for tag, block, orig in (("tail", tail, orig_tail), ("head", head, orig_head)):
            for lab, t in (d.get(tag).items() if isinstance(d.get(tag), dict) else []):
                m = re.match(r"^[TH](\d+)$", str(lab).strip())
                t = clean(t)
                if not m or not t:
                    continue
                i = int(m.group(1)) - 1
                if 0 <= i < len(block["units"]) and not block["units"][i].get("notes") and block["units"][i]["t"] == orig[i] and t != orig[i]:
                    block["units"][i]["t"] = t
                    n += 1
        ah = clean(d.get("add_head"))
        if ah and not any(not u.get("notes") for u in head["units"]) and head["units"] and head["units"][0]["t"] == orig_head[0]:
            head["units"].insert(0, {"t": ah, "notes": []})
            n += 1
    return n


def _join_all(st, model):
    body = [k for k, s in enumerate(st["sections"]) if s.get("kind") != "summary" and s.get("blocks")]
    pairs = [(body[i - 1], body[i]) for i in range(1, len(body))]
    _stage(st, "join", done=0, total=len(pairs))
    done = total_n = 0
    if pairs:
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_join_pair, st, a, b, model): (a, b) for a, b in pairs}
            for f in as_completed(futs):
                try:
                    total_n += f.result() or 0
                except _Stop:
                    continue
                except Exception:
                    pass
                done += 1
                with _LOCK:
                    st["prog"] = {"done": done, "total": len(pairs)}
                _save(st)
    _check_stop(st)
    with _LOCK:
        st["joined"] = True
        st["joined_n"] = total_n
    _save(st)
    _log(st, "절 사이의 잇는 문장 %d개를 손질했습니다 (경계 %d곳)" % (total_n, len(pairs)))


# ---------- 7-1 Claude 의 생각 — 해설 층 (2026-10-08) ----------
# 사용자: '탄탈럼 장을 쭉 읽었는데 도움이 안 되는 느낌' → 까닭은 사실만 있고 설명·판단이 없어서(이 기능의 약속이 교과서가 하는 일의 절반을 금지해 두었다).
# 사실 층은 그대로 두고 절마다 Claude 의 풀이를 따로 단다: 큰 그림 · 바탕 원리(일반 지식) · 엇갈림의 까닭 · 근거의 강약 · 실무 요점. 화면은 마스코트 + 앰버로 '서재의 문장으로 확인한 것이 아니다' 하고 갈라 보인다.
_CMT_HEADS = ["큰 그림", "바탕 원리", "엇갈림의 까닭", "근거의 강약", "실무 요점"]


def _comment_prompt(st, k):
    ch, P = st["chapter"], st.get("papers") or []
    sec = ch["sections"][k]
    num = lambda keys: sorted({P[int(x.split(":")[0])].get("n") for x in keys if P[int(x.split(":")[0])].get("n")})
    cite = lambda u: (" [%s]" % ",".join(str(x) for x in num(u["src"]))) if u.get("src") else ""
    body = "\n".join(line for b in sec.get("blocks") or [] for line in _block_text(st, b, cite))
    pis = sorted({int(x.split(":")[0]) for b in sec.get("blocks") or [] for u in b.get("units") or [] for x in u.get("src") or []})
    plist = "\n".join("[%d] %s — %s" % (P[i]["n"], P[i]["short"], str(P[i].get("about") or P[i]["title"])[:160]) for i in pis if P[i].get("n"))
    toc = "\n".join("%s%d. %s" % ("▶ " if i == k else "  ", i + 1, s["title"]) for i, s in enumerate(ch["sections"]) if s.get("kind") != "summary")
    return (
        "아래는 연구자가 자기 서재의 논문만으로 쓴 전공 교과서 한 장의 한 절이다. 사실 문장은 모두 논문의 원문 문장에 묶여 있고 원문과 대조한 것이라, 이 절에는 설명·해석·판단이 없다. "
        "연구자가 읽고 '사실만 있어 도움이 안 된다'고 했다.\n"
        "당신은 이 분야(기계가공·재료)의 선배 연구자로서 이 절에 '생각'을 단다. 서재의 문장으로 확인된 것이 아니라 당신의 풀이라는 것을 읽는 이가 알고 읽으므로, 바탕 지식을 써도 된다.\n"
        "JSON 으로만: {\"parts\": [{\"h\": \"큰 그림|바탕 원리|엇갈림의 까닭|근거의 강약|실무 요점 가운데 하나\", \"t\": \"한국어 한 문단\"}], \"ok\": true}\n"
        "쓰는 법:\n"
        "- 이 절에 실제로 할 말이 있는 갈래만 2~4개 고른다(없는 갈래는 억지로 쓰지 않는다). 갈래마다 한 문단, 결론부터, 짧은 문장. 모두 합쳐 이 절 본문의 1/3 을 넘기지 않는다.\n"
        "- 큰 그림: 이 절의 사실들이 합쳐서 무엇을 말하는지, 이 분야에서 이 물음이 왜 중요한지. 절의 문장을 되풀이하지 않는다.\n"
        "- 바탕 원리: 절의 현상을 이해하는 데 필요한 원리(재료·소성·절삭 역학)를 일반 지식으로 설명한다. 이 절의 논문이 말한 것이 아니면 '일반적으로' 처럼 서재 밖의 지식임이 드러나게 쓴다.\n"
        "- 엇갈림의 까닭: 연구끼리 다르거나 조건이 좁은 곳을 짚고, 왜 그럴지 가설을 둘 이상 댄다(조건 차이·측정 방법·재료 상태·규모). 단정하지 않는다.\n"
        "- 근거의 강약: 어느 주장이 한 논문·한 조건에만 기대는지, 어느 것은 여러 연구가 받치는지. 논문은 [번호]로.\n"
        "- 실무 요점: 이 절을 읽은 연구자가 실험·가공을 설계할 때 바로 쓸 말 — 무엇을 고르고, 무엇을 재고, 무엇을 조심할지.\n"
        "- 절이 말한 것과 다르게 보면 '절은 …라고 하지만' 하고 밝힌다. 수치는 절에 있는 것만 쓴다. '중요하다', '주목할 만하다', '흥미롭다' 같은 빈말과 인사말, 이 지시문의 낱말을 되풀이하는 말은 쓰지 않는다.\n\n"
        "[장] %s — %s\n[절의 차례]\n%s\n\n[이 절] %d. %s\n%s\n\n[이 절이 인용한 논문]\n%s\n\n[이 서재로는 답하지 못한 것]\n%s") % (
        ch.get("title") or "", ch.get("scope") or "", toc, k + 1, sec["title"], body, plist or "(없음)", "\n".join("- " + g for g in ch.get("gaps") or []) or "(없음)")


def _comment_all(st, model, eff, force=False, sid=None):
    """절마다 Claude 의 생각. sid 가 없으면(쓰는 흐름 안) st 에 바로 끼우고 저장, 있으면(요청으로 돌 때) 결과만 돌려 준다(부르는 쪽이 파일을 다시 읽어 끼운다 — 대화가 같이 바뀔 수 있다)"""
    secs = (st.get("chapter") or {}).get("sections") or []
    todo = [k for k, s in enumerate(secs) if s.get("kind") != "summary" and s.get("blocks") and (force or not s.get("comment"))]
    if sid is None:
        _stage(st, "comment", done=0, total=len(todo))
    results = {}
    if not todo:
        return results

    def one(k):
        d = _ask_json(st, _comment_prompt(st, k), model, eff, 900, need="parts")
        parts = []
        for p in (d.get("parts") if d and isinstance(d.get("parts"), list) else [])[:5]:
            if not isinstance(p, dict):
                continue
            h, t = str(p.get("h") or "").strip(), _clean(str(p.get("t") or "")).strip()
            if h in _CMT_HEADS and 20 <= len(t) <= 1500 and h not in [x["h"] for x in parts]:
                parts.append({"h": h, "t": t})
        return parts
    done = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(one, k): k for k in todo}
        for f in as_completed(futs):
            k = futs[f]
            try:
                parts = f.result()
            except _Stop:
                continue
            except Exception:
                parts = []
            done += 1
            if parts:
                results[k] = {"parts": parts, "t": time.time(), "model": model}
            if sid is None:
                with _LOCK:
                    if parts:
                        secs[k]["comment"] = results[k]
                    st["prog"] = {"done": done, "total": len(todo)}
                _save(st)
    if sid is None:
        with _LOCK:
            st.setdefault("stats", {})["comments"] = sum(1 for s in secs if s.get("comment"))
        _save(st)
        _log(st, "절 %d개에 Claude 의 생각을 달았습니다" % len(results))
    return results


def _basics_prompt(st):
    """바탕 이론 — 장이 설명 없이 쓰는 기초 개념을 교과서처럼. 서재의 문장이 아니라 Claude 의 지식(그렇게 표시되어 실린다)"""
    ch = st.get("chapter") or {}
    toc = "\n".join("%d. %s%s" % (i + 1, s["title"], (" — " + s["aim"]) if s.get("aim") else "") for i, s in enumerate([x for x in ch.get("sections") or [] if x.get("kind") != "summary"]))
    terms = "\n".join("- %s — %s" % (t.get("ko"), t.get("en")) for t in ch.get("terms") or []) or "(없음)"
    return (
        "아래는 연구자가 자기 서재의 논문만으로 쓴 전공 교과서 한 장이다. 사실 문장은 모두 논문의 원문에 묶여 있어, 논문이 말하지 않은 기초 — 이 장이 쓰는 개념이 무엇이고 왜 그런지 — 는 비어 있다. "
        "연구자는 이 분야의 초보(기계공학 대학원생)라 장을 읽기 전에 바탕이 되는 교과서 지식이 필요하다.\n"
        "당신은 재료·소성·가공을 가르치는 교수로서, 이 장을 읽는 데 필요한 바탕 이론을 교과서처럼 쓴다. 서재로 확인한 것이 아니라 당신의 지식이라는 것을 읽는 이가 알고 읽는다(그렇게 표시되어 실린다).\n"
        "JSON 으로만: {\"parts\": [{\"h\": \"소제목(한국어, 10자 안팎)\", \"t\": \"한국어 한두 문단\"}], \"ok\": true}\n"
        "쓰는 법:\n"
        "- 이 장이 설명 없이 쓰는 개념부터: 장의 본문과 [용어]를 훑어, 정의하지 않고 쓰는 개념(예: 쌍정이면 격자와 결정면·미끄럼계·버거스 벡터·전단 변형률·슈미트 인자)을 골라 차례대로 세운다. 가장 기본에서 시작해 장의 첫 절이 서는 데까지 — 이 장의 '0절' 이다.\n"
        "- 갈래 5~9개, 갈래마다 한두 문단(300~700자). 정의 → 왜 그런지(원리) → 그것이 변형·가공에서 왜 중요한지 순서로. 교과서처럼 평서문으로, 짧은 문장, 결론부터.\n"
        "- 처음 나오는 용어는 한국어(영어)로 적고 [용어]의 한국어를 그대로 쓴다. 장이 어느 절에서 그것을 깊이 다루면 '자세한 것은 n절' 로 가리킨다.\n"
        "- 수치는 교과서에 있는 보편적인 값(크기 정도)만 쓰고 특정 연구의 값은 쓰지 않는다. 식은 꼭 필요할 때 한 줄로(예: τ = σ cos φ cos λ).\n"
        "- 장의 문장을 되풀이하지 않는다. '중요하다', '흥미롭다' 같은 빈말과 인사말, 이 지시문의 낱말을 되풀이하는 말은 쓰지 않는다. 확실하지 않은 것은 쓰지 않는다 — 초보가 그대로 믿는다.\n\n"
        "[장의 차례]\n%s\n\n[용어]\n%s\n\n[장 전체]\n%s") % (toc, terms, _chapter_text(st)[:60000])


def _basics_run(st, model, eff):
    d = _ask_json(st, _basics_prompt(st), model, eff, 1200, need="parts")
    parts = []
    for p in (d.get("parts") if d and isinstance(d.get("parts"), list) else [])[:10]:
        if not isinstance(p, dict):
            continue
        h, t = str(p.get("h") or "").strip(), _clean(str(p.get("t") or "")).strip()
        if 2 <= len(h) <= 40 and 80 <= len(t) <= 2500 and h not in [x["h"] for x in parts]:
            parts.append({"h": h[:40], "t": t})
    return parts


def basics_start(sid, body):
    effort = body.get("effort") if body.get("effort") in _CHAT_EFFORT else "xhigh"
    with _LOCK:
        st = _load(sid)
        if not st or not st.get("chapter"):
            return {"error": "다 쓴 장에서만 쓸 수 있습니다"}
        if st.get("basics_pending"):
            return {"error": "이미 쓰는 중입니다"}
        st["basics_pending"] = time.time()
        st.pop("basics_error", None)
        st["basics_opts"] = {"effort": effort}
        _save(st)
    threading.Thread(target=_basics_job, args=(sid,), name="study-basics", daemon=True).start()
    return {"ok": True}


def _basics_job(sid):
    st = _load(sid)
    if not st:
        return
    model, eff = _CHAT_EFFORT.get((st.get("basics_opts") or {}).get("effort"), _CHAT_EFFORT["xhigh"])
    t0, err, parts = time.time(), "", []
    try:
        parts = _basics_run(st, model, eff)
        if not parts:
            err = "Claude 가 바탕 이론을 주지 않았습니다" + _why()
    except Exception as e:
        err = "Claude 호출 실패: " + str(e)[:200]
    with _LOCK:
        st2 = _load(sid)
        if not st2:
            return
        if parts:
            st2["basics"] = {"parts": parts, "t": time.time(), "model": model, "sec": round(time.time() - t0)}
        st2.pop("basics_pending", None)
        if err:
            st2["basics_error"] = err
        else:
            st2.pop("basics_error", None)
        st2["calls"], st2["tok"] = st.get("calls", st2.get("calls", 0)), st.get("tok", st2.get("tok"))
        _save(st2)


def comment_start(sid, body):
    effort = body.get("effort") if body.get("effort") in _CHAT_EFFORT else "xhigh"
    with _LOCK:
        st = _load(sid)
        if not st or not st.get("chapter"):
            return {"error": "다 쓴 장에서만 달 수 있습니다"}
        if st.get("comment_pending"):
            return {"error": "이미 쓰는 중입니다"}
        st["comment_pending"] = time.time()
        st.pop("comment_error", None)
        st["comment_opts"] = {"effort": effort, "force": bool(body.get("force"))}
        _save(st)
    threading.Thread(target=_comment_job, args=(sid,), name="study-comment", daemon=True).start()
    return {"ok": True}


def _comment_job(sid):
    st = _load(sid)
    if not st:
        return
    opts = st.get("comment_opts") or {}
    model, eff = _CHAT_EFFORT.get(opts.get("effort"), _CHAT_EFFORT["xhigh"])
    err = ""
    try:
        results = _comment_all(st, model, eff, force=bool(opts.get("force")), sid=sid)
        if not results:
            err = "Claude 가 생각을 주지 않았습니다" + _why()
    except Exception as e:
        results, err = {}, "Claude 호출 실패: " + str(e)[:200]
    with _LOCK:
        st2 = _load(sid)
        if not st2:
            return
        secs = (st2.get("chapter") or {}).get("sections") or []
        if opts.get("force") and results:
            for s in secs:
                s.pop("comment", None)
        for k, c in results.items():
            if k < len(secs):
                secs[k]["comment"] = c
        st2.setdefault("stats", {})["comments"] = sum(1 for s in secs if s.get("comment"))
        st2.pop("comment_pending", None)
        if err:
            st2["comment_error"] = err
        else:
            st2.pop("comment_error", None)
        st2["calls"], st2["tok"] = st.get("calls", st2.get("calls", 0)), st.get("tok", st2.get("tok"))
        _save(st2)


# ---------- 흐름 ----------
def _run(st):
    model, eff = ms._SEL_EFFORT.get((st.get("opts") or {}).get("effort"), ms._SEL_EFFORT["xhigh"])
    caps = _DEPTH.get((st.get("opts") or {}).get("depth"), _DEPTH["mid"])
    if not st.get("plan"):
        _stage(st, "plan")
        try:
            nlib = len([f for f in os.listdir(cfg["ARCHIVE"]) if f.lower().endswith(".pdf")])
        except OSError:
            nlib = 0
        _plan(st, nlib)
        _save(st)
    _check_stop(st)
    if not st.get("papers"):
        _stage(st, "scan")
        cands, nfiles, nsent, odd = _scan(st["plan"]["concepts"])
        st["scan"] = {"files": nfiles, "sents": nsent, "cands": len(cands), "odd": odd}
        if not cands:
            raise RuntimeError("서재에서 이 주제를 말하는 문장을 찾지 못했습니다 (찾은 낱말: %s)" % " / ".join(", ".join(c["en"][:5]) for c in st["plan"]["concepts"]))
        _log(st, "서재의 논문 %d편(문장 %s개)을 훑어 주제 문장이 있는 논문 %d편을 찾았습니다" % (nfiles, format(nsent, ","), len(cands)))
        _stage(st, "select")
        _select(st, cands, caps)
        _log(st, "읽을 논문 %d편을 골랐습니다 (통째로 %d편 · 맞은 대목만 %d편)" % (len(st["papers"]), sum(1 for p in st["papers"] if p["role"] == "core"), sum(1 for p in st["papers"] if p["role"] != "core")))
        _save(st)
    _check_stop(st)
    tops = _top_set()
    for p in st["papers"]:
        if "top" not in p:
            p["top"] = _is_top(p.get("journal", ""), tops)
    _read_all(st, model)
    if not st.get("figidx"):       # 읽은 논문의 그림 색인(Sonnet 이 그림을 보고 종류·설명) — 쓰기가 도식을 고르고 풀어 쓰는 재료. 못 만들어도 장은 쓴다
        _stage(st, "figs")
        try:
            _fig_index_all(st)
        except _Stop:
            raise
        except Exception as e:
            st["figidx"] = {"err": str(e)[:200]}
        _save(st)
    _check_stop(st)
    notes = _all_notes(st)
    if len(notes) < 6:
        raise RuntimeError("서재의 논문에서 이 주제에 관한 내용을 충분히 찾지 못했습니다 (메모 %d개)" % len(notes))
    if not st.get("outline"):
        _log(st, "논문 %d편에서 메모 %d개를 뽑았습니다" % (sum(1 for p in st["papers"] if p.get("read")), len(notes)))
        _stage(st, "outline")
        _outline(st, model, eff)
        st["sections"] = [{"title": s["title"], "aim": s.get("aim", ""), "kind": s.get("kind", ""), "blocks": None, "checked": False} for s in st["outline"]["sections"]]
        st["removed"] = []
        if st["outline"].get("spine"):
            _log(st, "장의 줄기(%s): %s" % ("서재의 리뷰 짜임을 빌림" if st["outline"].get("review_based") else "리뷰 없이", st["outline"]["spine"][:160]))
        _save(st)
    _check_stop(st)
    if any(s.get("blocks") is None for s in st["sections"]):      # 아직 쓰지 않은 절이 있으면 절마다 논지부터 (다 쓴 절은 그대로)
        _claims_all(st, model, eff)
        nc = sum(len(s.get("claims") or []) for s in st["outline"]["sections"])
        if nc:
            _log(st, "절 %d개에 논지 %d개를 세웠습니다 (논지에 들지 않은 메모 %d개는 번외로)" % (
                sum(1 for s in st["outline"]["sections"] if s.get("claims")), nc, sum(len(s.get("extra") or []) for s in st["outline"]["sections"])))
    nsec = len(st["sections"])
    todo = [k for k, s in enumerate(st["sections"]) if s.get("blocks") is None]
    _stage(st, "write", done=nsec - len(todo), total=nsec)
    if todo:
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_write_section, st, k, model, eff): k for k in todo}
            for f in as_completed(futs):
                k = futs[f]
                try:
                    blocks = f.result()
                except _Stop:
                    continue
                except Exception:
                    blocks = []
                with _LOCK:
                    if blocks is not None:
                        st["sections"][k]["blocks"] = blocks
                    st["prog"] = {"done": sum(1 for s in st["sections"] if s.get("blocks") is not None), "total": nsec}
                _save(st)
        _check_stop(st)
        if any(s.get("blocks") is None for s in st["sections"]):
            raise RuntimeError("절 %d개를 쓰지 못했습니다%s — 「이어서」 로 남은 절부터 다시 씁니다" % (sum(1 for s in st["sections"] if s.get("blocks") is None), _why()))
    if not st.get("joined"):      # 절 사이 잇기 — 쓴 뒤, 대조 전에(고친 잇는 문장도 대조가 본다)
        _join_all(st, model)
    todo = [k for k, s in enumerate(st["sections"]) if not s.get("checked")]
    _stage(st, "verify", done=nsec - len(todo), total=nsec)
    if todo:
        err = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_verify_section, st, k, "opus" if model == "sonnet" else model): k for k in todo}   # 대조는 빠른 모델로 하지 않는다
            for f in as_completed(futs):
                k = futs[f]
                try:
                    blocks, removed, nfix = f.result()
                except _Stop:
                    continue
                except Exception as e:
                    err.append(str(e))
                    continue
                with _LOCK:
                    st["sections"][k].update(blocks=blocks, checked=True, nfix=nfix)
                    st["removed"] = (st.get("removed") or []) + removed
                    st["prog"] = {"done": sum(1 for s in st["sections"] if s.get("checked")), "total": nsec}
                _save(st)
        _check_stop(st)
        if err:
            raise RuntimeError(err[0] + " — 「이어서」 로 남은 절부터 다시 대조합니다")
    _stage(st, "wrap")
    _wrap(st)
    try:        # Claude 의 생각(해설 층) — 못 달아도 장은 완성이다
        _comment_all(st, model, eff)
    except Exception:
        pass


def _job(sid):
    st = _load(sid)
    if not st:
        _JOBS.pop(sid, None)
        return
    t0 = time.time()
    try:
        st["status"], st["error"] = "running", ""
        _save(st)
        _run(st)
        st["status"], st["stage"] = "done", ""
        st["done_t"] = time.time()
    except _Stop:
        st["status"] = "stopped"
    except Exception as e:
        st["status"], st["error"] = "error", (str(e) or e.__class__.__name__)[:400]
    finally:
        st["sec"] = round(st.get("sec", 0) + time.time() - t0)
        st["version"] = VERSION
        _JOBS.pop(sid, None)
        _save(st)


def scope_check(body):
    """「쓰기 시작」 전에 주제가 한 장에 담기에 넓은지 본다 → {n, cap, broad, why, options: [{topic, focus, n}]}.
    사용자(2026-10-09): '이거이거로 만들어 줘 할 때 분야가 너무 넓으면 알아서 좁히거나 되물음 하나'. Sonnet 둘(계획 + 좁히기), 20초쯤."""
    topic = str(body.get("topic") or "").strip()
    if len(topic) < 2:
        return {"error": "공부할 주제를 적어 주세요"}
    st = {"id": "scope", "topic": topic[:800], "tok": {"in": 0, "cached": 0}, "calls": 0}
    try:
        nlib = len([f for f in os.listdir(cfg["ARCHIVE"]) if f.lower().endswith(".pdf")])
    except OSError:
        nlib = 0
    _plan(st, nlib)
    cands, nfiles, nsent, odd = _scan(st["plan"]["concepts"])
    cap = _DEPTH.get(body.get("depth"), _DEPTH["mid"])[1]
    out = {"n": len(cands), "cap": cap, "broad": False, "why": "", "options": [], "title": st["plan"].get("title", ""), "scope": st["plan"].get("scope", "")}
    if len(cands) <= max(30, cap + 6):
        return out
    rows = []
    for k, c in enumerate(cands[:60]):
        m = ms.paper_meta(c["file"])
        line = ms._paper_line(c["file"])
        rows.append("K%d | %s · %s | %s%s" % (k + 1, ms.paper_short(c["file"]), m.get("journal", ""), str(m.get("title") or "")[:140], (" | " + line[:160]) if line else ""))
    prompt = (
        "연구자가 자기 서재의 논문만으로 전공 교과서 한 장을 쓰려 한다. 적은 [주제]가 서재 논문 %d편에 걸리는데 한 장에 읽을 수 있는 것은 %d편이다. 후보 논문의 제목과 한 줄 요약을 보고, "
        "이 주제가 한 장에 담기에 넓은지 판단하고 넓으면 좁힌 주제를 2~4개 제안하라.\n"
        "JSON 으로만: {\"broad\": true, \"why\": \"넓은(또는 알맞은) 까닭 한 문장(한국어)\", \"options\": [{\"topic\": \"좁힌 주제(한국어, 장 제목처럼 — 원래 주제의 말을 살려서)\", \"focus\": \"무엇을 중심으로 하고 무엇을 빼는지 한 구절\", \"n\": 후보 가운데 이 주제에 드는 편수}]}\n"
        "규칙: 좁힌 주제는 후보 논문이 실제로 받치는 것만(8편 이상), 서로 겹치지 않게, 첫째를 추천으로. 재료·공정·현상·스케일·방법 가운데 후보가 갈리는 축으로 좁힌다. "
        "주제가 이미 한 장 분량이면 broad 를 false 로 하고 options 는 빈 목록.\n\n[주제] %s\n[계획이 본 범위] %s\n\n[후보 논문]\n%s") % (len(cands), cap, topic[:400], st["plan"].get("scope", ""), "\n".join(rows))
    d = _ask_json(st, prompt, "sonnet", None, 240)
    if isinstance(d, dict):
        out["broad"] = bool(d.get("broad"))
        out["why"] = str(d.get("why") or "").strip()[:300]
        for o in (d.get("options") or [])[:4]:
            if isinstance(o, dict) and str(o.get("topic") or "").strip():
                try:
                    n = int(o.get("n") or 0)
                except (TypeError, ValueError):
                    n = 0
                out["options"].append({"topic": str(o["topic"]).strip()[:160], "focus": str(o.get("focus") or "").strip()[:200], "n": n})
        if not out["options"]:
            out["broad"] = False
    return out


def rewrite_start(sid):
    """다 쓴 장을 짜기부터 새 규칙으로 다시 쓴다(읽은 논문·메모·그림 색인은 그대로 — 읽기 비용이 없다) → 새 장. 옛 장은 그대로 남는다."""
    src = _load(sid)
    if not src or not src.get("papers") or not any(p.get("read") for p in src["papers"]):
        return {"error": "읽은 논문이 없는 장은 다시 쓸 수 없습니다"}
    if len(_JOBS) >= 2:
        return {"error": "이미 두 개를 쓰고 있습니다 — 끝난 뒤에 시작하세요"}
    nid = "st_" + time.strftime("%Y%m%d_%H%M%S")
    st = json.loads(json.dumps({k: src[k] for k in ("topic", "opts", "plan", "scan", "papers", "figidx") if k in src}, ensure_ascii=False))
    for p in st["papers"]:
        p.pop("n", None)
    st.update(id=nid, t=time.time(), status="running", stage="outline", calls=0, sec=0, tok={"in": 0, "cached": 0}, removed=[], joined=False, log=[], rewrite_of=sid)
    _save(st)
    return _spawn(nid)


def start(body):
    topic = str(body.get("topic") or "").strip()
    if len(topic) < 2:
        return {"error": "공부할 주제를 적어 주세요"}
    if len(_JOBS) >= 2:
        return {"error": "이미 두 개를 쓰고 있습니다 — 끝난 뒤에 시작하세요"}
    sid = "st_" + time.strftime("%Y%m%d_%H%M%S")
    st = {"id": sid, "topic": topic[:800], "t": time.time(), "status": "running", "stage": "plan",
          "opts": {"depth": body.get("depth") if body.get("depth") in _DEPTH else "mid", "effort": body.get("effort") if body.get("effort") in ms._SEL_EFFORT else "xhigh"}}
    _save(st)
    return _spawn(sid)


def _spawn(sid):
    with _LOCK:
        if sid in _JOBS:
            return {"id": sid}
        _JOBS[sid] = {"stop": False, "t0": time.time()}
    threading.Thread(target=_job, args=(sid,), name="study", daemon=True).start()
    return {"id": sid}


def resume(sid):
    st = _load(sid)
    if not st:
        return {"error": "찾지 못했습니다"}
    if st.get("status") == "done":
        return {"id": sid}
    st["status"], st["error"] = "running", ""
    _save(st)
    return _spawn(sid)


def get(sid):
    st = _load(sid)
    if not st:
        return {"error": "찾지 못했습니다"}
    st["status"] = _status(st)
    st["live"] = _live(sid)
    st["now"] = time.time()
    if st["status"] == "done":      # 화면에 필요 없는 중간 재료는 보내지 않는다
        st.pop("sections", None)
        for p in st.get("papers") or []:
            p["nnotes"] = len(p.get("notes") or [])
            p.pop("notes", None)
    else:
        for p in st.get("papers") or []:
            p["nnotes"] = len(p.get("notes") or [])
            p.pop("notes", None)
        for s in st.get("sections") or []:
            s.pop("blocks", None)
    return st


def _fig_label(st, fig):
    p = st["papers"][fig["pi"]]
    return "%s%s, %s %d, p.%s" % (p["short"], (" [%d]" % p["n"]) if p.get("n") else "", "Fig." if fig.get("kind") == "fig" else "Table", fig["n"], fig.get("page", "?"))


def _block_text(st, b, cite, ids=None):
    """블록 하나를 글로(md·대화 배경 공용). cite(u) = ' [3,5]' 꼴, ids(ui) = 문장 id 머리(없으면 빈 글). 그림·표의 번호는 문단 순서대로 붙이지 않고 출처로 가리킨다"""
    if "h" in b:
        return ["### " + b["h"]]
    pre = (lambda ui: ids(ui) + " ") if ids else (lambda ui: "")
    if b.get("table"):
        cols = (b["table"].get("cols") or [])
        rows = ["표: " + (b["table"].get("cap") or ""), "| " + " | ".join(cols + ["출처"]) + " |", "|" + "---|" * (len(cols) + 1)]
        for ui, u in enumerate(b.get("units") or []):
            cells = list(u.get("cells") or [u["t"]])[:len(cols)] + [""] * max(0, len(cols) - len(u.get("cells") or []))
            rows.append("| " + " | ".join(pre(ui) + c if i == 0 else c for i, c in enumerate(cells)) + " |" + cite(u).strip() + " |")
        return rows
    if b.get("fig"):
        u = (b.get("units") or [{}])[0]
        return ["그림: %s%s%s (%s)" % (pre(0), u.get("t", ""), cite(u), _fig_label(st, b["fig"]))]
    return [" ".join("%s%s%s" % (pre(ui), u["t"], cite(u)) for ui, u in enumerate(b.get("units") or []))]


def to_md(st):
    ch = st.get("chapter") or {}
    papers = st.get("papers") or []
    src = st.get("src") or {}
    num = lambda keys: sorted({papers[int(k.split(":")[0])].get("n") for k in keys if papers[int(k.split(":")[0])].get("n")})
    cite = lambda u: (" [%s]" % ",".join(str(n) for n in num(u["src"]))) if u.get("src") else ""
    out = ["# " + ch.get("title", st.get("topic", "")), "", "> %s" % ch.get("scope", ""),
           "> 내 서재의 논문 %d편으로 씀 · %s · 사실 문장은 모두 논문의 원문 문장에 묶여 있고 원문과 대조했습니다 (%s)" % (
               (st.get("stats") or {}).get("cited", 0), time.strftime("%Y-%m-%d", time.localtime(st.get("t") or 0)), st.get("version", VERSION)), ""]
    if (st.get("basics") or {}).get("parts"):
        out += ["## 바탕 이론 — Claude 의 풀이 (서재의 문장으로 확인한 것이 아닙니다)", ""]
        for p in st["basics"]["parts"]:
            out += ["### " + p["h"], "", p["t"], ""]
    k = 0
    for sec in ch.get("sections") or []:
        if sec.get("kind") == "summary":
            out += ["## 핵심 정리", ""]
            for b in sec["blocks"]:
                for u in b.get("units") or []:
                    out.append("- %s%s" % (u["t"], cite(u)))
            out.append("")
            continue
        k += 1
        out += ["## %d. %s" % (k, sec["title"]), ""]
        for b in sec["blocks"]:
            out += _block_text(st, b, cite) + [""]
        if (sec.get("comment") or {}).get("parts"):
            out += ["> **Claude 의 생각** — 서재의 문장으로 확인한 것이 아니라 Claude 의 풀이입니다."] + ["> **%s.** %s" % (p["h"], p["t"]) for p in sec["comment"]["parts"]] + [""]
    if ch.get("gaps"):
        out += ["## 이 서재로는 답하지 못한 것", ""] + ["- " + g for g in ch["gaps"]] + [""]
    if ch.get("terms"):
        out += ["## 용어", ""] + ["- %s — %s" % (t["ko"], t["en"]) for t in ch["terms"]] + [""]
    cited = sorted((p for p in papers if p.get("n")), key=lambda p: p["n"])
    out += ["## 참고문헌", ""] + ["[%d] %s" % (p["n"], p.get("ref", "")) for p in cited] + [""]
    out += ["## 근거 문장 (논문의 원문 그대로)", ""]
    for pi, p in sorted(((i, p) for i, p in enumerate(papers) if p.get("n")), key=lambda x: x[1]["n"]):
        out.append("**[%d] %s**" % (p["n"], p["short"]))
        ks = sorted((k2 for k2 in src if k2.startswith("%d:" % pi)), key=lambda k2: int(re.sub(r"\D", "", k2.split(":")[1]) or 0))
        for k2 in ks:
            s = src[k2]
            out.append("- (%s%s) %s" % (k2.split(":")[1], (", p." + str(s["page"])) if s.get("page") else "", s["en"]))
        out.append("")
    return "\n".join(out)

# ---------- 묻기: 장의 글을 드래그해 Claude 에게 (원고의 대화와 같은 길) ----------
# 사용자(2026-10-06): '드래그해서 질문할 수 있게 — Claude Opus 로(엑스트라 이상), 원고처럼'. 배경(장 전체 + 참고문헌)은 시스템 프롬프트로 주어
# 두 번째 물음부터 캐시에서 읽히게 하고, 고른 대목의 근거 원문과 물음만 물음에 붙인다. 답은 서버가 그 대화에 얹어 저장한다 — 창을 닫아도 남는다.
_CHAT_EFFORT = {"xhigh": ("opus", "xhigh"), "max": ("opus", "max")}
_CHAT_RULE = (
    "답하는 법:\n"
    "- 답은 사람이 읽는 한국어 글이다. 두괄식으로: 첫 문장에 답, 그 다음에 까닭과 근거, 마지막에 연구자가 확인하거나 더 볼 것. 짧은 문장으로 한 번에 한 가지씩. 인사말·되묻는 말·잘된 점의 나열은 넣지 않는다.\n"
    "- 근거는 [고른 대목의 근거 원문] 과 장의 글이 우선이고, 그 다음이 참고문헌의 요약과 읽을 때 뽑은 메모다(장에 실리지 않은 사실은 여기서 — 메모의 s번호로 원문 문장을 가리킬 수 있다). 거기서 답할 수 있으면 그것으로 답하고, 논문은 장의 [번호] 로 가리킨다(예: Liu 등(2019)[2]). 원문 문장을 끌어 쓸 때는 그 문장의 번호(s12)도 적는다.\n"
    "- 장·근거·참고문헌의 요약과 메모에 없는 것을 당신의 지식으로 보태야 하면, 그 부분은 '서재 밖:' 으로 시작하는 문단에 따로 적어 갈라 둔다 — 연구자는 서재에서 확인한 것과 아닌 것을 구분해야 한다. 지어낸 수치·인용은 절대 쓰지 않는다.\n"
    "- 고른 대목이 근거를 넘어 말했거나 근거와 다르게 읽힌다고 보이면 그렇게 말한다(이 장은 Claude 가 쓴 것이라 틀릴 수 있다).\n"
    "- 내부 표시(메모 번호, 규칙 이름)는 쓰지 않는다. 마크다운 제목·굵게 표시 없이 문단으로 쓴다(필요하면 번호 목록).\n")


def _chapter_text(st):
    """장 전체를 글로 — 절마다 문장과 인용 번호, 참고문헌. 물음마다 글자 하나까지 같아야 캐시가 맞는다 (다 쓴 장은 바뀌지 않는다)."""
    ch = st.get("chapter") or {}
    P = st.get("papers") or []
    num = lambda keys: sorted({P[int(k.split(":")[0])].get("n") for k in keys if P[int(k.split(":")[0])].get("n")})
    cite = lambda u: (" [%s]" % ",".join(str(x) for x in num(u["src"]))) if u.get("src") else ""
    out = ["# " + str(ch.get("title") or ""), str(ch.get("scope") or ""), ""]
    k = 0
    for sec in ch.get("sections") or []:
        if sec.get("kind") == "summary":
            out.append("## 핵심 정리")
        else:
            k += 1
            out.append("## %d. %s" % (k, sec["title"]))
        for b in sec.get("blocks") or []:
            out += _block_text(st, b, cite)
        if (sec.get("comment") or {}).get("parts"):
            out += ["> Claude 의 생각 (서재의 문장으로 확인한 것이 아니라 Claude 의 풀이다): " + " / ".join("[%s] %s" % (p["h"], p["t"]) for p in sec["comment"]["parts"])]
        out.append("")
    if ch.get("gaps"):
        out += ["## 이 서재로는 답하지 못한 것"] + ["- " + g for g in ch["gaps"]] + [""]
    cited = sorted((p for p in P if p.get("n")), key=lambda p: p["n"])
    out += ["## 참고문헌 (장의 [번호])"] + ["[%d] %s — %s" % (p["n"], p["short"], p.get("ref") or p.get("title") or "") for p in cited]
    return "\n".join(out)


def _chat_papers(st, cap_notes=60):
    """참고문헌마다 요약 파일과 읽을 때 뽑은 메모 — 묻는 Claude 가 장뿐 아니라 그 논문들을 알고 답하게(사용자 2026-10-08: 'Claude 는 참고자료를 다 알고 있어야지, 보고서만 아는 것 같다').
    시스템 프롬프트에 붙여 캐시에 올린다(요약 파일·메모는 바뀌지 않는다). 장 하나에 요약 16편 ≈ 7만 자 + 메모 300개 ≈ 3만 자."""
    P = st.get("papers") or []
    cited = sorted((p for p in P if p.get("n")), key=lambda p: p["n"])
    # 배경(장 + 이 블록)이 Claude 의 창을 넘지 않게: 장 본문 길이를 뺀 나머지를 논문 수로 나눠 편마다 상한. 시험(탄탈럼 16편, 편당 6천 자): 첫 물음 18만 토큰(글자 수 ≈ 토큰 수)이라 전체를 14만 자로 묶는다
    body = sum(len(u["t"]) for s in (st.get("chapter") or {}).get("sections") or [] for b in s.get("blocks") or [] for u in b.get("units") or [])
    cap_total = max(40000, 140000 - body)
    cap_each = max(1500, min(6000, cap_total // max(1, len(cited))))
    out = ["# 참고문헌의 요약과 읽을 때 뽑은 메모 (장의 [번호] 순) — 요약은 그 논문의 요약 파일(길면 앞부분만), 메모는 장을 쓰기 전에 그 논문을 읽고 뽑은 사실(s번호 = 그 논문 원문의 문장 번호, '전한 말' = 그 논문이 남의 연구를 전한 문장)"]
    total = 0
    for p in cited:
        summ = ""
        try:
            with io.open(os.path.join(cfg["GEN_DIR"], os.path.splitext(p["file"])[0] + ".요약.md"), encoding="utf-8") as f:
                summ = _clean(f.read()).strip()
        except Exception:
            summ = ""
        notes = "\n".join("- (s%s)%s %s" % (",".join(str(x) for x in n.get("s") or []), "" if n.get("own", True) else " [전한 말]", n["pt"]) for n in (p.get("notes") or [])[:cap_notes])
        if len(notes) > cap_each // 2:
            notes = notes[:cap_each // 2] + "\n- …(메모 뒷부분 생략)"
        room = max(600, cap_each - len(notes))
        if len(summ) > room:
            summ = summ[:room] + " …(요약 뒷부분 생략)"
        block = "## [%d] %s (%s %s) — %s\n%s\n### 읽을 때 뽑은 메모\n%s" % (p["n"], p["short"], p.get("journal") or "", p.get("year") or "", p.get("title") or "", summ or "(요약 파일 없음)", notes or "(없음)")
        if total + len(block) > cap_total:
            out.append("## … 나머지 논문의 요약은 길이 때문에 생략")
            break
        out.append(block)
        total += len(block)
    return "\n\n".join(out)


def _chat_system(st):
    return ("아래는 연구자가 자기 서재(영어 논문들)의 논문만으로 쓴 전공 교과서의 한 장이다. 사실 문장은 모두 논문의 원문 문장에 묶여 있고 원문과 대조한 것이다. "
            "연구자가 이 장의 한 대목을 골라 묻는다. [번호] 는 장 끝 참고문헌의 번호다. 장 뒤에는 참고문헌마다의 요약과 읽을 때 뽑은 메모가 있다 — 장에 실리지 않은 사실도 거기서 찾아 답한다.\n\n" + _chapter_text(st) + "\n\n" + _chat_papers(st))


def _unit_at(st, key):
    try:
        a, b, c = (int(x) for x in str(key).split("."))
        u = st["chapter"]["sections"][a]["blocks"][b]["units"][c]
        return st["chapter"]["sections"][a], u
    except (ValueError, KeyError, IndexError, TypeError):
        return None, None


def _chat_evidence(st, keys):
    """고른 대목에 든 문장들의 근거 원문 → 글. 문장마다: 장의 문장, 그 아래 근거 원문(논문 [번호], 문장 번호, 전한 말 표시, 영어, 번역)"""
    P, src = st.get("papers") or [], st.get("src") or {}
    lines, seen = [], set()
    for key in keys[:14]:
        _sec, u = _unit_at(st, key)
        if not u:
            continue
        lines.append("· " + u["t"])
        for k in u.get("src") or []:
            s = src.get(k)
            if not s or k in seen:
                continue
            seen.add(k)
            p = P[int(k.split(":")[0])]
            lines.append("    [%s] %s · %s%s%s: \"%s\"%s" % (p.get("n", "?"), p["short"], k.split(":")[1], (" · p.%s" % s["page"]) if s.get("page") else "",
                                                           "" if s.get("own") else " · 이 논문이 남의 연구를 전한 문장", s["en"][:700], (" / 번역: " + s["ko"][:400]) if s.get("ko") else ""))
    return "\n".join(lines)


def _chat_prompt(st, chat, question, lib):
    sec, _u = _unit_at(st, (chat.get("keys") or [""])[0])
    ev = _chat_evidence(st, chat.get("keys") or [])
    th = [m for m in chat.get("thread") or [] if m.get("text")]
    past = "\n\n".join("%s: %s" % ("연구자" if m.get("role") == "user" else "Claude", m["text"][:3000]) for m in th[-8:-1]) if len(th) > 1 else ""
    out = ("연구자가 장에서 고른 대목을 놓고 묻는다.\n\n[고른 대목]%s\n%s\n\n[고른 대목의 근거 원문]\n%s\n\n" % (
        (" (절: %s)" % sec["title"]) if sec else "", chat.get("quote", "")[:3000], ev or "(근거 표시 없는 이음 문장)"))
    if past:
        out += "[지난 대화]\n" + past + "\n\n"
    out += "[물음]\n" + question + "\n\n" + _CHAT_RULE
    if lib:
        out += ("\n[서재를 찾아볼 수 있다]\n연구자가 모은 논문의 번역 폴더는 \"%s\" 다(지금 작업 폴더가 아니니 Grep·Glob·Read 에 이 경로를 주어라). 논문마다 \"<이름>.요약.md\"(한국어 요약)와 \"<이름>.번역.md\"(한국어 전문 번역, 문장마다 [sN] 표식)가 있고, "
                "원문 PDF 는 \"%s\" 에 같은 이름으로 있다.\n- 장과 근거만으로 답할 수 없는 물음일 때만 찾아라. Grep 으로 용어(영어·한국어)를 *.요약.md 에서 찾고, 필요한 논문의 .번역.md 에서 그 부분만 Read 한다(통째로 읽지 마라). 도구는 많아야 8번.\n"
                "- 서재에서 확인한 것은 (저자 연도, 파일 이름)으로 가리키고, 확인하지 못한 것은 확인하지 못했다고 말하라.") % (cfg["GEN_DIR"], cfg["ARCHIVE"])
    else:
        out += "\n(너는 파일을 열 수 없다. 장과 여기 준 근거만 보고 답하라. 더 확인할 것이 있으면 무엇을 봐야 하는지 말하라.)"
    return out


def _chat_find(st, cid):
    return next((x for x in st.get("chats") or [] if x.get("id") == cid), None)


def chat_ask(sid, body):
    """물음을 대화에 적어 저장하고 스레드로 답을 받는다 → {chat}. 이어 묻기는 chat 에 그 대화의 id."""
    question = str(body.get("question") or "").strip()
    if len(question) < 2:
        return {"error": "물음을 적어 주세요"}
    effort = body.get("effort") if body.get("effort") in _CHAT_EFFORT else "xhigh"
    with _LOCK:
        st = _load(sid)
        if not st or not st.get("chapter"):
            return {"error": "다 쓴 장에서만 물을 수 있습니다"}
        chat = _chat_find(st, str(body.get("chat") or ""))
        if not chat:
            keys = [str(k) for k in (body.get("keys") or []) if _unit_at(st, k)[1]][:14]
            sec, _u = _unit_at(st, keys[0]) if keys else (None, None)
            chat = {"id": "q%d" % int(time.time() * 1000), "quote": str(body.get("quote") or "").strip()[:3000], "keys": keys,
                    "sec": sec["title"] if sec else "", "t": time.time(), "thread": []}
            st.setdefault("chats", []).append(chat)
        if chat.get("pending"):
            return {"error": "이 대화는 아직 답을 기다리고 있습니다"}
        th = chat.setdefault("thread", [])
        if th and th[-1].get("role") == "user" and chat.get("error"):   # 지난번 물음이 답을 못 받았다 → 그 물음을 바꿔 다시
            th[-1] = {"role": "user", "text": question, "t": time.time()}
        else:
            th.append({"role": "user", "text": question, "t": time.time()})
        chat["pending"] = time.time()
        chat.pop("error", None)
        chat["opts"] = {"effort": effort, "lib": bool(body.get("lib"))}
        _save(st)
        cid = chat["id"]
    threading.Thread(target=_chat_job, args=(sid, cid), name="study-chat", daemon=True).start()
    return {"chat": cid}


def _chat_job(sid, cid):
    st = _load(sid)
    chat = _chat_find(st, cid) if st else None
    if not chat:
        return
    question = (chat.get("thread") or [{}])[-1].get("text") or ""
    model, eff = _CHAT_EFFORT.get((chat.get("opts") or {}).get("effort"), _CHAT_EFFORT["xhigh"])
    lib = bool((chat.get("opts") or {}).get("lib"))
    t0 = time.time()
    res, err = None, ""
    cancelled = False
    try:
        run = cfg.get("claude_run")
        res = run(_chat_prompt(st, chat, question, lib), timeout=1500, model=model, effort=eff, tools="Read,Grep,Glob" if lib else "",
                  add_dirs=[cfg["GEN_DIR"], cfg["ARCHIVE"]] if lib else (), system=_chat_system(st), job=_chat_jobkey(sid, chat)) if run else None
        cancelled = bool((res or {}).get("cancelled"))
        if not cancelled and (not res or not (res.get("text") or "").strip()):
            err = "Claude 응답이 없습니다" + _why()
    except Exception as e:
        err = "Claude 호출 실패: " + str(e)[:200]
    with _LOCK:
        st = _load(sid)
        chat = _chat_find(st, cid) if st else None
        if not chat:
            return
        chat.pop("pending", None)
        if cancelled:   # 「취소」 — 그 물음을 대화에서 빼고, 첫 물음이었으면 대화 자체를 없앤다 (오류로 적지 않는다)
            th = chat.get("thread") or []
            if th and th[-1].get("role") == "user":
                th.pop()
            chat.pop("error", None)
            if not th:
                st["chats"] = [x for x in st.get("chats") or [] if x.get("id") != cid]
            _save(st)
            return
        if err:
            chat["error"] = err
        else:
            chat.pop("error", None)
            chat["thread"].append({"role": "claude", "text": res["text"].strip(), "t": time.time(), "sec": round(time.time() - t0),
                                   "model": res.get("model", ""), "tok": res.get("tok"), "turns": res.get("turns", 1), "lib": lib})
        _save(st)


def _chat_jobkey(sid, chat):
    """돌고 있는 claude 호출의 이름 — 물음마다 다르게(pending 시각) 해서 지난 물음의 취소가 다음 물음에 걸리지 않게."""
    return "study:%s:%s:%d" % (sid, chat.get("id"), int(chat.get("pending") or 0))


def chat_cancel(sid, cid):
    """답을 기다리는 물음을 취소 — 돌고 있는 claude 를 끊는다 (사용자 2026-10-09: '잘못 물어도 끝까지 기다려야 한다').
    물음을 대화에서 빼는 것은 _chat_job 의 마무리(끊긴 호출이 cancelled 로 돌아오면)."""
    with _LOCK:
        st = _load(sid)
        chat = _chat_find(st, cid) if st else None
        if not chat:
            return {"error": "대화를 찾지 못했습니다"}
        if not chat.get("pending"):
            return {"error": "이미 답이 왔습니다"}
        job = _chat_jobkey(sid, chat)
    cancel = cfg.get("claude_cancel")
    if not cancel:
        return {"error": "이 서버는 취소를 지원하지 않습니다"}
    cancel(job)
    return {"ok": True}


def chats_of(sid):
    st = _load(sid)
    if not st:
        return {"error": "찾지 못했습니다"}
    return {"chats": st.get("chats") or [], "now": time.time()}


def chat_delete(sid, cid):
    with _LOCK:
        st = _load(sid)
        if not st:
            return {"error": "찾지 못했습니다"}
        st["chats"] = [x for x in st.get("chats") or [] if x.get("id") != cid]
        _save(st)
    return {"ok": True}

# ---------- 마인드맵(그림) 재료 ----------
# 사용자(2026-10-06): 'PPT 나 figure 하나 정도로 마인드맵 같은 기능 — 여기서 뭘 했고, 어떤 연구가 이루어졌고, 결과는 어떤지 시각화'.
# Claude 는 짧은 이름표만 짓고, 이름표마다 그것을 말하는 장의 문장 id(keys)를 적게 한다 — 없는 것은 버린다. 그림은 화면(study.html)이 그린다.
def _chapter_text_ids(st):
    """장 전체 — 문장마다 [u절.덩이.문장] id 를 앞에."""
    ch = st.get("chapter") or {}
    P = st.get("papers") or []
    num = lambda keys: sorted({P[int(k.split(":")[0])].get("n") for k in keys if P[int(k.split(":")[0])].get("n")})
    cite = lambda u: (" [%s]" % ",".join(str(x) for x in num(u["src"]))) if u.get("src") else ""
    out = ["# " + str(ch.get("title") or ""), str(ch.get("scope") or ""), ""]
    for si, sec in enumerate(ch.get("sections") or []):
        if sec.get("kind") == "summary":
            continue
        out.append("## (si=%d) %s%s" % (si, sec["title"], (" — " + sec["aim"]) if sec.get("aim") else ""))
        for bi, b in enumerate(sec.get("blocks") or []):
            out += _block_text(st, b, cite, ids=lambda ui, si=si, bi=bi: "[u%d.%d.%d]" % (si, bi, ui))
        out.append("")
    cited = sorted((p for p in P if p.get("n")), key=lambda p: p["n"])
    out += ["## 참고문헌 (장의 [번호]) — 논문마다 '무엇을 어떤 재료·조건·방법으로 했나'"] + ["[%d] %s — %s" % (p["n"], p["short"], (p.get("about") or p.get("title") or "")[:200]) for p in cited]
    return "\n".join(out)


def _map_prompt(st):
    return (
        "아래 장(연구자가 자기 서재의 논문만으로 쓴 교과서 한 장)을 한 장의 마인드맵으로 요약할 재료를 만든다. 연구자가 발표 자료에 넣어 "
        "'이 주제에서 무엇을 다루고, 어떤 연구가 무엇을 해서 무엇을 보았고, 아직 모르거나 엇갈리는 것은 무엇인지' 를 한눈에 보게 하려는 것이다. 그림은 프로그램이 그린다 — 당신은 짧은 이름표와 그것을 받치는 문장 id 만 준다.\n"
        "JSON 으로만 답하라:\n"
        "{\"root\": \"장의 주제를 12자 안팎으로\",\n"
        " \"branches\": [{\"si\": 0, \"title\": \"절 이름을 14자 안팎으로\",\n"
        "   \"points\": [{\"label\": \"요점 20자 안팎\", \"detail\": \"그 요점을 한 문장으로(40자 안팎)\", \"keys\": [\"u0.1.2\"]}],\n"
        "   \"papers\": [{\"n\": 4, \"did\": \"이 절과 관련해 그 연구가 무엇을 어떻게 했나(25자 안팎)\", \"found\": \"무엇을 보았나(30자 안팎, 수치는 문장 그대로)\", \"keys\": [\"u0.3.1\"]}],\n"
        "   \"open\": [\"엇갈리거나 아직 모르는 것(25자 안팎)\"]}]}\n"
        "규칙:\n"
        "- 절(si)마다 하나씩, 장의 차례대로. points 는 절마다 3~5개 — 배우는 사람이 기억할 요점(개념·기전·영향 인자·값의 범위). papers 는 절마다 2~5편 — 그 절의 내용을 실제로 받치는 논문만, [번호] 는 장의 참고문헌 번호.\n"
        "- 모든 label·detail·did·found 는 keys 에 적은 장의 문장이 말하는 것을 줄인 것이어야 한다. keys 는 1~4개, 장에 있는 id 그대로. 장에 없는 사실·수치·논문은 넣지 않는다. keys 로 받칠 수 없는 항목은 넣지 않는다.\n"
        "- open 은 장의 글이나 '이 서재로는 답하지 못한 것'에 실제로 있는 것만, 0~2개.\n"
        "- 이름표는 명사구로 짧게. 번역투·'~에 대한 연구' 같은 군말을 뺀다. 전문 용어는 장에 쓰인 한국어 그대로.\n\n"
        + _chapter_text_ids(st))


# ---------- 발표 자료(PPT) — 장을 바탕으로 슬라이드 (사용자 2026-10-09: '공부한 것을 바탕으로 1쪽 특성, 2쪽 연구 동향 식으로 PPT 를 만들어 줬으면 — 읽은 걸 자료화하는 데 시간이 걸린다') ----------
# 흐름: Claude(Opus) 에게 장 전체(문장 id)·그림 목록(그림 읽기 포함)·Claude 의 생각을 주고 쪽마다 제목·글머리표(keys)·그림 번호·발표자 메모(keys)를 JSON 으로 받는다 →
# _deck_clean: 장에 없는 id 를 든 글머리표·메모는 버리고, 수치가 그 문장(과 근거 원문)에 없으면 버린다(지어내기 방지는 마인드맵과 같은 원리) →
# pptx_min.build 로 .pptx(제목 쪽 · 내용 쪽 = 제목+글머리표+오른쪽 그림+출처 줄 · 참고문헌 쪽, 발표자 메모) → 공부\발표\<장 id>_<deck id>.pptx, st.decks[] 에 재료를 남긴다.
_FTYPE_KO = {"schematic": "도식", "photo": "사진", "plot": "그래프", "setup": "장치", "mixed": "혼합"}


def _deck_dir():
    d = os.path.join(cfg["DIR"], "발표")
    os.makedirs(d, exist_ok=True)
    return d


def _deck_figs(st):
    """장에 실린 그림(표 제외): 번호 → {no, pi, n, img, path, ftype, text(장의 그림 설명), cap_en, desc(그림 읽기), sec}"""
    out = {}
    for sec in (st.get("chapter") or {}).get("sections") or []:
        for b in sec.get("blocks") or []:
            f = b.get("fig") or {}
            if not f.get("no") or not f.get("img") or f.get("kind") == "table":
                continue
            fp = os.path.join(_fig_dir(st["id"]), f["img"])
            if not os.path.isfile(fp):
                continue
            u = (b.get("units") or [{}])[0]
            out[int(f["no"])] = {"no": int(f["no"]), "pi": f.get("pi"), "n": f.get("n"), "img": f["img"], "path": fp, "ftype": f.get("ftype") or "", "page": f.get("page"),
                                 "text": u.get("t") or "", "cap_en": f.get("cap_en") or "", "desc": f.get("desc") or "", "sec": sec.get("title") or ""}
    return out


def _deck_pool(st):
    """발표에 쓸 수 있는 그림 전부: 장에 실린 그림(설명이 있다) + 장이 인용한 논문들의 그림 색인(좋음·보통). id = 'F<논문 순번>.<Fig 번호>'"""
    P = st.get("papers") or []
    pool = {}
    for f in _deck_figs(st).values():
        if not isinstance(f.get("pi"), int):
            continue
        fid = "F%d.%s" % (f["pi"], f["n"])
        pool[fid] = {"id": fid, "pi": f["pi"], "n": f["n"], "path": f["path"], "ftype": f["ftype"], "what": f["text"] or f["desc"] or f["cap_en"], "desc": f["desc"], "shows": "",
                     "cap_en": f["cap_en"], "page": f.get("page"), "in_ch": True, "quality": "good"}
    fi = _fi()
    if fi:
        for pi, p in enumerate(P):
            if not p.get("n"):
                continue
            d = fi.load(p["file"])
            if not d:
                continue
            for x in d.get("figs") or []:
                try:
                    n = int(x.get("n"))
                except (TypeError, ValueError):
                    continue
                fid = "F%d.%d" % (pi, n)
                if fid in pool or (x.get("quality") or "ok") == "poor" or not x.get("what"):
                    continue
                path = os.path.join(fi.img_dir(p["file"]), x.get("img") or ("fig%d.png" % n))
                if not os.path.isfile(path):
                    continue
                pool[fid] = {"id": fid, "pi": pi, "n": n, "path": path, "ftype": x.get("type") or "", "what": x.get("what") or "", "desc": x.get("what") or "", "shows": x.get("shows") or "",
                             "cap_en": x.get("cap") or "", "page": x.get("page"), "in_ch": False, "quality": x.get("quality") or "ok"}
    return pool


def _deck_tables(st):
    """장에 실린 표: 번호 → {no, cap, cols, rows, sec}"""
    out = {}
    for sec in (st.get("chapter") or {}).get("sections") or []:
        for b in sec.get("blocks") or []:
            t = b.get("table") or {}
            if not t.get("no"):
                continue
            rows = [[str(c) for c in u.get("cells")] for u in b.get("units") or [] if isinstance(u.get("cells"), list)]
            if rows:
                out[int(t["no"])] = {"no": int(t["no"]), "cap": str(t.get("cap") or ""), "cols": [str(c) for c in t.get("cols") or []], "rows": rows, "sec": sec.get("title") or ""}
    return out


def _deck_prompt(st, req, nslides, pool=None, text="lecture"):
    P = st.get("papers") or []
    pool = pool if pool is not None else _deck_pool(st)
    tables = _deck_tables(st)
    by_pi = {}
    for f in pool.values():
        by_pi.setdefault(f["pi"], []).append(f)
    figtxt = []
    for pi in sorted(by_pi, key=lambda k: int(P[k].get("n") or 999) if k < len(P) else 999):
        p = P[pi] if pi < len(P) else {}
        figtxt.append("논문 [%s] %s (%s):" % (p.get("n"), p.get("short"), p.get("journal") or "?"))
        for f in sorted(by_pi[pi], key=lambda x: int(x["n"]) if str(x["n"]).isdigit() else 99):
            tag = " · ".join(x for x in [_FTYPE_KO.get(f["ftype"], f["ftype"]), {"good": "좋음", "ok": "보통"}.get(f["quality"], ""), "장에 실림" if f["in_ch"] else ""] if x)
            figtxt.append("  %s (Fig. %s%s): %s%s" % (f["id"], f["n"], (" · " + tag) if tag else "", (f["what"] or "")[:260], (" — 어울리는 자리: " + f["shows"][:120]) if f.get("shows") else ""))
    tabtxt = "\n".join("표 %d (%s): %s — 열: %s, 행 %d개 (예: %s)" % (t["no"], t["sec"][:30], t["cap"][:120], " | ".join(t["cols"])[:120], len(t["rows"]), " | ".join(t["rows"][0])[:100])
                       for t in sorted(tables.values(), key=lambda x: x["no"]))
    cmts = []
    for si, sec in enumerate((st.get("chapter") or {}).get("sections") or []):
        for part in (sec.get("comment") or {}).get("parts") or []:
            cmts.append("(si=%d) %s: %s" % (si, part.get("h", ""), (part.get("t") or "")[:700]))
    plan = ("[발표의 목적과 쪽 구성 — 연구자가 적은 것]\n" + req) if req else "[발표의 목적] 연구실 세미나에서 이 장의 내용을 발표한다. 쪽 구성은 당신이 장의 줄기대로 짠다."
    nstr = (("정확히 %d쪽 — 더도 덜도 말고. 연구자가 적은 쪽 구성이 이 수와 다르면 이 수를 따르고 내용을 합치거나 나눈다" % nslides) if nslides
            else "연구자가 쪽 구성에 쪽 수를 적었으면 그대로(제목 쪽은 따로 붙으므로 '제목 포함 n쪽' 이면 내용은 n-1쪽), 적지 않았으면 6~10쪽 사이에서 내용에 맞게")
    if text == "lecture":   # 강의 자료 꼴 (사용자 2026-10-10: 교수님 강의 자료처럼 '설명은 이 정도')
        intro = "이 발표는 그림과 짧은 설명으로 된 **강의 자료** 꼴이다 — 쪽마다 주제 2~4개를 두고 주제마다 한 줄 설명과 원인·조건·결과·대책 같은 짧은 항목을 단다. 당신은 쪽마다 제목·주제와 설명 항목·그림(둘, 셋도 됨)·표·발표자 메모와, 주제·메모마다 그것을 받치는 장의 문장 id 를 준다.\n"
        bullet_schema = "   \"bullets\": [{\"t\": \"주제 구절(8~22자)\", \"sub\": [\"한 줄 설명(45자 안팎)\", \"원인: … · …\", \"대책: …\"], \"keys\": [\"u0.1.2\"]}],\n"
        bullet_rule = ("- bullets 는 쪽마다 주제 2~4개. 주제마다 t(주제 구절 8~22자)와 sub 1~4줄(줄마다 45자 안팎) — 강의 자료처럼 첫 줄은 정의나 한 줄 설명, 그 다음은 '원인: …', '조건: …', '결과: …', '문제: …', '대책: …' 처럼 머리말을 붙인 짧은 항목(항목 안의 나열은 · 로). 긴 문장을 쓰지 않는다. "
                       "t 와 sub 모두 keys(1~4개, 장에 있는 id 그대로)의 문장이 말하는 것만. 장에 없는 사실·수치·논문은 넣지 않는다. 수치는 문장에 적힌 그대로(단위 포함).\n")
    else:                   # 키워드만 (v3)
        intro = "이 발표는 **그림이 주인공**이다 — 연구자는 그림을 보여 주며 말로 설명하고, 글은 키워드만 둔다. 당신은 쪽마다 제목·키워드·그림(둘, 셋도 됨)·표·발표자 메모와, 키워드·메모마다 그것을 받치는 장의 문장 id 를 준다.\n"
        bullet_schema = "   \"bullets\": [{\"t\": \"키워드 구절(8~22자, 명사형 — 문장이 아니다)\", \"keys\": [\"u0.1.2\"]}],\n"
        bullet_rule = "- bullets 는 쪽마다 4~6개의 **키워드 구절** — 문장이 아니라 '끈적한 금속(~200 HV)', '칩 두께비 40–50', '사행 유동 → 접힘 칩' 같은 짧은 구절. keys(1~4개, 장에 있는 id 그대로)의 문장이 말하는 것만. 장에 없는 사실·수치·논문은 넣지 않는다. 수치는 문장에 적힌 그대로(단위 포함).\n"
    return (   # 장 글·그림 설명에 % 가 들어 있으므로 % 형식화를 쓰지 않고 이어 붙인다
        "아래 장(연구자가 자기 서재의 논문만으로 쓴 교과서 한 장)을 바탕으로 발표 슬라이드의 재료를 만든다. 슬라이드 파일은 프로그램이 만든다. "
        + intro
        + plan + "\n슬라이드 수: " + nstr + " (제목 쪽은 프로그램이 따로 붙인다)\n"
        "JSON 으로만 답하라:\n"
        "{\"title\": \"발표 제목(장 제목을 바탕으로 20자 안팎)\", \"subtitle\": \"부제 한 줄 — 무엇을 다루는지\",\n"
        " \"slides\": [{\"h\": \"쪽 제목(16자 안팎)\",\n"
        + bullet_schema +
        "   \"figs\": [{\"id\": \"F3.5\", \"cap\": \"이 그림이 무엇을 보여 주는지 한 줄(30~70자) — 그림 설명·그림 읽기·캡션이 말하는 것만, 연구자가 가리키며 말할 곳을 담아\"}],\n"
        "   \"table\": 1,\n"
        "   \"note\": {\"t\": \"발표자 메모 — 키워드마다 한두 문장씩 말로 풀고, 그림마다 어디를 보라고 할지 한 문장(4~8문장)\", \"keys\": [\"u0.1.2\"]}}]}\n"
        "규칙:\n"
        + bullet_rule +
        "- figs 는 쪽마다 **둘**이 기본이고 **셋**도 된다(연구자가 주문에서 더 달라고 했거나, 셋이 조건 → 관찰 → 결과처럼 한 이야기를 이룰 때). 맞는 것이 하나뿐이면 하나, 없으면 비운다 — 채우려고 고르지 않는다. [그림 목록]에서 **양질**의 그림을 고른다: 무엇을 보여 주는지 분명한 도식·사진·그래프, '좋음' 을 먼저, 장에 실린 것과 색인의 것 모두 후보. 같은 그림을 두 쪽에 쓰지 않는다. 둘을 고를 때는 서로 다른 것을 말하는 짝(개념 도식 + 관찰 사진, 조건 + 결과)으로.\n"
        "- cap 은 그림 아래에 실릴 말 — '무슨 그림인지'(무엇을 어떻게 찍었나·어느 축과 조건인가·어디를 볼 것인가)를 그림 설명·그림 읽기·캡션이 말하는 범위 안에서. 출처는 프로그램이 참고문헌으로 단다.\n"
        "- 연구자가 쪽 구성을 적었으면 그 차례와 제목을 따른다 — 쪽마다 그 주제에 맞는 문장을 장 전체에서 고른다. 적지 않았으면 장의 줄기대로: 배경과 왜 문제인가 → 핵심 개념·기전 → 연구들이 본 것(엇갈림 포함) → 영향 인자·조건 → 남은 물음.\n"
        "- table 은 그 쪽이 수치를 견줄 때 [표 목록]의 번호 하나(없으면 null). 표는 그림 옆에 놓이므로 그 쪽은 figs 를 둘까지.\n"
        "- note 는 연구자가 읽을 말 — 화면에는 키워드만 있으니 여기에 설명을 다 담는다: 키워드마다 한두 문장, 그림마다 무엇을 가리킬지, 어느 연구가 무엇을 했는지(논문은 [번호]로). keys 의 문장이 말하는 것만. 'Claude 의 생각'에서 가져온 말은 '풀이:' 로 시작해 사실과 가른다.\n"
        "- 키워드·cap 에 [번호] 인용을 적지 않는다 — 출처는 프로그램이 단다. 번역투·'~에 대한 연구' 같은 군말을 뺀다. 전문 용어는 장에 쓰인 한국어 그대로.\n\n"
        "[그림 목록 — 장이 인용한 논문의 그림. id 로 고른다]\n" + ("\n".join(figtxt) or "(없음)") + "\n\n[표 목록]\n" + (tabtxt or "(없음)") + "\n\n[Claude 의 생각 — 장의 사실 층과 갈라 표시된 풀이]\n" + ("\n".join(cmts) or "(없음)") + "\n\n" + _chapter_text_ids(st)
    )


def deck_start(sid, body):
    effort = body.get("effort") if body.get("effort") in _CHAT_EFFORT else "xhigh"
    req = str(body.get("req") or "").strip()[:2000]
    try:
        n = max(0, min(40, int(body.get("n") or 0)))   # 전체 쪽 수(제목 쪽 포함, 참고문헌 쪽 제외). 0 = 자동
    except (TypeError, ValueError):
        n = 0
    if n == 1:
        n = 2
    with _LOCK:
        st = _load(sid)
        if not st or not st.get("chapter"):
            return {"error": "다 쓴 장에서만 만들 수 있습니다"}
        if st.get("deck_pending"):
            return {"error": "이미 만드는 중입니다"}
        st["deck_pending"] = time.time()
        st.pop("deck_error", None)
        st["deck_opts"] = {"effort": effort, "req": req, "n": n, "refs": body.get("refs") is True, "text": body.get("text") if body.get("text") in ("lecture", "keywords") else "lecture"}   # 참고문헌 쪽은 고른 때만 (출처는 쪽마다 아래에)
        _save(st)
    threading.Thread(target=_deck_job, args=(sid,), name="study-deck", daemon=True).start()
    return {"ok": True}


def _deck_index(st):
    """장이 인용한 논문 가운데 그림 색인이 없는 것을 만든다 (Sonnet, 편당 45초·2만 5천 토큰; 한 번 만들면 다음부터 공짜). 못 만들어도 장에 실린 그림으로 간다."""
    fi = _fi()
    if not fi:
        return 0
    items = [(p["file"], p.get("title") or "") for p in st.get("papers") or [] if p.get("n") and not fi.load(p["file"])]
    if not items:
        return 0
    try:
        fi.ensure(items, model="sonnet", workers=4)
    except Exception:
        pass
    return len(items)


def _deck_job(sid):
    st = _load(sid)
    if not st:
        return
    opts = st.get("deck_opts") or {}
    model, eff = _CHAT_EFFORT.get(opts.get("effort"), _CHAT_EFFORT["xhigh"])
    t0 = time.time()
    res, err, deck = None, "", None
    try:
        nidx = _deck_index(st)
        pool = _deck_pool(st)
        k = max(1, int(opts.get("n") or 0) - 1) if opts.get("n") else 0   # 내용 쪽 수 (제목 쪽을 뺀 것)
        run = cfg.get("claude_run")
        res = run(_deck_prompt(st, opts.get("req") or "", k, pool, opts.get("text") or "lecture"), timeout=1800, model=model, effort=eff, tools="") if run else None
        d = _json_of((res or {}).get("text") or "")
        if not d or not isinstance(d.get("slides"), list):
            err = "Claude 가 슬라이드 재료를 주지 않았습니다" + _why()
        else:
            deck = _deck_clean(st, d, k, pool)
            deck["refs"] = opts.get("refs") is True
            if not deck["slides"]:
                err = "장의 문장이 받치는 키워드가 없어 만들지 못했습니다"
            else:
                deck.update(id="d%d" % int(time.time() * 1000), t=time.time(), sec=round(time.time() - t0), model=res.get("model", ""), tok=res.get("tok"), req=opts.get("req") or "", effort=opts.get("effort"), n=opts.get("n") or 0,
                            indexed=nidx, pool=len(pool))
                deck["file"] = _deck_write(st, deck, pool)
    except Exception as e:
        err = "만들지 못했습니다: " + str(e)[:200]
    with _LOCK:
        st = _load(sid)
        if not st:
            return
        st.pop("deck_pending", None)
        if err:
            st["deck_error"] = err
        else:
            st.setdefault("decks", []).append(deck)
            st.pop("deck_error", None)
        _save(st)


def _deck_clean(st, d, k=0, pool=None):
    """Claude 의 답에서 장의 문장이 받치는 키워드·수치·메모만 남기고(keys 검사 + 수치 대조), 그림 id·표 번호를 장의 것으로 맞춘다. k 가 있으면 내용 쪽을 k 까지만."""
    P = st.get("papers") or []
    pool = pool if pool is not None else _deck_pool(st)
    tables = _deck_tables(st)
    src = st.get("src") or {}

    def keys_of(x):
        out = []
        for kk in (x.get("keys") if isinstance(x.get("keys"), list) else [])[:6]:
            kk = re.sub(r"^u", "", str(kk).strip())
            if _unit_at(st, kk)[1] and kk not in out:
                out.append(kk)
        return out

    def evidence(keys):   # keys 의 장 문장 + 그 근거 원문 (수치 대조용)
        texts = []
        for kk in keys:
            u = _unit_at(st, kk)[1]
            if not u:
                continue
            texts.append(u.get("t") or "")
            for sk in u.get("src") or []:
                texts.append((src.get(sk) or {}).get("en") or "")
        return texts

    def cites(keys):
        ns = set()
        for kk in keys:
            u = _unit_at(st, kk)[1]
            for sk in (u or {}).get("src") or []:
                try:
                    n = P[int(sk.split(":")[0])].get("n")
                except (ValueError, IndexError):
                    n = None
                if n:
                    ns.add(int(n))
        return sorted(ns)

    short = lambda v, n: re.sub(r"\s+", " ", str(v or "")).strip()[:n]
    nocite = lambda t: re.sub(r"(?<![A-Za-z0-9])[A-Z][a-z]?(?:[-\u2013]?\d+(?:\.\d+)?[A-Z][a-z]?)+(?![A-Za-z0-9])", " ", re.sub(r"\[\d{1,3}(?:\s*[,\u2013\-]\s*\d{1,3})*\]", " ", t))   # 인용 번호·합금 이름(Ta-2.5W)의 숫자는 수치가 아니다
    ints = lambda v: [int(x) for x in (v if isinstance(v, list) else [v]) if str(x).strip().lstrip("-").isdigit()]
    slides, dropped, used_figs, used_tabs = [], [], set(), set()
    for sl in d.get("slides") or []:
        if not isinstance(sl, dict):
            continue
        h = short(sl.get("h"), 60)
        bullets = []
        for b in (sl.get("bullets") or [])[:8]:
            if not isinstance(b, dict):
                continue
            t, keys = short(b.get("t"), 120), keys_of(b)
            if not t:
                continue
            if not keys:
                dropped.append({"slide": h, "t": t, "why": "장의 문장 id 가 없음"})
                continue
            miss = _nums_missing(nocite(t), evidence(keys))
            if miss:
                dropped.append({"slide": h, "t": t, "why": "수치가 근거에 없음: " + ", ".join(sorted(miss)[:4])})
                continue
            subs = []
            for x in (b.get("sub") if isinstance(b.get("sub"), list) else [])[:4]:
                xs = short(x, 110)
                if not xs:
                    continue
                m2 = _nums_missing(nocite(xs), evidence(keys))
                if m2:
                    dropped.append({"slide": h, "t": "(설명) " + xs, "why": "수치가 근거에 없음: " + ", ".join(sorted(m2)[:4])})
                    continue
                subs.append(xs)
            bullets.append({"t": t, "keys": keys, "sub": subs})
        if not h or not bullets:
            continue
        allk = [x for b in bullets for x in b["keys"]]
        allev = evidence(allk)
        fl = []
        for fg in (sl.get("figs") if isinstance(sl.get("figs"), list) else [])[:3]:
            fid = str((fg.get("id") if isinstance(fg, dict) else fg) or "").strip()
            if not fid.startswith("F"):
                fid = "F" + fid
            f = pool.get(fid)
            if not f or fid in used_figs or any(x["id"] == fid for x in fl) or len(fl) >= 3:
                continue
            cap = short(fg.get("cap") if isinstance(fg, dict) else "", 160) or short(f["what"], 110)
            fl.append({"id": fid, "pi": f["pi"], "n": f["n"], "ftype": f["ftype"], "in_ch": f["in_ch"], "cap": cap})
        for f in fl:
            used_figs.add(f["id"])
        tab = None
        tno = ints(sl.get("table"))
        if tno and tno[0] in tables and tno[0] not in used_tabs:
            used_tabs.add(tno[0])
            tab = {"no": tno[0], "cap": tables[tno[0]]["cap"]}
            if len(fl) > 2:
                fl = fl[:2]
        stats, statev = [], []       # 수치 상자는 뺐다 (사용자 2026-10-10: '왼쪽 아래 수치들 넣은 건 좀 짜쳐')
        note = None
        nt = sl.get("note") if isinstance(sl.get("note"), dict) else ({"t": sl.get("note")} if isinstance(sl.get("note"), str) else None)
        if nt and short(nt.get("t"), 2000):
            nk = keys_of(nt)
            t = short(nt.get("t"), 2000)
            miss = _nums_missing(nocite(re.sub(r"풀이:.*", "", t)), evidence(nk) + allev + statev) if nk or bullets else set()
            if miss:
                dropped.append({"slide": h, "t": "(메모) " + t[:80], "why": "수치가 근거에 없음: " + ", ".join(sorted(miss)[:4])})
            else:
                note = {"t": t, "keys": nk}
        slides.append({"h": h, "bullets": bullets, "figs": fl, "table": tab, "stats": stats, "note": note,
                       "cites": cites(allk + [x for st_ in stats for x in st_["keys"]] + ((note or {}).get("keys") or []))})
    if k and len(slides) > k:
        dropped.append({"slide": "", "t": "쪽 %d개가 정한 수(%d)를 넘어 뒤의 %d쪽을 뺐다" % (len(slides), k, len(slides) - k), "why": "쪽 수"})
        slides = slides[:k]
    return {"title": short(d.get("title"), 80) or (st.get("chapter") or {}).get("title") or "발표 자료", "subtitle": short(d.get("subtitle"), 160), "slides": slides, "dropped": dropped}


def _ref_line(p):
    return p.get("ref") or ("%s. %s. %s %s." % (p.get("short"), p.get("title"), p.get("journal"), p.get("year")))


def _ref_short(p):
    """그림 아래 한 줄용 참고문헌: 'Davis JM et al. Int J Mach Tools Manuf 2020;157:103607' — 참고문헌 전체를 좁은 열에 달면 4~6줄이 되어 그림이 작아진다(2026-10-10, 그림 셋 쪽). 전체는 발표자 메모에"""
    line = _ref_line(p).strip().rstrip(".")
    m = re.search(r"\.\s+([A-Z][^.]*?\s(?:19|20)\d\d(?:;[^.\s]+)?)$", line)
    jpart = m.group(1) if m else ("%s %s" % (p.get("journal") or "", p.get("year") or "")).strip()
    auth = line.split(". ")[0] if ". " in line else (p.get("short") or "")
    first = auth.split(",")[0].strip()
    return "%s%s %s" % (first, " et al." if "," in auth else ".", jpart)


def _deck_write(st, deck, pool=None):
    """deck 재료 → .pptx (공부\\발표\\<장 id>_<deck id>.pptx) + 쪽 미리보기 PNG(같은 이름의 폴더). 파일 이름을 돌려준다."""
    import pptx_min
    P = st.get("papers") or []
    pool = pool if pool is not None else _deck_pool(st)
    tables = _deck_tables(st)
    ch = st.get("chapter") or {}
    stem = "%s_%s" % (st["id"], deck["id"])
    path = os.path.join(_deck_dir(), stem + ".pptx")
    byn = {str(p.get("n")): p for p in P if p.get("n")}
    pj = lambda n: ("%s, %s" % (byn[str(n)].get("short"), byn[str(n)].get("journal") or "?")) if str(n) in byn else str(n)
    slides, allc = [], set()
    for sl in deck["slides"]:
        panels = []
        for fg in sl.get("figs") or []:
            f = pool.get(fg.get("id")) if fg.get("id") else None
            if not f:
                continue
            p = P[f["pi"]] if isinstance(f["pi"], int) and f["pi"] < len(P) else {}
            ref = (_ref_short(p) + " — Fig. %s" % f["n"]) if p else "Fig. %s" % f["n"]
            panels.append({"kind": "fig", "path": f["path"], "caption": fg.get("cap") or (f["what"] or "")[:110], "ref": ref})
            if p.get("n"):
                allc.add(int(p["n"]))
        if sl.get("table") and tables.get(sl["table"].get("no")):
            t = tables[sl["table"]["no"]]
            panels.append({"kind": "table", "cap": "표. " + t["cap"][:90], "cols": t["cols"], "rows": t["rows"]})
        cs = sorted(set(sl.get("cites") or []))
        allc |= set(cs)
        foot = ("출처: " + " · ".join("[%d] %s" % (n, pj(n)) for n in cs)) if cs else ""
        note = ((sl.get("note") or {}).get("t") or "")
        if (sl.get("layout") or "") == "fig_only" and sl.get("bullets"):   # 그림만 둔 쪽은 키워드를 메모로
            note = "키워드: " + " · ".join(b["t"] for b in sl["bullets"]) + ("\n\n" + note if note else "")
        if cs:
            note += ("\n\n" if note else "") + "출처\n" + "\n".join("[%d] %s" % (n, _ref_line(byn[str(n)])) for n in cs if str(n) in byn)
        slides.append({"h": sl["h"], "bullets": [{"t": b["t"], "sub": list(b.get("sub") or [])} for b in sl["bullets"]],
                       "stats": [], "panels": panels, "foot": foot, "note": note, "layout": sl.get("layout") or "auto"})
    refs = ["[%s] %s" % (p.get("n"), _ref_line(p)) for p in sorted((p for p in P if p.get("n") and int(p["n"]) in allc), key=lambda p: int(p["n"]))]
    prev_dir = os.path.join(_deck_dir(), stem)
    r = pptx_min.build(path, {"title": deck["title"], "subtitle": (deck.get("subtitle") or "") + "\n" + time.strftime("%Y-%m-%d"), "deck_title": deck["title"], "skip_refs": deck.get("refs", True) is not True,
                              "footer": "Athenaeum 공부 장 「%s」 에서 — 내 서재의 논문 %d편으로 쓴 장, 키워드마다 논문의 원문 문장에 묶여 있음" % (ch.get("title") or "", (st.get("stats") or {}).get("cited") or len(P)),
                              "title_note": "이 발표 자료는 Athenaeum 의 공부 장을 바탕으로 만들었습니다. 키워드와 메모는 장의 문장을 줄인 것이고, 장의 문장은 논문의 원문 문장과 대조한 것입니다.",
                              "slides": slides, "refs": refs}, preview_dir=prev_dir)
    deck["previews"] = r.get("previews", 0)
    return stem + ".pptx"


def deck_layout(sid, body):
    """쪽 하나의 배치·그림 순서를 바꾸고 pptx·미리보기만 다시 쓴다 (Claude 없음, 1~3초). 사용자 2026-10-10: '쪽 구성을 내가 정할 수 있게'"""
    import pptx_min
    lay = str(body.get("layout") or "auto")
    if lay not in pptx_min.LAYOUT_KEYS:
        return {"error": "모르는 배치"}
    with _LOCK:
        st = _load(sid)
        decks = (st or {}).get("decks") or []
        d = next((x for x in decks if x.get("id") == str(body.get("deck") or "")), None)
        if not d:
            return {"error": "자료를 찾지 못했습니다"}
        try:
            i = int(body.get("slide") or 0)
        except (TypeError, ValueError):
            i = -1
        if not (0 <= i < len(d.get("slides") or [])):
            return {"error": "쪽 번호가 맞지 않습니다"}
        sl = d["slides"][i]
        sl["layout"] = lay
        if body.get("rotate") and len(sl.get("figs") or []) > 1:
            sl["figs"] = sl["figs"][1:] + sl["figs"][:1]
        try:
            d["file"] = _deck_write(st, d)
        except Exception as e:
            return {"error": "다시 그리기 실패: " + str(e)[:200]}
        d["rev"] = time.time()
        _save(st)
    return {"ok": True, "rev": d["rev"]}


def deck_delete(sid, did):
    with _LOCK:
        st = _load(sid)
        if not st:
            return {"error": "찾지 못했습니다"}
        gone = next((d for d in st.get("decks") or [] if d.get("id") == did), None)
        st["decks"] = [d for d in st.get("decks") or [] if d.get("id") != did]
        _save(st)
    if gone and gone.get("file"):
        fp = os.path.join(_deck_dir(), os.path.basename(gone["file"]))
        if os.path.isfile(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
        shutil.rmtree(os.path.join(_deck_dir(), os.path.basename(gone["file"])[:-5]), ignore_errors=True)   # 쪽 미리보기 PNG 폴더
    return {"ok": True}


def map_start(sid, body):
    effort = body.get("effort") if body.get("effort") in _CHAT_EFFORT else "xhigh"
    with _LOCK:
        st = _load(sid)
        if not st or not st.get("chapter"):
            return {"error": "다 쓴 장에서만 그릴 수 있습니다"}
        if st.get("map_pending"):
            return {"error": "이미 만드는 중입니다"}
        st["map_pending"] = time.time()
        st.pop("map_error", None)
        st["map_opts"] = {"effort": effort}
        _save(st)
    threading.Thread(target=_map_job, args=(sid,), name="study-map", daemon=True).start()
    return {"ok": True}


def _map_job(sid):
    st = _load(sid)
    if not st:
        return
    model, eff = _CHAT_EFFORT.get((st.get("map_opts") or {}).get("effort"), _CHAT_EFFORT["xhigh"])
    t0 = time.time()
    res, err, m = None, "", None
    try:
        run = cfg.get("claude_run")
        res = run(_map_prompt(st), timeout=1800, model=model, effort=eff, tools="") if run else None
        d = _json_of((res or {}).get("text") or "")
        if not d or not isinstance(d.get("branches"), list):
            err = "Claude 가 그림 재료를 주지 않았습니다" + _why()
        else:
            m = _map_clean(st, d)
            if not m["branches"]:
                err = "장의 문장이 받치는 항목이 없어 그리지 못했습니다"
    except Exception as e:
        err = "Claude 호출 실패: " + str(e)[:200]
    with _LOCK:
        st = _load(sid)
        if not st:
            return
        st.pop("map_pending", None)
        if err:
            st["map_error"] = err
        else:
            m.update(t=time.time(), sec=round(time.time() - t0), model=res.get("model", ""), tok=res.get("tok"))
            st["map"] = m
            st.pop("map_error", None)
        _save(st)


def _map_clean(st, d):
    """Claude 의 답에서 장의 문장이 받치는 항목만 남긴다. keys 는 'u0.1.2' → '0.1.2'."""
    P = st.get("papers") or []
    by_n = {p.get("n"): i for i, p in enumerate(P) if p.get("n")}
    nsec = len((st.get("chapter") or {}).get("sections") or [])

    def keys_of(x):
        out = []
        for k in (x.get("keys") if isinstance(x.get("keys"), list) else [])[:6]:
            k = re.sub(r"^u", "", str(k).strip())
            if _unit_at(st, k)[1] and k not in out:
                out.append(k)
        return out

    def short(v, n):
        return re.sub(r"\s+", " ", str(v or "")).strip()[:n]

    branches, seen = [], set()
    for b in d.get("branches") or []:
        if not isinstance(b, dict):
            continue
        try:
            si = int(b.get("si"))
        except (TypeError, ValueError):
            continue
        if si < 0 or si >= nsec or si in seen:
            continue
        sec = st["chapter"]["sections"][si]
        if sec.get("kind") == "summary":
            continue
        seen.add(si)
        points = [{"label": short(p.get("label"), 40), "detail": short(p.get("detail"), 120), "keys": keys_of(p)} for p in (b.get("points") or [])[:6] if isinstance(p, dict)]
        points = [p for p in points if p["label"] and p["keys"]]
        papers = []
        for p in (b.get("papers") or [])[:6]:
            if not isinstance(p, dict):
                continue
            try:
                nn = int(p.get("n"))
            except (TypeError, ValueError):
                continue
            ks = keys_of(p)
            if nn in by_n and ks:
                papers.append({"n": nn, "pi": by_n[nn], "did": short(p.get("did"), 50), "found": short(p.get("found"), 60), "keys": ks})
        if points or papers:
            branches.append({"si": si, "title": short(b.get("title"), 30) or sec["title"][:30], "points": points, "papers": papers,
                             "open": [short(x, 50) for x in (b.get("open") or [])[:3] if short(x, 50)]})
    branches.sort(key=lambda x: x["si"])
    return {"root": short(d.get("root"), 24) or (st["chapter"].get("title") or "")[:24], "branches": branches}


# ---------- HTTP ----------
def _q(url, k):
    return (urllib.parse.parse_qs(url.query).get(k) or [""])[0]


def handle_get(h, url):
    p = url.path
    if p == "/study":
        with open(os.path.join(cfg["BASE"], "study.html"), "rb") as f:
            return h._send(200, f.read(), "text/html; charset=utf-8")
    if p == "/api/study":
        return h._send(200, {"items": list_studies(), "running": len(_JOBS), "version": VERSION})
    if p == "/api/study/get":
        return h._send(200, get(_q(url, "id")))
    if p == "/api/study/chats":
        return h._send(200, chats_of(_q(url, "id")))
    if p == "/api/study/deckimg":      # 발표 자료의 쪽 미리보기 PNG: ?id=<공부 id>&name=<pptx 파일>&n=2
        name = os.path.basename(_q(url, "name"))
        try:
            n = int(_q(url, "n") or 0)
        except ValueError:
            n = 0
        fp = os.path.join(_deck_dir(), name[:-5] if name.lower().endswith(".pptx") else name, "s%02d.png" % n)
        if name.startswith(_q(url, "id") + "_") and n > 0 and os.path.isfile(fp):
            with open(fp, "rb") as f:
                return h._send(200, f.read(), "image/png")
        return h._send(404, {"error": "no preview"})
    if p == "/api/study/deck":         # 발표 자료 .pptx 내려받기: ?id=<공부 id>&name=<파일>
        name = os.path.basename(_q(url, "name"))
        fp = os.path.join(_deck_dir(), name)
        if name.lower().endswith(".pptx") and name.startswith(_q(url, "id") + "_") and os.path.isfile(fp):
            with open(fp, "rb") as f:
                return h._send(200, f.read(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        return h._send(404, {"error": "no deck"})
    if p == "/api/study/fig":          # 장에 실은 그림(논문 PDF 에서 잘라 둔 PNG): ?id=<공부 id>&name=P5_fig4.png
        name = os.path.basename(_q(url, "name"))
        fp = os.path.join(_fig_dir(_q(url, "id")), name)
        if name.lower().endswith(".png") and os.path.isfile(fp):
            with open(fp, "rb") as f:
                return h._send(200, f.read(), "image/png")
        return h._send(404, {"error": "no fig"})
    return h._send(404, {"error": "unknown"})


def handle_post(h, body):
    try:
        op, sid = body.get("op"), str(body.get("id") or "")
        if op == "start":
            return h._send(200, start(body))
        if op == "scope":
            return h._send(200, scope_check(body))
        if op == "rewrite":
            return h._send(200, rewrite_start(sid))
        if op == "resume":
            return h._send(200, resume(sid))
        if op == "stop":
            if sid in _JOBS:
                _JOBS[sid]["stop"] = True
            return h._send(200, {"ok": True})
        if op == "delete":
            if _live(sid):
                return h._send(200, {"error": "쓰는 중에는 지울 수 없습니다 — 먼저 멈추세요"})
            fp = _path(sid)
            if os.path.isfile(fp) and os.path.dirname(os.path.abspath(fp)) == os.path.abspath(cfg["DIR"]):
                os.remove(fp)
                shutil.rmtree(_fig_dir(sid), ignore_errors=True)     # 그 장의 그림도
            return h._send(200, {"ok": True})
        if op == "ask":
            return h._send(200, chat_ask(sid, body))
        if op == "map":
            return h._send(200, map_start(sid, body))
        if op == "comment":
            return h._send(200, comment_start(sid, body))
        if op == "basics":
            return h._send(200, basics_start(sid, body))
        if op == "deck":
            return h._send(200, deck_start(sid, body))
        if op == "deck_delete":
            return h._send(200, deck_delete(sid, str(body.get("deck") or "")))
        if op == "deck_layout":
            return h._send(200, deck_layout(sid, body))
        if op == "chat_delete":
            return h._send(200, chat_delete(sid, str(body.get("chat") or "")))
        if op == "cancel":
            return h._send(200, chat_cancel(sid, str(body.get("chat") or "")))
        if op == "md":
            st = _load(sid)
            if not st or not st.get("chapter"):
                return h._send(200, {"error": "아직 쓴 글이 없습니다"})
            return h._send(200, {"md": to_md(st), "name": re.sub(r'[\\/:*?"<>|]', " ", (st["chapter"].get("title") or "공부")).strip()[:60] + ".md"})
        return h._send(404, {"error": "unknown"})
    except Exception as e:
        return h._send(200, {"error": str(e)[:300]})
