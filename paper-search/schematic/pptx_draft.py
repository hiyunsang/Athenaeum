# -*- coding: utf-8 -*-
"""연구자가 PowerPoint 로 그린 초안(.pptx) → 쪽마다 SVG(대강의 모습) + 도형 목록(글) (2026-11-27).

사용자: '그림에 내가 그린 초안도 넣을 수 있게'. 초안을 도형으로 그린 .pptx 는 안에 그림 파일이 없어서
전에는 아무것도 꺼내지 못했다(ppt/media 만 봤다). PowerPoint 는 띄우지 않는다(CLAUDE.md 3절 — 인스턴스가
하나뿐이라 사용자의 창에 붙는다). 파일 안의 DrawingML 을 직접 읽어 그린다 — 정확한 렌더가 아니라 도식을 다시
그리는 쪽이 볼 '초안의 모습'(도형·선·화살표·글·그림의 자리와 색)이고, 글자·자리를 정확히 알려 주는 도형 목록을 같이 낸다.
"""
import io, re, math, base64, zipfile, posixpath, colorsys
import xml.etree.ElementTree as ET

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
RN = "{http://schemas.openxmlformats.org/package/2006/relationships}"
EMU = 9525.0                      # 1px(96 dpi) = 9525 EMU
FONT = "Malgun Gothic, Arial, sans-serif"
_PRST_CLR = {"black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF", "yellow": "FFFF00",
             "gray": "808080", "grey": "808080", "darkGray": "A9A9A9", "lightGray": "D3D3D3", "orange": "FFA500"}
_SCHEME_ALIAS = {"tx1": "dk1", "tx2": "dk2", "bg1": "lt1", "bg2": "lt2"}
_GEOM_KO = {"rect": "상자", "roundRect": "둥근 상자", "ellipse": "원", "triangle": "삼각형", "rtTriangle": "직각삼각형", "diamond": "마름모",
            "parallelogram": "평행사변형", "trapezoid": "사다리꼴", "hexagon": "육각형", "rightArrow": "오른쪽 화살표 도형", "leftArrow": "왼쪽 화살표 도형",
            "upArrow": "위 화살표 도형", "downArrow": "아래 화살표 도형", "leftRightArrow": "양쪽 화살표 도형", "chevron": "갈매기 도형", "homePlate": "오각 화살표",
            "plus": "더하기", "star5": "별", "can": "원통", "cube": "육면체", "line": "선", "custom": "자유형"}


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _x(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _rels(z, part):
    """part 의 관계 → {rId: 대상 경로(zip 안)}"""
    d, b = posixpath.split(part)
    rp = posixpath.join(d, "_rels", b + ".rels")
    out = {}
    try:
        root = ET.fromstring(z.read(rp))
    except (KeyError, ET.ParseError):
        return out
    for r in root.iter(RN + "Relationship"):
        t = r.get("Target") or ""
        if r.get("TargetMode") == "External":
            continue
        out[r.get("Id")] = posixpath.normpath(posixpath.join(d, t)) if not t.startswith("/") else t.lstrip("/")
    return out


# ---------- 색 ----------
def _mod(rgb, el):
    """lumMod/lumOff/tint/shade/alpha — PowerPoint 의 테마 색 변형(근사)"""
    r, g, b = [int(rgb[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    alpha = 1.0
    for m in el:
        tag = m.tag.replace(A, "")
        v = _f(m.get("val"), 100000) / 100000.0
        if tag in ("lumMod", "lumOff"):
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            l = l * v if tag == "lumMod" else min(1.0, l + v)
            r, g, b = colorsys.hls_to_rgb(h, l, s)
        elif tag == "tint":
            r, g, b = [c + (1 - c) * (1 - v) for c in (r, g, b)]
        elif tag == "shade":
            r, g, b = [c * v for c in (r, g, b)]
        elif tag == "alpha":
            alpha = v
    return "%02X%02X%02X" % tuple(int(max(0, min(1, c)) * 255 + 0.5) for c in (r, g, b)), alpha


def _color(el, sch, ph=None):
    """색 요소의 부모(a:solidFill 등) → ('#RRGGBB', 투명도) 또는 (None, 1)"""
    if el is None:
        return None, 1.0
    for c in el:
        tag = c.tag.replace(A, "")
        base = None
        if tag == "srgbClr":
            base = (c.get("val") or "000000").upper()
        elif tag == "schemeClr":
            v = c.get("val") or ""
            if v == "phClr":
                base = (ph or "000000").lstrip("#")
            else:
                base = sch.get(_SCHEME_ALIAS.get(v, v))
        elif tag == "sysClr":
            base = (c.get("lastClr") or ("FFFFFF" if c.get("val") == "window" else "000000")).upper()
        elif tag == "prstClr":
            base = _PRST_CLR.get(c.get("val") or "", "000000")
        elif tag == "scrgbClr":
            base = "%02X%02X%02X" % tuple(int(_f(c.get(k)) / 100000.0 * 255) for k in ("r", "g", "b"))
        if base and re.fullmatch(r"[0-9A-Fa-f]{6}", base):
            rgb, a = _mod(base, c)
            return "#" + rgb, a
    return None, 1.0


def _theme(z, pres_rels):
    sch = {"dk1": "000000", "lt1": "FFFFFF", "dk2": "44546A", "lt2": "E7E6E6", "accent1": "4472C4", "accent2": "ED7D31",
           "accent3": "A5A5A5", "accent4": "FFC000", "accent5": "5B9BD5", "accent6": "70AD47", "hlink": "0563C1", "folHlink": "954F72"}
    tp = next((t for t in pres_rels.values() if "/theme/" in "/" + t), None)
    try:
        root = ET.fromstring(z.read(tp)) if tp else None
    except (KeyError, ET.ParseError):
        root = None
    cs = root.find(".//" + A + "clrScheme") if root is not None else None
    for el in (cs if cs is not None else []):
        k = el.tag.replace(A, "")
        for c in el:
            v = c.get("val") if c.tag == A + "srgbClr" else c.get("lastClr")
            if v and re.fullmatch(r"[0-9A-Fa-f]{6}", v):
                sch[k] = v.upper()
    return sch


# ---------- 모양 ----------
def _geom(prst, x, y, w, h):
    """미리 정해진 도형 → SVG 모양(속성 없는 꼴). 모르는 모양은 None (점선 상자로)."""
    mn = min(w, h)
    def poly(pts):
        return "<polygon points='%s'" % " ".join("%.1f,%.1f" % p for p in pts)
    if prst in ("rect", "flowChartProcess", "snip1Rect", "snip2SameRect", "round1Rect", "flowChartPredefinedProcess", "textRect", "frame", "plaque", "can", "cube", "flowChartDocument"):
        return "<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f'" % (x, y, w, h)
    if prst in ("roundRect", "flowChartAlternateProcess", "round2SameRect", "flowChartTerminator"):
        rx = mn * (0.5 if prst == "flowChartTerminator" else 0.1667)
        return "<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' rx='%.1f'" % (x, y, w, h, rx)
    if prst in ("ellipse", "flowChartConnector", "donut", "pie", "chord"):
        return "<ellipse cx='%.1f' cy='%.1f' rx='%.1f' ry='%.1f'" % (x + w / 2, y + h / 2, w / 2, h / 2)
    if prst in ("triangle", "flowChartExtract"):
        return poly([(x + w / 2, y), (x + w, y + h), (x, y + h)])
    if prst == "rtTriangle":
        return poly([(x, y), (x, y + h), (x + w, y + h)])
    if prst in ("diamond", "flowChartDecision"):
        return poly([(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)])
    if prst in ("parallelogram", "flowChartInputOutput"):
        a = 0.25 * mn
        return poly([(x + a, y), (x + w, y), (x + w - a, y + h), (x, y + h)])
    if prst == "trapezoid":
        a = 0.25 * mn
        return poly([(x + a, y), (x + w - a, y), (x + w, y + h), (x, y + h)])
    if prst in ("hexagon", "octagon"):
        a = 0.25 * mn
        return poly([(x + a, y), (x + w - a, y), (x + w, y + h / 2), (x + w - a, y + h), (x + a, y + h), (x, y + h / 2)])
    if prst in ("homePlate", "pentagon"):
        a = 0.5 * mn if prst == "homePlate" else 0.0
        if prst == "pentagon":
            return poly([(x + w / 2, y), (x + w, y + 0.38 * h), (x + 0.81 * w, y + h), (x + 0.19 * w, y + h), (x, y + 0.38 * h)])
        return poly([(x, y), (x + w - a, y), (x + w, y + h / 2), (x + w - a, y + h), (x, y + h)])
    if prst == "chevron":
        a = 0.5 * mn
        return poly([(x, y), (x + w - a, y), (x + w, y + h / 2), (x + w - a, y + h), (x, y + h), (x + a, y + h / 2)])
    if prst in ("rightArrow", "leftArrow", "leftRightArrow"):
        a, t, b = 0.5 * mn, y + 0.25 * h, y + 0.75 * h
        if prst == "rightArrow":
            return poly([(x, t), (x + w - a, t), (x + w - a, y), (x + w, y + h / 2), (x + w - a, y + h), (x + w - a, b), (x, b)])
        if prst == "leftArrow":
            return poly([(x + w, t), (x + a, t), (x + a, y), (x, y + h / 2), (x + a, y + h), (x + a, b), (x + w, b)])
        return poly([(x, y + h / 2), (x + a, y), (x + a, t), (x + w - a, t), (x + w - a, y), (x + w, y + h / 2), (x + w - a, y + h), (x + w - a, b), (x + a, b), (x + a, y + h)])
    if prst in ("upArrow", "downArrow"):
        a, l, r = 0.5 * mn, x + 0.25 * w, x + 0.75 * w
        if prst == "upArrow":
            return poly([(l, y + h), (l, y + a), (x, y + a), (x + w / 2, y), (x + w, y + a), (r, y + a), (r, y + h)])
        return poly([(l, y), (l, y + h - a), (x, y + h - a), (x + w / 2, y + h), (x + w, y + h - a), (r, y + h - a), (r, y)])
    if prst in ("plus", "flowChartSummingJunction"):
        a = 0.25 * mn
        return poly([(x + a, y), (x + w - a, y), (x + w - a, y + a), (x + w, y + a), (x + w, y + h - a), (x + w - a, y + h - a), (x + w - a, y + h),
                     (x + a, y + h), (x + a, y + h - a), (x, y + h - a), (x, y + a), (x + a, y + a)])
    if prst in ("star5", "star4", "star6", "star8"):
        n = int(prst[4:]); pts = []
        for i in range(2 * n):
            rr = 0.5 if i % 2 == 0 else 0.2
            ang = -math.pi / 2 + i * math.pi / n
            pts.append((x + w / 2 + rr * w * math.cos(ang), y + h / 2 + rr * h * math.sin(ang)))
        return poly(pts)
    return None


def _cust(sp, x, y, w, h):
    """a:custGeom 의 경로 → SVG path d (arcTo 는 끝점까지 곧은 선으로 근사)"""
    out = []
    for pa in sp.iter(A + "path"):
        pw, ph = _f(pa.get("w"), 0) or None, _f(pa.get("h"), 0) or None
        sx = w / pw if pw else w / 21600.0
        sy = h / ph if ph else h / 21600.0
        def pt(e):
            return "%.1f,%.1f" % (x + _f(e.get("x")) * sx, y + _f(e.get("y")) * sy)
        for c in pa:
            tag = c.tag.replace(A, "")
            ps = c.findall(A + "pt")
            if tag == "moveTo" and ps:
                out.append("M" + pt(ps[0]))
            elif tag == "lnTo" and ps:
                out.append("L" + pt(ps[0]))
            elif tag == "cubicBezTo" and len(ps) == 3:
                out.append("C" + " ".join(pt(p) for p in ps))
            elif tag == "quadBezTo" and len(ps) == 2:
                out.append("Q" + " ".join(pt(p) for p in ps))
            elif tag == "close":
                out.append("Z")
    return " ".join(out)


class _Xf:
    """그룹 안 좌표 → 쪽 좌표 (기울임은 없다 — 그룹의 회전은 무시)"""
    def __init__(self, sx=1.0, sy=1.0, tx=0.0, ty=0.0):
        self.sx, self.sy, self.tx, self.ty = sx, sy, tx, ty

    def box(self, x, y, w, h):
        return self.tx + x * self.sx, self.ty + y * self.sy, w * self.sx, h * self.sy

    def child(self, off, ext, choff, chext):
        kx = ext[0] / chext[0] if chext[0] else 1.0
        ky = ext[1] / chext[1] if chext[1] else 1.0
        return _Xf(self.sx * kx, self.sy * ky, self.tx + (off[0] - choff[0] * kx) * self.sx, self.ty + (off[1] - choff[1] * ky) * self.sy)


def _xfrm(el):
    xf = el.find(A + "xfrm") if el is not None else None
    if xf is None:
        return None
    off, ext = xf.find(A + "off"), xf.find(A + "ext")
    if off is None or ext is None:
        return None
    return {"x": _f(off.get("x")) / EMU, "y": _f(off.get("y")) / EMU, "w": _f(ext.get("cx")) / EMU, "h": _f(ext.get("cy")) / EMU,
            "rot": _f(xf.get("rot")) / 60000.0, "fh": xf.get("flipH") == "1", "fv": xf.get("flipV") == "1", "el": xf}


def _style_ref(sp, which, sch):
    st = sp.find(P + "style")
    ref = st.find(A + which) if st is not None else None
    if ref is None:
        return None, 1.0, 0
    c, a = _color(ref, sch)
    return c, a, int(_f(ref.get("idx"), 0))


def _arrowhead(x1, y1, x2, y2, lw, col):
    """(x1,y1)→(x2,y2) 방향으로 (x2,y2)에 화살촉"""
    d = math.hypot(x2 - x1, y2 - y1) or 1.0
    ux, uy = (x2 - x1) / d, (y2 - y1) / d
    L = max(7.0, 3.2 * lw)
    W = L * 0.55
    bx, by = x2 - ux * L, y2 - uy * L
    return "<polygon points='%.1f,%.1f %.1f,%.1f %.1f,%.1f' fill='%s'/>" % (x2, y2, bx - uy * W, by + ux * W, bx + uy * W, by - ux * W, col)


def _wrap(text, fs, width):
    """글을 대강의 글자 폭으로 줄바꿈 (한글 1em · 그 밖 0.55em)"""
    if width <= fs:
        return [text]
    lines, cur, cw = [], "", 0.0
    for ch in text:
        w = fs * (1.0 if ord(ch) > 0x2E80 else 0.55)
        if cur and cw + w > width:
            cut = cur.rfind(" ")
            if cut > 0 and len(cur) - cut < 12:
                lines.append(cur[:cut]); cur = cur[cut + 1:]; cw = sum(fs * (1.0 if ord(c) > 0x2E80 else 0.55) for c in cur)
            else:
                lines.append(cur); cur, cw = "", 0.0
        cur += ch; cw += w
    if cur:
        lines.append(cur)
    return lines


def _text(sp, x, y, w, h, rot, sch, def_col):
    """도형의 글 → (SVG 조각, 글 한 줄)"""
    tb = sp.find(P + "txBody")
    if tb is None:
        tb = sp.find(A + "txBody")
    if tb is None:
        return "", ""
    bp = tb.find(A + "bodyPr")
    bp = bp if bp is not None else ET.Element("x")
    li, ri = _f(bp.get("lIns"), 91440) / EMU, _f(bp.get("rIns"), 91440) / EMU
    ti, bi = _f(bp.get("tIns"), 45720) / EMU, _f(bp.get("bIns"), 45720) / EMU
    anchor = bp.get("anchor") or "t"
    nowrap = bp.get("wrap") == "none"
    rows, plain = [], []
    for p in tb.findall(A + "p"):
        ppr = p.find(A + "pPr")
        algn = (ppr.get("algn") if ppr is not None else None) or "l"
        runs, fs, bold, col = [], None, False, None
        for r in p:
            tag = r.tag.replace(A, "")
            if tag in ("r", "fld"):
                t = r.find(A + "t")
                rp = r.find(A + "rPr")
                if rp is not None:
                    fs = fs or (_f(rp.get("sz"), 0) / 100.0 or None)
                    bold = bold or rp.get("b") == "1"
                    c, _ = _color(rp.find(A + "solidFill"), sch)
                    col = col or c
                runs.append(t.text if t is not None and t.text else "")
            elif tag == "br":
                runs.append("\n")
        if fs is None:
            ep = p.find(A + "endParaRPr")
            fs = (_f(ep.get("sz"), 0) / 100.0 if ep is not None else 0) or 18.0
        s = "".join(runs)
        for part in s.split("\n"):
            rows.append({"t": part, "fs": fs * 96 / 72.0, "b": bold, "c": col or def_col, "al": algn})
        if s.strip():
            plain.append(s.replace("\n", " ").strip())
    if not plain:
        return "", ""
    iw = max(1.0, w - li - ri)
    lines = []
    for r in rows:
        for t in ([r["t"]] if nowrap else _wrap(r["t"], r["fs"], iw)):
            lines.append(dict(r, t=t))
    tot = sum(l["fs"] * 1.2 for l in lines)
    if anchor == "ctr":
        cy = y + ti + (h - ti - bi - tot) / 2
    elif anchor == "b":
        cy = y + h - bi - tot
    else:
        cy = y + ti
    out = []
    for l in lines:
        cy += l["fs"] * 1.2
        if not l["t"].strip():
            continue
        if l["al"] == "ctr":
            tx, an = x + li + iw / 2, "middle"
        elif l["al"] == "r":
            tx, an = x + w - ri, "end"
        else:
            tx, an = x + li, "start"
        out.append("<text x='%.1f' y='%.1f' font-size='%.1f' text-anchor='%s' fill='%s'%s>%s</text>" % (
            tx, cy - l["fs"] * 0.28, l["fs"], an, l["c"], " font-weight='bold'" if l["b"] else "", _x(l["t"])))
    svg = "".join(out)
    if rot and svg:
        svg = "<g transform='rotate(%.2f %.1f %.1f)'>%s</g>" % (rot, x + w / 2, y + h / 2, svg)
    return svg, " / ".join(plain)


def _where(x, y, w, h, W, H):
    return "왼쪽 %d%% 위 %d%% 크기 %d×%d%%" % (round(100 * x / W), round(100 * y / H), round(100 * w / W), round(100 * h / H))


def _slide(z, part, W, H, sch, scale, frame=None):
    """쪽 하나 → (SVG, 도형 목록, 그린 것의 테두리 상자). frame=(x, y, w, h) 이면 그 부분만 그리고 %도 그 부분 기준."""
    root = ET.fromstring(z.read(part))
    rels = _rels(z, part)
    svg, items = [], []
    FX, FY, FW, FH = frame or (0.0, 0.0, W, H)
    bb = [1e18, 1e18, -1e18, -1e18]

    def seen(x, y, w, h):
        bb[0] = min(bb[0], x, x + w); bb[1] = min(bb[1], y, y + h); bb[2] = max(bb[2], x, x + w); bb[3] = max(bb[3], y, y + h)

    def where(x, y, w, h):
        return _where(x - FX, y - FY, w, h, FW, FH)
    tree = root.find(".//" + P + "spTree")
    if tree is None:
        return "", [], None

    def walk(node, xf):
        for el in node:
            tag = el.tag.replace(P, "")
            if tag == "grpSp":
                gp = el.find(P + "grpSpPr")
                g = gp.find(A + "xfrm") if gp is not None else None
                if g is not None and g.find(A + "off") is not None:
                    o, e = g.find(A + "off"), g.find(A + "ext")
                    co, ce = g.find(A + "chOff"), g.find(A + "chExt")
                    if co is None or ce is None:
                        co, ce = o, e
                    walk(el, xf.child((_f(o.get("x")) / EMU, _f(o.get("y")) / EMU), (_f(e.get("cx")) / EMU, _f(e.get("cy")) / EMU),
                                      (_f(co.get("x")) / EMU, _f(co.get("y")) / EMU), (_f(ce.get("cx")) / EMU, _f(ce.get("cy")) / EMU)))
                else:
                    walk(el, xf)
            elif tag in ("sp", "cxnSp"):
                shape(el, xf, tag == "cxnSp")
            elif tag == "pic":
                pic(el, xf)
            elif tag == "graphicFrame":
                t = _xfrm(el)
                if t is None:
                    gx = el.find(P + "xfrm")
                    if gx is not None and gx.find(A + "off") is not None:
                        o, e = gx.find(A + "off"), gx.find(A + "ext")
                        t = {"x": _f(o.get("x")) / EMU, "y": _f(o.get("y")) / EMU, "w": _f(e.get("cx")) / EMU, "h": _f(e.get("cy")) / EMU}
                if t:
                    x, y, w, h = xf.box(t["x"], t["y"], t["w"], t["h"])
                    seen(x, y, w, h)
                    cells = [c.text.strip() for c in el.iter(A + "t") if c.text and c.text.strip()]
                    kind = "표" if el.find(".//" + A + "tbl") is not None else "차트·개체"
                    svg.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' fill='none' stroke='#888' stroke-dasharray='5 3'/>"
                               "<text x='%.1f' y='%.1f' font-size='14' text-anchor='middle' fill='#666'>%s</text>" % (x, y, w, h, x + w / 2, y + h / 2, kind))
                    items.append("- %s %s%s" % (kind, where(x, y, w, h), (" · 칸의 글: " + " | ".join(cells[:24])) if cells else ""))

    def pic(el, xf):
        t = _xfrm(el.find(P + "spPr"))
        if not t:
            return
        x, y, w, h = xf.box(t["x"], t["y"], t["w"], t["h"])
        seen(x, y, w, h)
        bl = el.find(".//" + A + "blip")
        tgt = rels.get(bl.get(R + "embed")) if bl is not None else None
        ext = posixpath.splitext(tgt or "")[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp"}.get(ext)
        data = None
        if tgt and mime:
            try:
                data = z.read(tgt)
            except (KeyError, zipfile.BadZipFile, OSError):      # 깨진 그림 파일 — 빈 자리로
                data = None
        if data and len(data) < 8e6:
            svg.append("<image x='%.1f' y='%.1f' width='%.1f' height='%.1f' preserveAspectRatio='none' href='data:%s;base64,%s'/>" % (
                x, y, w, h, mime, base64.b64encode(data).decode("ascii")))
            items.append("- 그림(붙여 넣은 이미지) %s" % where(x, y, w, h))
        else:
            svg.append("<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' fill='#f2f2f2' stroke='#999'/>" % (x, y, w, h))
            items.append("- 그림(읽지 못한 형식 %s) %s" % (ext or "?", where(x, y, w, h)))

    def shape(el, xf, conn):
        sppr = el.find(P + "spPr")
        t = _xfrm(sppr)
        if not t:
            return
        x, y, w, h = xf.box(t["x"], t["y"], t["w"], t["h"])
        seen(x, y, w, h)
        pg = sppr.find(A + "prstGeom")
        prst = pg.get("prst") if pg is not None else ("custom" if sppr.find(A + "custGeom") is not None else "rect")
        # 채움
        if sppr.find(A + "noFill") is not None:
            fill, fa = None, 1.0
        else:
            fe = next((sppr.find(A + k) for k in ("solidFill", "gradFill", "pattFill") if sppr.find(A + k) is not None), None)
            if fe is not None:
                if fe.tag == A + "gradFill":
                    gs = fe.find(".//" + A + "gs")
                    fill, fa = _color(gs, sch)
                elif fe.tag == A + "pattFill":
                    fill, fa = _color(fe.find(A + "fgClr"), sch)
                else:
                    fill, fa = _color(fe, sch)
            else:
                c, a, idx = _style_ref(el, "fillRef", sch)
                fill, fa = (c, a) if idx > 0 else (None, 1.0)
        # 선
        ln = sppr.find(A + "ln")
        lcol, la, lw, dash = None, 1.0, 1.0, ""
        sc, sa, sidx = _style_ref(el, "lnRef", sch)
        if ln is not None and ln.find(A + "noFill") is not None:
            lcol = None
        elif ln is not None and ln.find(A + "solidFill") is not None:
            lcol, la = _color(ln.find(A + "solidFill"), sch)
        elif sidx > 0:
            lcol, la = sc, sa
        elif conn or prst == "line":
            lcol = "#000000"
        if ln is not None and ln.get("w"):
            lw = max(0.5, _f(ln.get("w")) / EMU)
        elif sidx > 0 or conn:
            lw = max(1.0, 0.75 * sidx + 0.5)
        pd = ln.find(A + "prstDash") if ln is not None else None
        if pd is not None and (pd.get("val") or "solid") != "solid":
            dash = " stroke-dasharray='%.1f %.1f'" % (4 * lw, 3 * lw)
        he = ln.find(A + "headEnd") if ln is not None else None
        te = ln.find(A + "tailEnd") if ln is not None else None
        head = he is not None and (he.get("type") or "none") != "none"
        tail = te is not None and (te.get("type") or "none") != "none"
        fc, _, fidx = _style_ref(el, "fontRef", sch)
        tcol = fc or "#000000"
        rot = t["rot"]
        k = el.find(P + "nvSpPr/" + P + "cNvSpPr") if not conn else None
        is_tx = k is not None and k.get("txBox") == "1"
        label = ""
        if conn or prst in ("line", "straightConnector1") or prst.startswith(("bentConnector", "curvedConnector")):
            x1, y1, x2, y2 = (x + w, y, x, y + h) if t["fh"] and not t["fv"] else (x, y + h, x + w, y) if t["fv"] and not t["fh"] else (x + w, y + h, x, y) if t["fh"] and t["fv"] else (x, y, x + w, y + h)
            col = lcol or "#000000"
            if prst.startswith("bentConnector") and abs(w) > 1 and abs(h) > 1:
                mx = (x1 + x2) / 2
                d = "M%.1f,%.1f L%.1f,%.1f L%.1f,%.1f L%.1f,%.1f" % (x1, y1, mx, y1, mx, y2, x2, y2)
                a0, a1 = (mx, y1), (mx, y2)
            elif prst.startswith("curvedConnector"):
                mx = (x1 + x2) / 2
                d = "M%.1f,%.1f C%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (x1, y1, mx, y1, mx, y2, x2, y2)
                a0, a1 = (mx, y1), (mx, y2)
            else:
                d = "M%.1f,%.1f L%.1f,%.1f" % (x1, y1, x2, y2)
                a0, a1 = (x2, y2), (x1, y1)
            g = "<path d='%s' fill='none' stroke='%s' stroke-width='%.1f'%s/>" % (d, col, lw, dash)
            if tail:
                g += _arrowhead(a1[0], a1[1], x2, y2, lw, col)
            if head:
                g += _arrowhead(a0[0], a0[1], x1, y1, lw, col)
            if rot:
                g = "<g transform='rotate(%.2f %.1f %.1f)'>%s</g>" % (rot, x + w / 2, y + h / 2, g)
            svg.append(g)
            kind = "화살표" if (head or tail) else "선"
            frm, to = ((x2, y2), (x1, y1)) if head and not tail else ((x1, y1), (x2, y2))
            items.append("- %s%s (%d%%, %d%%) → (%d%%, %d%%)%s%s" % (kind, " (양쪽)" if head and tail else "", round(100 * (frm[0] - FX) / FW), round(100 * (frm[1] - FY) / FH),
                                                                round(100 * to[0] / W), round(100 * to[1] / H), " · 점선" if dash else "", " · 색 " + col if col.upper() != "#000000" else ""))
        else:
            if prst == "custom":
                d = _cust(sppr, x, y, w, h)
                body = "<path d='%s'" % d if d else None
            else:
                body = _geom(prst, x, y, w, h)
            known = body is not None
            if body is None:
                body = "<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f'" % (x, y, w, h)
            att = " fill='%s'%s" % (fill, " fill-opacity='%.2f'" % fa if fa < 0.99 else "") if fill else " fill='none'"
            if lcol:
                att += " stroke='%s' stroke-width='%.1f'%s%s" % (lcol, lw, " stroke-opacity='%.2f'" % la if la < 0.99 else "", dash)
            elif not fill and not known:
                att += " stroke='#999' stroke-dasharray='4 3'"
            tr = []
            if rot:
                tr.append("rotate(%.2f %.1f %.1f)" % (rot, x + w / 2, y + h / 2))
            if t["fh"] or t["fv"]:
                tr.append("translate(%.1f %.1f) scale(%d %d) translate(%.1f %.1f)" % (x + w / 2, y + h / 2, -1 if t["fh"] else 1, -1 if t["fv"] else 1, -(x + w / 2), -(y + h / 2)))
            if fill or lcol or not known:
                svg.append(body + att + ((" transform='%s'" % " ".join(tr)) if tr else "") + "/>")
            if (tail or head) and prst == "custom":   # 화살촉 달린 자유 곡선 — 끝의 두 점 방향으로
                pts = re.findall(r"(-?[\d.]+),(-?[\d.]+)", _cust(sppr, x, y, w, h))
                if len(pts) >= 2:
                    (ax, ay), (bx, by) = [tuple(map(float, p)) for p in pts[-2:]]
                    svg.append(_arrowhead(ax, ay, bx, by, lw, lcol or "#000000"))
            s, label = _text(el, x, y, w, h, rot, sch, tcol if (fill and not is_tx) else (tcol if fidx and fc and not is_tx else "#000000"))
            svg.append(s)
            if fill or lcol or label:
                name = "글" if not fill and not lcol else _GEOM_KO.get(prst, prst)
                bits = [where(x, y, w, h)]
                if fill:
                    bits.append("채움 " + fill)
                if lcol and not is_tx:
                    bits.append("테두리 " + lcol + (" 점선" if dash else ""))
                if rot:
                    bits.append("회전 %d°" % round(rot))
                bare = not fill and not lcol
                # 채운 상자 위에 같은 자리의 글상자를 따로 얹은 꼴(흔하다) — 목록에서는 한 줄로
                prev = next((it for it in reversed(items) if isinstance(it, dict) and not it["label"] and
                             max(abs(a - b) for a, b in zip(it["box"], (x, y, w, h))) < 3), None) if bare and label else None
                if prev is not None:
                    prev["label"] = label
                else:
                    items.append({"name": name, "box": (x, y, w, h), "bits": bits, "label": label})

    walk(tree, _Xf())
    # 같은 꼴·같은 색의 글 없는 조각이 많으면(결정립처럼 잘게 그린 것) 한 줄로 — 목록이 조각으로 차서 글이 밀려나지 않게
    groups = {}
    for it in items:
        if isinstance(it, dict) and not it["label"]:
            groups.setdefault((it["name"], tuple(b for b in it["bits"][1:])), []).append(it)
    out_items, done = [], set()
    for it in items:
        if isinstance(it, str):
            out_items.append(it); continue
        key = (it["name"], tuple(b for b in it["bits"][1:]))
        g = groups.get(key) if not it["label"] else None
        if g and len(g) >= 12:
            if key in done:
                continue
            done.add(key)
            x0 = min(b["box"][0] for b in g); y0 = min(b["box"][1] for b in g)
            x1 = max(b["box"][0] + b["box"][2] for b in g); y1 = max(b["box"][1] + b["box"][3] for b in g)
            out_items.append("- %s 조각 %d개(잘게 나눠 그린 것 — 결정립·무늬 따위) · 모인 자리 %s%s" % (
                it["name"], len(g), where(x0, y0, x1 - x0, y1 - y0), (" · " + " · ".join(key[1])) if key[1] else ""))
            continue
        out_items.append("- " + " · ".join([it["name"]] + it["bits"] + (['글: "%s"' % it["label"][:160]] if it["label"] else [])))
    items = out_items
    body = "".join(svg)
    out = ("<svg xmlns='http://www.w3.org/2000/svg' width='%d' height='%d' viewBox='%.1f %.1f %.1f %.1f' font-family='%s'>"
           "<rect x='%.1f' y='%.1f' width='%.1f' height='%.1f' fill='#ffffff'/>%s</svg>" % (round(FW * scale), round(FH * scale), FX, FY, FW, FH, FONT, FX, FY, FW, FH, body))
    return out, items, (tuple(bb) if bb[2] > bb[0] else None)


def read(path, max_slides=3, width=1600):
    """.pptx → [{n, svg, w, h, outline}] — 도형이나 그림이 있는 쪽만, 앞에서부터 max_slides 쪽."""
    z = zipfile.ZipFile(path)
    pres = ET.fromstring(z.read("ppt/presentation.xml"))
    prel = _rels(z, "ppt/presentation.xml")
    sz = pres.find(P + "sldSz")
    W = _f(sz.get("cx"), 12192000) / EMU if sz is not None else 1280.0
    H = _f(sz.get("cy"), 6858000) / EMU if sz is not None else 720.0
    sch = _theme(z, prel)
    out = []
    for i, sid in enumerate(pres.iter(P + "sldId")):
        part = prel.get(sid.get(R + "id"))
        if not part:
            continue
        try:
            _, items, bb = _slide(z, part, W, H, sch, 1.0)
        except (KeyError, ET.ParseError):
            continue
        if not items:
            continue
        # 그린 것이 쪽의 일부에만 있으면 그 부분만 잘라 크게 (초안을 쪽 귀퉁이에 작게 그려 두는 일이 흔하다)
        fr = None
        if bb:
            pad = 0.03 * max(W, H)
            x0, y0 = max(0.0, bb[0] - pad), max(0.0, bb[1] - pad)
            x1, y1 = min(W, bb[2] + pad), min(H, bb[3] + pad)
            if x1 > x0 and y1 > y0 and (x1 - x0) * (y1 - y0) < 0.6 * W * H:
                fr = (x0, y0, x1 - x0, y1 - y0)
        FW, FH = (fr[2], fr[3]) if fr else (W, H)
        scale = min(width / FW, width * 0.75 / FH, 4.0)
        svg, items, _ = _slide(z, part, W, H, sch, scale, fr)
        head = ("PowerPoint 초안 %d쪽 — 그린 부분만 잘라 봄(쪽의 왼쪽 %d~%d%%, 위 %d~%d%%). 아래 %%는 잘라 낸 그림의 폭·높이에 대한 비율, 왼쪽 위가 0" % (
                    i + 1, round(100 * fr[0] / W), round(100 * (fr[0] + fr[2]) / W), round(100 * fr[1] / H), round(100 * (fr[1] + fr[3]) / H)) if fr else
                "PowerPoint 초안 %d쪽 — 쪽 크기 %d×%d (아래 %%는 쪽의 폭·높이에 대한 비율, 왼쪽 위가 0)" % (i + 1, round(W), round(H)))
        out.append({"n": i + 1, "svg": svg, "w": round(FW * scale), "h": round(FH * scale), "outline": head + "\n" + "\n".join(items[:90])})
        if len(out) >= max_slides:
            break
    return out
