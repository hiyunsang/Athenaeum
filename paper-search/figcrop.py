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
_CAP_STYLE = re.compile(r"^(?i:fig\.?|figure|table)\s*\d+\s*[.:|]?\s*[.:|]?\s*(?:[A-Z(]|[a-z](?:,\s*[a-z])*(?:[.)]|\s+(?:[A-Z(\d]|[a-z][.)]))|[a-z][-\u2013]\S)")
# 캡션은 대문자·괄호로 시작하거나 소문자 부분 기호('a Rake face …', 'a) …', 'a. …', 'a, b …', 'c-EBSD')로 시작한다 — 전에는 re.I 라 [A-Z] 가 소문자도 받아
# 'Fig. 6 is a SEM image …'·'Fig. 2 includes …' 같은 본문 문장이 캡션이 됐다(Davis 2020 의 Fig. 6·15 가 그래서 안 잘렸다, 서재 398편에서 81곳; 2026-11-07). 소문자 낱말로 시작하는 캡션('von Mises …')은 놓친다
_LABEL_ONLY = re.compile(r"^\s*(?:Fig\.?|Figure|Table)\s*\d+\s*[.:|]?\s*$", re.I)   # 번호만 홀로 있는 블록 ('Figure 1')


def _join_labels(texts):
    """'Figure 1' 번호만 홀로 한 블록이고 캡션 글이 다음 블록(바로 아래나 같은 줄 오른쪽)에 있는 편집(Annual Reviews 꼴)은 둘을 한 블록으로 —
    안 그러면 캡션을 하나도 못 잡아 그림 색인이 0장이 된다(Beyerlein 2014 ARMR: 그림 10장·'Figure N' 줄 20개인데 색인 0장, 2026-10-09)"""
    pair = {}
    for i, tb in enumerate(texts):
        if not _LABEL_ONLY.match(tb["t"]):
            continue
        b = tb["bbox"]
        best = None
        for j, ob in enumerate(texts):
            if j == i or len(ob["t"]) < 8 or _LABEL_ONLY.match(ob["t"]):
                continue
            o = ob["bbox"]
            gap_below, gap_right = o[1] - b[3], o[0] - b[2]
            if -2 <= gap_below <= 24 and (_hov(o, b) > 0 or abs(o[0] - b[0]) < 12):
                d = gap_below
            elif abs(o[1] - b[1]) <= 6 and -2 <= gap_right <= 24:
                d = gap_right
            else:
                continue
            if best is None or d < best[0]:
                best = (d, j)
        if best and best[1] not in pair.values():
            pair[i] = best[1]
    if not pair:
        return texts
    skip, out = set(pair.values()), []
    for i, tb in enumerate(texts):
        if i in skip:
            continue
        if i in pair:
            ob = texts[pair[i]]
            b, o = tb["bbox"], ob["bbox"]
            out.append({"t": tb["t"].strip() + " " + ob["t"], "bbox": (min(b[0], o[0]), min(b[1], o[1]), max(b[2], o[2]), max(b[3], o[3]))})
        else:
            out.append(tb)
    return out


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
    texts = _join_labels(texts)
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


def _thin_lines(page):
    """표의 괘선: 가늘고(높이 3pt 미만) 긴(40pt 이상) 가로 드로잉 → [(x0, y, x1)]"""
    out = []
    try:
        for dr in page.get_drawings():
            r = dr.get("rect")
            if r is not None and (r.y1 - r.y0) < 3 and (r.x1 - r.x0) >= 40:
                out.append((r.x0, (r.y0 + r.y1) / 2, r.x1))
    except Exception:
        pass
    return out


def _prose(s):
    """산문인가: 문장 경계('. X')가 둘 이상이고 숫자가 적다 — 표의 칸 글이 한 블록으로 합쳐진 것(숫자·단위가 많고 문장 경계가 없다)과 가른다"""
    n = len(s)
    if n < 90:
        return False
    sents = len(re.findall(r"[.!?]\s+[A-Z(]", s))
    digits = sum(c.isdigit() for c in s) / float(n)
    return sents >= 2 and digits < 0.06


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
    first_err = None      # 캡션으로 보인 블록에서 영역을 못 잡은 까닭 — 끝까지 못 찾으면 이것을 돌려준다
    try:
        for pi, page in enumerate(doc):
            texts, rects, W, H = _page_info(page)
            caps = [tb for tb in texts if is_caption(tb["t"], kind, n)]
            if not caps:
                continue
            for cap in caps:      # 같은 번호의 캡션으로 보이는 블록이 여럿이면(본문의 'Fig. 9 (b) accurately …' 가 먼저 잡힌 쪽) 영역이 잡히는 것을 쓰고, 안 되면 다음 블록·다음 쪽으로 (Maeng 2025 Fig. 9, 2026-11-09)
                cb = cap["bbox"]
                cw = cb[2] - cb[0]
                body = [t for t in texts if t is not cap and len(t["t"]) >= 90 and (t["bbox"][2] - t["bbox"][0]) > 120 and _hov(t["bbox"], cb) > cw * 0.4]
                ocaps = [t for t in texts if t is not cap and is_caption(t["t"])]      # 다른 그림·표의 캡션 — 그림·표의 영역은 이것을 넘지 않는다 (2026-11-09)
                rect = None
                if kind == "fig":
                    above = [t["bbox"][3] for t in body if t["bbox"][3] <= cb[1] + 2]
                    floor = max(above) if above else 0.0
                    # 한 단에 그림이 위아래로 놓인 편집: 위 그림의 캡션은 짧아 '본문' 에 들지 않으므로 바닥이 그 위의 본문까지 올라가 위 그림과 그 캡션까지 통째로 잘려 왔다
                    # (Aurich 2009 CIRPA 의 Fig. 2·7·14·36·38·43·47·49·51, Huang 2023 Fig. 25·31, Fang 2007 Fig. 6, Alao 2024 Fig. 6, Maeng 2025 Fig. 2 — 사용자 '폰에서 사진이 깨진다', 2026-11-09)
                    # — 같은 단의 다른 캡션은 바닥이다 (표의 캡션이면 표의 몸통은 아직 들 수 있다: 표는 캡션이 위에 있다)
                    oc = [o["bbox"][3] for o in ocaps if o["bbox"][3] <= cb[1] + 2 and _hov(o["bbox"], cb) > min(cw, o["bbox"][2] - o["bbox"][0]) * 0.3]
                    if oc:
                        floor = max(floor, max(oc))
                    # 그림의 이미지 상자가 캡션 윗선 아래로 여백만큼(15~30pt) 삐져나온 편집이 흔하다(O'Toole 2021·la Monaca 2022·Nagaraj 2019; 2026-11-07) — 상자 높이의 30%·45pt 까지는 위의 그림으로 본다
                    cands = [r for r in rects if r[3] <= cb[1] + max(6, min(45, 0.3 * (r[3] - r[1]))) and r[1] >= floor - 4 and _hov(r, cb) > min(cw, r[2] - r[0]) * 0.3]
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
                        # 캡션 옆에 그림을 두는 편집(Springer 의 좁은 캡션): 같은 높이의 옆 상자. 옆 단의 본문이 끼지 않게 본문 블록 앞에서 자른다.
                        # 한 줄 캡션은 그림의 윗줄에만 걸친다(Piquard 2024 Fig. 8: 겹침 7pt) — 세로 겹침이 작아도 윗선이 16pt 안이면 옆 상자로 본다 (2026-11-07)
                        side = [r for r in rects if _hov(r, cb) < 5 and (r[3] - r[1]) >= 40 and (_vov(r, cb) > 10 or abs(r[1] - cb[1]) <= 16)]
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
                        # 마지막 수단(캡션 위 260pt 를 캡션 폭으로) — 그림이 글자·가는 선으로만 그려져 상자가 안 잡히는 쪽(스캔·선 그래프)을 위한 것. 그 자리가 글이면(본문 문장·수식 더미)
                        # 그림이 아니다 — 전에는 그대로 잘라 와 본문 글과 식 (30)이 그림 자리에 들어갔다(Piquard 2024 Fig. 8, 2026-11-07). 글 판정: 8낱말 넘는 60자 이상 블록이 하나라도, 또는 자리 안의 글자가 260자 넘으면
                        rect = fitz.Rect(cb[0] - 4, max(floor + 2, cb[1] - 260, 56), cb[2] + 4, cb[1] - 1) if cb[1] - floor > 60 else None      # 위 56pt 는 쪽 머리글
                        if rect is not None:
                            scan = any((r[2] - r[0]) * (r[3] - r[1]) > W * H * 0.5 for r in rects)      # 쪽을 덮는 그림(스캔) — 글자 층은 OCR 이라 믿지 않는다
                            inside = lambda rc: [t for t in texts if t is not cap and 60 < t["bbox"][1] < H - 60 and _vov(t["bbox"], rc) > 6 and _hov(t["bbox"], rc) > min(40, (t["bbox"][2] - t["bbox"][0]) * 0.5)]
                            prose = [t for t in inside(rect) if len(t["t"]) >= 60 and len(t["t"].split()) >= 8]
                            if prose and not scan:      # 산문 아래부터 — Hastings 1980 Fig. 2 는 글 네 줄 아래의 그래프였다; 남는 높이가 40pt 도 안 되면 글이다(Nie 2022 Fig. 2)
                                top = max(t["bbox"][3] for t in prose) + 2
                                if rect.y1 - top < 40:
                                    first_err = first_err or {"err": "캡션 위가 글이다 (그림 상자가 없다)", "page": pi + 1}
                                    continue
                                rect = fitz.Rect(rect.x0, top, rect.x1, rect.y1)
                            rest = inside(rect)
                            if not scan and (sum(len(t["t"]) for t in rest) > 260 or sum(1 for t in rest if "=" in t["t"]) >= 4):      # 글자가 많거나 식 더미
                                first_err = first_err or {"err": "캡션 위가 글이다 (그림 상자가 없다)", "page": pi + 1}
                                continue
                else:
                    # 표가 놓인 단(column)의 가로 범위 — 캡션이 짧으면 캡션 폭만으로는 오른쪽 칸이 잘렸다(Liu 2018 Table 1)
                    # 캡션 옆에 표를 두는 편집(Springer: 'Table 1 QSD characteristics' 가 왼쪽, 표는 오른쪽으로 쪽 폭의 대부분 — Piquard 2024): 캡션 오른쪽에서 시작해
                    # 쪽 가운데를 넘는 짧은(산문 아닌) 글 블록이 캡션 윗선 40pt 안에 있으면 쪽 전체 폭. 옆 단의 블록은 가운데 뒤에서 시작하므로 걸리지 않는다 (2026-11-07)
                    # 그 블록은 캡션의 윗선보다 아래에서 시작하고 쪽 머리글(위 56pt)이 아니어야 한다 — 쪽 맨 위의 표에서는 가운데 맞춘 러닝 헤더('F.Z. Fang et al. / JMPT 184 (2007)')가 걸려 쪽 전체 폭이 됐다(Fang 2007 Table 1, 2026-11-09)
                    beside = any(t is not cap and len(t["t"]) < 90 and not _prose(t["t"]) and t["bbox"][0] >= cb[2] + 5 and t["bbox"][0] < W * 0.45
                                 and t["bbox"][2] > W * 0.55 and -4 <= t["bbox"][1] - cb[1] <= 40 and t["bbox"][1] > 56 for t in texts)
                    if cw < W * 0.55 and not beside:
                        cx0, cx1 = (W * 0.04, W * 0.5 + 6) if cb[0] < W * 0.45 else (W * 0.5 - 6, W * 0.96)
                    else:
                        cx0, cx1 = W * 0.04, W * 0.96
                    # 표 아래의 경계 = 그 단에서 캡션 아래에 오는 첫 '산문'(_prose: 문장 경계 둘 이상, 숫자 적음)이나 절 제목('3. Engineering …').
                    # 표의 칸 글이 한 블록으로 합쳐져 길어도 산문이 아니면 표의 일부다(Yan 2003 Table 1 이 첫 행에서, Sun 2019 Table 2 가 머리 행에서 잘렸다)
                    def _stop(t):
                        s = t["t"]
                        return _prose(s) or (len(s) < 90 and re.match(r"^\d+(\.\d+)*\.\s+[A-Z][a-z]+", s))      # 표의 행 '3.5 Cutting speed …' 는 번호 뒤에 마침표가 없다
                    def _ceiling(cx0, cx1):      # 표 아래의 경계 = 산문·절 제목, 그리고 다음 표·그림의 캡션(Jang 2012 Table 1 이 Table 2 의 캡션을 칸 글로 보고 Table 2 까지 이어졌다, 2026-11-09)
                        # 산문은 단 안에 든 것(두 단에 걸친 표 아래의 한 단 문단)과, 단과 반 넘게 겹치는 것(한 단 편집의 쪽 전체 폭 문단 — 단 안에 든 것만 보면 경계가 없어 끝까지 내려갔다) 둘 다
                        ys_ = [t["bbox"][1] for t in texts if t is not cap and t["bbox"][1] >= cb[3] - 2 and _stop(t)
                               and ((t["bbox"][0] >= cx0 - 4 and t["bbox"][2] <= cx1 + 4) or _hov(t["bbox"], (cx0, 0, cx1, H)) > (cx1 - cx0) * 0.5)]
                        ys_ += [o["bbox"][1] for o in ocaps if o["bbox"][1] >= cb[3] - 2 and _hov(o["bbox"], (cx0, 0, cx1, H)) > 10]
                        return min(ys_) if ys_ else H
                    ceil_ = _ceiling(cx0, cx1)

                    # 표의 괘선(가는 가로선)을 캡션 아래에서 차례로 따라 내려간다 — 선 사이가 320pt 넘게 비면 다른 표·그림이다
                    def _lines(cx0, cx1, ceil_):
                        ys = sorted((y, x0_, x1_) for x0_, y, x1_ in _thin_lines(page) if y >= cb[3] - 4 and y <= ceil_ + 4 and x0_ < cx1 and x1_ > cx0)
                        last, wide, cnt = cb[3], [], 0
                        for y, x0_, x1_ in ys:
                            if y - last > 320:          # 머리 괘선과 바닥 괘선 사이가 멀 수 있다(긴 표) — 산문(ceil_) 앞까지만 보므로 넉넉히
                                break
                            last = y; cnt += 1
                            if x1_ > cx1 + 20 or x0_ < cx0 - 20:
                                wide.append((x0_, x1_))
                        return last, wide, cnt
                    last, wide, nln = _lines(cx0, cx1, ceil_)
                    if len(wide) >= 2:      # 괘선 둘 이상이 단 밖까지 그어져 있으면 표는 그 폭이다 — 두 단에 걸친 표가 캡션이 놓인 단의 폭으로 잘려 오른쪽 칸이 없어졌다(Jang 2012 Table 2, 2026-11-09)
                        cx0, cx1 = min([cx0] + [w[0] - 4 for w in wide]), max([cx1] + [w[1] + 4 for w in wide])
                        ceil_ = _ceiling(cx0, cx1)
                        last, wide, nln = _lines(cx0, cx1, ceil_)
                    lines_last = last      # 괘선으로 따라간 마지막 자리 — 괘선이 둘 이상(위·아래 테두리)이면 표의 상자는 여기 위에서 시작해야 한다
                    # 괘선이 없는 표(또는 바닥 괘선이 그려지지 않은 표)는 캡션 아래로 이어지는 칸 글 블록을 따라 내려간다
                    for t in sorted(texts, key=lambda t: t["bbox"][1]):
                        tb = t["bbox"]
                        if t is not cap and tb[1] >= cb[3] - 2 and tb[1] <= last + 40 and tb[3] <= ceil_ + 2 and tb[0] >= cx0 - 4 and tb[2] <= cx1 + 4 and tb[1] < H - 60 and not _prose(t["t"]):      # 쪽 아래 60pt 는 꼬리글(쪽 번호·로고)
                            last = max(last, tb[3])
                    # 표의 상자(음영 칸·표 속 그림)는 표 안에서 시작한다 — 괘선이 둘 이상이면 마지막 괘선(바닥 테두리) 위에서, 괘선이 없으면 따라 내려온 몸통 아래 30pt 안에서.
                    # 표 바로 아래(24~28pt)에 놓인 다음 그림이 몸통에 붙은 상자로 들어와 표 크롭이 그 그림까지 내려갔다(Huang 2023 Table 4, Fang 2007 Table 1 — 2026-11-09)
                    rtop = lines_last + 8 if nln >= 2 else last + 30
                    cands = [r for r in rects if r[1] >= cb[3] - 6 and r[1] <= rtop and r[3] <= ceil_ + 4 and r[1] < H - 60 and _hov(r, (cx0, 0, cx1, H)) > (r[2] - r[0]) * 0.5]
                    bottom = max([r[3] for r in cands] + [last]) if (cands or last > cb[3]) else min(ceil_ - 2, cb[3] + 220)
                    x0 = min([r[0] for r in cands] + [cb[0]]) - 4
                    x1 = max([r[2] for r in cands] + [cb[2]]) + 4
                    for t in texts:      # 표의 칸 글이 캡션보다 넓으면 거기까지 — 칸 글이 한 블록으로 길게 합쳐진 것(Fang 2007 Table 1 의 340자)도 산문이 아니면 표다 (2026-11-09; 전에는 90자 미만만 보아 값 열이 잘렸다)
                        tb = t["bbox"]
                        if t is not cap and not _prose(t["t"]) and tb[1] >= cb[3] - 2 and tb[3] <= bottom + 2 and tb[0] >= cx0 - 4 and tb[2] <= cx1 + 4:
                            x0, x1 = min(x0, tb[0] - 4), max(x1, tb[2] + 4)
                    rect = fitz.Rect(x0, cb[1] - 1, x1, bottom + 2)      # 표는 캡션까지 포함한다 (표 머리가 캡션 바로 아래)
                if rect is None:
                    first_err = first_err or {"err": "그림 영역을 찾지 못함", "page": pi + 1}
                    continue
                rect = rect & page.rect
                if rect.height < 40 or rect.width < 60:
                    first_err = first_err or {"err": "그림 영역이 너무 작음 (%.0f×%.0f)" % (rect.width, rect.height), "page": pi + 1}
                    continue
                os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
                page.get_pixmap(clip=rect, dpi=dpi, alpha=False).save(out_png)
                return {"page": pi + 1, "w": round(rect.width), "h": round(rect.height), "cap": cap["t"][:600]}
        return first_err or {"err": "캡션을 찾지 못함"}
    finally:
        doc.close()
