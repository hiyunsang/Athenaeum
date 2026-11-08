# -*- coding: utf-8 -*-
"""서재 문장 색인 — 물음을 받자마자 서재 전체의 문장에서 그 낱말이 든 문장을 찍어 꺼낸다. 모델을 부르지 않고 1초 안에 끝난다.
사용자(2026-11-09, 연구 노트의 부족한 점 — '대화가 서재를 제목 한 줄로만 안다' 에): '데이터베이스 색인을 잘해놓으면 되는 거 아닌가. 주제 맞는 거만 찾음 되잖아.'

무엇을 색인하나 — 논문마다 study._paper_sents 의 읽힐 문장(_readable: 제목 줄·참고문헌·짧은 조각은 뺌)을
  · 영어 원문: 소문자 알파벳·숫자 토큰(불용어, 숫자만인 것은 뺌). 질의는 어간의 앞머리로 맞춘다(serrat → serrated·serration)
  · 한국어 번역: 한글 두 글자 묶음(bigram). 질의의 한국어 낱말(조사를 뗀 것)은 그 묶음이 모두 든 문장으로 — '절삭력' 은 '절삭'·'삭력' 둘 다 든 문장
postings 는 array('I') 에 (논문 번호 << 16 | 문장 자리) 로 묶는다. 논문의 제목·요약 한 줄은 따로(ptok) 두어 논문 단위 점수에 더한다.

질의(search) — 물음의 낱말마다 무게 = idf(드문 낱말이 무겁다) × 출처 무게(물음 1.0 · 주제 제목 같은 배경 낱말은 작게) → 문장마다 맞은 낱말의 무게 합.
  너무 흔한 낱말(문장의 1/4 넘게 드는 것)은 점수에서 뺀다. 물음의 '뚜렷한' 낱말(문장의 5% 아래)이 둘 이상 맞았거나 아주 드문 낱말(0.5% 아래)이 하나라도 맞은 문장만 후보.
  논문별로 묶어 점수 순으로, 논문마다 몇 문장씩. 서재가 바뀌면(논문이 들어오거나 번역이 다시 만들어지면) 60초 안에 알아보고 배경에서 다시 만든다 — 그동안은 옛 색인으로.

서버가 init 으로 함수를 준다(paper_sents·readable·is_korean·archive_pdfs·paper_line·pref_abbrs·GEN_DIR·ARCHIVE). start() 는 main() 에서만 — 켠 뒤 25초에 배경에서 만든다.
"""
import array
import bisect
import math
import os
import re
import threading
import time

cfg = {}
_SWAP = threading.Lock()      # 색인 바꿔 끼우기
_BUILD = threading.Lock()     # 한 번에 하나만 만든다
_IDX = None
_ST = {"building": False, "t": 0, "sec": 0, "err": "", "checked": 0, "builds": 0}
STAMP_EVERY = 60              # 서재가 바뀌었나 보는 간격(초)
COMMON = 0.25                 # 문장의 이 비율 넘게 드는 낱말은 점수에서 뺀다
STRONG = 0.05                 # 이 아래로 드는 낱말이 '뚜렷한' 낱말
RARE = 0.005                  # 이 아래면 하나만 맞아도 후보

_STOP_EN = set("""a an the of in on at to for from by with without as is are was were be been being it its this that these those and or but not no nor so than then thus
such which who whom whose what when where while we our us they their them he she his her you your i my me do does did done have has had having can could may might shall
should will would also into onto upon over under between among within through during after before about above below up down out off again further here there all any both
each few more most other some own same only very just because however therefore hence moreover although though whereas since due via per using used use based respectively
well et al fig figs table eq ref refs i.e e.g vs etc""".split())
# 연구 노트가 쓰던 한국어 규칙을 여기로 옮겼다(notes 가 이것을 쓴다) — 조사와 서술어 꼴을 떼고, 토의에서 늘 나오는 낱말은 뺀다
_KO_STOP = {"연구", "논문", "결과", "실험", "분석", "가능", "경우", "대한", "통해", "있다", "없다", "때문", "그리고", "하지만", "영향", "특성", "조건", "방법", "과정", "기존", "제안", "확인", "비교", "관찰", "측정",
            "모델", "설계", "가설", "물음", "수준", "변수", "우리", "연구실", "논의", "정리", "다음", "지금", "이런", "어떤", "무엇", "것은", "것이", "것을", "하나", "둘째", "첫째", "서재", "탐색", "공부", "주제", "카드", "토의",
            "같은", "그런", "이것", "그것", "저것", "여기", "거기", "뭔가", "어디", "얘기", "이야기", "생각", "느낌", "정도", "부분", "내용", "관련", "자체", "사실", "경향", "문제", "질문", "대답", "설명", "이유", "그냥", "너무", "조금", "많이", "다시", "아직", "거의", "약간", "뭐가", "뭐지", "그래서", "그러면", "그럼", "근데", "그런데", "아니", "맞음", "맞아", "좋음", "좋아", "별로", "일단", "결국", "바로", "계속", "먼저", "나중", "오히려", "역시", "혹시", "아마", "어차피", "사람", "경우에", "때문에",
            "그래", "그게", "이게", "그거", "이거", "저거", "진짜", "정말", "완전", "대충", "아니면", "그러니까", "그니까", "왜냐", "어떻게", "어느", "무슨", "그쪽", "이쪽", "내가", "니가", "네가", "너가", "얘가", "얘는", "자기", "자꾸", "애초", "굳이", "차라리", "하필", "제대로", "대체", "도대체", "여전히", "이제", "이미", "전에", "지난", "요즘", "그때", "이때", "항상", "가끔", "자주", "종종",
            "바꿔", "바꾸", "써서", "써도", "써봐", "해서", "해도", "해봐", "하자", "되나", "하나", "할까", "될까", "볼까", "되지", "하지", "않나", "않지", "있나", "없나", "맞나", "맞지", "틀려", "틀림", "싶어", "싶다", "싶은", "같아", "같음", "다름", "달라", "달리", "어때", "어떠", "어떨", "어쩌", "글쎄", "다만", "말고", "빼고", "대신", "위해", "따라", "통한", "관해", "대해", "의해", "인해", "보면", "하면", "되면", "하고", "되고", "보고", "있고", "없고", "하는", "되는", "있는", "없는", "않는", "않고", "않을", "않았", "했는", "했던", "하던", "그만", "헛소리", "미안", "고마워", "감사",
            "말해", "말하", "알려", "보여", "줘서", "주세요", "보자", "하자", "찾아", "찾아서", "봐서", "봐줘", "해줘", "줘봐", "해볼", "해보", "해본", "해도", "했어", "했다", "했지", "했나", "같네", "같고", "같지", "거야", "거지", "거든", "건데", "건가", "걸까", "텐데", "겠네", "겠지", "겠다", "라고", "라는", "이라고", "이라는", "다고", "다는", "냐고", "냐는", "자고", "자는"}   # '말해 줘'·'알려 줘'·'찾아 봐' 같은 부탁의 말 — 첫 실전에서 '말해' 가 질의 낱말로 들어갔다
_KO_TAIL = re.compile(r"(으로써|으로는|에서는|에서도|으로|에서|부터|까지|에게|처럼|보다|이라|이다|들의|들이|들을|들은|들|을|를|이|가|은|는|의|에|로|과|와|도|만|나|고|며|서|면|적인|적)$")
_KO_PRED = ("다", "요", "데", "게", "까", "니", "면", "서", "지만", "는지", "라서", "하게", "히", "들", "네", "지", "죠", "음", "임", "냐", "나요", "는데", "거든", "잖아", "는가", "을까", "ㄹ까")
_KO_VERB_END = ("해", "돼", "봐", "줘", "놔", "니까", "거든", "던데", "은가", "구나", "네요", "세요", "어요", "아요", "지요", "습니", "읍니")   # 세 글자 넘는 낱말이 이것으로 끝나면 서술어 꼴('확인해'·'판단해'·'세워봐') — 두 글자 명사(이해·분해·용해)는 지킨다
_TOK = re.compile(r"[a-z0-9]+")
_HAN = re.compile(r"[가-힣]+")


def init(**kw):
    cfg.update(kw)


def _tok_en(text):
    out = set()
    for t in _TOK.findall((text or "").lower()):
        if len(t) < 2 or t in _STOP_EN or t.isdigit():
            continue
        out.add(t)
    return out


def _bg_ko(text):
    out = set()
    for run in _HAN.findall(text or ""):
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    return out


def ko_words(text):
    """한국어 글의 내용 낱말 — 조사를 떼고 토의의 흔한 말·서술어 꼴을 뺀 것(차례대로, 겹침 없이)"""
    out, seen = [], set()
    for w in _HAN.findall(text or ""):
        if len(w) < 2 or w in _KO_STOP:   # '그래서' 는 조사를 떼기 전에 걸러야 한다 — 떼면 '그래' 가 되어 '그래프' 의 문장에 걸렸다
            continue
        w2 = _KO_TAIL.sub("", w)
        if len(w2) < 2 or w2 in _KO_STOP or w2.endswith(_KO_PRED) or (len(w2) >= 3 and w2.endswith(_KO_VERB_END)) or w2 in seen:
            continue
        seen.add(w2)
        out.append(w2)
    return out


def en_words(text):
    """영어 낱말(소문자, 불용어·숫자 뺌, 차례대로 겹침 없이) — 한국어 글에 섞인 영어 용어·약어도 여기서 나온다"""
    out, seen = [], set()
    for t in _TOK.findall((text or "").lower()):
        if len(t) < 2 or t in _STOP_EN or t.isdigit() or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _meta(f):
    stem = os.path.splitext(f)[0]
    parts = stem.split("_", 3)
    if len(parts) == 4 and parts[0].isdigit():
        return {"year": parts[0], "journal": parts[1], "author": parts[2], "title": parts[3]}
    return {"year": "", "journal": "", "author": "", "title": stem}


def _key(f):
    """논문의 재료가 바뀌었나 — 정렬표·번역문·PDF 의 시각"""
    stem = os.path.splitext(f)[0]
    out = []
    for p in (os.path.join(cfg["GEN_DIR"], stem + ".번역.정렬.json"), os.path.join(cfg["GEN_DIR"], stem + ".번역.md"), os.path.join(cfg["ARCHIVE"], f)):
        try:
            out.append(int(os.path.getmtime(p)))
        except OSError:
            out.append(-1)
    return tuple(out)


def _stamp():
    """서재가 바뀌었나 — PDF 수와 정렬표의 수·가장 새 시각"""
    try:
        pdfs = [e for e in os.scandir(cfg["ARCHIVE"]) if e.name.lower().endswith(".pdf")]
    except OSError:
        pdfs = []
    n_al, mt = 0, 0
    try:
        for e in os.scandir(cfg["GEN_DIR"]):
            if e.name.endswith(".번역.정렬.json"):
                n_al += 1
                try:
                    mt = max(mt, int(e.stat().st_mtime))
                except OSError:
                    pass
    except OSError:
        pass
    pm = 0
    for e in pdfs:
        try:
            pm = max(pm, int(e.stat().st_mtime))
        except OSError:
            pass
    return (len(pdfs), n_al, mt, pm)


def _build():
    t0 = time.time()
    files = list(cfg["archive_pdfs"]())
    tops = set()
    try:
        tops = set(str(x).upper() for x in (cfg["pref_abbrs"]() if cfg.get("pref_abbrs") else ()))
    except Exception:
        tops = set()
    papers, en, ko, ptok, nsent = [], {}, {}, [], 0
    for f in files:
        try:
            sents = cfg["paper_sents"](f)
        except Exception:
            sents = []
        if not sents:
            continue
        try:
            if cfg.get("is_korean") and cfg["is_korean"](sents):   # 번역본 PDF 가 논문 이름으로 든 것 — 원문이 한국어라 색인하지 않는다
                continue
        except Exception:
            pass
        try:
            ok = cfg["readable"](sents) if cfg.get("readable") else sents
        except Exception:
            ok = sents
        okid = {id(s) for s in ok}
        pi = len(papers)
        m = _meta(f)
        try:
            line = re.sub(r"\s+", " ", cfg["paper_line"](f) or "").strip() if cfg.get("paper_line") else ""
        except Exception:
            line = ""
        papers.append({"file": f, "stem": os.path.splitext(f)[0], "key": _key(f), "n": len(sents), "year": m["year"], "journal": m["journal"], "author": m["author"],
                       "title": m["title"], "line": line, "star": bool(m["journal"]) and m["journal"].upper() in tops, "tr": str(sents[0].get("sid") or "").startswith("s")})
        ptok.append((_tok_en(m["title"] + " " + line), _bg_ko(line)))
        for pos, s in enumerate(sents):
            if id(s) not in okid or pos >= 65535:
                continue
            code = (pi << 16) | pos
            for t in _tok_en(s.get("en") or ""):
                a = en.get(t)
                if a is None:
                    en[t] = a = array.array("I")
                a.append(code)
            kt = s.get("ko") or ""
            if kt:
                for b in _bg_ko(kt):
                    a = ko.get(b)
                    if a is None:
                        ko[b] = a = array.array("I")
                    a.append(code)
            nsent += 1
    return {"papers": papers, "en": en, "ko": ko, "voc": sorted(en), "ptok": ptok, "nsent": nsent, "t": time.time(), "sec": round(time.time() - t0, 1), "stamp": _stamp()}


def _rebuild():
    global _IDX
    _ST["building"] = True
    try:
        idx = _build()
        with _SWAP:
            _IDX = idx
        _ST.update(t=idx["t"], sec=idx["sec"], err="", checked=time.time())
        _ST["builds"] += 1
        try:
            print("libindex: 서재 문장 색인 — 논문 %d편 · 문장 %d개 · %.1f초" % (len(idx["papers"]), idx["nsent"], idx["sec"]), flush=True)
        except Exception:
            pass
    except Exception as e:
        _ST["err"] = str(e)[:200]
        try:
            print("libindex 실패:", e, flush=True)
        except Exception:
            pass
    finally:
        _ST["building"] = False


def _bg_rebuild():
    if not _BUILD.acquire(blocking=False):
        return
    try:
        _rebuild()
    finally:
        _BUILD.release()


def ensure(wait=True):
    """색인을 돌려준다. 아직 없으면 만든다(wait=False 면 다른 스레드가 만드는 중일 때 None). 있으면 서재가 바뀌었는지 60초마다 보고 바뀌었으면 배경에서 다시."""
    if _IDX is None:
        if not wait and _ST["building"]:
            return None
        with _BUILD:
            if _IDX is None:
                _rebuild()
        return _IDX
    now = time.time()
    if now - _ST["checked"] > STAMP_EVERY and not _ST["building"]:
        _ST["checked"] = now
        try:
            st = _stamp()
        except Exception:
            st = None
        if st and st != _IDX.get("stamp"):
            threading.Thread(target=_bg_rebuild, name="libindex", daemon=True).start()
    return _IDX


def start(delay=25):
    """서버를 켠 뒤 배경에서 만든다 — 첫 물음이 색인을 기다리지 않게"""
    def go():
        time.sleep(delay)
        try:
            ensure()
        except Exception:
            pass
    threading.Thread(target=go, name="libindex-start", daemon=True).start()


def status():
    idx = _IDX
    if not idx:
        return {"ready": False, "building": _ST["building"], "err": _ST["err"]}
    mem = sum(len(a) for a in idx["en"].values()) * 4 + sum(len(a) for a in idx["ko"].values()) * 4
    return {"ready": True, "papers": len(idx["papers"]), "sents": idx["nsent"], "tokens": len(idx["en"]), "bigrams": len(idx["ko"]), "t": idx["t"], "sec": idx["sec"],
            "mem_mb": round(mem / 1048576, 1), "building": _ST["building"], "builds": _ST["builds"], "err": _ST["err"]}


# ---------- 질의 ----------
def _stem(t):
    for suf, mn in (("ing", 7), ("ies", 6), ("ed", 6), ("es", 6), ("al", 7), ("ly", 6), ("s", 5)):
        if t.endswith(suf) and len(t) >= mn:
            return t[:-len(suf)]
    return t


def _expand(idx, tok):
    """영어 낱말 하나 → 색인의 토큰들. 4자 이하(약어·짧은 말)는 그대로와 복수형만, 그 밖은 어간의 앞머리로."""
    en = idx["en"]
    if len(tok) <= 4:
        return [w for w in (tok, tok + "s", tok + "es") if w in en]
    stem = _stem(tok)
    voc = idx["voc"]
    i, j = bisect.bisect_left(voc, stem), bisect.bisect_right(voc, stem + "￿")
    cands = voc[i:j]
    if len(cands) > 80:   # 짧은 어간이 수백 낱말에 걸리면 가까운 꼴만
        cands = [w for w in cands if len(w) <= len(stem) + 3][:80]
    if not cands and tok in en:
        cands = [tok]
    return cands


def _codes_en(idx, toks):
    out = set()
    for w in toks:
        a = idx["en"].get(w)
        if a is not None:
            out.update(a)
    return out


def _codes_ko(idx, word, bgsets):
    bgs = [word[i:i + 2] for i in range(len(word) - 1)]
    arrs = []
    for b in bgs:
        a = idx["ko"].get(b)
        if a is None:
            return set()
        arrs.append((len(a), b, a))
    arrs.sort()
    out = None
    for _n, b, a in arrs:
        s = bgsets.get(b)
        if s is None:
            s = bgsets[b] = set(a)
        out = set(s) if out is None else (out & s)
        if not out:
            break
    return out or set()


def terms_of(idx, text, w=1.0, src="q"):
    """글 → 질의 낱말들 [{t, kind en|ko, toks, codes, df, idf, w, src, common}]"""
    N = max(1, idx["nsent"])
    out, bgsets, seen = [], {}, set()
    for tok in en_words(text):
        toks = _expand(idx, tok)
        codes = _codes_en(idx, toks)
        if not codes or tok in seen:
            continue
        seen.add(tok)
        df = len(codes)
        out.append({"t": tok, "kind": "en", "toks": toks[:12], "codes": codes, "df": df, "idf": math.log(1 + N / df), "w": w, "src": src, "common": df > N * COMMON})
    for word in ko_words(text):
        if word in seen:
            continue
        codes = _codes_ko(idx, word, bgsets)
        if not codes:
            continue
        seen.add(word)
        df = len(codes)
        out.append({"t": word, "kind": "ko", "toks": [word], "codes": codes, "df": df, "idf": math.log(1 + N / df), "w": w, "src": src, "common": df > N * COMMON})
    return out


_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def mentions(idx, text):
    """글 속의 '저자 연도'(Zan 2026 · Fang et al. 2005 · (Kümmel, 2015)) → 그 논문들의 번호. 연도 앞 세 낱말 안의 라틴 낱말을 파일 이름의 첫 저자와 맞춘다(성이 두 낱말이면 뒤에서부터 이어 붙여)."""
    by = {}
    for pi, p in enumerate(idx["papers"]):
        a = re.sub(r"[^a-z]", "", (p.get("author") or "").lower())
        if a and p.get("year"):
            by.setdefault(p["year"], []).append((a, pi))
    out = []
    norm = lambda w: re.sub(r"[^a-z]", "", w.lower().replace("ü", "u").replace("ö", "o").replace("ä", "a").replace("é", "e"))
    for m in _YEAR.finditer(text or ""):
        cands = by.get(m.group(1))
        if not cands:
            continue
        words = [norm(w) for w in re.findall(r"[A-Za-zÀ-ž][A-Za-zÀ-ž'\-]*", text[max(0, m.start() - 60):m.start()])]
        words = [w for w in words if w and w not in ("et", "al", "and", "the", "of", "in", "by", "from")][-3:]
        found = False
        for i in range(len(words) - 1, -1, -1):
            au = "".join(words[i:])
            if len(au) < 2:
                continue
            for a, pi in cands:
                if a == au or (len(au) >= 3 and (a.startswith(au) or au.startswith(a))):
                    if pi not in out:
                        out.append(pi)
                    found = True
            if found:
                break
    return out


def search(question, context="", k=50, per_paper=6, max_papers=14, chars=12000, wait=True):
    """물음(과 배경 글)으로 서재 문장을 찾는다 → {terms, papers[{file, stem, short, author, star, year, journal, title, line, tr, score, mention, sents[{pos, sid, en, ko, page, sec, score, hit}]}], n, cand, nsent, sec}
    물음이 '저자 연도' 로 가리킨 논문은 낱말이 안 맞아도 맨 앞에 넣는다(그 논문의 맞는 문장, 없으면 앞머리 문장)."""
    t0 = time.time()
    idx = ensure(wait=wait)
    if not idx:
        return {"terms": [], "papers": [], "n": 0, "cand": 0, "nsent": 0, "sec": 0, "ready": False}
    N = max(1, idx["nsent"])
    terms = terms_of(idx, question, 1.0, "q")
    if context:
        have = {x["t"] for x in terms}
        terms += [t for t in terms_of(idx, context, 0.45, "ctx") if t["t"] not in have]
    q_strong = [i for i, t in enumerate(terms) if t["src"] == "q" and t["df"] <= N * STRONG]
    need = 2 if len(q_strong) >= 2 else 1
    scores, hits = {}, {}
    for i, t in enumerate(terms):
        if t["common"]:
            continue
        wt = t["idf"] * t["w"]
        for code in t["codes"]:
            scores[code] = scores.get(code, 0.0) + wt
            h = hits.get(code)
            if h is None:
                hits[code] = [i]
            else:
                h.append(i)
    cands = []
    for code, sc in scores.items():
        h = hits[code]
        qs = [i for i in h if terms[i]["src"] == "q"]
        if not qs:
            continue
        ns = sum(1 for i in qs if terms[i]["df"] <= N * STRONG)
        if ns >= need or any(terms[i]["df"] <= N * RARE for i in qs):
            cands.append((sc, code))
    cands.sort(reverse=True)
    byp = {}
    for sc, code in cands:
        pi, pos = code >> 16, code & 0xffff
        byp.setdefault(pi, []).append((sc, pos))
    men = mentions(idx, question)
    for pi in men:   # 물음이 가리킨 논문 — 후보에 없으면 그 논문에서 어느 낱말이든 맞은 문장을, 그것도 없으면 빈 채로(아래서 앞머리 문장)
        if pi not in byp:
            L = sorted(((sc, code & 0xffff) for code, sc in scores.items() if code >> 16 == pi), reverse=True)
            byp[pi] = L[:per_paper]
    ranked = []
    for pi, L in byp.items():
        p = idx["papers"][pi]
        pen, pko = idx["ptok"][pi]
        bonus = 0.0
        for t in terms:
            if t["common"]:
                continue
            if (t["kind"] == "en" and any(w in pen for w in t["toks"])) or (t["kind"] == "ko" and all(t["t"][i:i + 2] in pko for i in range(len(t["t"]) - 1))):
                bonus += 0.6 * t["idf"] * t["w"]
        if pi in men:
            bonus += 1000.0   # 가리킨 논문은 맨 앞에
        ranked.append((sum(s for s, _p in L[:3]) + bonus, pi, L))
    ranked.sort(reverse=True)
    out, total, used = [], 0, 0
    for psc, pi, L in ranked:
        if len(out) >= max_papers or total >= k or used >= chars:
            break
        p = idx["papers"][pi]
        if _key(p["file"]) != p["key"]:   # 이 논문의 번역이 바뀌었다 — 자리가 어긋나니 건너뛴다(다음 60초 안에 색인을 다시 만든다)
            continue
        try:
            sents = cfg["paper_sents"](p["file"])
        except Exception:
            continue
        picked = []
        for sc, pos in L[:per_paper]:
            if pos >= len(sents):
                continue
            s = sents[pos]
            hit = sorted({terms[i]["t"] for i in hits.get((pi << 16) | pos, [])})
            picked.append({"pos": pos, "sid": s.get("sid") or "", "en": s.get("en") or "", "ko": s.get("ko") or "", "page": s.get("page"), "sec": s.get("sec") or "", "score": round(sc, 2), "hit": hit})
        if not picked and pi in men:   # 가리킨 논문인데 맞는 문장이 없다 — 앞머리(초록) 문장 셋
            try:
                head = (cfg["readable"](sents) if cfg.get("readable") else sents)[:3]
            except Exception:
                head = sents[:3]
            for s in head:
                pos = sents.index(s)
                picked.append({"pos": pos, "sid": s.get("sid") or "", "en": s.get("en") or "", "ko": s.get("ko") or "", "page": s.get("page"), "sec": s.get("sec") or "", "score": 0, "hit": []})
        if not picked:
            continue
        picked.sort(key=lambda x: x["pos"])
        out.append({"file": p["file"], "stem": p["stem"], "short": ("%s %s" % (p["author"], p["year"])).strip(), "author": p["author"], "star": p["star"], "year": p["year"], "journal": p["journal"],
                    "title": p["title"], "line": p["line"], "tr": p["tr"], "score": round(psc - (1000.0 if pi in men else 0.0), 2), "mention": pi in men, "sents": picked})
        total += len(picked)
        used += sum(len(x["en"]) + 24 for x in picked)
    return {"terms": [{"t": t["t"], "kind": t["kind"], "df": t["df"], "src": t["src"], "common": t["common"], "toks": t["toks"][:6]} for t in terms],
            "papers": out, "n": total, "cand": len(cands), "nsent": N, "sec": round(time.time() - t0, 3), "ready": True}


def format_hits(res, label="L", line_chars=110):
    """search 의 결과 → (글, {"L1 s45": {file, sid, en, ko, page, sec, short, star, year, journal}}). 글은 논문마다 머리줄 + 문장 줄."""
    lines, refs = [], {}
    for i, p in enumerate(res.get("papers") or []):
        lab = "%s%d" % (label, i + 1)
        head = "%s %s%s%s %s — %s%s" % (lab, "★" if p.get("star") else "", p.get("year") or "", (" " + p["journal"]) if p.get("journal") else "", p.get("author") or p.get("short", "").split(" ")[0], (p.get("title") or "")[:90],
                                        " (물음이 가리킨 논문)" if p.get("mention") else "")
        if p.get("line"):
            head += " :: " + p["line"][:line_chars]
        lines.append(head)
        for s in p["sents"]:
            where = []
            if s.get("page") is not None:
                try:
                    where.append("p.%d" % (int(s["page"]) + 1))
                except (TypeError, ValueError):
                    pass
            if s.get("sec"):
                where.append(str(s["sec"])[:40])
            lines.append("  %s%s: %s" % (s["sid"], (" (" + " · ".join(where) + ")") if where else "", s["en"]))
            refs["%s %s" % (lab, s["sid"])] = {"file": p["file"], "sid": s["sid"], "en": s["en"], "ko": s.get("ko") or "", "page": s.get("page"), "sec": s.get("sec") or "",
                                                "short": p.get("short") or "", "star": bool(p.get("star")), "year": p.get("year") or "", "journal": p.get("journal") or "", "tr": bool(p.get("tr"))}
    return "\n".join(lines), refs
