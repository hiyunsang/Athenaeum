# -*- coding: utf-8 -*-
"""수식 색인 — 번역할 때 PDF 에서 잘라 둔 수식 그림(번역\\수식\\<stem>\\pN_K.png)을 Claude(Sonnet — 그림을 본다)에게 읽혀 LaTeX·원문 식 번호·뜻·기호를 받아 둔다.
공부가 모델·관계식을 설명하는 자리에 식을 걸고 풀어 쓰는 재료 (사용자 2026-10-29: '수식에 대해 이야기하는데 수식이 없음 — 수식을 쉽게 가져오는 방법을 논의해 볼 필요').
원칙: 그림이 진본이고 LaTeX 는 Claude 의 전사다 — 화면은 '전사' 표시와 「원문 수식」(그림)을 늘 같이 둔다. 전사가 수식 엔진(mathtex)에서 그려지지 않으면 그림으로 떨어진다.
논문마다 번역\\수식색인\\<stem>.json = {file, t, mtime, model, ok, n, eqs: [{img, sid, psid, page, ctx, n, latex, what, vars[{s, m}], partial, key, mml_ok}], v, sec, tok, calls}
"""
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

cfg = {}
_LOCK = threading.Lock()
INDEX_V = 1
MAX_EQS = 60          # 논문 하나에서 읽는 수식 수 — 문서 차례대로 앞에서부터(뒤쪽은 대개 부록·보조 산식). Jin 2011 은 89개
CHUNK = 20            # 한 호출에 읽히는 그림 수 (그림마다 Read 한 번)
_RETRY_AFTER = 600    # 실패한 색인은 10분 뒤에 다시
_EQ_MARK = re.compile(r"\[\[EQ:([^\]]+?)\]\]")
_SID = re.compile(r"\[s(\d+)\]")
_TAG = re.compile(r"<[^>]+>")


def init(**kw):
    cfg.update(kw)
    cfg["DIR"] = os.path.join(cfg["GEN_DIR"], "수식색인")
    cfg["IMG"] = os.path.join(cfg["GEN_DIR"], "수식")
    os.makedirs(cfg["DIR"], exist_ok=True)


def _stem(f):
    return os.path.splitext(os.path.basename(f))[0]


def index_path(f):
    return os.path.join(cfg["DIR"], _stem(f) + ".json")


def img_dir(f):
    return os.path.join(cfg["IMG"], _stem(f))


def _mtime(f):
    try:
        return os.path.getmtime(os.path.join(cfg["ARCHIVE"], f))
    except OSError:
        return 0


def load(f):
    """그 논문의 수식 색인 — 있고 PDF 가 그 뒤 바뀌지 않았으면. 없으면 None"""
    d = cfg["load_json"](index_path(f), None)
    if not isinstance(d, dict) or not isinstance(d.get("eqs"), list):
        return None
    if d.get("mtime") and abs(_mtime(f) - d["mtime"]) > 2:
        return None
    return d


def _clean(t):
    return re.sub(r"\s+", " ", _EQ_MARK.sub(" ", _TAG.sub("", t or ""))).strip()


def _img_page(img):
    """그림 이름 pN_K.png 의 N = 쪽(1부터, server 가 pi + 1 로 붙인다) — 정렬표에 쪽이 없는 문장의 식(584개 중 377개, 2026-10-29)에 쓴다"""
    m = re.match(r"p([0-9]+)_", img or "")
    return int(m.group(1)) if m else None


def crops_of(f):
    """번역문의 [[EQ:…]] 자리 → [{img, sid, psid, page, ctx(식이 든 문장의 번역), prev(앞 문장), next(다음 문장), raw(정렬표의 원문 글자 — 깨져 있다)}] (그림 파일이 있는 것만, 문서 차례, MAX_EQS 까지)"""
    stem = _stem(f)
    md_p = os.path.join(cfg["GEN_DIR"], stem + ".번역.md")
    if not os.path.isfile(md_p):
        return []
    try:
        md = io.open(md_p, encoding="utf-8").read()
    except OSError:
        return []
    al = cfg["load_json"](os.path.join(cfg["GEN_DIR"], stem + ".번역.정렬.json"), None) or {}
    segs = []                                   # (sid, 글) — [sN] 표식 단위
    for line in md.split("\n"):
        parts = _SID.split(line)
        if len(parts) == 1:
            if line.strip() and segs:
                segs[-1] = (segs[-1][0], segs[-1][1] + " " + line.strip())
            continue
        for i in range(1, len(parts), 2):
            segs.append(("s" + parts[i], parts[i + 1].strip()))
    out = []
    for i, (sid, text) in enumerate(segs):
        imgs = _EQ_MARK.findall(text)
        if not imgs:
            continue
        a = al.get(sid[1:]) if isinstance(al, dict) else None
        a = a if isinstance(a, dict) else {}
        for img in imgs:
            if not os.path.isfile(os.path.join(img_dir(f), img)):
                continue
            out.append({"img": img, "sid": sid, "psid": segs[i - 1][0] if i > 0 else "", "page": (a.get("p") + 1) if isinstance(a.get("p"), int) else _img_page(img),
                        "ctx": _clean(text)[:300], "prev": _clean(segs[i - 1][1])[:300] if i > 0 else "", "next": _clean(segs[i + 1][1])[:200] if i + 1 < len(segs) else "",
                        "raw": re.sub(r"\s+", " ", str(a.get("t") or ""))[:300]})
            if len(out) >= MAX_EQS:
                return out
    return out


_PROMPT = (
    "아래는 논문 한 편의 수식 그림들이다(번역할 때 PDF 에서 잘라 낸 PNG, 폴더 \"%s\"). 그림마다 Read 도구로 파일을 열어 보고(하나씩, 모두), 교과서를 쓸 사람이 그 식을 걸고 풀어 쓸 수 있게 적어라.\n"
    "JSON 으로만 답하라: {\"eqs\": [{\"img\": \"p3_1.png\", \"n\": \"그림에 보이는 원문의 식 번호 — (3) 이면 \\\"3\\\", 그림에 번호가 둘 이상이면 모두 \\\"12,13\\\", 없으면 \\\"\\\"\", "
    "\"latex\": \"식을 LaTeX 로 전사 — 그림에 보이는 그대로(기호·첨자·분수·근호·적분·합·행렬). 여러 줄이면 \\\\\\\\ 로 줄을 나눈다. 식 번호는 넣지 않는다. 확실하지 않은 글자는 보이는 대로 두고 지어내지 않는다\", "
    "\"what\": \"이 식이 무엇을 무엇으로 구하는(또는 정의하는) 식인지 한국어 한 문장\", \"vars\": [{\"s\": \"φ\", \"m\": \"전단각\"}], \"partial\": false, \"key\": false}]}\n"
    "- vars: 식의 기호 가운데 앞·소개·다음 문장이 뜻을 말해 주는 것만(8개까지). 문장이 말하지 않는 뜻은 적지 않는다.\n"
    "- partial: 그림이 식의 일부만 담았거나(위·아래 줄이 잘림, 좌변만, 이어지는 줄이 다른 그림에) 흐리면 true. 그림에 식이 없으면(표·그림 조각·글만) latex 를 빈 글로 두고 partial true.\n"
    "- key: 그 논문의 모델·법칙을 이루는 핵심 관계식(전단각 식, 힘 모델, 지배 방정식, 판정 기준)이면 true. 기호의 정의, 중간 산식, 단순 치환, 경계 조건, 무차원화는 false.\n"
    "- 'PDF 글자' 는 같은 자리에서 추출된 글자(깨져 있다 — 기호 이름·첨자를 맞추는 참고로만, 그 글자를 믿고 그림에 없는 것을 적지 않는다).\n"
    "- 논문: %s\n\n[수식 목록 — 파일 이름 · 앞 문장 · 소개 문장(식이 든 문장) · 다음 문장 · PDF 글자]\n%s")


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


def _mml_ok(latex):
    """전사한 LaTeX 가 원고의 수식 엔진(mathtex)에서 그려지는가 — 안 그려지면 화면이 그림으로 떨어진다"""
    if not latex:
        return False
    try:
        import mathtex
        return bool(mathtex.to_mathml(latex[:4000], True))
    except Exception:
        return False


def build(f, title="", model="sonnet", force=False):
    """논문 하나의 수식 색인을 만든다(있으면 그대로). 그림 20장마다 Claude 호출 하나 — 그림마다 Read 로 열어 본다."""
    if not force:
        d = load(f)
        if d and d.get("v") == INDEX_V and (d.get("ok") or time.time() - (d.get("t") or 0) < _RETRY_AFTER):
            return d
    t0 = time.time()
    crops = crops_of(f)
    base = {"file": f, "t": time.time(), "mtime": _mtime(f), "model": model, "n": len(crops), "eqs": [], "ok": False, "v": INDEX_V}
    if not crops:
        base["ok"] = True
        cfg["save_json"](index_path(f), base)
        return base
    run = cfg.get("claude_run")
    by, tok_in, tok_c, calls, got = {}, 0, 0, 0, False
    for i in range(0, len(crops), CHUNK):
        part = crops[i:i + CHUNK]
        listing = "\n".join("%s (p.%s)\n    앞: \"%s\"\n    소개: \"%s\"\n    다음: \"%s\"\n    PDF 글자: \"%s\"" % (x["img"], x.get("page") or "?", x["prev"], x["ctx"], x["next"], x["raw"]) for x in part)
        res = None
        try:
            res = run(_PROMPT % (img_dir(f), title or _stem(f)[:120], listing), timeout=900, model=model, tools="Read", add_dirs=[img_dir(f)]) if run else None
        except Exception:
            res = None
        calls += 1
        tok = (res or {}).get("tok") or {}
        tok_in += int(tok.get("in") or 0)
        tok_c += int(tok.get("cached") or 0)
        d = _json_of(res.get("text")) if res else None
        for x in (d or {}).get("eqs") or []:
            if isinstance(x, dict) and str(x.get("img") or "").strip():
                by[str(x["img"]).strip()] = x
                got = True
    eqs = []
    for x in crops:
        y = by.get(x["img"]) or {}
        latex = re.sub(r"\s+", " ", str(y.get("latex") or "")).strip()[:1500]
        vars_ = [{"s": str(v.get("s") or "").strip()[:24], "m": str(v.get("m") or "").strip()[:60]} for v in (y.get("vars") or [])[:8] if isinstance(v, dict) and str(v.get("s") or "").strip()] if isinstance(y.get("vars"), list) else []
        eqs.append({"img": x["img"], "sid": x["sid"], "psid": x.get("psid") or "", "page": x.get("page"), "ctx": x["ctx"][:240],
                    "n": re.sub(r"[^\w.,\-]", "", str(y.get("n") or ""))[:12], "latex": latex, "what": re.sub(r"\s+", " ", str(y.get("what") or "")).strip()[:300],
                    "vars": vars_, "partial": bool(y.get("partial")) or not latex, "key": bool(y.get("key")) and bool(latex), "mml_ok": _mml_ok(latex)})
    base.update(eqs=eqs, ok=got, sec=round(time.time() - t0), tok={"in": tok_in, "cached": tok_c} if tok_in else None, calls=calls)
    cfg["save_json"](index_path(f), base)
    return base


def ensure(items, model="sonnet", workers=4, on_done=None):
    """여러 논문의 색인을 한꺼번에(없는 것만). items = [(파일, 제목)] → {파일: 색인}"""
    out, todo = {}, []
    for f, title in items:
        d = load(f)
        if d and d.get("v") == INDEX_V and (d.get("ok") or time.time() - (d.get("t") or 0) < _RETRY_AFTER):
            out[f] = d
        else:
            todo.append((f, title))
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(build, f, title, model): f for f, title in todo}
            for fu in as_completed(futs):
                f = futs[fu]
                try:
                    out[f] = fu.result()
                except Exception:
                    out[f] = None
                if on_done:
                    try:
                        on_done(f, out[f])
                    except Exception:
                        pass
    return out


def summary(d):
    """(전사된 식 수, 핵심 관계식 수, 엔진이 그리는 식 수)"""
    eqs = (d or {}).get("eqs") or []
    return sum(1 for e in eqs if e.get("latex")), sum(1 for e in eqs if e.get("key")), sum(1 for e in eqs if e.get("mml_ok"))


def eq(f, k):
    """그 논문의 k 번째(1부터) 식"""
    d = load(f)
    eqs = (d or {}).get("eqs") or []
    return eqs[k - 1] if 1 <= k <= len(eqs) else None
