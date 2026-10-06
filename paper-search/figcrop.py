# -*- coding: utf-8 -*-
"""
논문 PDF 에서 그림·표를 잘라 PNG 로 — 공부(교과서 한 장)에 논문의 그림을 싣기 위한 것 (2026-10-07).

사용자: '교과서처럼 사진 같은 것도 있으면 좋겠다 — 연성·취성에서 기다란 타원형 홈, 가공 깊이가 깊어지면 균열이 생기는 그 그림'.
그림은 Claude 가 그리는 것이 아니라 서재 논문의 PDF 에서 그대로 잘라 온다(지어낼 수 없는 재료). 장에는 원문 캡션과 쪽을 같이 적는다.

방법: 페이지의 텍스트 블록 가운데 'Fig. N' / 'Table N' 으로 시작하는 캡션 블록을 찾고(본문의 'Fig. 1(c) shows…' 참조 문장은 뺀다),
그림은 캡션 위(표는 캡션 아래)에 있는 삽입 그림·벡터 드로잉 영역을 모아 그 상자를 자른다. 위에 아무것도 없으면(옆에 캡션을 두는 편집) 같은 높이의 옆을 본다.
PyMuPDF 만 쓴다. server 를 import 하지 않는다(서버를 import 하면 수집 감시가 같이 뜬다).
"""
import os
import re

_CAP = re.compile(r"^\s*(Fig\.?|Figure|Table)\s*(\d+)\s*[.:|]?\s*[.:|]?\s*(.*)$", re.I | re.S)
# 본문의 참조 문장: 번호 바로 뒤에 괄호·소문자(Fig. 1(c), Fig. 1a shows) 또는 동사. 'Fig.1 (a) A +ve…' 처럼 빈칸 뒤 괄호는 캡션이다
_CAP_REF = re.compile(r"^(?i:fig\.?|figure|table)\s*\d+(?:\(|[a-z]|\s+(?i:shows?|illustrates?|presents?|depicts?|compares?|summari[sz]es?|gives?|and|to|in|of)\b)")
_CAP_STYLE = re.compile(r"^(?:fig\.?|figure|table)\s*\d+\s*[.:|]?\s*[.:|]?\s*[A-Z(]", re.I)


def _fitz():
    try:
        import pymupdf as fitz   # PyMuPDF 1.24+ 의 새 이름
    except ImportError:
        import fitz
    return fitz


def _merge(rects, pad=6.0):
    """겹치거나 가까운 상자를 하나로"""
    rects = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        while rects:
            a = rects.pop()
            for b in list(rects):
                if a[0] - pad <= b[2] and b[0] - pad <= a[2] and a[1] - pad <= b[3] and b[1] - pad <= a[3]:
                    a = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    rects.remove(b)
                    changed = True
            out.append(a)
        rects = out
    return rects


def _page_info(page):
    """(텍스트 블록[{t, bbox}], 그림 상자들, 폭, 높이)"""
    d = page.get_text("dict")
    W, H = page.rect.width, page.rect.height
    texts, imgs = [], []
    for b in d["blocks"]:
        if b.get("type") == 1:
            imgs.append(tuple(b["bbox"]))
        elif b.get("type") == 0:
            t = " ".join("".join(sp.get("text", "") for sp in ln.get("spans", [])) for ln in b.get("lines", []))
            t = " ".join(t.split())
            if t:
                texts.append({"t": t, "bbox": tuple(b["bbox"])})
    try:
        for dr in page.get_drawings():
            r = dr.get("rect")
            if r is not None and (r.x1 - r.x0) * (r.y1 - r.y0) > 400:
                imgs.append((r.x0, r.y0, r.x1, r.y1))
    except Exception:
        pass
    # 페이지 전체를 덮는 그림(스캔 배경)은 빼고 합친다 — 안 그러면 모든 그림이 그것과 합쳐져 사라진다. 가는 선(머리줄의 괘선)도 뺀다
    imgs = [r for r in imgs if (r[2] - r[0]) * (r[3] - r[1]) < W * H * 0.8 and (r[2] - r[0]) >= 3 and (r[3] - r[1]) >= 3]
    rects = [r for r in _merge(imgs) if (r[2] - r[0]) * (r[3] - r[1]) < W * H * 0.8]
    return texts, rects, W, H


def _hov(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _vov(a, b):
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def is_caption(text, kind=None, n=None):
    """'Fig. 3. …' 꼴의 캡션 글인가 → (kind, n, 본문) 또는 None"""
    m = _CAP.match(text or "")
    if not m or len(m.group(3)) < 8 or _CAP_REF.match(text) or not _CAP_STYLE.match(text):
        return None
    k = "table" if m.group(1).lower().startswith("t") else "fig"
    if kind and k != kind:
        return None
    if n is not None and int(m.group(2)) != n:
        return None
    return k, int(m.group(2)), m.group(3).strip()


def captions(pdf_path):
    """PDF 의 모든 캡션 → [{kind, n, page(1부터), text}] (같은 번호가 여럿이면 처음 것)"""
    fitz = _fitz()
    doc = fitz.open(pdf_path)
    out, seen = [], set()
    try:
        for pi, page in enumerate(doc):
            for tb in _page_info(page)[0]:
                c = is_caption(tb["t"])
                if c and (c[0], c[1]) not in seen:
                    seen.add((c[0], c[1]))
                    out.append({"kind": c[0], "n": c[1], "page": pi + 1, "text": c[2][:600]})
    finally:
        doc.close()
    out.sort(key=lambda x: (x["kind"], x["n"]))
    return out


def crop(pdf_path, kind, n, out_png, dpi=150):
    """그림(kind='fig')·표('table') N 을 잘라 out_png 로. → {page, w, h, cap} 또는 {err}"""
    fitz = _fitz()
    doc = fitz.open(pdf_path)
    try:
        for pi, page in enumerate(doc):
            texts, rects, W, H = _page_info(page)
            cap = None
            for tb in texts:
                if is_caption(tb["t"], kind, n):
                    cap = tb
                    break
            if not cap:
                continue
            cb = cap["bbox"]
            cw = cb[2] - cb[0]
            body = [t for t in texts if t is not cap and len(t["t"]) >= 90 and (t["bbox"][2] - t["bbox"][0]) > 120 and _hov(t["bbox"], cb) > cw * 0.4]
            rect = None
            if kind == "fig":
                above = [t["bbox"][3] for t in body if t["bbox"][3] <= cb[1] + 2]
                floor = max(above) if above else 0.0
                cands = [r for r in rects if r[3] <= cb[1] + 6 and r[1] >= floor - 4 and _hov(r, cb) > min(cw, r[2] - r[0]) * 0.3]
                if cands:
                    top = max(min(r[1] for r in cands), floor + 2)
                    # 그림과 본문 사이의 짧은 줄(절 제목 '3. TAPER CUT' 같은)은 그림이 아니다 — 모든 그림 상자보다 위에 있는 글은 바닥을 올린다
                    for t in texts:
                        tb = t["bbox"]
                        if t is not cap and len(t["t"]) >= 12 and tb[3] <= top + 2 and tb[1] >= floor - 2 and _hov(tb, cb) > cw * 0.3:
                            top = max(top, tb[3] + 1)
                    x0 = min([r[0] for r in cands] + [cb[0]]) - 4
                    x1 = max([r[2] for r in cands] + [cb[2]]) + 4
                    for t in texts:      # 옆 단의 본문이 끼지 않게 — 캡션 밖의 긴 글 블록 앞에서 자른다
                        tb = t["bbox"]
                        if t is not cap and len(t["t"]) >= 90 and _vov(tb, (x0, top, x1, cb[1])) > 10:
                            if tb[0] >= cb[2] - 5 and tb[0] < x1:
                                x1 = min(x1, tb[0] - 4)
                            elif tb[2] <= cb[0] + 5 and tb[2] > x0:
                                x0 = max(x0, tb[2] + 4)
                    rect = fitz.Rect(x0, top - 2, x1, cb[1] - 1)
                if rect is None or rect.height < 40:
                    # 캡션 옆에 그림을 두는 편집(Springer 의 좁은 캡션): 같은 높이의 옆 상자. 옆 단의 본문이 끼지 않게 본문 블록 앞에서 자른다
                    side = [r for r in rects if _vov(r, cb) > 10 and _hov(r, cb) < 5 and (r[3] - r[1]) >= 40]
                    if side:
                        x0 = min(r[0] for r in side) - 4
                        x1 = max(r[2] for r in side) + 4
                        y0 = min(r[1] for r in side) - 3
                        y1 = max(r[3] for r in side) + 3
                        for t in texts:
                            tb = t["bbox"]
                            if t is not cap and len(t["t"]) >= 90 and _vov(tb, (x0, y0, x1, y1)) > 10:
                                if tb[0] > cb[2] and tb[0] < x1:
                                    x1 = min(x1, tb[0] - 4)
                                elif tb[2] < cb[0] and tb[2] > x0:
                                    x0 = max(x0, tb[2] + 4)
                        rect = fitz.Rect(x0, y0, x1, y1)
                if rect is None or rect.height < 40:
                    rect = fitz.Rect(cb[0] - 4, max(floor + 2, cb[1] - 260), cb[2] + 4, cb[1] - 1) if cb[1] - floor > 60 else None
            else:
                below = [t["bbox"][1] for t in body if t["bbox"][1] >= cb[3] - 2]
                ceil_ = min(below) if below else H
                cands = [r for r in rects if r[1] >= cb[3] - 6 and r[3] <= ceil_ + 4 and _hov(r, cb) > min(cw, r[2] - r[0]) * 0.3]
                bottom = max(r[3] for r in cands) if cands else min(ceil_ - 2, cb[3] + 220)
                x0 = min([r[0] for r in cands] + [cb[0]]) - 4
                x1 = max([r[2] for r in cands] + [cb[2]]) + 4
                rect = fitz.Rect(x0, cb[1] - 1, x1, bottom + 2)      # 표는 캡션까지 포함한다 (표 머리가 캡션 바로 아래)
            if rect is None:
                return {"err": "그림 영역을 찾지 못함", "page": pi + 1}
            rect = rect & page.rect
            if rect.height < 40 or rect.width < 60:
                return {"err": "그림 영역이 너무 작음 (%.0f×%.0f)" % (rect.width, rect.height), "page": pi + 1}
            os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
            page.get_pixmap(clip=rect, dpi=dpi, alpha=False).save(out_png)
            return {"page": pi + 1, "w": round(rect.width), "h": round(rect.height), "cap": cap["t"][:600]}
        return {"err": "캡션을 찾지 못함"}
    finally:
        doc.close()
