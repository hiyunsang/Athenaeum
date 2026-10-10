# -*- coding: utf-8 -*-
"""pptx_min — 표준 라이브러리만으로 .pptx 를 쓴다 (python-pptx 없이; 포터블판에 의존성을 더하지 않으려고).
공부의 「발표 자료」 가 쓴다. 16:9, 맑은 고딕(설치된 PC 에서 그 글꼴로 보인다 — 파일에는 이름만 든다).

쪽의 짜임은 '상자' 목록(layout_*)으로 먼저 세우고, 같은 상자를 ① .pptx 의 도형으로 ② 미리보기 PNG(PyMuPDF, 있으면)로 그린다 — 화면의 쪽 미리보기와 파일이 같은 자리를 쓴다.

build(path, deck, preview_dir=None) → {slides, pics, previews}
deck = {title, subtitle, footer, title_note, deck_title,
        slides: [{h, msg, bullets: [{t, sub: [str]}], stats: [{v, label}], panels: [{kind: fig, path, caption} | {kind: table, cap, cols, rows}], foot, note}],
        refs: [str], skip_refs: bool}
"""
import os, struct, zipfile, time

EMU = 914400
W_IN, H_IN = 13.333, 7.5
W, H = 12192000, 6858000
FONT = "Malgun Gothic"
ACCENT, ACCENT_SOFT, ACCENT_DARK = "1F5FBF", "EEF3FB", "1A2B4A"
GRAY, GRAY2, TEXT, LINE, HEAD_FILL, STAT_FILL = "6B6B6B", "9A9A9A", "1A1A1A", "D9DEE7", "E9EEF6", "F5F7FA"
NS_P = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def esc(t):
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def inch(x):
    return int(round(x * EMU))


def png_size(path):
    """PNG·JPEG 의 가로·세로. 못 읽으면 (4, 3)."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return max(1, w), max(1, h)
        if head[:2] == b"\xff\xd8":
            with open(path, "rb") as f:
                data = f.read()
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return max(1, w), max(1, h)
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except Exception:
        pass
    return 4, 3


def fit(iw, ih, bw, bh):
    r = min(bw / float(iw), bh / float(ih))
    return iw * r, ih * r


# ---------- 상자(layout) ----------
def P(t, sz, b=False, color=TEXT, lvl=None, align="l", after=0, line=1.1, i=False):
    return {"t": str(t or ""), "sz": sz, "b": b, "color": color, "lvl": lvl, "align": align, "after": after, "line": line, "i": i}


def text(x, y, w, h, paras, anchor="t", fill=None):
    return {"kind": "text", "x": x, "y": y, "w": w, "h": h, "paras": paras, "anchor": anchor, "fill": fill}


def rect(x, y, w, h, fill):
    return {"kind": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill}


def pic(x, y, w, h, path):
    return {"kind": "pic", "x": x, "y": y, "w": w, "h": h, "path": path}


def table(x, y, w, h, cols, rows, sz=9):
    return {"kind": "table", "x": x, "y": y, "w": w, "h": h, "cols": cols, "rows": rows, "sz": sz}


def _lines(t, per):
    return max(1, -(-len(t) // max(8, int(per))))


def _para_h(p, width_in):
    """문단 높이(in) — 한글 한 자 폭 ≈ 0.98 × 글자 크기"""
    per = max(8, int(width_in * 72 / (p["sz"] * 0.98)))
    return _lines(p["t"], per) * p["sz"] * p["line"] * 1.2 / 72.0 + p["after"] / 72.0


def _fit_bullets(bullets, width_in, avail, sizes=(16, 15, 14, 13, 12, 11)):
    """글머리표 문단들을 avail 높이에 들어가는 가장 큰 글꼴로. 가장 작은 글꼴로도 넘치면 세부(sub)를 뒤 글머리표부터 하나씩 덜어 낸다 (수치 상자 뒤로 글이 숨지 않게)"""
    bl = [{"t": b["t"], "sub": list(b.get("sub") or [])} for b in bullets]
    while True:
        paras, h = [], 0
        for sz in sizes:
            paras = []
            for b in bl:
                paras.append(P(b["t"], sz, lvl=0, after=5, line=1.12))
                for x in b["sub"]:
                    paras.append(P(x, max(9.5, sz - 3.5), lvl=1, color=GRAY, after=3, line=1.1))
            h = sum(_para_h(p, width_in - 0.3 * (p["lvl"] + 1)) for p in paras)
            if h <= avail:
                return paras, h
        cand = [b for b in bl if b["sub"]]
        if not cand:
            return paras, h
        cand[-1]["sub"].pop()


def _row_heights(cols, rows, width_in, sz):
    """표 행마다의 높이(in) — 가장 긴 칸의 줄 수로"""
    ws = col_widths(cols, rows, width_in)
    out = []
    for row in [cols] + list(rows):
        lines = 1
        for ci, w in enumerate(ws):
            t = str(row[ci]) if ci < len(row) else ""
            lines = max(lines, _lines(t, max(4, int((w - 0.12) * 72 / (sz * 1.08)))))   # 굵은 글꼴·한글은 넓다 — 보수적으로
        out.append(lines * sz * 1.25 / 72.0 + 0.09)
    return out


def _cap_h(cap, width_in):
    return 0.12 + 0.165 * _lines(cap, int(width_in * 72 / (9.5 * 0.95)))


def layout_title(deck):
    T, sub, footer = str(deck.get("title") or "발표 자료"), str(deck.get("subtitle") or ""), str(deck.get("footer") or "")
    boxes = [rect(0, 0, 0.35, H_IN, ACCENT),
             text(1.0, 2.3, W_IN - 2.0, 1.9, [P(T, 36, True, line=1.1)], anchor="b"),
             text(1.0, 4.35, W_IN - 2.0, 1.2, [P(s, 16, color=GRAY, after=4) for s in sub.split("\n") if s.strip()], anchor="t")]
    if footer:
        boxes.append(text(1.0, H_IN - 0.7, W_IN - 2.0, 0.4, [P(footer, 10, color=GRAY)], anchor="b"))
    return boxes


def _fig_block(pn, x, y, w, h_max):
    """그림 하나를 (x, y) 에서 폭 w 안에 비율대로, 아래에 캡션(무슨 그림인지)과 참고문헌 줄. 돌려주는 값: (상자들, 쓴 높이)"""
    cap, ref = pn.get("caption") or "", pn.get("ref") or ""
    ch = _cap_h(cap, w) + (0.1 + 0.15 * _lines(ref, int(w * 72 / (8.5 * 0.95))) if ref else 0)
    iw, ih = png_size(pn.get("path") or "")
    fw, fh = fit(iw, ih, w, max(0.6, h_max - ch - 0.05))
    out = [pic(x + (w - fw) / 2, y, fw, fh, pn.get("path"))]
    paras = [P(cap, 9.5, color="3A3A3A", line=1.12, after=1)]
    if ref:
        paras.append(P(ref, 8.5, color=GRAY2, line=1.1))
    out.append(text(x, y + fh + 0.05, w, ch, paras, anchor="t"))
    return out, fh + 0.05 + ch


def _figs_need(pn, w):
    """폭 w 일 때 그림 블록의 자연 높이"""
    cap, ref = pn.get("caption") or "", pn.get("ref") or ""
    ch = _cap_h(cap, w) + (0.1 + 0.15 * _lines(ref, int(w * 72 / (8.5 * 0.95))) if ref else 0)
    iw, ih = png_size(pn.get("path") or "")
    return w * ih / float(iw) + 0.05 + ch, ch


# 쪽 배치 — 화면의 메뉴와 같은 차례 (study.html 의 DK_LAYOUTS 와 맞출 것). needs: f=그림 수 최소, t=표 필요
LAYOUTS = [
    ("auto", "자동 (면적이 큰 배치)", 0, False),
    ("row", "그림 가로로 나란히", 2, False),
    ("col", "그림 세로로 쌓기", 2, False),
    ("big_left", "큰 하나 왼쪽 + 작은 것 오른쪽", 2, False),
    ("one_top", "위에 하나 크게 + 아래 나란히", 2, False),
    ("one_bottom", "위에 나란히 + 아래 하나 크게", 2, False),
    ("kw_top", "키워드 위 · 그림 아래 가로", 1, False),
    ("kw_bottom", "그림 위 가로 · 키워드 아래", 1, False),
    ("fig_only", "그림만 크게 (키워드는 메모로)", 1, False),
    ("kw_only", "키워드만", 0, False),
    ("table_bottom", "표를 아래 전체 폭에", 0, True),
    ("table_only", "표만", 0, True),
]
LAYOUT_KEYS = [k for k, _l, _f, _t in LAYOUTS]


def layout_content(s, idx, total, deck_title=""):
    """내용 쪽 하나의 상자들. s["layout"](LAYOUTS 의 키, 기본 auto)대로 — 그림 수·표 유무에 안 맞는 배치는 auto 로 떨어진다 (사용자 2026-10-10: 쪽마다 배치를 직접 고르게)"""
    L, R = 0.6, 0.6
    boxes = []
    if deck_title:
        boxes.append(text(W_IN - R - 6.0, 0.16, 6.0, 0.3, [P(deck_title, 9, color=GRAY2, align="r")]))
    boxes.append(text(L, 0.5, W_IN - L - R, 0.85, [P(s.get("h") or "", 26, True, line=1.05)], anchor="b"))
    boxes.append(rect(L, 1.42, 0.9, 0.04, ACCENT))
    body_top, body_bot = 1.62, H_IN - 0.98
    full_w = W_IN - L - R
    zone_h = body_bot - body_top
    gap = 0.25
    panels = [p for p in (s.get("panels") or []) if p]
    tab = next((p for p in panels if p.get("kind") == "table"), None)
    figs_all = [p for p in panels if p.get("kind") != "table"][:3]
    bullets = [b for b in (s.get("bullets") or []) if (b.get("t") or "").strip()]
    lay = s.get("layout") or "auto"
    if lay not in LAYOUT_KEYS:
        lay = "auto"
    # 배치가 재료에 안 맞으면 auto 로
    need = next((f for k, _l, f, _t in LAYOUTS if k == lay), 0)
    need_tab = next((t for k, _l, _f, t in LAYOUTS if k == lay), False)
    if len(figs_all) < need or (need_tab and not tab):
        lay = "auto"

    def kw_box(x, y, w, h, sizes=(18, 17, 16, 15, 14, 13, 12, 11), bl=None):
        paras, _h = _fit_bullets(bl if bl is not None else bullets, w, h, sizes=sizes)
        boxes.append(text(x, y, w, h, paras, anchor="t"))

    def fig_at(pn, x, y, w, h):
        boxes.extend(_fig_block(pn, x, y, w, h)[0])

    def area(pn, w, h):
        need_, ch = _figs_need(pn, w)
        iw, ih = png_size(pn.get("path") or "")
        fw, fh = fit(iw, ih, w, max(0.6, h - ch - 0.05))
        return fw * fh

    def table_block(pn, x, y, w, h_max):
        """표 하나를 (x, y) 에서 폭 w 로 — 행 높이는 글 줄 수만큼, h_max 를 넘는 행은 덜어 낸다"""
        sz_t = 9
        cap_c = 70 if w > 6 else 44
        rows = [[(str(c)[:cap_c] + "…") if len(str(c)) > cap_c else str(c) for c in r] for r in (pn.get("rows") or [])[:8]]
        allrows = len(pn.get("rows") or [])
        rhs = _row_heights(pn.get("cols") or [], rows, w, sz_t)
        while len(rows) > 1 and 0.32 + sum(rhs) > h_max:
            rows.pop(); rhs.pop()
        cap = (pn.get("cap") or "") + ((" (…외 %d행은 장에서)" % (allrows - len(rows))) if allrows > len(rows) else "")
        return [text(x, y, w, 0.3, [P(cap, 10, True, line=1.05)], anchor="t"), dict(table(x, y + 0.32, w, sum(rhs), pn.get("cols") or [], rows, sz=sz_t), rhs=rhs)]

    def best_mode(figs, w, h):
        """(w, h) 안에 그림 n장을 놓는 배치 가운데 그림 면적 합이 가장 큰 것 → (mode, 면적). 가로로 긴 그림은 위아래(col)가, 세로로 긴 그림은 나란히(row)가 이긴다 (사용자 2026-10-11: '가로세로비도 생각해서')"""
        n = len(figs)
        if n == 0:
            return "auto", 0.0
        if n == 1:
            return "auto", area(figs[0], w, h)
        wn, hn = (w - gap * (n - 1)) / n, (h - gap * (n - 1)) / n
        w2, h2 = (w - gap) / 2, (h - gap) / 2
        wb, ws = (w - gap) * 0.58, (w - gap) * 0.42
        cand = [(sum(area(pn, wn, h) for pn in figs), "row"), (sum(area(pn, w, hn) for pn in figs), "col")]
        if n == 3:
            cand += [(area(figs[0], wb, h) + area(figs[1], ws, h2) + area(figs[2], ws, h2), "big_left"),
                     (area(figs[0], w2, h2) + area(figs[1], w2, h2) + area(figs[2], w, h2), "one_bottom"),
                     (area(figs[0], w, h2) + area(figs[1], w2, h2) + area(figs[2], w2, h2), "one_top")]
        a, m = max(cand)
        return m, a

    def arrange(figs, x, y, w, h, mode):
        """그림 1~3장을 (x, y, w, h) 안에 mode 대로. auto 는 면적이 큰 배치"""
        n = len(figs)
        if n == 0:
            return
        if n == 1:
            fig_at(figs[0], x, y, w, h); return
        wn, hn = (w - gap * (n - 1)) / n, (h - gap * (n - 1)) / n
        w2, h2 = (w - gap) / 2, (h - gap) / 2
        wb, ws = (w - gap) * 0.58, (w - gap) * 0.42
        if mode == "auto":
            mode = best_mode(figs, w, h)[0]
        if mode == "row":
            for k, pn in enumerate(figs):
                fig_at(pn, x + k * (wn + gap), y, wn, h)
        elif mode == "col":
            for k, pn in enumerate(figs):
                fig_at(pn, x, y + k * (hn + gap), w, hn)
        elif mode == "big_left":
            fig_at(figs[0], x, y, wb, h)
            rest = figs[1:]
            hr = (h - gap * (len(rest) - 1)) / len(rest)
            for k, pn in enumerate(rest):
                fig_at(pn, x + wb + gap, y + k * (hr + gap), ws, hr)
        elif mode == "one_top":
            fig_at(figs[0], x, y, w, h2)
            rest = figs[1:]
            wr = (w - gap * (len(rest) - 1)) / len(rest)
            for k, pn in enumerate(rest):
                fig_at(pn, x + k * (wr + gap), y + h2 + gap, wr, h2)
        else:   # one_bottom: 위에 나란히 + 아래 하나 크게
            rest = figs[:-1]
            wr = (w - gap * (len(rest) - 1)) / len(rest)
            for k, pn in enumerate(rest):
                fig_at(pn, x + k * (wr + gap), y, wr, h2)
            fig_at(figs[-1], x, y + h2 + gap, w, h2)

    kw_h = 1.5   # 키워드를 위·아래 띠로 둘 때의 높이
    rich = any(b.get("sub") for b in bullets)
    if lay == "auto" and figs_all and not tab:   # 자동: 오른쪽 영역의 가장 좋은 배치 vs 키워드 위·그림 아래 가로 — 그림이 모두 가로로 길면 뒤쪽이 훨씬 크다
        figs_p0 = figs_all[:3]
        kw_w0 = ((6.4, 6.4, 5.2, 4.4)[min(3, len(figs_p0))]) if rich else (3.4 if len(figs_p0) >= 3 else 3.9)
        m_r, a_r = best_mode(figs_p0, full_w - kw_w0 - 0.3, zone_h)
        n0 = len(figs_p0)
        a_top = sum(area(pn, (full_w - gap * (n0 - 1)) / n0, zone_h - kw_h - 0.15) for pn in figs_p0)
        if a_top > a_r * 1.15:
            lay = "kw_top"
    if lay == "kw_only":
        kw_box(L, body_top, full_w, zone_h, sizes=(20, 18, 17, 16, 15, 14, 13, 12))
    elif lay == "fig_only":
        arrange(figs_all, L, body_top, full_w, zone_h, "auto")
    elif lay in ("kw_top", "kw_bottom"):
        half = (len(bullets) + 1) // 2
        cols = [bullets[:half], bullets[half:]] if len(bullets) > 2 else [bullets]
        cw = (full_w - gap * (len(cols) - 1)) / len(cols)
        ky = body_top if lay == "kw_top" else body_bot - kw_h
        for k, bl in enumerate(cols):
            kw_box(L + k * (cw + gap), ky, cw, kw_h, sizes=(16, 15, 14, 13, 12, 11), bl=bl)
        fy = body_top + kw_h + 0.15 if lay == "kw_top" else body_top
        arrange(figs_all, L, fy, full_w, zone_h - kw_h - 0.15, "row" if len(figs_all) > 1 else "auto")
    elif lay == "table_only":
        kw_w = 5.6 if any(b.get("sub") for b in bullets) else 3.9
        kw_box(L, body_top, kw_w, zone_h)
        boxes.extend(table_block(tab, L + kw_w + 0.3, body_top, full_w - kw_w - 0.3, zone_h))
    elif lay == "table_bottom":
        kw_w = 5.6 if any(b.get("sub") for b in bullets) else 3.9
        h_top = zone_h * 0.55 - gap
        figs_p = figs_all[:2]
        kw_box(L, body_top, kw_w, h_top)
        if figs_p:
            arrange(figs_p, L + kw_w + 0.3, body_top, full_w - kw_w - 0.3, h_top, "row")
        boxes.extend(table_block(tab, L, body_top + h_top + gap, full_w, zone_h - h_top - gap))
    else:
        figs_p = figs_all[:3 if not tab else 2]
        has_right = bool(figs_p or tab)
        kw_w = (((6.4, 6.4, 5.2, 4.4)[min(3, len(figs_p))]) if rich else (3.4 if len(figs_p) >= 3 else 3.9)) if has_right else full_w
        fig_x, fig_w = L + kw_w + 0.3, full_w - kw_w - 0.3
        kw_box(L, body_top, kw_w, zone_h)
        if tab and figs_p:            # 그림 + 표: 옆으로 나란히 (사용자: '표는 그림 옆에'); 그림이 둘이면 왼쪽 반에 위아래로
            w2 = (fig_w - gap) / 2
            arrange(figs_p, fig_x, body_top, w2, zone_h, "col" if len(figs_p) > 1 else "auto")
            boxes.extend(table_block(tab, fig_x + w2 + gap, body_top, w2, zone_h))
        elif tab:
            boxes.extend(table_block(tab, fig_x, body_top, fig_w, zone_h))
        else:
            arrange(figs_p, fig_x, body_top, fig_w, zone_h, lay if lay in ("row", "col", "big_left", "one_top", "one_bottom") else "auto")
    if s.get("foot"):
        boxes.append(text(L, H_IN - 0.86, W_IN - L - R - 1.0, 0.56, [P(s["foot"], 9.5, color=GRAY, line=1.15)], anchor="b"))
    boxes.append(text(W_IN - R - 0.8, H_IN - 0.62, 0.8, 0.3, [P("%d / %d" % (idx, total), 10, color=GRAY, align="r")], anchor="b"))
    return boxes


def layout_refs(chunk, label, idx, total):
    L, R = 0.6, 0.6
    return [text(L, 0.5, W_IN - L - R, 0.85, [P(label, 26, True)], anchor="b"), rect(L, 1.42, 0.9, 0.04, ACCENT),
            text(L, 1.65, W_IN - L - R, H_IN - 2.5, [P(r, 11 if len(chunk) > 8 else 12, after=4, line=1.1) for r in chunk] or [P("(없음)", 12, color=GRAY)], anchor="t"),
            text(W_IN - R - 0.8, H_IN - 0.62, 0.8, 0.3, [P("%d / %d" % (idx, total), 10, color=GRAY, align="r")], anchor="b")]


# ---------- 상자 → pptx 도형 ----------
def _rpr(p):
    return ('<a:rPr lang="ko-KR" altLang="en-US" sz="%d"%s%s dirty="0"><a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
            '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface="%s"/></a:rPr>' % (int(p["sz"] * 100), ' b="1"' if p["b"] else "", ' i="1"' if p.get("i") else "", p["color"], FONT, FONT, FONT))


def _para_xml(p):
    ppr = '<a:pPr algn="%s"' % p["align"]
    if p["lvl"] is not None:
        ppr += ' marL="%d" indent="-%d" lvl="%d"' % (inch(0.3 * (p["lvl"] + 1)), inch(0.26), p["lvl"])
    ppr += '><a:lnSpc><a:spcPct val="%d"/></a:lnSpc>' % int(p["line"] * 100000)   # 1/1000 % — 100% = 100000
    if p["after"]:
        ppr += '<a:spcAft><a:spcPts val="%d"/></a:spcAft>' % int(p["after"] * 100)
    if p["lvl"] is not None:
        ppr += '<a:buClr><a:srgbClr val="%s"/></a:buClr><a:buFont typeface="Arial"/><a:buChar char="%s"/>' % (ACCENT if p["lvl"] == 0 else GRAY2, "•" if p["lvl"] == 0 else "–")
    else:
        ppr += "<a:buNone/>"
    ppr += "</a:pPr>"
    return "<a:p>%s<a:r>%s<a:t>%s</a:t></a:r></a:p>" % (ppr, _rpr(p), esc(p["t"]))


def _sp_text(id_, box):
    paras = "".join(_para_xml(p) for p in box["paras"]) or '<a:p><a:endParaRPr lang="ko-KR"/></a:p>'
    body = '<p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="%s"><a:normAutofit/></a:bodyPr><a:lstStyle/>%s</p:txBody>' % (box["anchor"], paras)
    fill = ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % box["fill"]) if box.get("fill") else "<a:noFill/>"
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="t%d"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>%s</p:spPr>%s</p:sp>' % (id_, id_, inch(box["x"]), inch(box["y"]), inch(box["w"]), inch(box["h"]), fill, body))


def _sp_rect(id_, box):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="r%d"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="%s"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ko-KR"/></a:p></p:txBody></p:sp>' % (id_, id_, inch(box["x"]), inch(box["y"]), inch(box["w"]), inch(box["h"]), box["fill"]))


def _sp_pic(id_, box, rid):
    return ('<p:pic><p:nvPicPr><p:cNvPr id="%d" name="p%d"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>' % (id_, id_, rid, inch(box["x"]), inch(box["y"]), inch(box["w"]), inch(box["h"])))


def col_widths(cols, rows, total):
    """열 너비 — 글자 수에 비례, 최소 몫은 둔다"""
    n = max(1, len(cols))
    ln = [max([len(str(c))] + [len(str(r[i])) if i < len(r) else 0 for r in rows]) for i, c in enumerate(cols)]
    ln = [min(40, max(4, x)) for x in ln]
    tot = float(sum(ln)) or 1.0
    return [total * (0.5 / n + 0.5 * x / tot) for x in ln]


def _sp_table(id_, box):
    cols, rows, sz = box["cols"], box["rows"], box["sz"]
    ws = col_widths(cols, rows, box["w"])
    rhs = box.get("rhs") or [box["h"] / (len(rows) + 1)] * (len(rows) + 1)

    def tc(t, head):
        pr = P(t, sz, head, ACCENT_DARK if head else TEXT, line=1.05)
        fill = ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % HEAD_FILL) if head else "<a:noFill/>"
        return ('<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>%s</a:txBody><a:tcPr marL="54864" marR="54864" marT="27432" marB="27432" anchor="ctr">'
                '<a:lnL><a:noFill/></a:lnL><a:lnR><a:noFill/></a:lnR><a:lnT w="6350"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:lnT><a:lnB w="6350"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:lnB>%s</a:tcPr></a:tc>'
                % (_para_xml(pr), LINE, LINE, fill))
    trs = '<a:tr h="%d">%s</a:tr>' % (inch(rhs[0]), "".join(tc(c, True) for c in cols))
    for ri, r in enumerate(rows, 1):
        trs += '<a:tr h="%d">%s</a:tr>' % (inch(rhs[ri] if ri < len(rhs) else rhs[-1]), "".join(tc(r[i] if i < len(r) else "", False) for i in range(len(cols))))
    return ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="%d" name="tbl%d"/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
            '<p:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            '<a:tbl><a:tblPr firstRow="1" bandRow="0"/><a:tblGrid>%s</a:tblGrid>%s</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
            % (id_, id_, inch(box["x"]), inch(box["y"]), inch(box["w"]), inch(box["h"]), "".join('<a:gridCol w="%d"/>' % inch(w) for w in ws), trs))


def _slide_xml(boxes, rels, media):
    shapes, sid = [], 2
    for b in boxes:
        if b["kind"] == "text":
            shapes.append(_sp_text(sid, b))
        elif b["kind"] == "rect":
            shapes.append(_sp_rect(sid, b))
        elif b["kind"] == "table":
            shapes.append(_sp_table(sid, b))
        elif b["kind"] == "pic" and b.get("path") and os.path.isfile(b["path"]):
            rid = "rId%d" % (len(rels) + 2)
            ext = os.path.splitext(b["path"])[1].lower() or ".png"
            mname = "image%d%s" % (len(media) + 1, ext)
            media[mname] = b["path"]
            rels.append((rid, "image", "../media/" + mname))
            shapes.append(_sp_pic(sid, b, rid))
        sid += 1
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sld %s><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>' % (NS_P, "".join(shapes)))


def _notes_xml(txt):
    paras = "".join(_para_xml(P(t, 12, after=4)) for t in (txt or "").split("\n") if t.strip()) or '<a:p><a:endParaRPr lang="ko-KR"/></a:p>'
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:notes %s><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
            '<p:txBody><a:bodyPr/><a:lstStyle/>%s</p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>' % (NS_P, paras))


# ---------- 고정 부품 ----------
_THEME = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Athenaeum"><a:themeElements>'
          '<a:clrScheme name="Athenaeum"><a:dk1><a:srgbClr val="1A1A1A"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
          '<a:accent1><a:srgbClr val="%s"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="4472C4"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
          '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
          '<a:fontScheme name="Athenaeum"><a:majorFont><a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
          '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
          '<a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst>'
          '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
          '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
          '</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>' % (ACCENT, FONT, FONT, FONT, FONT))

_MASTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldMaster %s><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>'
           '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
           '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
           '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
           '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="2800"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles></p:sldMaster>' % NS_P)

_LAYOUT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldLayout %s type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>'
           '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
           '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>' % NS_P)

_NOTES_MASTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:notesMaster %s><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>'
                 '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                 '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr>'
                 '<p:spPr><a:xfrm><a:off x="685800" y="1143000"/><a:ext cx="5486400" cy="3086100"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln w="12700"><a:solidFill><a:prstClr val="black"/></a:solidFill></a:ln></p:spPr></p:sp>'
                 '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr>'
                 '<p:spPr><a:xfrm><a:off x="685800" y="4400550"/><a:ext cx="5486400" cy="3600450"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ko-KR"/></a:p></p:txBody></p:sp>'
                 '</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
                 '<p:notesStyle><a:lvl1pPr><a:defRPr sz="1200"/></a:lvl1pPr></p:notesStyle></p:notesMaster>' % NS_P)

_RELS_ROOT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
              '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>')


def _rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join('<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/%s" Target="%s"/>' % (rid, typ, tgt) for rid, typ, tgt in items) + "</Relationships>")


# ---------- 본체 ----------
def slide_boxes(deck):
    """쪽마다의 상자 목록과 발표자 메모 — build 와 미리보기가 같이 쓴다"""
    content = [s for s in (deck.get("slides") or []) if s]
    refs = [str(r) for r in (deck.get("refs") or []) if str(r).strip()]
    chunks = [] if deck.get("skip_refs") else ([refs[i:i + 12] for i in range(0, len(refs), 12)] or [[]])
    total = 1 + len(content) + len(chunks)
    out = [(layout_title(deck), str(deck.get("title_note") or ""))]
    for i, s in enumerate(content, 2):
        out.append((layout_content(s, i, total, deck.get("deck_title") or deck.get("title") or ""), str(s.get("note") or "")))
    for ci, chunk in enumerate(chunks):
        out.append((layout_refs(chunk, "참고문헌" + (" (%d/%d)" % (ci + 1, len(chunks)) if len(chunks) > 1 else ""), len(content) + 2 + ci, total), ""))
    return out


def build(path, deck, preview_dir=None):
    pages = slide_boxes(deck)
    slides_xml, slide_rels, media, notes = [], [], {}, []
    for boxes, note in pages:
        rels = []
        slides_xml.append(_slide_xml(boxes, rels, media))
        slide_rels.append(rels)
        notes.append(note)
    nslides = len(slides_xml)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>', '<Default Extension="xml" ContentType="application/xml"/>',
          '<Default Extension="png" ContentType="image/png"/>', '<Default Extension="jpg" ContentType="image/jpeg"/>', '<Default Extension="jpeg" ContentType="image/jpeg"/>',
          '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
          '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
          '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
          '<Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>',
          '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
          '<Override PartName="/ppt/theme/theme2.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
          '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>',
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
          '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>']
    for i in range(1, nslides + 1):
        ct.append('<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % i)
        ct.append('<Override PartName="/ppt/notesSlides/notesSlide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>' % i)
    ct.append("</Types>")
    pres = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentation %s saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:notesMasterIdLst><p:notesMasterId r:id="rId%d"/></p:notesMasterIdLst>'
            '<p:sldIdLst>%s</p:sldIdLst><p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/>'
            '<p:defaultTextStyle><a:defPPr><a:defRPr lang="ko-KR"/></a:defPPr></p:defaultTextStyle></p:presentation>'
            % (NS_P, nslides + 2, "".join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, i + 2) for i in range(nslides)), W, H))
    pres_rels = [("rId1", "slideMaster", "slideMasters/slideMaster1.xml")] + [("rId%d" % (i + 2), "slide", "slides/slide%d.xml" % (i + 1)) for i in range(nslides)] + \
                [("rId%d" % (nslides + 2), "notesMaster", "notesMasters/notesMaster1.xml"), ("rId%d" % (nslides + 3), "theme", "theme/theme1.xml"), ("rId%d" % (nslides + 4), "presProps", "presProps.xml")]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>Athenaeum</dc:creator><cp:lastModifiedBy>Athenaeum</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>' % (esc(deck.get("title")), now, now))
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Athenaeum</Application><Slides>%d</Slides></Properties>' % nslides)
    tmp = path + ".%d.tmp" % os.getpid()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", _RELS_ROOT)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", _rels(pres_rels))
        z.writestr("ppt/presProps.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentationPr %s/>' % NS_P)
        z.writestr("ppt/theme/theme1.xml", _THEME)
        z.writestr("ppt/theme/theme2.xml", _THEME)
        z.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "theme", "../theme/theme1.xml")]))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]))
        z.writestr("ppt/notesMasters/notesMaster1.xml", _NOTES_MASTER)
        z.writestr("ppt/notesMasters/_rels/notesMaster1.xml.rels", _rels([("rId1", "theme", "../theme/theme2.xml")]))
        for i, (sx, rels) in enumerate(zip(slides_xml, slide_rels), 1):
            z.writestr("ppt/slides/slide%d.xml" % i, sx)
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % i, _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")] + rels + [("rId%d" % (len(rels) + 2), "notesSlide", "../notesSlides/notesSlide%d.xml" % i)]))
            z.writestr("ppt/notesSlides/notesSlide%d.xml" % i, _notes_xml(notes[i - 1]))
            z.writestr("ppt/notesSlides/_rels/notesSlide%d.xml.rels" % i, _rels([("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"), ("rId2", "slide", "../slides/slide%d.xml" % i)]))
        for mname, src in media.items():
            z.write(src, "ppt/media/" + mname)
    os.replace(tmp, path)
    n_prev = render_previews(pages, preview_dir) if preview_dir else 0
    return {"slides": nslides, "pics": len(media), "previews": n_prev}


# ---------- 미리보기 PNG (PyMuPDF 가 있을 때만; 화면의 쪽 미리보기용 — 글꼴·줄바꿈이 PowerPoint 와 똑같지는 않다) ----------
def _font_files():
    d = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    reg, bold = os.path.join(d, "malgun.ttf"), os.path.join(d, "malgunbd.ttf")
    reg = reg if os.path.isfile(reg) else None
    return reg, (bold if os.path.isfile(bold) else reg)


def _para_height(p, width_pt, reg, bold):
    """문단이 차지할 높이(pt) — 글자 폭으로 줄 수를 어림"""
    try:
        import fitz
        fnt = fitz.Font(fontfile=bold if p["b"] else reg) if reg else fitz.Font("helv")
        tw = fnt.text_length(p["t"], fontsize=p["sz"]) if p["t"] else 1
    except Exception:
        tw = len(p["t"]) * p["sz"] * 0.9
    lines = max(1, int(tw / max(10.0, width_pt - 2)) + 1)
    return lines * p["sz"] * p["line"] * 1.18


def render_previews(pages, outdir, dpi=96):
    try:
        import fitz
    except ImportError:
        return 0
    os.makedirs(outdir, exist_ok=True)
    reg, bold = _font_files()
    PT = 72.0
    rgb = lambda h: tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    n = 0
    for idx, (boxes, _note) in enumerate(pages, 1):
        doc = fitz.open()
        page = doc.new_page(width=W_IN * PT, height=H_IN * PT)
        for b in boxes:
            r = fitz.Rect(b["x"] * PT, b["y"] * PT, (b["x"] + b["w"]) * PT, (b["y"] + b["h"]) * PT)
            try:
                if b["kind"] == "rect":
                    page.draw_rect(r, color=None, fill=rgb(b["fill"]))
                elif b["kind"] == "pic":
                    if b.get("path") and os.path.isfile(b["path"]):
                        page.insert_image(r, filename=b["path"], keep_proportion=True)
                elif b["kind"] == "table":
                    cols, rows = b["cols"], b["rows"]
                    ws = col_widths(cols, rows, b["w"])
                    rhs = b.get("rhs") or [b["h"] / (len(rows) + 1)] * (len(rows) + 1)
                    y0 = b["y"]
                    for ri, row in enumerate([cols] + rows):
                        rh = rhs[ri] if ri < len(rhs) else rhs[-1]
                        if ri:
                            y0 += rhs[ri - 1] if ri - 1 < len(rhs) else rhs[-1]
                        if ri == 0:
                            page.draw_rect(fitz.Rect(b["x"] * PT, y0 * PT, (b["x"] + b["w"]) * PT, (y0 + rh) * PT), color=None, fill=rgb(HEAD_FILL))
                        page.draw_line(fitz.Point(b["x"] * PT, (y0 + rh) * PT), fitz.Point((b["x"] + b["w"]) * PT, (y0 + rh) * PT), color=rgb(LINE), width=0.5)
                        cx = b["x"]
                        for ci, w in enumerate(ws):
                            t = str(row[ci]) if ci < len(row) else ""
                            cr = fitz.Rect((cx + 0.06) * PT, (y0 + 0.04) * PT, (cx + w - 0.06) * PT, (y0 + rh + 0.3) * PT)
                            kw = {"fontname": "F1" if ri == 0 else "F0", "fontfile": bold if ri == 0 else reg} if reg else {"fontname": "helv"}
                            page.insert_textbox(cr, t, fontsize=b["sz"], color=rgb(ACCENT_DARK if ri == 0 else TEXT), lineheight=1.15, **kw)
                            cx += w
                elif b["kind"] == "text":
                    if b.get("fill"):
                        page.draw_rect(r, color=None, fill=rgb(b["fill"]))
                    y = r.y0
                    if b["anchor"] in ("b", "ctr"):
                        tot = sum(_para_height(p, r.width - ((0.3 * (p["lvl"] + 1)) * PT if p["lvl"] is not None else 0), reg, bold) + p["after"] for p in b["paras"])
                        y = max(r.y0, r.y1 - tot) if b["anchor"] == "b" else r.y0 + max(0, (r.height - tot) / 2)
                    for p in b["paras"]:
                        indent = (0.3 * (p["lvl"] + 1)) * PT if p["lvl"] is not None else 0
                        if p["lvl"] is not None:
                            page.insert_text(fitz.Point(r.x0 + indent - 0.2 * PT, y + p["sz"] * 1.05), "•" if p["lvl"] == 0 else "–", fontsize=p["sz"], color=rgb(ACCENT if p["lvl"] == 0 else GRAY2), fontname="helv")
                        h = _para_height(p, r.width - indent, reg, bold)
                        tr = fitz.Rect(r.x0 + indent, y, r.x1, y + h + 4)
                        kw = {"fontname": "F1" if p["b"] else "F0", "fontfile": bold if p["b"] else reg} if reg else {"fontname": "helv"}   # 같은 이름으로 두 파일을 등록하면 먼저 것만 쓰인다
                        page.insert_textbox(tr, p["t"], fontsize=p["sz"], color=rgb(p["color"]), align={"l": 0, "ctr": 1, "r": 2}.get(p["align"], 0), lineheight=p["line"], **kw)
                        y += h + p["after"]
            except Exception:
                pass
        pix = page.get_pixmap(dpi=dpi)
        pix.save(os.path.join(outdir, "s%02d.png" % idx))
        doc.close()
        n += 1
    return n


if __name__ == "__main__":   # python pptx_min.py out.pptx [preview_dir]
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "pptx_min_test.pptx"
    deck = {"title": "시험 발표", "subtitle": "pptx_min 보기\n2026-10-09", "footer": "Athenaeum", "deck_title": "시험 발표", "skip_refs": True,
            "slides": [{"h": "첫 슬라이드 — 키워드·수치·표",
                        "bullets": [{"t": "글머리표 하나", "sub": ["받치는 세부 — 조건과 값 12.5 µm", "둘째 세부"]}, {"t": "둘 — 조금 더 긴 문장을 넣어 줄바꿈이 어떻게 되는지 본다, 숫자 12.5 µm 와 영어 built-up edge 도", "sub": []}, {"t": "셋"}],
                        "stats": [{"v": "15,800 MPa", "label": "탄탈럼의 최대 비절삭력"}, {"v": "40–50", "label": "칩 두께비"}],
                        "panels": [{"kind": "table", "cap": "표 1. 시험 표", "cols": ["논문", "용융점", "그 밖"], "rows": [["Wang 2002", "2950°C", "비열 0.153 J/g°C"], ["Davis 2020", "3017 °C", "경도 ~200 HV"]]}],
                        "foot": "출처: [1] Davis 2020, IJMTM · [3] Wang 2023, JMP", "note": "발표자 메모"}],
            "refs": []}
    print(build(out, deck, sys.argv[2] if len(sys.argv) > 2 else None))
