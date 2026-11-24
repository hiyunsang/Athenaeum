# -*- coding: utf-8 -*-
"""논문의 참고문헌 목록 (2026-11-24, 사용자: '논문 읽다가 참고문헌 번호에 따라 바로 doi 열 수 있거나 서재에 있으면 서재에 있는 거 열 수 있게').

PDF 뒤쪽의 참고문헌 목록을 읽어 항목마다 글 · DOI · 링크 · 서재의 파일을 붙인다. 읽기 창과 휴대폰 화면이 인용 번호([12])나 (저자, 연도)를 누르면 뜨는 창의 재료다.
모델 호출은 없다. DOI 는 ① PDF 가 그 항목에 걸어 둔 링크 ② 항목의 글에 적힌 DOI ③ 둘 다 없으면 누른 때에 Crossref 에 그 글로 한 번 물어(제목 낱말·연도 대조) 찾는다.

  refs(name)          → {style: num|ay|"", refs: [{i, n, key, a, y, t, doi, url}]}   (PDF mtime 으로 기억)
  view(name)          → 위에 서재의 파일(file · ftitle)을 붙인 것 — 서재가 바뀌므로 부를 때마다 맞춘다
  lookup(name, i)     → {doi, title} | {none: True}   (Crossref, 디스크에 기억)
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

cfg = {}
_CACHE = {}
_LOCK = threading.Lock()
V = 1

_HEAD = re.compile(r"(?m)^[ \t]*(?:\d{1,2}\.?[ \t]+)?(References|REFERENCES|R E F E R E N C E S|Reference|REFERENCE|Bibliography|BIBLIOGRAPHY|Literature [Cc]ited|References and [Nn]otes|REFERENCES AND NOTES|References [Cc]ited)[ \t]*:?[ \t]*$")
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]]+)")
_SKIP_URL = re.compile(r"^mailto:|^javascript:|crossmark\.crossref|creativecommons\.org|orcid\.org|doi\.org/10\.13039/", re.I)   # 10.13039 = 연구비 기관
_STOP = set("the and for with from that this into onto over under their there which were been have has had its are was not but using during between through based effect effects study analysis investigation".split())


def init(**kw):
    cfg.update(kw)


def _clean(t):
    t = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", t)       # Springer 의 DOI 글자 사이에 든 보이지 않는 글자
    t = re.sub(r"\u00ad\s*", "", t)                          # 줄 끝의 soft hyphen
    t = re.sub(r"(?<=[a-z])-\s*\n\s*(?=[a-z])", "", t)   # 줄 끝 하이픈
    return re.sub(r"\s+", " ", t).strip()


def _doi_clean(d):
    d = urllib.parse.unquote(str(d or "")).strip()
    d = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), d)   # PDF 링크에 괄호가 \050 \051 로 적힌 것 (옛 Elsevier 의 S0924-0136(01)01262-6)
    d = d.replace("\ufb01", "fi").replace("\ufb02", "fl").replace("\ufb00", "ff").replace("\ufb03", "ffi").replace("\ufb04", "ffl")
    d = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", d, flags=re.I)
    d = d.rstrip(".,;]}>'\"")
    while d.endswith(")") and d.count(")") > d.count("("):   # 닫는 괄호는 짝이 안 맞을 때만 뗀다 — S0924-0136(01)01262-6 의 괄호는 DOI 의 일부
        d = d[:-1].rstrip(".,;")
    return d


def _find_head(texts):
    """참고문헌 머리말이 있는 쪽 · 그 쪽 글에서의 자리 · 머리말 글 → (쪽, 자리, 글). 없으면 (-1, 0, '')"""
    for i in range(len(texts) - 1, -1, -1):
        ms = list(_HEAD.finditer(texts[i]))
        if ms:
            return i, ms[-1].end(), ms[-1].group(1)
    return -1, 0, ""


def _pick_marks(ms):
    """표식 후보(글의 차례) → 번호마다 하나 [(번호, match)] (글의 차례로). 1..N 이 거의 다 있을 때의 N 까지만 받는다.
    2단 편집은 글이 번호 차례로 나오지 않으므로(1–16, 31–46, 17–30 …) 차례를 요구하지 않고, 같은 번호가 여럿이면 앞뒤 표식이 이웃 번호인 것을 고른다."""
    nums = [int(m.group(1)) for m in ms]
    have = set(nums)
    N, got = 0, 0
    for n in range(1, 1000):
        if n in have:
            got += 1
            if got >= 0.9 * n:
                N = n
        if n - got > max(8, 0.2 * n):
            break
    if N < 5:
        return []
    chosen = {}
    for idx, m in enumerate(ms):
        n = nums[idx]
        if n > N:
            continue
        near = (idx > 0 and nums[idx - 1] == n - 1) or (idx + 1 < len(ms) and nums[idx + 1] == n + 1)
        sc = 1 if near else 0
        if n not in chosen or sc > chosen[n][0]:
            chosen[n] = (sc, m)
    return sorted(((n, v[1]) for n, v in chosen.items()), key=lambda t: t[1].start())


_DOI_TXT = re.compile(r"10\.\d{4,9}/[^\s\"<>\]]+")


def _body_doi(raw, loose=False):
    """항목의 글(줄바꿈 그대로) → (DOI, 그 DOI 가 끝나는 자리). 글의 DOI 는 줄 끝에서 자주 끊긴다('10.1016/j.' 다음 줄에 나머지):
    '.'·'-'·'/' 로 끝난 조각 뒤에 조각 하나만 더 있고 항목이 끝나면 잇고, 그렇게 끝났는데 이을 수 없으면 버린다(틀린 DOI 를 여느니 찾아보게)."""
    m = _DOI_TXT.search(raw)
    if not m:
        return "", -1
    doi, end = m.group(0), m.end()
    rest = raw[end:]
    toks = rest.split()
    if doi[-1] in ".-/" and toks:
        tail_ok = len(toks) == 1 or (len(toks) == 2 and toks[1] in (".", ","))
        if tail_ok and re.search(r"\d", toks[0]) and re.match(r"^[\w.\-/()]+\.?$", toks[0]) and len(toks[0]) <= 40:
            k = rest.index(toks[0]) + len(toks[0])
            return _doi_clean(doi + toks[0]), end + k
        return "", -1
    if loose and len(toks) == 1 and re.match(r"^[ \t]*\n", rest) and re.match(r"^[\w.\-/()]+$", toks[0]) and len(toks[0]) <= 40:
        # Springer 는 DOI 를 아무 자리에서나 줄을 바꾼다(글자 사이에 보이지 않는 글자를 넣어서) — 다음 줄의 조각이 항목의 마지막이면 잇는다
        return _doi_clean(doi + toks[0]), end + rest.index(toks[0]) + len(toks[0])
    d = _doi_clean(doi)
    if len(d.split("/", 1)[-1]) < 5:
        return "", -1
    return d, end


def _numbered(tail, loose=False):
    """번호 목록 → ([(번호, 글, DOI)], 'br'|'dot'|'par') — 다섯 개 넘을 때만"""
    best, kind = [], ""
    for k, rx in (("br", r"(?m)^[ \t]*\[(\d{1,3})\][ \t]*"), ("dot", r"(?m)^[ \t]*(\d{1,3})\.[ \t\n]+(?=[A-Z\u00c0-\u017f])"), ("par", r"(?m)^[ \t]*\(?(\d{1,3})\)[ \t]+(?=[A-Z])")):
        ch = _pick_marks(list(re.finditer(rx, tail)))
        if len(ch) > len(best):
            best, kind = ch, k
    if len(best) < 5:
        return [], ""
    out = []
    for j, (n, m) in enumerate(best):
        end = best[j + 1][1].start() if j + 1 < len(best) else min(len(tail), m.end() + 520)
        raw = tail[m.end():end]
        doi, dend = _body_doi(raw, loose)
        if doi and dend > 60 and len(raw) - dend > 12:   # DOI 로 끝나는 항목 뒤에 붙은 쪽 머리글·꼬리글을 뗀다
            raw = raw[:dend]
        body = _clean(raw)
        if j + 1 == len(best) and not doi:   # 글의 마지막 항목 뒤에는 부록·저자 소개가 올 수 있다 — 문장 끝에서 끊는다
            cut = re.search(r"(?:(?:19|20)\d{2}[a-z]?\)?|\d)\.(?=\s+[A-Z][a-z]+\s+[a-z]+\s+[a-z]+\s)", body[60:])
            if cut:
                body = body[:60 + cut.end()]
        if len(body) > 900:
            body = body[:700]
        if len(body) > 12:
            out.append((n, body, doi))
    out.sort(key=lambda t: t[0])
    return out, kind


_AY_START = re.compile(r"(?:(?<=[.\d\)])|^)\s+(?=(?:(?:[Vv]an|[Vv]on|[Dd]e|[Dd]el|[Dd]er|[Ll]a|[Ll]e|[Dd]i|[Dd]a)\s+){0,2}[A-ZÀ-ſ][\w'’\-]+(?:\s+[A-ZÀ-ſ][\w'’\-]+)?,\s+(?:[A-ZÀ-ſ]\.[\s\-]?){1,5},?\s)")
_AY_START2 = re.compile(r"(?m)^(?=[A-ZÀ-ſ][\w'’\-]+(?:\s+[a-z]{2,3})?\s+[A-Z]{1,4}(?:,|\s+\((?:19|20)\d{2}))")   # Springer: 'Childs THC, Maekawa K (2013) …'


def _author_year(tail):
    """저자-연도 목록 → [(성, 연도, 글)] — 다섯 개 넘을 때만"""
    flat = " " + _clean(tail)
    best = []
    for parts in (_AY_START.split(flat), [_clean(x) for x in _AY_START2.split(tail)]):
        out = []
        for p in parts:
            p = p.strip()
            if not (25 < len(p) < 1100):
                continue
            ym = re.search(r"\b((?:19|20)\d{2})([a-z])?\b", p)
            sm = re.match(r"((?:(?:[Vv]an|[Vv]on|[Dd]e|[Dd]el|[Dd]er|[Ll]a|[Ll]e|[Dd]i|[Dd]a)\s+){0,2}[A-ZÀ-ſ][\w'’\-]+)", p)
            if ym and sm and ym.start() < 400:
                out.append((sm.group(1), ym.group(1) + (ym.group(2) or ""), p))
        if len(out) > len(best):
            best = out
    return best if len(best) >= 5 else []


def _links_by_number(doc, start, numbers, kind, head_rect=None):
    """번호 목록의 항목마다 PDF 가 걸어 둔 바깥 링크 → {번호: [주소]}. 표식 낱말의 자리(단의 왼쪽 끝)로 링크가 어느 항목의 것인지 가린다."""
    out = {}
    if not numbers:
        return out
    want = set(numbers)
    rx = re.compile(r"^\[(\d{1,3})\]$") if kind == "br" else re.compile(r"^\(?(\d{1,3})\)$") if kind == "par" else re.compile(r"^(\d{1,3})\.$")
    last = 0
    seen = set()
    for pi in range(start, len(doc)):
        page = doc[pi]
        try:
            links = [l for l in page.get_links() if l.get("uri") and not _SKIP_URL.search(l["uri"])]
            if pi == start and head_rect is not None:   # 머리말이 있는 쪽 — 머리말보다 앞(본문·사사)의 링크는 뺀다
                mid = page.rect.width / 2.0
                links = [l for l in links if not (l["from"].x1 < head_rect.x0 - 20 or ((l["from"].y0 + l["from"].y1) / 2.0 < head_rect.y0 and not (l["from"].x0 > mid and head_rect.x0 < mid - 20)))]
        except Exception:
            links = []
        if not links:
            continue
        cands = []
        for w in page.get_text("words"):
            m = rx.match(w[4])
            if m and int(m.group(1)) in want:
                cands.append((w[0], w[1], int(m.group(1))))
        if not cands:
            for l in links:   # 표식이 없는 쪽 = 앞 쪽 마지막 항목이 이어진 것
                if last:
                    out.setdefault(last, []).append(l["uri"])
            continue
        # 표식은 단의 왼쪽 끝에 줄지어 있다 — 가장 흔한 왼쪽 자리(둘까지) 근처의 것만
        buckets = {}
        for x, y, n in cands:
            buckets.setdefault(int(round(x / 12.0)), []).append((x, y, n))
        tops = sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:3]
        cols = sorted(sum(v[0] for v in vs) / len(vs) for _, vs in tops if len(vs) >= 2 or len(cands) <= 3)
        marks = [(x, y, n) for x, y, n in cands if any(abs(x - c) <= 14 for c in cols) and n not in seen]
        firsts = {}
        for x, y, n in marks:
            firsts.setdefault(n, (x, y, n))
        marks = sorted(firsts.values(), key=lambda t: t[2])
        if not marks:
            continue
        seen.update(n for _, _, n in marks)
        for l in links:
            r = l["from"]
            cy = (r.y0 + r.y1) / 2.0
            col = max([c for c in cols if c <= r.x0 + 16] or [min(cols)])
            mine = [m for m in marks if abs(m[0] - col) <= 14]
            above = [m for m in mine if m[1] <= cy - 0.5]   # 표식 낱말의 윗선이 링크 줄의 가운데보다 위 — 여유를 두면 바로 윗줄의 링크가 다음 항목 것이 된다
            if above:
                n = max(above, key=lambda t: t[1])[2]
            elif mine:
                n = min(m[2] for m in mine) - 1   # 그 단의 첫 표식 위 = 앞 단·앞 쪽에서 이어진 항목
            else:
                n = last
            if n >= 1 and n in want:
                out.setdefault(n, []).append(l["uri"])
        last = max(n for _, _, n in marks)
    return out


def _extract(name):
    import fitz
    d = fitz.open(os.path.join(cfg["ARCHIVE"], name))
    try:
        texts = [p.get_text() for p in d]
        zw = any(chr(0x200b) in t for t in texts[-12:])
        hp, pos, head_word = _find_head(texts)
        if hp >= 0:
            tail = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", texts[hp][pos:] + "\n" + "\n".join(texts[hp + 1:]))
            start = hp
        else:   # 머리말을 못 찾은 PDF — 뒤쪽 절반에서 번호 목록만 찾아 본다
            start = max(0, len(texts) // 2)
            tail = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", "\n".join(texts[start:]))
        items, kind = _numbered(tail, zw)
        out = []
        if items:
            links = {}
            try:
                head_rect = None
                if hp >= 0:
                    hits = d[hp].search_for(head_word) if head_word else []
                    head_rect = hits[-1] if hits else None
                links = _links_by_number(d, start, [x[0] for x in items], kind, head_rect)
            except Exception:
                links = {}
            for n, body, tdoi in items:
                doi, url = "", ""
                for u in links.get(n) or []:
                    if re.search(r"doi\.org/10\.", u, re.I):
                        doi = doi or _doi_clean(u)
                    elif not url and u.lower().startswith("http"):
                        url = u
                doi = doi or tdoi
                ym = re.search(r"\b((?:19|20)\d{2})\b", body)
                out.append({"n": n, "t": body, "doi": doi, "url": url, "y": ym.group(1) if ym else "", "tdoi": tdoi})
            style = "num"
        elif hp >= 0:
            ay = _author_year(tail)
            for a, y, body in ay:
                out.append({"n": None, "a": a, "y": y, "key": re.sub(r"[^a-zà-ſ]", "", a.lower()) + "|" + y, "t": body, "doi": _body_doi(body)[0], "url": ""})
            style = "ay" if out else ""
        else:
            style = ""
        for i, r in enumerate(out):
            r["i"] = i
        return {"v": V, "style": style, "refs": out}
    finally:
        d.close()


def refs(name):
    fp = os.path.join(cfg["ARCHIVE"], name)
    try:
        mt = os.path.getmtime(fp)
    except OSError:
        return {"style": "", "refs": []}
    c = _CACHE.get(name)
    if c and c[0] == mt:
        return c[1]
    try:
        data = _extract(name)
    except Exception as e:
        data = {"v": V, "style": "", "refs": [], "err": str(e)[:160]}
    with _LOCK:
        if len(_CACHE) > 60:
            _CACHE.clear()
        _CACHE[name] = (mt, data)
    return data


def _fold(t):
    """견주기 좋게: 소문자 · 발음 부호 뗌 (Kümmel = Kummel)"""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(t or "")) if not unicodedata.combining(c)).lower().strip()


def view(name):
    """참고문헌 목록 + 서재에 있는 논문의 파일. 서재에서 찾는 것은 제목 낱말이 거의 다 들어 있고 연도가 그 줄에 있을 때만(엉뚱한 논문을 열지 않게)."""
    data = refs(name)
    out = []
    ref_file, meta = cfg.get("ref_file"), cfg.get("paper_meta")
    found = _found()
    for r in data.get("refs") or []:
        x = dict(r)
        x.pop("tdoi", None)
        f = ""
        try:
            f = ref_file(r["t"]) if ref_file else ""
        except Exception:
            f = ""
        if f and f != name:
            try:
                m = meta(f) if meta else {}
            except Exception:
                m = {}
            yr = str(m.get("year") or "")
            au = _fold(str(m.get("author") or (f.split("_")[2] if f.count("_") >= 3 else "")))
            # 제목 낱말이 겹치는 다른 논문을 열지 않게: 연도와 첫 저자의 성이 그 줄에 있어야 한다
            if yr and yr in r["t"] and au and re.search(r"(?<![a-z])" + re.escape(au) + r"(?![a-z])", _fold(r["t"])):
                x["file"] = f
                x["ftitle"] = str(m.get("title") or "")[:160]
        if not x.get("doi"):
            hit = found.get(_hash(r["t"]))
            if hit and hit.get("doi"):
                x["doi"] = hit["doi"]
                x["found"] = True
            elif hit:
                x["nodoi"] = True   # 전에 찾아 봤고 없었다 — 화면이 다시 묻지 않는다
        out.append(x)
    return {"style": data.get("style") or "", "refs": out, "n": len(out)}


# ---------- DOI 찾기 (Crossref) ----------
def _found_path():
    return os.path.join(cfg["MAP_DIR"], "참고문헌DOI.json")


def _found():
    try:
        return cfg["load_json"](_found_path(), {}) or {}
    except Exception:
        return {}


def _hash(t):
    return hashlib.sha1(re.sub(r"\s+", " ", t or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def _words(t):
    return [w for w in re.findall(r"[a-z]{4,}", (t or "").lower()) if w not in _STOP]


def _title_ok(title, line):
    """Crossref 가 준 제목이 그 참고문헌 줄의 것인가 — 제목 낱말의 80% 가 줄에 있고, 낱말이 셋은 넘어야 한다"""
    ws = _words(title)
    if len(ws) < 3:
        return False
    low = set(re.findall(r"[a-z]{4,}", (line or "").lower()))
    return sum(1 for w in ws if w in low) / float(len(ws)) >= 0.8


def lookup(name, i):
    """i 번째 항목의 DOI 를 Crossref 에서 찾는다(그 줄의 글로 한 번 묻고 제목·연도를 대조). 결과는 기억한다(없음도 30일)."""
    data = refs(name)
    rs = data.get("refs") or []
    try:
        r = rs[int(i)]
    except (ValueError, IndexError, TypeError):
        return {"error": "그 항목이 없습니다"}
    if r.get("doi"):
        return {"doi": r["doi"]}
    h = _hash(r["t"])
    found = _found()
    hit = found.get(h)
    if hit and (hit.get("doi") or time.time() - (hit.get("t") or 0) < 30 * 86400):
        return {"doi": hit["doi"], "title": hit.get("title", "")} if hit.get("doi") else {"none": True}
    res = {"t": int(time.time())}
    err = ""
    try:
        q = urllib.parse.urlencode({"query.bibliographic": r["t"][:400], "rows": 3, "select": "DOI,title,issued,published-print,published-online"})
        req = urllib.request.Request("https://api.crossref.org/works?" + q, headers={"User-Agent": "athenaeum/1.0 (reference lookup)"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace"))
        for it in ((d.get("message") or {}).get("items") or []):
            title = re.sub(r"<[^>]+>", "", ((it.get("title") or [""])[0] or ""))
            year = ""
            for k in ("published-print", "published-online", "issued"):
                parts = (it.get(k) or {}).get("date-parts") or [[None]]
                if parts and parts[0] and parts[0][0]:
                    year = str(parts[0][0])
                    break
            near = [str(int(year) + dy) for dy in (-1, 0, 1)] if year.isdigit() else []
            if it.get("DOI") and _title_ok(title, r["t"]) and (not near or any(y in r["t"] for y in near)):
                res.update(doi=it["DOI"], title=title[:200])
                break
    except Exception as e:
        err = str(e)[:120]
    if err:
        return {"error": "Crossref 에 닿지 못했습니다 — " + err}
    with _LOCK:
        found = _found()
        found[h] = res
        if len(found) > 6000:
            for k in sorted(found, key=lambda k: found[k].get("t") or 0)[:1000]:
                found.pop(k, None)
        cfg["save_json"](_found_path(), found)
    return {"doi": res["doi"], "title": res.get("title", "")} if res.get("doi") else {"none": True}


def handle_get(h, url):
    q = urllib.parse.parse_qs(url.query)
    name = (q.get("file") or [""])[0]
    if not name or os.path.basename(name) != name or not os.path.isfile(os.path.join(cfg["ARCHIVE"], name)):
        return h._send(404, {"error": "논문을 찾지 못했습니다"})
    if url.path == "/api/refs/doi":
        return h._send(200, lookup(name, (q.get("i") or [""])[0]))
    return h._send(200, view(name))
