# -*- coding: utf-8 -*-
"""figstyle — 그림체 도감.

「그림 만들기」 창이 '이런 그림체 / 저런 그림체' 를 실제 논문의 그림으로 보여 주려고, 우선 저널(★)과 본보기 저자의 논문에 든 도식을
가벼운 모델(Sonnet)이 한 번씩 보고 그림체를 적어 둔다 (사용자 2026-11-19: 'MTM 연구나 Dragos 씨 논문 같은 데서 사진 가져와서 예시로 들고,
이런 스타일, 저런 스타일 물어보고').

  원고\\_그림체도감.json = {v, authors[본보기 저자], author_hits{이름: [파일]}, items{"<stem>|<img>": {…그림체…}}, runs[…]}
  항목 = {file, n, img, short, abbr, year, top, by(본보기 저자), family, form, palette, line, fill, font, label, score 0~3, note, t}

그림 목록은 원고 모듈의 _fig_library()(그림 색인에 든 그림)를 받아 쓴다 — 이 모듈은 그림을 자르지 않는다.
읽는 값: 그림 여덟 장에 Sonnet 호출 하나(30~60초). 저절로 돌지 않는다 — 창의 「도감 만들기」 를 눌렀을 때만.
"""
import io, json, os, re, shutil, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

cfg = {}
V = 1
BATCH = 8
FAMILIES = [
    ("line", "흑백 선화", "검은 선으로만 그리고 색을 채우지 않는다. 재료는 빗금이나 점으로 가른다."),
    ("gray_accent", "회색조 + 강조색", "회색으로 그리고 봐야 할 곳에만 색 하나둘을 쓴다."),
    ("flat_color", "플랫 컬러", "구역·부품마다 또렷한 단색을 채우고 외곽선을 굵게 두른다."),
    ("pastel", "파스텔", "옅은 색을 채우고 선은 가늘게. 차분한 느낌."),
    ("shaded3d", "입체 · 그라데이션", "음영과 그라데이션으로 입체감을 낸다. 여기서는 색과 음영만 따라 그린다(평면)."),
    ("colormap", "컬러맵 · 장", "온도나 응력 같은 분포를 색으로 칠한 것이 주인공."),
    ("block", "블록도", "상자와 화살표로 장치·절차를 잇는다."),
]
FAMILY_KEYS = [f[0] for f in FAMILIES]
FORM_KEYS = ["cutting", "grinding", "setup", "flow", "steps", "compare", "graph", "micro", "mechanics", "general"]
_JOB = {"running": False, "done": 0, "total": 0, "t0": 0, "err": "", "tok": 0, "stop": False, "last": None}
_LOCK = threading.Lock()

_PROMPT = """아래 그림 파일 %d개는 기계가공·재료 분야 논문에 실린 도식이다. 폴더 "%s" 에 있다. Read 도구로 하나씩 열어 보고, 그림마다 '그림체'를 적어라.
내용이 아니라 그린 방식을 본다 — 다른 연구자가 "이런 그림체로 그려 달라" 고 고를 때 쓸 분류다.

%s

그림마다 적을 것:
- family: 그림체의 갈래 하나
    line = 검은 선만, 채우지 않음(빗금·점으로 구분) / gray_accent = 회색조에 강조색 하나둘 / flat_color = 구역마다 또렷한 단색 + 굵은 외곽선 /
    pastel = 옅은 색 채움 + 가는 선 / shaded3d = 음영·그라데이션으로 입체감(3D 렌더풍) / colormap = 온도·응력 분포를 색으로 칠한 것이 주인공 /
    block = 상자와 화살표의 블록도·흐름도 / other = 사진·현미경 사진·측정 그래프가 대부분이라 그림체의 본보기가 못 되는 것
- form: 무엇을 그린 그림인가 — cutting(공구·칩·공작물) | grinding(연삭·밀링 운동학) | setup(실험 장치 구성) | flow(흐름도·개념도) | steps(과정의 단계를 나란히) |
    compare(조건을 나란히 비교) | graph(축과 곡선의 개념 그래프) | micro(미세조직·결정) | mechanics(힘·응력 벡터) | general
- palette: 쓴 색을 한국어 몇 낱말로 (예: "검정만", "회색 + 빨강", "파랑·주황·초록", "연보라·회색")
- line: thin | medium | bold        - fill: none | hatch | flat | gradient | colormap
- font: serif | sans                 - label: leader(지시선) | box(상자) | direct(도형 위에 직접) | legend(범례)
- score: 그림체의 본보기로 삼을 만한가 — 3 = 깔끔하고 정돈된 도식(선·라벨·여백이 고르다) / 2 = 무난 / 1 = 조잡하거나 흐린 옛 스캔 / 0 = 도식이 아니다
- note: 이 그림체의 특징 한 줄 (한국어, 40자 안)

JSON 하나로만 답한다(다른 말 없이): {"figs": [{"k": "k01", "family": "...", "form": "...", "palette": "...", "line": "...", "fill": "...", "font": "...", "label": "...", "score": 2, "note": "..."}]}"""


def init(**kw):
    cfg.update(kw)


def path():
    return os.path.join(cfg["MS_DIR"], "_그림체도감.json")


def load():
    d = cfg["load_json"](path(), None)
    if not isinstance(d, dict) or d.get("v") != V:
        d = {"v": V, "authors": [], "author_hits": {}, "items": {}, "runs": []}
    d.setdefault("items", {})
    d.setdefault("authors", [])
    d.setdefault("author_hits", {})
    d.setdefault("runs", [])
    return d


def key_of(x):
    return "%s|%s" % (os.path.splitext(os.path.basename(x["file"]))[0], x["img"])


def _json_of(text):
    text = text or ""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for cand in ([m.group(1)] if m else []) + [text[text.find("{"):text.rfind("}") + 1]]:
        try:
            d = json.loads(cand)
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
    return None


def author_files(names, known=None):
    """본보기 저자의 이름이 PDF 첫 쪽(저자 줄)에 있는 논문 — {이름: [파일]}. 서재 편수가 같으면 known 을 그대로 쓴다."""
    names = [n.strip() for n in names if n and n.strip()][:6]
    if not names:
        return {}
    try:
        files = sorted(f for f in os.listdir(cfg["ARCHIVE"]) if f.lower().endswith(".pdf"))
    except OSError:
        return {}
    known = known or {}
    if known.get("_n") == len(files) and all(n in known for n in names):
        return {n: known[n] for n in names}
    try:
        import fitz
    except Exception:
        return {n: [f for f in files if n.lower() in f.lower()] for n in names}
    out = {n: [] for n in names}
    for f in files:
        low = f.lower()
        hit = [n for n in names if n.lower() in low]
        if len(hit) < len(names):
            try:
                with fitz.open(os.path.join(cfg["ARCHIVE"], f)) as d:
                    head = (d[0].get_text() if len(d) else "")[:2500].lower()
            except Exception:
                head = ""
            hit += [n for n in names if n not in hit and re.search(r"\b%s\b" % re.escape(n.lower()), head)]
        for n in hit:
            out[n].append(f)
    out["_n"] = len(files)
    return out


def candidates(lib_items, authors=(), per_paper=3, cat=None):
    """도감에 넣을 그림: 우선 저널(★)이거나 본보기 저자의 논문에 든 도식·장치 그림, 논문마다 per_paper 장까지(도식 먼저)."""
    cat = cat if cat is not None else load()
    hits = author_files(authors, cat.get("author_hits"))
    by = {}
    for n, fs in hits.items():
        if n != "_n":
            for f in fs:
                by.setdefault(f, n)
    per = {}
    for x in lib_items:
        if x.get("type") not in ("schematic", "setup"):
            continue
        if not (x.get("top") or x["file"] in by):
            continue
        per.setdefault(x["file"], []).append(x)
    out = []
    for f, xs in per.items():
        xs.sort(key=lambda x: (x["type"] != "schematic", x.get("n") or 0))
        lim = per_paper + 2 if f in by else per_paper       # 본보기 저자의 논문은 조금 더
        for x in xs[:lim]:
            y = dict(x)
            y["by"] = by.get(f, "")
            out.append(y)
    return out, hits


def _batch(items, model):
    """그림 여덟 장을 임시 폴더에 k01.png … 로 복사해 한 번에 읽힌다 → {열쇠: 그림체}"""
    d = tempfile.mkdtemp(prefix="ath_figstyle_")
    try:
        lines, paths, keys = [], [], {}
        for i, x in enumerate(items, 1):
            k = "k%02d" % i
            src = os.path.join(cfg["GEN_DIR"], "그림", os.path.splitext(os.path.basename(x["file"]))[0], x["img"])
            if not os.path.isfile(src):
                continue
            dst = os.path.join(d, k + ".png")
            shutil.copyfile(src, dst)
            paths.append(dst)
            keys[k] = x
            lines.append("- %s.png — %s" % (k, (x.get("what") or "")[:110]))
        if not keys:
            return {}, 0
        run = cfg["claude_run"]
        kw = dict(timeout=600, model=model, tools="Read", add_dirs=[d])
        prompt = _PROMPT % (len(keys), d, "\n".join(lines))
        try:
            res = run(prompt, images=paths, **kw)
        except TypeError:
            res = run(prompt, **kw)
        j = _json_of((res or {}).get("text")) or {}
        tok = ((res or {}).get("tok") or {}).get("in") or 0
        out = {}
        for y in j.get("figs") or []:
            if not isinstance(y, dict):
                continue
            k = re.sub(r"\.png$", "", str(y.get("k") or "").strip().lower())
            x = keys.get(k)
            if not x:
                continue
            fam = str(y.get("family") or "").strip().lower()
            form = str(y.get("form") or "").strip().lower()
            try:
                score = max(0, min(3, int(y.get("score"))))
            except (TypeError, ValueError):
                score = 1
            out[key_of(x)] = {"file": x["file"], "n": x.get("n"), "img": x["img"], "short": x.get("short") or "", "abbr": x.get("abbr") or "", "year": x.get("year") or "",
                              "top": bool(x.get("top")), "by": x.get("by") or "", "what": (x.get("what") or "")[:160],
                              "family": fam if fam in FAMILY_KEYS else "other", "form": form if form in FORM_KEYS else "general",
                              "palette": str(y.get("palette") or "").strip()[:40], "line": str(y.get("line") or "").strip().lower()[:8],
                              "fill": str(y.get("fill") or "").strip().lower()[:10], "font": str(y.get("font") or "").strip().lower()[:6],
                              "label": str(y.get("label") or "").strip().lower()[:8], "score": score, "note": re.sub(r"\s+", " ", str(y.get("note") or "")).strip()[:60], "t": time.time()}
        return out, tok
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _run(todo, authors, hits, model, workers):
    t0 = time.time()
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

    def one(b):
        if _JOB["stop"]:
            return
        try:
            got, tok = _batch(b, model)
        except Exception as e:
            got, tok = {}, 0
            _JOB["err"] = str(e)[:160]
        with _LOCK:
            cat = load()
            cat["items"].update(got)
            cat["authors"] = list(authors)
            cat["author_hits"] = hits
            cfg["save_json"](path(), cat)
            _JOB["done"] += len(b)
            _JOB["tok"] += tok
            if not got and not _JOB["err"]:
                _JOB["err"] = "답을 읽지 못한 묶음이 있습니다"
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(one, batches))
    finally:
        with _LOCK:
            cat = load()
            cat["runs"] = (cat.get("runs") or [])[-9:] + [{"t": time.time(), "n": _JOB["done"], "sec": round(time.time() - t0), "tok": _JOB["tok"], "model": model}]
            cfg["save_json"](path(), cat)
        _JOB.update(running=False, last=time.time())


def build_start(lib_items, authors=None, per_paper=3, limit=400, model="sonnet", workers=4):
    """아직 읽지 않은 후보를 읽기 시작한다(배경 스레드). → 상태"""
    with _LOCK:
        if _JOB["running"]:
            return status()
        cat = load()
        authors = [a.strip() for a in (authors if authors is not None else cat.get("authors") or []) if a and a.strip()][:6]
        cand, hits = candidates(lib_items, authors, per_paper, cat)
        todo = [x for x in cand if key_of(x) not in cat["items"]][:max(BATCH, int(limit))]
        cat["authors"], cat["author_hits"] = authors, hits
        cfg["save_json"](path(), cat)
        if not todo:
            return dict(status(), nothing=True)
        _JOB.update(running=True, done=0, total=len(todo), t0=time.time(), err="", tok=0, stop=False)
    threading.Thread(target=_run, args=(todo, authors, hits, model, workers), daemon=True).start()
    return status()


def stop():
    _JOB["stop"] = True
    return status()


def status():
    return {k: _JOB[k] for k in ("running", "done", "total", "t0", "err", "tok", "last")}


def view(lib_items, per_paper=3):
    """창이 그릴 도감: 갈래마다 본보기 그림(점수 → 본보기 저자 → 우선 저널 → 최근 순)"""
    cat = load()
    have = set(key_of(x) for x in lib_items)
    items = [v for k, v in cat["items"].items() if k in have and v.get("score", 0) >= 2 and v.get("family") in FAMILY_KEYS]
    items.sort(key=lambda v: (-v.get("score", 0), not v.get("by"), not v.get("top"), -(int(v["year"]) if str(v.get("year")).isdigit() else 0), v["file"], v.get("n") or 0))
    fams = []
    for key, name, desc in FAMILIES:
        xs = [v for v in items if v["family"] == key]
        fams.append({"key": key, "name": name, "desc": desc, "n": len(xs), "items": xs[:60]})
    cand, _ = candidates(lib_items, cat.get("authors") or [], per_paper, cat)
    left = sum(1 for x in cand if key_of(x) not in cat["items"])
    journals = {}
    for v in items:
        journals[v.get("abbr") or ""] = journals.get(v.get("abbr") or "", 0) + 1
    return {"families": fams, "read": len(cat["items"]), "shown": len(items), "left": left, "candidates": len(cand), "authors": cat.get("authors") or [],
            "author_papers": {n: len(fs) for n, fs in (cat.get("author_hits") or {}).items() if n != "_n"},
            "journals": sorted(journals.items(), key=lambda kv: -kv[1])[:8], "status": status(), "runs": (cat.get("runs") or [])[-3:]}


def note_for(keys):
    """고른 본보기 그림의 그림체를 글로 — 그리기 지시문에 붙인다(그림을 읽는 것에 더해 흔들리지 않게)"""
    cat = load()
    out = []
    for k in keys:
        v = cat["items"].get(k)
        if not v:
            continue
        fam = next((f for f in FAMILIES if f[0] == v.get("family")), None)
        out.append("- %s Fig. %s: %s%s · 색 %s · 선 %s · 채움 %s · 글꼴 %s · 라벨 %s%s" % (
            v.get("short"), v.get("n"), (fam[1] + " — ") if fam else "", v.get("note") or "", v.get("palette") or "?", v.get("line") or "?",
            v.get("fill") or "?", v.get("font") or "?", v.get("label") or "?", ""))
    return "\n".join(out)
