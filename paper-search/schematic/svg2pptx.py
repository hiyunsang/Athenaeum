# -*- coding: utf-8 -*-
"""svg2pptx — schematic.lib 이 그린 SVG 를 PowerPoint 도형으로 옮긴다.

그림 한 장을 쪽에 붙이는 것이 아니라 도형·선·화살표·글상자를 하나하나 PowerPoint 에서 고칠 수 있게 한다
(사용자 2026-11-18: '내가 쓴 글을 드래그해서 이거 바탕으로 만들어 달라고 하면 PPT 파일로 만들어 주면 됨').

  convert(svg_text, raster=None) → {"shapes": 도형 XML, "w": EMU, "h": EMU, "media": [그림 파일], "stats": {...}}
  to_pptx(svg_text, path, raster=None, title="") → stats

옮기는 것: rect·circle·ellipse·line·polygon·polyline·path(M L H V C S Q T A Z) → 기본 도형이나 자유형,
text → 글상자(흰 테두리 halo 는 글자 광선 효과로), 그라데이션·빗금 → 그라데이션·무늬 채우기, 점선·끝 모양.
lib 가 <g data-k="…"> 로 표시해 둔 묶음은 뜻을 살린다:
  arrow  → 끝에 화살촉이 달린 선 하나 (촉을 따로 그린 삼각형이 아니라)
  label  → 글이 든 상자 하나
  sub    → 아래 첨자가 든 글상자 하나
  part   → 묶음(그룹) — 공구·칩·치수선처럼 같이 움직일 것
  raster → 그림 한 장 (흐림 필터를 쓴 온도장처럼 도형으로 옮길 수 없는 것; raster 콜백이 PNG 로 그린다)
옮기지 못하는 것(클립·필터·알 수 없는 요소)은 건너뛰고 stats.skipped 에 센다.

단위: SVG 1px = 9525 EMU(96 dpi). 글자 크기 px × 0.75 = pt.
PowerPoint 를 열어 확인한 것이 아니라 규격(DrawingML)대로 쓴 것이다 — 고칠 때는 요소의 차례(spPr: xfrm → 도형 → 채우기 → 선,
ln: 채우기 → 점선 → 이음 → headEnd → tailEnd, rPr: 채우기 → 효과 → latin → ea)를 지킬 것.
"""
import math
import re
import xml.etree.ElementTree as ET

PX = 9525
EA_FONT = "Malgun Gothic"
_NAMED = {"black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF", "gray": "808080", "grey": "808080",
          "yellow": "FFFF00", "orange": "FFA500", "purple": "800080", "brown": "A52A2A", "silver": "C0C0C0", "navy": "000080",
          "lightgray": "D3D3D3", "lightgrey": "D3D3D3", "darkgray": "A9A9A9", "darkgrey": "A9A9A9", "gold": "FFD700", "cyan": "00FFFF",
          "magenta": "FF00FF", "teal": "008080", "maroon": "800000", "olive": "808000", "pink": "FFC0CB", "steelblue": "4682B4",
          "dimgray": "696969", "dimgrey": "696969", "gainsboro": "DCDCDC", "whitesmoke": "F5F5F5", "crimson": "DC143C", "tomato": "FF6347"}
_INHERIT = ("fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "font-family", "font-size",
            "font-weight", "font-style", "text-anchor", "fill-opacity", "stroke-opacity")
I = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _esc(t):
    return str(t if t is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _emu(v):
    return int(round(float(v) * PX))


def _num(v, default=0.0):
    try:
        s = str(v).strip()
        if s.endswith("%"):
            return float(s[:-1]) / 100.0
        return float(re.sub(r"[a-z]+$", "", s))
    except (TypeError, ValueError):
        return default


def _floats(s):
    return [float(x) for x in re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", s or "")]


def _tag(el):
    t = el.tag
    return t.split("}", 1)[1] if isinstance(t, str) and "}" in t else t


# ---------------------------------------------------------------- 행렬
def mmul(m, n):
    """m ∘ n — n 을 먼저, m 을 나중에"""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (a * a2 + c * b2, b * a2 + d * b2, a * c2 + c * d2, b * c2 + d * d2, a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def mapply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def parse_transform(s):
    m = I
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = _floats(args)
        if name == "translate" and v:
            t = (1.0, 0.0, 0.0, 1.0, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            t = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0], 0.0, 0.0)
        elif name == "rotate" and v:
            a = math.radians(v[0])
            t = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0.0, 0.0)
            if len(v) >= 3:
                t = mmul(mmul((1.0, 0.0, 0.0, 1.0, v[1], v[2]), t), (1.0, 0.0, 0.0, 1.0, -v[1], -v[2]))
        elif name == "matrix" and len(v) == 6:
            t = tuple(v)
        else:
            continue
        m = mmul(m, t)
    return m


def _mscale(m):
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0


def _axis(m):
    return abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9


# ---------------------------------------------------------------- 길(path)
def _arc_cubics(x0, y0, rx, ry, phi, fa, fs, x, y):
    """SVG 타원 호 → 3차 베지어들 [(c1, c2, p)]"""
    if rx == 0 or ry == 0 or (x0 == x and y0 == y):
        return [((x0, y0), (x, y), (x, y))]
    rx, ry = abs(rx), abs(ry)
    ph = math.radians(phi)
    cp, sp = math.cos(ph), math.sin(ph)
    dx, dy = (x0 - x) / 2.0, (y0 - y) / 2.0
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        co = -co
    cxp, cyp = co * rx * y1 / ry, -co * ry * x1 / rx
    cx, cy = cp * cxp - sp * cyp + (x0 + x) / 2.0, sp * cxp + cp * cyp + (y0 + y) / 2.0

    def ang(ux, uy, vx, vy):
        d = math.hypot(ux, uy) * math.hypot(vx, vy)
        if not d:
            return 0.0
        a = math.acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / d)))
        return -a if ux * vy - uy * vx < 0 else a
    th1 = ang(1, 0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    dth = ang((x1 - cxp) / rx, (y1 - cyp) / ry, (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not fs and dth > 0:
        dth -= 2 * math.pi
    elif fs and dth < 0:
        dth += 2 * math.pi
    n = max(1, int(math.ceil(abs(dth) / (math.pi / 2.0) - 1e-9)))
    out, d = [], dth / n
    t = 4.0 / 3.0 * math.tan(d / 4.0)

    def pt(a):
        return (cx + rx * math.cos(a) * cp - ry * math.sin(a) * sp, cy + rx * math.cos(a) * sp + ry * math.sin(a) * cp)

    def der(a):
        return (-rx * math.sin(a) * cp - ry * math.cos(a) * sp, -rx * math.sin(a) * sp + ry * math.cos(a) * cp)
    a0 = th1
    for _ in range(n):
        a1 = a0 + d
        p0, p1 = pt(a0), pt(a1)
        d0, d1 = der(a0), der(a1)
        out.append(((p0[0] + t * d0[0], p0[1] + t * d0[1]), (p1[0] - t * d1[0], p1[1] - t * d1[1]), p1))
        a0 = a1
    out[-1] = (out[-1][0], out[-1][1], (x, y))
    return out


_PTOK = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")
_PARGS = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7, "z": 0}


def parse_path(d):
    """→ [{"start": (x, y), "segs": [("L", p) | ("C", c1, c2, p) | ("Q", c, p)], "closed": bool}] (절대 좌표)"""
    toks = [(a, b) for a, b in _PTOK.findall(d or "")]
    subs, cur = [], None
    x = y = sx = sy = 0.0
    last_c = last_q = None
    i, cmd = 0, None
    while i < len(toks):
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
            if cmd in "Zz":
                if cur is not None:
                    cur["closed"] = True
                    x, y = sx, sy
                    cur = None
                last_c = last_q = None
            continue
        if cmd is None or cmd in "Zz":
            i += 1
            continue
        n = _PARGS[cmd.lower()]
        if i + n > len(toks) or any(t[0] for t in toks[i:i + n]):
            i += 1
            continue
        v = [float(t[1]) for t in toks[i:i + n]]
        i += n
        rel = cmd.islower()
        c = cmd.lower()
        if c == "m":
            x, y = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            sx, sy = x, y
            cur = {"start": (x, y), "segs": [], "closed": False}
            subs.append(cur)
            cmd = "l" if rel else "L"
            last_c = last_q = None
            continue
        if cur is None:
            cur = {"start": (x, y), "segs": [], "closed": False}
            subs.append(cur)
            sx, sy = x, y
        if c == "l":
            x, y = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            cur["segs"].append(("L", (x, y)))
            last_c = last_q = None
        elif c == "h":
            x = x + v[0] if rel else v[0]
            cur["segs"].append(("L", (x, y)))
            last_c = last_q = None
        elif c == "v":
            y = y + v[0] if rel else v[0]
            cur["segs"].append(("L", (x, y)))
            last_c = last_q = None
        elif c in "cs":
            if c == "c":
                c1 = (x + v[0], y + v[1]) if rel else (v[0], v[1])
                v = v[2:]
            else:
                c1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
            c2 = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            p = (x + v[2], y + v[3]) if rel else (v[2], v[3])
            cur["segs"].append(("C", c1, c2, p))
            x, y = p
            last_c, last_q = c2, None
        elif c in "qt":
            if c == "q":
                q = (x + v[0], y + v[1]) if rel else (v[0], v[1])
                v = v[2:]
            else:
                q = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
            p = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            cur["segs"].append(("Q", q, p))
            x, y = p
            last_q, last_c = q, None
        elif c == "a":
            p = (x + v[5], y + v[6]) if rel else (v[5], v[6])
            for c1, c2, pe in _arc_cubics(x, y, v[0], v[1], v[2], int(v[3]) != 0, int(v[4]) != 0, p[0], p[1]):
                cur["segs"].append(("C", c1, c2, pe))
            x, y = p
            last_c = last_q = None
    return [s for s in subs if s["segs"]]


def _poly_subs(points, closed):
    v = _floats(points)
    pts = [(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]
    if len(pts) < 2:
        return []
    return [{"start": pts[0], "segs": [("L", p) for p in pts[1:]], "closed": closed}]


def _ellipse_subs(cx, cy, rx, ry):
    k = 0.5522847498
    return [{"start": (cx + rx, cy), "closed": True, "segs": [
        ("C", (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry)), ("C", (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy)),
        ("C", (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry)), ("C", (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy))]}]


def _tsubs(subs, m):
    out = []
    for s in subs:
        segs = []
        for g in s["segs"]:
            segs.append((g[0],) + tuple(mapply(m, p[0], p[1]) for p in g[1:]))
        out.append({"start": mapply(m, *s["start"]), "segs": segs, "closed": s["closed"]})
    return out


def _subs_bbox(subs):
    xs, ys = [], []
    for s in subs:
        xs.append(s["start"][0])
        ys.append(s["start"][1])
        for g in s["segs"]:
            for p in g[1:]:
                xs.append(p[0])
                ys.append(p[1])
    return (min(xs), min(ys), max(xs), max(ys))


def _flatten(sub, n=14):
    """곡선을 잘게 나눈 점 목록"""
    pts = [sub["start"]]
    for g in sub["segs"]:
        p0 = pts[-1]
        if g[0] == "L":
            pts.append(g[1])
            continue
        for i in range(1, n + 1):
            t = i / float(n)
            u = 1.0 - t
            if g[0] == "Q":
                c, p = g[1], g[2]
                pts.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p[1]))
            else:
                c1, c2, p = g[1], g[2], g[3]
                pts.append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p[0],
                            u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p[1]))
    return pts


def _clip_poly(pts, r):
    """다각형을 사각형 r = (x0, y0, x1, y1) 로 자른다 (Sutherland–Hodgman)"""
    def cut(pts, inside, inter):
        out = []
        for i in range(len(pts)):
            a, b = pts[i - 1], pts[i]
            ia, ib = inside(a), inside(b)
            if ia and ib:
                out.append(b)
            elif ia and not ib:
                out.append(inter(a, b))
            elif not ia and ib:
                out.append(inter(a, b))
                out.append(b)
        return out

    def ix(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / ((b[0] - a[0]) or 1e-12))

    def iy(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / ((b[1] - a[1]) or 1e-12), y)
    for inside, inter in ((lambda p: p[0] >= r[0], ix(r[0])), (lambda p: p[0] <= r[2], ix(r[2])), (lambda p: p[1] >= r[1], iy(r[1])), (lambda p: p[1] <= r[3], iy(r[3]))):
        if len(pts) < 3:
            return []
        pts = cut(pts, inside, inter)
    out = []
    for p in pts:
        if not out or abs(out[-1][0] - p[0]) > 1e-6 or abs(out[-1][1] - p[1]) > 1e-6:
            out.append(p)
    return out


def _clip_seg(a, b, r):
    """선분을 사각형으로 자른다 (Liang–Barsky) → (a', b') 또는 None"""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - r[0]), (dx, r[2] - a[0]), (-dy, a[1] - r[1]), (dy, r[3] - a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return None
            t0 = max(t0, t)
        else:
            if t < t0:
                return None
            t1 = min(t1, t)
    return (a[0] + t0 * dx, a[1] + t0 * dy), (a[0] + t1 * dx, a[1] + t1 * dy)


def _inside(bb, r, tol=0.75):
    return bb[0] >= r[0] - tol and bb[1] >= r[1] - tol and bb[2] <= r[2] + tol and bb[3] <= r[3] + tol


def _bb_union(bbs):
    bbs = [b for b in bbs if b]
    if not bbs:
        return None
    return (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))


# ---------------------------------------------------------------- 색
def _color(c):
    """'#abc'·'#aabbcc'·'rgb(…)'·이름 → 'AABBCC' (모르면 None)"""
    c = (c or "").strip().lower()
    if not c or c in ("none", "transparent"):
        return None
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) >= 6 and re.match(r"^[0-9a-f]{6}", h):
            return h[:6].upper()
        return None
    m = re.match(r"rgba?\(([^)]*)\)", c)
    if m:
        v = [x.strip() for x in m.group(1).split(",")]
        try:
            rgb = [int(round(float(x[:-1]) * 2.55)) if x.endswith("%") else int(float(x)) for x in v[:3]]
            return "%02X%02X%02X" % tuple(max(0, min(255, k)) for k in rgb)
        except ValueError:
            return None
    return _NAMED.get(c)


def _clr(hexv, alpha=1.0):
    a = max(0.0, min(1.0, float(alpha)))
    if a < 0.999:
        return '<a:srgbClr val="%s"><a:alpha val="%d"/></a:srgbClr>' % (hexv, int(round(a * 100000)))
    return '<a:srgbClr val="%s"/>' % hexv


class Conv(object):
    def __init__(self, raster=None):
        self.raster = raster
        self.defs = {}
        self.media = []
        self.sid = 1
        self.stats = {"shapes": 0, "texts": 0, "lines": 0, "arrows": 0, "groups": 0, "pictures": 0, "skipped": 0, "notes": []}
        self.w = self.h = 0.0
        self._halo = None
        self._solid = {}       # 도형의 꼴 → 바로 앞에 같은 꼴로 칠한 색 (빗금 무늬의 바탕색으로)
        self._svg_defs = ""
        self._clip = None      # 지금 걸려 있는 클립 사각형(패널) — 캔버스 좌표

    # ---------- 공통
    def nid(self):
        self.sid += 1
        return self.sid

    def note(self, s):
        if s not in self.stats["notes"]:
            self.stats["notes"].append(s)

    def props(self, el, inh):
        p = dict(inh)
        own = {}
        for k in _INHERIT:
            if k in el.attrib:
                own[k] = el.attrib[k]
        for part in (el.attrib.get("style") or "").split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                own[k.strip()] = v.strip()
        p.update(own)
        return p, own

    def fill_xml(self, paint, alpha, key=None):
        paint = (paint if paint is not None else "#000").strip()
        if paint in ("none", "transparent", ""):
            return "<a:noFill/>"
        m = re.match(r"url\(\s*['\"]?#([^)'\"\s]+)", paint)
        if m:
            d = self.defs.get(m.group(1))
            t = _tag(d) if d is not None else ""
            if t in ("linearGradient", "radialGradient"):
                return self.grad_xml(d, alpha)
            if t == "pattern":
                return self.patt_xml(d, alpha, key)
            self.note("알 수 없는 채우기(%s)는 비워 두었습니다" % m.group(1))
            return "<a:noFill/>"
        c = _color(paint)
        if c is None:
            return "<a:noFill/>"
        if key is not None:
            self._solid[key] = c
        return "<a:solidFill>%s</a:solidFill>" % _clr(c, alpha)

    def grad_xml(self, d, alpha):
        stops = []
        for s in d:
            if _tag(s) != "stop":
                continue
            st = dict(s.attrib)
            for part in (s.attrib.get("style") or "").split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    st[k.strip()] = v.strip()
            c = _color(st.get("stop-color") or "#000") or "000000"
            stops.append((max(0.0, min(1.0, _num(st.get("offset"), 0.0))), c, _num(st.get("stop-opacity"), 1.0) * alpha))
        if not stops:
            return "<a:noFill/>"
        if len(stops) == 1:
            return "<a:solidFill>%s</a:solidFill>" % _clr(stops[0][1], stops[0][2])
        stops.sort(key=lambda s: s[0])
        gs = "".join('<a:gs pos="%d">%s</a:gs>' % (int(round(o * 100000)), _clr(c, a)) for o, c, a in stops)
        if _tag(d) == "linearGradient":
            x1, y1 = _num(d.attrib.get("x1"), 0.0), _num(d.attrib.get("y1"), 0.0)
            x2, y2 = _num(d.attrib.get("x2"), 1.0), _num(d.attrib.get("y2"), 0.0)
            ang = int(round(math.degrees(math.atan2(y2 - y1, x2 - x1)) * 60000)) % 21600000
            return '<a:gradFill rotWithShape="1"><a:gsLst>%s</a:gsLst><a:lin ang="%d" scaled="1"/></a:gradFill>' % (gs, ang)
        cx, cy = _num(d.attrib.get("cx"), 0.5), _num(d.attrib.get("cy"), 0.5)
        pc = lambda v: int(round(max(0.0, min(1.0, v)) * 100000))
        return ('<a:gradFill rotWithShape="1"><a:gsLst>%s</a:gsLst><a:path path="circle"><a:fillToRect l="%d" t="%d" r="%d" b="%d"/></a:path></a:gradFill>'
                % (gs, pc(cx), pc(cy), pc(1 - cx), pc(1 - cy)))

    def patt_xml(self, d, alpha, key):
        ang = 0.0
        m = re.search(r"rotate\(\s*(-?[\d.]+)", d.attrib.get("patternTransform") or "")
        if m:
            ang = float(m.group(1))
        col, sp = "555555", _num(d.attrib.get("width"), 8.0)
        for ch in d.iter():
            c = _color(ch.attrib.get("stroke") or "")
            if c:
                col = c
                break
        a = ang % 180.0
        wide = sp >= 7
        if a < 22.5 or a >= 157.5:
            prst = "ltVert" if wide else "narVert"
        elif a < 67.5:
            prst = "wdUpDiag" if wide else "ltUpDiag"
        elif a < 112.5:
            prst = "ltHorz" if wide else "narHorz"
        else:
            prst = "wdDnDiag" if wide else "ltDnDiag"
        bg = self._solid.get(key) if key is not None else None
        bgx = _clr(bg, 1.0) if bg else _clr("FFFFFF", 0.0)
        return '<a:pattFill prst="%s"><a:fgClr>%s</a:fgClr><a:bgClr>%s</a:bgClr></a:pattFill>' % (prst, _clr(col, alpha), bgx)

    def ln_xml(self, p, alpha, scale=1.0, head=None, tail=None, force_cap=None):
        stroke = (p.get("stroke") or "none").strip()
        c = _color(stroke)
        if c is None and stroke.startswith("url("):
            m = re.match(r"url\(\s*['\"]?#([^)'\"\s]+)", stroke)
            d = self.defs.get(m.group(1)) if m else None
            if d is not None:
                for ch in d.iter():
                    c = _color(ch.attrib.get("stop-color") or ch.attrib.get("stroke") or "")
                    if c:
                        break
        if c is None:
            return "<a:ln><a:noFill/></a:ln>"
        sw = max(0.1, _num(p.get("stroke-width"), 1.0) * scale)
        a = alpha * _num(p.get("stroke-opacity"), 1.0)
        cap = {"round": "rnd", "square": "sq"}.get(force_cap or (p.get("stroke-linecap") or "").strip(), "flat")
        x = '<a:ln w="%d" cap="%s"><a:solidFill>%s</a:solidFill>' % (max(1, _emu(sw)), cap, _clr(c, a))
        dash = _floats(p.get("stroke-dasharray") or "") if (p.get("stroke-dasharray") or "none") != "none" else []
        if dash and sum(dash) > 0:
            if len(dash) % 2:
                dash = dash * 2
            x += "<a:custDash>" + "".join('<a:ds d="%d" sp="%d"/>' % (max(1000, int(round(dash[k] * scale / sw * 100000))),
                                                                       max(1000, int(round(dash[k + 1] * scale / sw * 100000))))
                                           for k in range(0, len(dash), 2)) + "</a:custDash>"
        join = (p.get("stroke-linejoin") or "miter").strip()
        x += "<a:round/>" if join == "round" else ("<a:bevel/>" if join == "bevel" else '<a:miter lim="800000"/>')
        if head:
            x += '<a:headEnd type="triangle" w="%s" len="%s"/>' % (head, head)
        if tail:
            x += '<a:tailEnd type="triangle" w="%s" len="%s"/>' % (tail, tail)
        return x + "</a:ln>"

    # ---------- 도형 쓰기
    def geom_xml(self, subs, bb, filled):
        x0, y0 = bb[0], bb[1]
        W, H = max(1, _emu(bb[2] - bb[0])), max(1, _emu(bb[3] - bb[1]))

        def pt(p):
            return '<a:pt x="%d" y="%d"/>' % (max(0, min(W, _emu(p[0] - x0))), max(0, min(H, _emu(p[1] - y0))))
        paths = []
        for s in subs:
            x = "<a:moveTo>%s</a:moveTo>" % pt(s["start"])
            for g in s["segs"]:
                if g[0] == "L":
                    x += "<a:lnTo>%s</a:lnTo>" % pt(g[1])
                elif g[0] == "Q":
                    x += "<a:quadBezTo>%s%s</a:quadBezTo>" % (pt(g[1]), pt(g[2]))
                else:
                    x += "<a:cubicBezTo>%s%s%s</a:cubicBezTo>" % (pt(g[1]), pt(g[2]), pt(g[3]))
            if s["closed"]:
                x += "<a:close/>"
            paths.append(x)
        return ('<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/><a:rect l="0" t="0" r="r" b="b"/><a:pathLst><a:path w="%d" h="%d"%s>%s</a:path></a:pathLst></a:custGeom>'
                % (W, H, "" if filled else ' fill="none"', "".join(paths)))

    def sp_xml(self, name, bb, geom, fill, ln, body="", rot=0, txbox=False):
        i = self.nid()
        xf = '<a:xfrm%s><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>' % (
            (' rot="%d"' % rot) if rot else "", _emu(bb[0]), _emu(bb[1]), max(1, _emu(bb[2] - bb[0])), max(1, _emu(bb[3] - bb[1])))
        return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s %d"/><p:cNvSpPr%s/><p:nvPr/></p:nvSpPr><p:spPr>%s%s%s%s</p:spPr>%s</p:sp>'
                % (i, _esc(name), i, ' txBox="1"' if txbox else "", xf, geom, fill, ln,
                   body or '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody>'))

    def line_xml(self, name, p1, p2, ln):
        i = self.nid()
        x0, y0, x1, y1 = min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]), max(p1[1], p2[1])
        fl = (' flipH="1"' if p2[0] < p1[0] else "") + (' flipV="1"' if p2[1] < p1[1] else "")
        return ('<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="%d" name="%s %d"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr><p:spPr>'
                '<a:xfrm%s><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom>%s</p:spPr></p:cxnSp>'
                % (i, _esc(name), i, fl, _emu(x0), _emu(y0), _emu(x1 - x0), _emu(y1 - y0), ln)), (x0, y0, x1, y1)

    def grp_xml(self, name, items):
        items = [it for it in items if it]
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        bb = _bb_union([it[1] for it in items]) or (0, 0, 1, 1)
        i = self.nid()
        ox, oy, cx, cy = _emu(bb[0]), _emu(bb[1]), max(1, _emu(bb[2] - bb[0])), max(1, _emu(bb[3] - bb[1]))
        self.stats["groups"] += 1
        return ('<p:grpSp><p:nvGrpSpPr><p:cNvPr id="%d" name="%s %d"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm>'
                '<a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/><a:chOff x="%d" y="%d"/><a:chExt cx="%d" cy="%d"/></a:xfrm></p:grpSpPr>%s</p:grpSp>'
                % (i, _esc(name), i, ox, oy, cx, cy, ox, oy, cx, cy, "".join(it[0] for it in items)), bb, "grp")

    # ---------- 글
    def run_xml(self, t, size_px, color, alpha, bold, italic, family, halo=None, sub=False):
        sz = max(100, int(round(size_px * 75)))
        a = ' lang="en-US" altLang="ko-KR" sz="%d"%s%s%s dirty="0"' % (sz, ' b="1"' if bold else "", ' i="1"' if italic else "", ' baseline="-25000"' if sub else "")
        x = "<a:rPr%s><a:solidFill>%s</a:solidFill>" % (a, _clr(color, alpha))
        if halo:
            x += '<a:effectLst><a:glow rad="%d">%s</a:glow></a:effectLst>' % (max(6350, _emu(halo[1])), _clr(halo[0], 0.92))
        fam = (family or "Arial").split(",")[0].strip().strip("'\"") or "Arial"
        x += '<a:latin typeface="%s"/><a:ea typeface="%s"/></a:rPr>' % (_esc(fam), EA_FONT)
        return '<a:r>%s<a:t>%s</a:t></a:r>' % (x, _esc(t))

    def text_box(self, x, y, runs, size, anchor, m, name="글"):
        """runs = [run_xml 글], (x, y) = 글의 밑줄 기준점 · anchor start|middle|end"""
        plain = "".join(r[1] for r in runs)
        s = _mscale(m)
        size_px = size * s
        px, py = mapply(m, x, y)
        th = math.atan2(m[1], m[0])
        cjk = any(ord(ch) > 0x2E7F for ch in plain)
        w = sum((1.0 if ord(ch) > 0x2E7F else 0.56) * size_px for ch in plain) * 1.06 + 4
        asc, lh = (1.09, 1.34) if cjk else (0.905, 1.15)
        h = lh * size_px
        bx = px - w / 2.0 if anchor == "middle" else (px - w if anchor == "end" else px)
        by = py - asc * size_px
        rot = 0
        if abs(th) > 1e-6:
            cx, cy = bx + w / 2.0 - px, by + h / 2.0 - py
            c1 = (px + cx * math.cos(th) - cy * math.sin(th), py + cx * math.sin(th) + cy * math.cos(th))
            bx, by = c1[0] - w / 2.0, c1[1] - h / 2.0
            rot = int(round(math.degrees(th) * 60000)) % 21600000
        algn = {"middle": "ctr", "end": "r"}.get(anchor, "l")
        body = ('<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" rtlCol="0" anchor="t"><a:spAutoFit/></a:bodyPr><a:lstStyle/>'
                '<a:p><a:pPr algn="%s"/>%s</a:p></p:txBody>' % (algn, "".join(r[0] for r in runs)))
        bb = (bx, by, bx + w, by + h)
        self.stats["texts"] += 1
        return self.sp_xml(name, bb, '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>', "<a:noFill/>", "", body, rot, True), bb, "text"

    def _text_style(self, p):
        size = _num(p.get("font-size"), 16.0)
        wt = (p.get("font-weight") or "normal").strip()
        bold = wt in ("bold", "bolder") or (wt.isdigit() and int(wt) >= 600)
        return size, bold, (p.get("font-style") or "").strip() == "italic", p.get("font-family") or "Arial"

    def text(self, el, m, p, own, op):
        s = "".join(el.itertext())
        if not s.strip():
            return None
        x, y = _num(el.attrib.get("x")), _num(el.attrib.get("y"))
        size, bold, italic, fam = self._text_style(p)
        st = (own.get("stroke") or "none").strip()
        if st != "none" and _color(st):   # 글자 뒤에 깐 테두리(halo) — 다음에 오는 같은 글의 광선 효과로
            self._halo = (s, x, y, _color(st), _num(own.get("stroke-width"), 2.0) / 2.0 * _mscale(m))
            return None
        halo = None
        if self._halo and self._halo[0] == s and abs(self._halo[1] - x) < 0.01 and abs(self._halo[2] - y) < 0.01:
            halo = (self._halo[3], self._halo[4])
        self._halo = None
        col = _color(p.get("fill") or "#000") or "000000"
        a = op * _num(p.get("fill-opacity"), 1.0) * _num(el.attrib.get("opacity"), 1.0)
        run = (self.run_xml(s, size * _mscale(m), col, a, bold, italic, fam, halo), s)
        tm = mmul(m, parse_transform(el.attrib.get("transform")))
        return self.text_box(x, y, [run], size, (p.get("text-anchor") or "start").strip(), tm, s.strip()[:24] or "글")

    def subtext(self, el, m, inh, op):
        texts = []
        for ch in el.iter():
            if _tag(ch) != "text":
                continue
            p, own = self.props(ch, inh)
            if (own.get("stroke") or "none") != "none":
                continue
            texts.append((ch, p))
        if not texts:
            return None
        ch0, p0 = texts[0]
        size, bold, italic, fam = self._text_style(p0)
        col = _color(p0.get("fill") or "#000") or "000000"
        hc = None
        for ch in el.iter():
            if _tag(ch) == "text" and (ch.attrib.get("stroke") or "none") != "none":
                hc = (_color(ch.attrib.get("stroke")) or "FFFFFF", _num(ch.attrib.get("stroke-width"), 2.0) / 2.0 * _mscale(m))
                break
        sc = _mscale(m)
        runs = [(self.run_xml("".join(ch0.itertext()), size * sc, col, op, bold, italic, fam, hc), "".join(ch0.itertext()))]
        for ch, p in texts[1:]:
            _, b2, i2, f2 = self._text_style(p)
            t = "".join(ch.itertext())
            runs.append((self.run_xml(t, size * sc, _color(p.get("fill") or "#000") or col, op, b2, i2, f2, hc, sub=True), t[:max(1, int(len(t) * 0.7))]))
        x, y = _num(el.attrib.get("data-x"), _num(ch0.attrib.get("x"))), _num(el.attrib.get("data-y"), _num(ch0.attrib.get("y")))
        self._halo = None
        return self.text_box(x, y, runs, size, el.attrib.get("data-anchor") or "start", m, "".join(r[1] for r in runs)[:24] or "글")

    def label(self, el, m, inh, op):
        r = t = None
        for ch in el:
            tg = _tag(ch)
            if tg == "rect" and r is None:
                r = ch
            elif tg == "text" and (ch.attrib.get("stroke") or "none") == "none":
                t = ch
        if r is None or t is None or not _axis(m):
            return None
        pr, _ = self.props(r, inh)
        pt_, _ = self.props(t, inh)
        x, y = mapply(m, _num(r.attrib.get("x")), _num(r.attrib.get("y")))
        w, h = _num(r.attrib.get("width")) * m[0], _num(r.attrib.get("height")) * m[3]
        bb = (min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h))
        rx = _num(r.attrib.get("rx"), 0.0) * abs(m[0])
        if rx > 0:
            adj = int(round(min(0.5, rx / max(1e-6, min(abs(w), abs(h)))) * 100000))
            geom = '<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val %d"/></a:avLst></a:prstGeom>' % adj
        else:
            geom = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        size, bold, italic, fam = self._text_style(pt_)
        s = "".join(t.itertext())
        run = self.run_xml(s, size * _mscale(m), _color(pt_.get("fill") or "#000") or "000000", op, bold, italic, fam)
        body = ('<p:txBody><a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" rtlCol="0" anchor="ctr"/><a:lstStyle/>'
                '<a:p><a:pPr algn="ctr"/>%s</a:p></p:txBody>' % run)
        self.stats["shapes"] += 1
        return self.sp_xml(s.strip()[:24] or "상자", bb, geom, self.fill_xml(pr.get("fill"), op * _num(pr.get("fill-opacity"), 1.0)),
                           self.ln_xml(pr, op, _mscale(m)), body), bb, "shape"

    # ---------- 화살표
    def arrow(self, el, m, inh, op):
        v = _floats(el.attrib.get("data-p") or "")
        shaft = next((ch for ch in el if _tag(ch) == "path"), None)
        if len(v) != 6 or shaft is None:
            return None
        p, _ = self.props(shaft, inh)
        p1, c, p2 = mapply(m, v[0], v[1]), mapply(m, v[2], v[3]), mapply(m, v[4], v[5])
        sw = max(0.1, _num(p.get("stroke-width"), 2.0))
        r = _num(el.attrib.get("data-head"), 12.0) / sw
        size = "sm" if r < 2.6 else ("med" if r < 4.3 else "lg")
        both = el.attrib.get("data-both") == "1"
        ln = self.ln_xml(p, op, _mscale(m), head=size if both else None, tail=size, force_cap="round")
        self.stats["arrows"] += 1
        mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
        if math.hypot(c[0] - mid[0], c[1] - mid[1]) < 0.6:
            x, bb = self.line_xml("화살표", p1, p2, ln)
            return x, bb, "line"
        subs = [{"start": p1, "segs": [("Q", c, p2)], "closed": False}]
        bb = _subs_bbox(subs)
        return self.sp_xml("화살표", bb, self.geom_xml(subs, bb, False), "<a:noFill/>", ln), bb, "line"

    # ---------- 그림으로 남기는 것
    def raster_group(self, el, m, op):
        if self.raster is None:
            self.stats["skipped"] += 1
            self.note("흐림·필터를 쓴 부분은 옮기지 못했습니다")
            return None
        bb = None
        for g in el.iter():
            cm = re.match(r"url\(\s*['\"]?#([^)'\"\s]+)", g.attrib.get("clip-path") or "")
            cd = self.defs.get(cm.group(1)) if cm else None
            if cd is not None:
                for r in cd:
                    if _tag(r) == "rect":
                        x, y, w, h = [_num(r.attrib.get(k)) for k in ("x", "y", "width", "height")]
                        a, b = mapply(m, x, y), mapply(m, x + w, y + h)
                        bb = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
                break
        if bb is None:
            probe = Conv(None)
            probe.defs = self.defs
            items = probe.children(el, m, {}, 1.0, flat=True)
            bb = _bb_union([it[1] for it in items])
        if not bb or bb[2] - bb[0] < 1 or bb[3] - bb[1] < 1:
            return None
        bb = (max(0.0, bb[0]), max(0.0, bb[1]), min(self.w, bb[2]), min(self.h, bb[3]))
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        frag = ET.tostring(el, encoding="unicode")
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="%.2f" height="%.2f" viewBox="%.2f %.2f %.2f %.2f">%s<g transform="matrix(%s)">%s</g></svg>'
               % (w, h, bb[0], bb[1], w, h, self._svg_defs, " ".join("%.6g" % k for k in m), frag))
        try:
            png = self.raster(svg, int(math.ceil(w)), int(math.ceil(h)))
        except Exception as e:
            png = None
            self.note("그림으로 옮기지 못한 부분이 있습니다 (%s)" % str(e)[:60])
        if not png:
            self.stats["skipped"] += 1
            return None
        self.media.append(png)
        rid = "rId%d" % (len(self.media) + 1)
        i = self.nid()
        a = ('<a:alphaModFix amt="%d"/>' % int(round(op * 100000))) if op < 0.999 else ""
        self.stats["pictures"] += 1
        return ('<p:pic><p:nvPicPr><p:cNvPr id="%d" name="그림 %d"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
                '<p:blipFill><a:blip r:embed="%s">%s</a:blip><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
                '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
                % (i, i, rid, a, _emu(bb[0]), _emu(bb[1]), _emu(w), _emu(h))), bb, "pic"

    # ---------- 도형 하나
    def _geom_key(self, el):
        t = _tag(el)
        if t in ("polygon", "polyline"):
            return (t, " ".join((el.attrib.get("points") or "").split()))
        if t == "path":
            return (t, " ".join((el.attrib.get("d") or "").split()))
        if t == "rect":
            return (t,) + tuple(el.attrib.get(k, "0") for k in ("x", "y", "width", "height", "rx"))
        if t in ("circle", "ellipse"):
            return (t,) + tuple(el.attrib.get(k, "0") for k in ("cx", "cy", "r", "rx", "ry"))
        return None

    def shape(self, el, m, p, op, stroke_from=None):
        """stroke_from: 같은 꼴로 바로 뒤에 온 테두리 요소의 속성 — 칠과 테두리를 한 도형으로"""
        t = _tag(el)
        em = mmul(m, parse_transform(el.attrib.get("transform")))
        op = op * _num(el.attrib.get("opacity"), 1.0)
        key = self._geom_key(el)
        sp = stroke_from if stroke_from is not None else p
        sc = _mscale(em)
        if t == "line":
            p1 = mapply(em, _num(el.attrib.get("x1")), _num(el.attrib.get("y1")))
            p2 = mapply(em, _num(el.attrib.get("x2")), _num(el.attrib.get("y2")))
            if self._clip:
                cs = _clip_seg(p1, p2, self._clip)
                if cs is None:
                    return None
                p1, p2 = cs
            x, bb = self.line_xml("선", p1, p2, self.ln_xml(p, op, sc))
            self.stats["lines"] += 1
            return x, bb, "line"
        fill_paint = p.get("fill") if p.get("fill") is not None else "#000"
        fa = op * _num(p.get("fill-opacity"), 1.0)
        name = "도형"
        preset = t in ("rect", "circle", "ellipse") and _axis(em)
        if preset and t != "rect" and self._clip:
            cx, cy = mapply(em, _num(el.attrib.get("cx")), _num(el.attrib.get("cy")))
            rx = _num(el.attrib.get("r"), _num(el.attrib.get("rx"))) * abs(em[0])
            ry = _num(el.attrib.get("r"), _num(el.attrib.get("ry"))) * abs(em[3])
            preset = _inside((cx - rx, cy - ry, cx + rx, cy + ry), self._clip)   # 패널 밖으로 나가는 원은 잘라야 하니 길로
        if preset:
            if t == "rect":
                x, y = mapply(em, _num(el.attrib.get("x")), _num(el.attrib.get("y")))
                w, h = _num(el.attrib.get("width")) * em[0], _num(el.attrib.get("height")) * em[3]
                bb = (min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h))
                rx = _num(el.attrib.get("rx"), _num(el.attrib.get("ry"), 0.0)) * abs(em[0])
                if self._clip and not _inside(bb, self._clip):
                    c = self._clip
                    bb = (max(bb[0], c[0]), max(bb[1], c[1]), min(bb[2], c[2]), min(bb[3], c[3]))
                    rx = 0
                if rx > 0 and min(bb[2] - bb[0], bb[3] - bb[1]) > 0:
                    adj = int(round(min(0.5, rx / min(bb[2] - bb[0], bb[3] - bb[1])) * 100000))
                    geom = '<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val %d"/></a:avLst></a:prstGeom>' % adj
                else:
                    geom = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
                name = "사각형"
            else:
                cx, cy = mapply(em, _num(el.attrib.get("cx")), _num(el.attrib.get("cy")))
                rx = _num(el.attrib.get("r"), _num(el.attrib.get("rx"))) * abs(em[0])
                ry = _num(el.attrib.get("r"), _num(el.attrib.get("ry"))) * abs(em[3])
                bb = (cx - rx, cy - ry, cx + rx, cy + ry)
                geom = '<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>'
                name = "원"
            if bb[2] - bb[0] <= 0 or bb[3] - bb[1] <= 0:
                return None
            self.stats["shapes"] += 1
            return self.sp_xml(name, bb, geom, self.fill_xml(fill_paint, fa, key), self.ln_xml(sp, op, sc)), bb, "shape"
        if t == "rect":
            x, y, w, h = [_num(el.attrib.get(k)) for k in ("x", "y", "width", "height")]
            subs = [{"start": (x, y), "segs": [("L", (x + w, y)), ("L", (x + w, y + h)), ("L", (x, y + h))], "closed": True}]
        elif t in ("circle", "ellipse"):
            subs = _ellipse_subs(_num(el.attrib.get("cx")), _num(el.attrib.get("cy")), _num(el.attrib.get("r"), _num(el.attrib.get("rx"))),
                                 _num(el.attrib.get("r"), _num(el.attrib.get("ry"))))
        elif t == "polygon":
            subs = _poly_subs(el.attrib.get("points"), True)
        elif t == "polyline":
            subs = _poly_subs(el.attrib.get("points"), False)
        else:
            subs = parse_path(el.attrib.get("d"))
        if not subs:
            return None
        subs = _tsubs(subs, em)
        nofill = (fill_paint or "").strip() in ("none", "transparent", "")
        if self._clip and not _inside(_subs_bbox(subs), self._clip):
            c, cut = self._clip, []
            for sb in subs:
                pts = _flatten(sb)
                if sb["closed"] or not nofill:
                    cp = _clip_poly(pts, c)
                    if len(cp) >= 3:
                        cut.append({"start": cp[0], "segs": [("L", q) for q in cp[1:]], "closed": True})
                    continue
                run = []
                for a, b in zip(pts, pts[1:]):
                    sg = _clip_seg(a, b, c)
                    if sg is not None and run and abs(run[-1][0] - sg[0][0]) < 1e-6 and abs(run[-1][1] - sg[0][1]) < 1e-6:
                        run.append(sg[1])
                        continue
                    if len(run) >= 2:
                        cut.append({"start": run[0], "segs": [("L", q) for q in run[1:]], "closed": False})
                    run = [sg[0], sg[1]] if sg is not None else []
                if len(run) >= 2:
                    cut.append({"start": run[0], "segs": [("L", q) for q in run[1:]], "closed": False})
            subs = cut
            if not subs:
                return None
        if nofill and len(subs) == 1 and len(subs[0]["segs"]) == 1 and subs[0]["segs"][0][0] == "L" and not subs[0]["closed"]:
            x, bb = self.line_xml("선", subs[0]["start"], subs[0]["segs"][0][1], self.ln_xml(sp, op, sc))
            self.stats["lines"] += 1
            return x, bb, "line"
        bb = _subs_bbox(subs)
        self.stats["shapes"] += 1
        closed = all(s["closed"] for s in subs)
        return (self.sp_xml("도형" if closed else "곡선", bb, self.geom_xml(subs, bb, not nofill), self.fill_xml(fill_paint, fa, key), self.ln_xml(sp, op, sc)),
                bb, "shape" if closed else "line")

    # ---------- 훑기
    def children(self, parent, m, inh, op, flat=False):
        items = []
        kids = list(parent)
        k = 0
        while k < len(kids):
            el = kids[k]
            k += 1
            t = _tag(el)
            if not isinstance(t, str):
                continue
            try:
                if t in ("defs", "title", "desc", "metadata", "clipPath", "style"):
                    continue
                p, own = self.props(el, inh)
                if t == "g":
                    kind = el.attrib.get("data-k") or ""
                    gm = mmul(m, parse_transform(el.attrib.get("transform")))
                    gop = op * _num(el.attrib.get("opacity"), 1.0)
                    cm = re.match(r"url\(\s*['\"]?#([^)'\"\s]+)", el.attrib.get("clip-path") or "")
                    cd = self.defs.get(cm.group(1)) if cm else None
                    if cd is not None and kind != "raster" and not el.attrib.get("filter") and _axis(gm):
                        cr = next((r for r in cd if _tag(r) == "rect"), None)
                        if cr is not None:
                            a, b = mapply(gm, _num(cr.attrib.get("x")), _num(cr.attrib.get("y"))), mapply(gm, _num(cr.attrib.get("x")) + _num(cr.attrib.get("width")), _num(cr.attrib.get("y")) + _num(cr.attrib.get("height")))
                            new = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
                            old = self._clip
                            self._clip = new if old is None else (max(old[0], new[0]), max(old[1], new[1]), min(old[2], new[2]), min(old[3], new[3]))
                            try:
                                items.extend(self.children(el, gm, p, gop, flat))
                            finally:
                                self._clip = old
                            continue
                    if kind == "raster" or el.attrib.get("filter"):
                        if flat:
                            items.extend(self.children(el, gm, p, gop, True))
                        else:
                            it = self.raster_group(el, gm, gop)
                            if it is not None:
                                items.append(it)
                        continue
                    it = None
                    if kind == "arrow":
                        it = self.arrow(el, gm, p, gop)
                    elif kind == "label":
                        it = self.label(el, gm, p, gop)
                    elif kind == "sub":
                        it = self.subtext(el, gm, p, gop)
                    if it is not None:
                        items.append(it)
                        continue
                    sub = self.children(el, gm, p, gop, flat)
                    if kind == "part" and not flat:
                        g = self.grp_xml(el.attrib.get("data-name") or "묶음", sub)
                        if g is not None:
                            items.append(g)
                    else:
                        items.extend(sub)
                    continue
                if t == "text":
                    it = self.text(el, m, p, own, op)
                elif t in ("rect", "circle", "ellipse", "line", "polygon", "polyline", "path"):
                    nxt = kids[k] if k < len(kids) else None
                    merged = None
                    if nxt is not None and t != "line" and _tag(nxt) == t and self._geom_key(nxt) == self._geom_key(el) \
                            and not el.attrib.get("transform") and not nxt.attrib.get("transform"):
                        p2, _ = self.props(nxt, inh)
                        if (p.get("stroke") or "none") == "none" and (p.get("fill") or "#000") != "none" and (p2.get("fill") or "#000") == "none" \
                                and (p2.get("stroke") or "none") != "none" and _num(el.attrib.get("opacity"), 1.0) == _num(nxt.attrib.get("opacity"), 1.0):
                            merged = p2
                            k += 1
                    it = self.shape(el, m, p, op, merged)
                else:
                    self.stats["skipped"] += 1
                    self.note("옮기지 못한 요소: <%s>" % t)
                    it = None
                if it is not None:
                    items.append(it)
            except Exception as e:
                self.stats["skipped"] += 1
                self.note("옮기지 못한 요소가 있습니다 (%s: %s)" % (t, str(e)[:50]))
        if flat:
            return items
        # 잘게 쪼갠 조각이 길게 이어지면(결정립·색 조각) 한 묶음으로 — 통째로 옮기고 지우기 쉽게
        out, run = [], []

        def flush():
            if len(run) >= 40:
                g = self.grp_xml("조각 묶음", list(run))
                out.append(g)
            else:
                out.extend(run)
            del run[:]
        for it in items:
            if it[2] == "shape":
                run.append(it)
            else:
                flush()
                out.append(it)
        flush()
        return out

    def convert(self, svg_text):
        root = ET.fromstring(svg_text.encode("utf-8") if isinstance(svg_text, str) else svg_text)
        vb = _floats(root.attrib.get("viewBox") or "")
        self.w = _num(root.attrib.get("width"), vb[2] if len(vb) == 4 else 900.0)
        self.h = _num(root.attrib.get("height"), vb[3] if len(vb) == 4 else 700.0)
        m = I
        if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
            m = (self.w / vb[2], 0.0, 0.0, self.h / vb[3], -vb[0] * self.w / vb[2], -vb[1] * self.h / vb[3])
        ET.register_namespace("", "http://www.w3.org/2000/svg")
        dparts = []
        for el in root.iter():
            if el.attrib.get("id"):
                self.defs[el.attrib["id"]] = el
            if _tag(el) == "defs":
                dparts.append(ET.tostring(el, encoding="unicode"))
        self._svg_defs = "".join(dparts)
        kids = list(root)
        # 쪽 전체를 덮는 흰 바탕은 뺀다 (쪽의 바탕이 흰색이다)
        for el in kids:
            if _tag(el) == "rect" and abs(_num(el.attrib.get("width")) - (vb[2] if len(vb) == 4 else self.w)) < 1 and abs(_num(el.attrib.get("height")) - (vb[3] if len(vb) == 4 else self.h)) < 1 \
                    and (_color(el.attrib.get("fill") or "") in ("FFFFFF", None)) and (el.attrib.get("stroke") or "none") == "none":
                root.remove(el)
                break
            if _tag(el) not in ("defs", "title", "desc", "metadata"):
                break
        items = self.children(root, m, {}, 1.0)
        return {"shapes": "".join(it[0] for it in items), "w": max(914400, _emu(self.w)), "h": max(914400, _emu(self.h)),
                "media": list(self.media), "stats": self.stats}


def convert(svg_text, raster=None):
    return Conv(raster).convert(svg_text)


def to_pptx(svg_text, path, raster=None, title=""):
    """SVG → 한 쪽짜리 .pptx (쪽 크기 = 그림 크기). stats 를 돌려준다."""
    import pptx_min
    r = convert(svg_text, raster)
    pptx_min.build_shapes(path, r["shapes"], r["w"], r["h"], r["media"], title)
    return r["stats"]
