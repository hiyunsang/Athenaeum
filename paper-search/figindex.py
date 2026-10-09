# -*- coding: utf-8 -*-
"""그림 색인 — 논문의 그림을 잘라 Claude(Sonnet — 그림을 본다)에게 종류·한 줄 설명을 받아 둔다. 공부가 그림을 고르고 풀어 쓸 때 쓴다.
사용자(2026-10-08): '공부에서 사진 기능이 정말 중요 — 기존 연구들이 그려 놓은 스케메틱을 잘 써야 이해가 된다. 어떻게 하면 그림을 적재적소에 놓고 글도 잘 쓸까' → ① 그림 색인 ② 도식은 근거 문장이 가리키지 않아도 후보로 ③ 교과서식 배치.
논문마다 번역\\그림색인\\<stem>.json = {file, t, mtime, model, ok, n, figs: [{n, page, cap, img, type, what, shows, labels, quality}]}, 그림 PNG 는 번역\\그림\\<stem>\\fig<n>.png
type: schematic(도식·개념도·모델·배치 도면) · photo(SEM·광학·TEM 사진) · plot(그래프) · setup(장치) · mixed · other. 쓰는 Claude 는 그림을 보지 못하므로 이 설명으로 고른다.
"""
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import figcrop

cfg = {}
_LOCK = threading.Lock()
TYPES = {"schematic": "도식", "photo": "사진", "plot": "그래프", "setup": "장치", "mixed": "복합", "other": "기타"}
MAX_FIGS = 14            # 논문 하나에서 읽는 그림 수 (뒤쪽 그림은 대개 보조 자료)
MAX_FIGS_REVIEW = 20     # 리뷰는 뒤쪽 그림도 본문이다 — Beyerlein 2014 의 HCP 쌍정 모드(Fig. 16)·쌍정 배아(Fig. 17)가 14장 상한 밖이었다(2026-10-09)
_REVIEW_RX = re.compile(r"\b(review|survey|overview|advances in|state[- ]of[- ]the[- ]art|perspectives?|progress in)\b", re.I)
_RETRY_AFTER = 600       # 실패한 색인은 10분 뒤에 다시


def init(**kw):
    cfg.update(kw)
    cfg["DIR"] = os.path.join(cfg["GEN_DIR"], "그림색인")
    cfg["IMG"] = os.path.join(cfg["GEN_DIR"], "그림")
    for d in (cfg["DIR"], cfg["IMG"]):
        os.makedirs(d, exist_ok=True)


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
    """그 논문의 색인 — 있고 PDF 가 그 뒤 바뀌지 않았으면. 없으면 None"""
    d = cfg["load_json"](index_path(f), None)
    if not isinstance(d, dict) or not isinstance(d.get("figs"), list):
        return None
    if d.get("mtime") and abs(_mtime(f) - d["mtime"]) > 2:
        return None
    return d


def fig(f, n):
    """논문 f 의 Fig. n 의 색인 항목(없으면 None)"""
    d = load(f)
    return next((x for x in (d or {}).get("figs") or [] if x.get("n") == n), None) if d else None


def crop_all(f):
    """논문의 그림(표는 뺌)을 모두 잘라 PNG 로 → [{n, page, cap, img}] (이미 잘라 둔 것은 그대로). 못 자른 그림은 빠진다"""
    pdf = os.path.join(cfg["ARCHIVE"], f)
    try:                       # 리뷰이거나 20쪽이 넘는 긴 논문은 뒤쪽 그림도 본문 — 파일 이름만으로는 리뷰를 못 알아본다(Beyerlein 2014 는 제목에 review 가 없다, 저널 ARMR)
        npages = len(figcrop._fitz().open(pdf))
    except Exception:
        npages = 0
    limit = MAX_FIGS_REVIEW if (_REVIEW_RX.search(f) or npages >= 20) else MAX_FIGS
    caps = [c for c in figcrop.captions(pdf) if c["kind"] == "fig"][:limit]
    d = img_dir(f)
    os.makedirs(d, exist_ok=True)
    out = []
    for c in caps:
        name = "fig%d.png" % c["n"]
        png = os.path.join(d, name)
        if not os.path.isfile(png):
            try:
                r = figcrop.crop(pdf, "fig", c["n"], png)
            except Exception as e:
                r = {"err": str(e)[:100]}
            if "err" in r or not os.path.isfile(png):
                continue
        out.append({"n": c["n"], "page": c["page"], "cap": c["text"][:600], "img": name})
    return out


_PROMPT = (
    "아래는 논문 한 편의 그림들이다(PDF 에서 잘라 낸 PNG, 폴더 \"%s\"). 그림마다 Read 도구로 파일을 열어 보고(하나씩, 모두), 교과서를 쓸 사람이 그림을 보지 않고도 어느 그림을 어디에 쓸지 고를 수 있게 적어라.\n"
    "JSON 으로만 답하라: {\"figs\": [{\"n\": 3, \"type\": \"schematic|photo|plot|setup|mixed|other\", "
    "\"what\": \"무엇을 그린 그림인가 — 한국어 한두 문장. (a)(b) 부분이 있으면 부분마다 짧게\", "
    "\"shows\": \"이 그림이 독자에게 보여 주는 것 — 교과서의 어떤 설명 옆에 두면 좋은가, 한 문장\", "
    "\"labels\": [\"그림 속 주요 글자·기호·화살표의 이름 (영어 그대로, 8개까지)\"], \"quality\": \"good|ok|poor\"}]}\n"
    "- type: schematic = 개념·기전·모델을 그린 도식(화살표 도해, 단면 그림, 힘·열의 도해, 공구-공작물 배치 도면), photo = SEM·광학·TEM 같은 실물 사진, plot = 곡선·막대·산점도 같은 그래프, "
    "setup = 실험 장치의 사진·도면, mixed = 도식과 사진·그래프가 섞인 것(주된 것을 what 에 적는다), other = 흐름도·그 밖.\n"
    "- 그림에 실제로 보이는 것만 적는다. 캡션이 말하지 않는 수치·결론을 보태지 않는다. quality 는 잘렸거나 흐리거나 캡션만 보이면 poor.\n"
    "- 논문: %s\n\n[그림 목록 — 파일 이름 — 캡션]\n%s")


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


def build(f, title="", model="sonnet", force=False):
    """논문 하나의 그림 색인을 만든다(있으면 그대로). Claude 호출 하나 — 그림마다 Read 로 열어 본다(그림 10장이면 1만 5천 토큰쯤)."""
    if not force:
        d = load(f)
        if d and (d.get("ok") or time.time() - (d.get("t") or 0) < _RETRY_AFTER):
            return d
    t0 = time.time()
    figs = crop_all(f)
    base = {"file": f, "t": time.time(), "mtime": _mtime(f), "model": model, "n": len(figs), "figs": figs, "ok": False}
    if not figs:
        base["ok"] = True
        cfg["save_json"](index_path(f), base)
        return base
    listing = "\n".join("%s — Fig. %d (p.%d): %s" % (x["img"], x["n"], x["page"], x["cap"][:400]) for x in figs)
    res = None
    try:
        run = cfg.get("claude_run")
        res = run(_PROMPT % (img_dir(f), title or _stem(f)[:120], listing), timeout=900, model=model, tools="Read", add_dirs=[img_dir(f)]) if run else None
    except Exception:
        res = None
    d = _json_of(res.get("text")) if res else None
    by = {}
    for x in (d or {}).get("figs") or []:
        if isinstance(x, dict) and str(x.get("n", "")).strip().isdigit():
            by[int(str(x["n"]).strip())] = x
    for x in figs:
        y = by.get(x["n"]) or {}
        typ = str(y.get("type") or "other").strip().lower()
        x["type"] = typ if typ in TYPES else "other"
        x["what"] = re.sub(r"\s+", " ", str(y.get("what") or "")).strip()[:400]
        x["shows"] = re.sub(r"\s+", " ", str(y.get("shows") or "")).strip()[:240]
        x["labels"] = [str(l).strip()[:40] for l in (y.get("labels") or [])[:8] if str(l).strip()] if isinstance(y.get("labels"), list) else []
        x["quality"] = str(y.get("quality") or "").strip().lower() if str(y.get("quality") or "").strip().lower() in ("good", "ok", "poor") else ("ok" if y else "poor")
    base.update(ok=bool(by), sec=round(time.time() - t0), tok=(res or {}).get("tok"), turns=(res or {}).get("turns"))
    cfg["save_json"](index_path(f), base)
    return base


def ensure(items, model="sonnet", workers=4, on_done=None):
    """여러 논문의 색인을 한꺼번에(없는 것만, 4편 동시). items = [(파일, 제목)] → {파일: 색인}"""
    out = {}
    todo = []
    for f, title in items:
        d = load(f)
        if d and (d.get("ok") or time.time() - (d.get("t") or 0) < _RETRY_AFTER):
            out[f] = d
        else:
            todo.append((f, title))
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
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
    """색인 하나의 통계 → (그림 수, 도식 수)"""
    figs = (d or {}).get("figs") or []
    return len(figs), sum(1 for x in figs if x.get("type") in ("schematic", "setup"))
