# -*- coding: utf-8 -*-
"""절삭 도식 생성기 시제품: (a) 선삭 거시도 (b) 열적 관점 (c) 확대 기계적 관점 (d) 확대 열적 관점.
결정립 = Voronoi, 변형 = 변위장, 구역/색 = 규칙. 출력 SVG → PyMuPDF 로 PNG."""
import math, random
import numpy as np
from scipy.spatial import Voronoi

random.seed(7); np.random.seed(7)
PW, PH = 690, 1210          # 확대 패널 (c)(d)
AW, AH = 690, 670           # 거시 패널 (a)(b)
GAP = 20
SERIF = "Georgia, 'Times New Roman', serif"


# ---------- 기하 도우미 ----------
def lerp(a, b, t): return a + (b - a) * t
def clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))
def hexc(rgb): return "#%02x%02x%02x" % tuple(int(round(clamp(v, 0, 255))) for v in rgb)


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return hexc([lerp(a[i], b[i], t) for i in range(3)])


def clip_poly(poly, box):
    """Sutherland-Hodgman: poly 를 box=(x0,y0,x1,y1) 로 자름"""
    x0, y0, x1, y1 = box

    def clip(pts, inside, inter):
        out = []
        for i in range(len(pts)):
            P, Q = pts[i - 1], pts[i]
            if inside(Q):
                if not inside(P):
                    out.append(inter(P, Q))
                out.append(Q)
            elif inside(P):
                out.append(inter(P, Q))
        return out

    def ix(P, Q, x):
        t = (x - P[0]) / (Q[0] - P[0]); return (x, P[1] + t * (Q[1] - P[1]))

    def iy(P, Q, y):
        t = (y - P[1]) / (Q[1] - P[1]); return (P[0] + t * (Q[0] - P[0]), y)

    pts = list(poly)
    for f in [(lambda p: p[0] >= x0, lambda P, Q: ix(P, Q, x0)), (lambda p: p[0] <= x1, lambda P, Q: ix(P, Q, x1)),
              (lambda p: p[1] >= y0, lambda P, Q: iy(P, Q, y0)), (lambda p: p[1] <= y1, lambda P, Q: iy(P, Q, y1))]:
        if not pts:
            break
        pts = clip(pts, *f)
    return pts


def voronoi_cells(seeds, box, pad=80):
    """seeds 의 Voronoi 셀(다각형)들. 바깥에 여유 점을 둘러 유한 셀만 얻고 box 로 자른다."""
    x0, y0, x1, y1 = box
    ring = []
    for x in np.arange(x0 - pad, x1 + pad + 1, 40):
        ring += [(x, y0 - pad), (x, y1 + pad)]
    for y in np.arange(y0 - pad, y1 + pad + 1, 40):
        ring += [(x0 - pad, y), (x1 + pad, y)]
    pts = np.array(list(seeds) + ring, dtype=float)
    vor = Voronoi(pts)
    cells = []
    for i in range(len(seeds)):
        reg = vor.regions[vor.point_region[i]]
        if not reg or -1 in reg:
            cells.append(None); continue
        poly = [tuple(vor.vertices[j]) for j in reg]
        poly = clip_poly(poly, box)
        cells.append(poly if len(poly) >= 3 else None)
    return cells


def path(pts, close=True):
    return "M " + " L ".join("%.1f %.1f" % (x, y) for x, y in pts) + (" Z" if close else "")


def text(x, y, s, size, fill, weight="normal", halo="#fff", anchor="middle", italic=False, halo_w=None):
    st = "font-family:%s;font-size:%dpx;font-weight:%s;%s" % (SERIF, size, weight, "font-style:italic;" if italic else "")
    hw = halo_w if halo_w is not None else max(3, size * 0.16)
    out = ""
    if halo:
        out += ('<text x="%.1f" y="%.1f" text-anchor="%s" style="%s" fill="%s" stroke="%s" stroke-width="%.1f" '
                'stroke-linejoin="round">%s</text>' % (x, y, anchor, st, halo, halo, hw, s))
    out += '<text x="%.1f" y="%.1f" text-anchor="%s" style="%s" fill="%s">%s</text>' % (x, y, anchor, st, fill, s)
    return out


def arrow(x1, y1, x2, y2, color, w=4, head=14):
    ang = math.atan2(y2 - y1, x2 - x1)
    hx, hy = x2 - head * math.cos(ang), y2 - head * math.sin(ang)
    p1 = (hx + head * 0.5 * math.sin(ang), hy - head * 0.5 * math.cos(ang))
    p2 = (hx - head * 0.5 * math.sin(ang), hy + head * 0.5 * math.cos(ang))
    return ('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%d" stroke-linecap="round"/>'
            % (x1, y1, hx, hy, color, w) +
            '<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s"/>' % (x2, y2, p1[0], p1[1], p2[0], p2[1], color))


def axis_box(x, y, dark="#000"):
    return ('<rect x="%d" y="%d" width="130" height="115" fill="#fff" stroke="#000" stroke-width="2.5"/>' % (x, y) +
            text(x + 22, y + 40, "O", 30, dark, halo=None) +
            arrow(x + 38, y + 50, x + 110, y + 50, dark, 3, 12) + text(x + 118, y + 44, "X", 28, dark, halo=None) +
            arrow(x + 38, y + 50, x + 38, y + 103, dark, 3, 12) + text(x + 60, y + 108, "Y", 28, dark, halo=None))


# ---------- 확대 패널 (c)(d) 기하 ----------
RAKE_Y = 245                     # 경사면(공구 윗면) 높이
TIP = (280.0, 300.0)             # 날끝 O


def surface_x(y):                # 가공면(TSZ 왼쪽 경계): O 에서 아래로 살짝 오른쪽 기울기
    return TIP[0] + (y - TIP[1]) * (55.0 / (PH - TIP[1]))


def flank_x(y):                  # 공구 여유면: 가공면보다 덜 기울어 아래로 갈수록 틈(공기)이 벌어짐
    return TIP[0] + (y - TIP[1]) * (18.0 / (PH - TIP[1]))


def tsz_w(y):                    # TSZ 폭: 위 130 → 아래 95
    return lerp(130, 95, clamp((y - TIP[1]) / (PH - TIP[1])))


def make_seeds():
    S = []
    # 1) 바탕 결정립 (전체, 간격 ~62, 지터)
    for y in np.arange(-40, PH + 60, 62):
        for x in np.arange(-40, PW + 60, 62):
            S.append((x + random.uniform(-22, 22), y + random.uniform(-22, 22), "base"))
    # 2) TSZ: 가공면 오른쪽 띠. 가로 촘촘·세로 성긴 씨앗 → 세로로 긴 결정립
    for y in np.arange(TIP[1] + 6, PH + 30, 30):
        w = tsz_w(y); sx = surface_x(y)
        d = 4.0
        while d < w:
            dx = lerp(7, 15, d / w)                       # 표면 가까울수록 가늘게
            S.append((sx + d + random.uniform(-2, 2), y + random.uniform(-9, 9), "tsz"))
            d += dx
    # 3) 표면 바로 옆 파편 결정립
    for y in np.arange(TIP[1] + 20, PH, 18):
        S.append((surface_x(y) + random.uniform(2, 10), y + random.uniform(-5, 5), "frag"))
    # 4) 칩 쪽: SSZ (경사면 위 납작한 결정립), PSZ (기울어진 중간 크기)
    for y in np.arange(150, RAKE_Y, 12):
        for x in np.arange(0, 240, 42):
            S.append((x + random.uniform(-10, 10), y + random.uniform(-3, 3), "ssz"))
    for y in np.arange(-20, RAKE_Y, 34):
        for x in np.arange(240, 470, 30):
            S.append((x + random.uniform(-8, 8), y + random.uniform(-8, 8), "psz"))
    return S


def displace(x, y):
    """변위장: TSZ 는 표면 쪽이 아래로 쓸려 내려가고(전단), 칩 쪽은 위로 갈수록 오른쪽으로 기움"""
    if y >= RAKE_Y - 2:
        d = x - surface_x(y)
        w = tsz_w(y)
        if 0 <= d < w * 1.15:
            f = clamp(1 - d / (w * 1.15))
            y = y + 140 * f ** 2 + 30 * f           # 아래로 쓸림 (표면 가까울수록 강하게)
            x = x - 10 * f                            # 표면 쪽으로 압축
        return x, y
    # 칩 영역: 경사면에서 멀어질수록 오른쪽으로 기움 (SSZ 는 강하게, PSZ 는 약하게)
    k = 0.75 if x < 260 else 0.45
    fall = clamp(1 - (x - 380) / 220) if x > 380 else 1.0
    return x + k * fall * (RAKE_Y - y), y


def region_of(seed):
    x, y, tag = seed
    if y < RAKE_Y:
        if x < 235:
            return "ssz"
        # PSZ: 날끝에서 위-오른쪽으로 뻗는 띠. 경사면 근처에서는 날끝 바로 옆까지만 (TSZ 쪽으로 흘러내리지 않게)
        if x < TIP[0] + 60 + (RAKE_Y - y) * 0.55:
            return "psz"
        return "base"
    d = x - surface_x(y)
    if d < -2:
        return "hidden"
    if d < tsz_w(y):
        return "tsz"
    return "base"


def grain_color(theme, seed, region):
    x, y, _ = seed
    r = math.hypot(x - TIP[0], y - TIP[1])
    if theme == "mech":
        if region == "tsz":
            d = (x - surface_x(y)) / tsz_w(y); t = clamp((y - TIP[1]) / (PH - TIP[1]))
            c = mix("#1b4f9e", "#7db4ea", clamp(0.15 + d * 0.9 - t * 0.25))
            return mix(c, "#0e2f6b", t * 0.45)
        if region == "ssz":
            return random.choice(["#6a3fa3", "#8657b8", "#a47cd0", "#b998dc"])
        if region == "psz":
            return random.choice(["#f2bf2f", "#f7cf55", "#fbdd82", "#f5c842"])
        return random.choice(["#ffffff", "#f3f3f3", "#e4e4e4", "#d3d3d3", "#bfbfbf", "#a8a8a8"])
    # thermal: 날끝에서 멀수록 옅게
    t = clamp(r / 1150)
    base = mix("#e2191b", "#f9d3c4", t ** 0.85)
    if region in ("tsz", "ssz", "psz"):
        base = mix(base, "#d81a16", 0.35 if region == "tsz" else 0.55)
    return mix(base, random.choice(["#ffffff", "#000000"]), random.uniform(0, 0.08))


def zoom_panel(theme):
    box = (0, 0, PW, PH)
    seeds = make_seeds()
    cells = voronoi_cells([(s[0], s[1]) for s in seeds], (-60, -60, PW + 60, PH + 60))
    out = []
    dark = theme == "thermal"
    # 배경(공기): 기계적=옅은 파랑, 열적=짙은 회색
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="%s"/>' % (PW, PH, "#dde8f3" if not dark else "#3a3a3a"))
    # 결정립
    for seed, poly in zip(seeds, cells):
        if poly is None:
            continue
        reg = region_of(seed)
        if reg == "hidden":
            continue
        pts = [displace(x, y) for x, y in poly]
        pts = clip_poly(pts, box)
        if len(pts) < 3:
            continue
        col = grain_color(theme, seed, reg)
        sw = 1.4 if reg in ("tsz", "frag") else 2.0
        out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="%.1f" stroke-linejoin="round"/>' % (path(pts), col, sw))
    if not dark:   # 날끝 주변 색 번짐
        out.append('<ellipse cx="300" cy="235" rx="120" ry="60" fill="url(#gTip)" opacity="0.55"/>')
    # 공기 틈(여유면과 가공면 사이)
    air = [TIP] + [(surface_x(y), y) for y in np.arange(TIP[1] + 40, PH + 1, 60)] + \
          [(surface_x(PH), PH), (flank_x(PH), PH)] + [(flank_x(y), y) for y in np.arange(PH - 60, TIP[1], -60)]
    out.append('<path d="%s" fill="%s"/>' % (path(air), "#e6eef7" if not dark else "#3a3a3a"))
    # 공구 인서트: 경사면 → 둥근 날끝 → 여유면
    r = 34
    tool = "M 0 %d L %d %d Q %d %d %d %d " % (RAKE_Y, TIP[0] - r, RAKE_Y, TIP[0] + 2, RAKE_Y, TIP[0] + 4, TIP[1] + 14)
    tool += " ".join("L %.1f %.1f" % (flank_x(y), y) for y in np.arange(TIP[1] + 60, PH + 1, 60))
    tool += " L %.1f %d L 0 %d Z" % (flank_x(PH), PH, PH)
    out.append('<path d="%s" fill="url(#%s)" stroke="#111" stroke-width="3.5" stroke-linejoin="round"/>'
               % (tool, "gToolM" if not dark else "gToolT"))
    # 라벨
    if not dark:
        navy = "#0d2b6b"; cream = "#fdf0c0"
        out.append(text(390, 48, "Mechanical Perspective", 28, "#000", halo="#fff", halo_w=7))
        out.append(text(115, 200, "SSZ", 60, "#6a3fa3", weight="bold", halo="#fff", halo_w=10))
        out.append(text(360, 165, "PSZ", 60, "#e8b400", weight="bold", halo="#fff", halo_w=10))
        out.append(text(385, 455, "TSZ", 60, "#2a6fd0", weight="bold", halo="#fff", halo_w=10))
        out.append(text(610, 300, "Workpiece", 28, "#000", halo="#fff", halo_w=7))
        out.append(text(225, 288, "O", 34, navy, weight="bold", halo="#fff", halo_w=6))
        out.append(text(115, 1178, "Cutting Insert", 28, navy, halo=cream, halo_w=6))
        for i, w in enumerate(["Flank -", "Workpiece", "Interaction"]):
            out.append(text(120, 335 + 33 * i, w, 26, navy, halo=cream, halo_w=5))
        out.append(arrow(220, 355, 275, 318, navy, 5, 16))
        for i, w in enumerate(["Surface", "Straining", "in TSZ"]):
            out.append(text(110, 495 + 33 * i, w, 26, navy, halo=cream, halo_w=5))
        out.append(arrow(200, 520, 292, 512, navy, 5, 16))
        out.append(text(215, 745, "V", 34, navy, halo=cream, halo_w=5, italic=True) + text(238, 758, "C", 22, navy, halo=cream, halo_w=4))
        out.append(arrow(215, 665, 215, 830, navy, 6, 20))
        for i, w in enumerate(["Deformed", "Material", "Layer"]):
            out.append(text(115, 1010 + 33 * i, w, 26, navy, halo=cream, halo_w=5))
        out.append(arrow(210, 1060, 348, 1105, navy, 5, 16))
    else:
        red = "#c8121a"
        out.append(text(390, 48, "Thermal Perspective", 28, "#000", halo="#fff", halo_w=7))
        out.append(text(115, 200, "SSZ", 60, "#fff", weight="bold", halo=red, halo_w=9))
        out.append(text(360, 165, "PSZ", 60, "#fff", weight="bold", halo=red, halo_w=9))
        out.append(text(385, 455, "TSZ", 60, "#fff", weight="bold", halo=red, halo_w=9))
        out.append(text(610, 300, "Workpiece", 28, "#000", halo="#fff", halo_w=7))
        out.append(text(225, 288, "O", 34, "#fff", weight="bold", halo="#7a0c10", halo_w=5))
        out.append(text(115, 1178, "Cutting Insert", 28, "#0d2b6b", halo="#fbe3da", halo_w=6))
        for i, w in enumerate(["Hot Metal", "enters TSZ"]):
            out.append(text(120, 330 + 33 * i, w, 26, "#fff", halo=red, halo_w=4))
        out.append(arrow(220, 345, 292, 300, "#fff", 6, 18))
        for i, w in enumerate(["Heat", "dissipation", "in TSZ"]):
            out.append(text(115, 480 + 33 * i, w, 26, "#fff", halo=red, halo_w=4))
        out.append(arrow(200, 505, 292, 512, "#fff", 6, 18))
        out.append(text(215, 745, "V", 34, "#fff", halo=red, halo_w=4, italic=True) + text(238, 758, "C", 22, "#fff", halo=red, halo_w=3))
        out.append(arrow(215, 665, 215, 830, "#fff", 6, 20))
        for i, w in enumerate(["Surface", "Cooling"]):
            out.append(text(120, 985 + 33 * i, w, 26, "#fff", halo=red, halo_w=4))
        out.append(arrow(215, 1005, 352, 1082, "#fff", 6, 18))
    out.append(axis_box(525, 110))
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="none" stroke="#000" stroke-width="7"/>' % (PW, PH))
    return "\n".join(out)


# ---------- 거시 패널 (a)(b) ----------
def chip_outline():
    """칩: 안쪽 곡선(경사면에서 떠오름) + 바깥 톱니 곡선"""
    inner = [(408, 372), (350, 352), (290, 318), (230, 272), (170, 218), (110, 155), (60, 88), (25, 20), (0, -30)]
    outer_pts = [(120, -60), (190, -5), (255, 55), (312, 122), (358, 195), (392, 270), (410, 330), (413, 372)]
    teeth = []
    for i in range(len(outer_pts) - 1):
        (x1, y1), (x2, y2) = outer_pts[i], outer_pts[i + 1]
        if i >= len(outer_pts) - 3:          # 날끝 가까운 구간은 톱니 없이 매끈하게 (칩 뿌리)
            teeth.append((x1, y1)); continue
        seg = math.hypot(x2 - x1, y2 - y1); n = max(1, int(seg / 38))
        nx, ny = (y2 - y1) / seg, -(x2 - x1) / seg          # 바깥 법선 (오른쪽-위)
        for k in range(n):
            t0, t1 = k / n, (k + 1) / n
            a = (lerp(x1, x2, t0), lerp(y1, y2, t0)); b = (lerp(x1, x2, t1), lerp(y1, y2, t1))
            depth = 34 * (0.6 + 0.4 * (1 - i / len(outer_pts)))
            tip = (lerp(a[0], b[0], 0.65) + nx * depth, lerp(a[1], b[1], 0.65) + ny * depth)
            teeth += [a, tip]
    teeth.append(outer_pts[-1])
    # 날끝 → 안쪽 곡선을 따라 위-왼쪽 → 바깥 곡선(톱니)을 따라 다시 날끝으로 (한 방향으로 닫힘)
    return inner + teeth


def macro_panel(theme):
    dark = theme == "thermal"
    out = []
    W, H = AW, AH
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="%s"/>' % (W, H, "#dfe9f3" if not dark else "#3d3d3d"))
    wp = [(455, -10), (446, 120), (434, 250), (420, 372), (432, 480), (447, 580), (462, 680), (W + 10, 680), (W + 10, -10)]
    out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="3"/>' % (path(wp), "url(#gWpM)" if not dark else "url(#gWpT)"))
    out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="3" stroke-linejoin="round"/>'
               % (path(chip_outline()), "url(#gChipM)" if not dark else "url(#gChipT)"))
    tool = [(-10, 375), (398, 375), (410, 378), (400, 500), (388, 680), (-10, 680)]
    out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="3"/>' % (path(tool), "url(#gToolAM)" if not dark else "url(#gToolAT)"))
    zc = ("#f4c542", "#7b4fb0", "#2a6fd0") if not dark else ("#e8141a",) * 3
    out.append('<path d="M 408 372 Q 372 300 368 232 Q 392 232 402 300 Q 414 340 416 372 Z" fill="%s" stroke="#111" stroke-width="2"/>' % zc[0])
    out.append('<path d="M 285 372 Q 340 352 400 368 Q 350 380 285 375 Z" fill="%s" stroke="#111" stroke-width="2"/>' % zc[1])
    out.append('<path d="M 418 378 L 424 380 Q 434 430 432 470 Q 426 430 418 378 Z" fill="%s" stroke="#111" stroke-width="2"/>' % zc[2])
    out.append('<rect x="378" y="355" width="82" height="125" fill="none" stroke="%s" stroke-width="3" stroke-dasharray="7 6"/>'
               % ("#111" if not dark else "#f5c400"))
    ac = "#111" if not dark else "#fff"
    out.append('<path d="M 300 60 Q 350 70 372 118" fill="none" stroke="%s" stroke-width="4"/>' % ac)
    out.append(arrow(366, 105, 374, 124, ac, 4, 14))
    if not dark:
        out.append('<path d="M 528 -10 Q 470 180 420 372" fill="none" stroke="#333" stroke-width="1.5" stroke-dasharray="14 5 3 5"/>')
        out.append('<line x1="0" y1="375" x2="%d" y2="375" stroke="#333" stroke-width="1.5" stroke-dasharray="14 5 3 5"/>' % W)
        out.append(arrow(437, 165, 405, 185, "#111", 2, 9) + arrow(437, 165, 470, 145, "#111", 2, 9))
        out.append(text(432, 150, "t", 26, "#000", halo=None, italic=True) + text(444, 158, "C", 16, "#000", halo=None))
    tc = "#000" if not dark else "#fff"
    hl = "#fff" if not dark else None
    out.append(text(300, 55, "Spindle", 28, tc, halo=hl, halo_w=6) + text(300, 88, "Rotation", 28, tc, halo=hl, halo_w=6))
    out.append(text(150, 240, "Chip", 30, tc, halo=hl, halo_w=6))
    out.append(text(120, 430, "Cutting Insert", 28, "#0d2b6b" if not dark else "#fff", halo="#fdf0c0" if not dark else None, halo_w=6))
    out.append(text(585, 505, "Workpiece", 28, tc, halo=hl, halo_w=6))
    if not dark:
        out.append('<rect x="430" y="298" width="66" height="30" fill="#f4c542"/>' + text(463, 322, "PSZ", 24, "#000", halo=None))
        out.append('<rect x="268" y="322" width="64" height="30" fill="#7b4fb0"/>' + text(300, 346, "SSZ", 24, "#fff", halo=None))
        out.append('<rect x="440" y="408" width="66" height="30" fill="#2a6fd0"/>' + text(473, 432, "TSZ", 24, "#fff", halo=None))
        out.append(text(385, 410, "O", 28, "#000", halo="#fff", halo_w=5))
    else:
        out.append(text(455, 318, "PSZ", 26, "#d0121a", halo="#fff", halo_w=6))
        out.append(text(300, 344, "SSZ", 26, "#d0121a", halo="#fff", halo_w=6))
        out.append(text(470, 428, "TSZ", 26, "#d0121a", halo="#fff", halo_w=6))
        out.append(text(385, 410, "O", 28, "#fff", halo="#7a0c10", halo_w=5))
        out.append('<rect x="580" y="40" width="96" height="270" fill="#fff" stroke="#000" stroke-width="2"/>' + text(628, 70, "T [°C]", 22, "#000", halo=None))
        for i, c in enumerate(["#ff0a0a", "#c40000", "#f08a2a", "#c9a100", "#3d6fd6", "#0c2a86"]):
            out.append('<rect x="613" y="%d" width="30" height="36" fill="%s" stroke="#000" stroke-width="1"/>' % (86 + i * 36, c))
    if not dark:
        out.append(axis_box(525, 95))
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="none" stroke="#000" stroke-width="7"/>' % (W, H))
    return "\n".join(out)


def defs():
    return """<defs>
<linearGradient id="gToolM" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fbe089"/><stop offset="0.5" stop-color="#f6cd4e"/><stop offset="1" stop-color="#f2bb2a"/></linearGradient>
<linearGradient id="gToolT" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c00d13"/><stop offset="0.45" stop-color="#e2574a"/><stop offset="1" stop-color="#f6bfae"/></linearGradient>
<radialGradient id="gTip" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#ffffff" stop-opacity="0.9"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>
<linearGradient id="gToolAM" x1="0" y1="0" x2="0.3" y2="1"><stop offset="0" stop-color="#fbe089"/><stop offset="1" stop-color="#f2bb2a"/></linearGradient>
<radialGradient id="gToolAT" cx="0.98" cy="0.02" r="1.1"><stop offset="0" stop-color="#e5141a"/><stop offset="0.25" stop-color="#c8231d"/><stop offset="0.5" stop-color="#8c3f5a"/><stop offset="0.75" stop-color="#2c5fb8"/><stop offset="1" stop-color="#0d2a7a"/></radialGradient>
<linearGradient id="gWpM" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#cfcfcf"/><stop offset="1" stop-color="#e6e6e6"/></linearGradient>
<radialGradient id="gWpT" cx="0.0" cy="0.56" r="0.95"><stop offset="0" stop-color="#e8141a"/><stop offset="0.12" stop-color="#f26a2c"/><stop offset="0.26" stop-color="#f0b24a"/><stop offset="0.42" stop-color="#7f9fc9"/><stop offset="0.7" stop-color="#20397f"/><stop offset="1" stop-color="#132559"/></radialGradient>
<linearGradient id="gChipM" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#c8c8c8"/><stop offset="1" stop-color="#e2e2e2"/></linearGradient>
<linearGradient id="gChipT" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f28a2a"/><stop offset="0.6" stop-color="#e83a1e"/><stop offset="1" stop-color="#e2141a"/></linearGradient>
</defs>"""


def build():
    TW = AW * 2 + GAP; TH = AH + 50 + PH + 60
    xb = AW + GAP; yb = AH + 50
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (TW, TH, TW, TH),
             defs(), '<rect width="%d" height="%d" fill="#fff"/>' % (TW, TH)]
    parts.append('<g>%s</g>' % macro_panel("mech"))
    parts.append('<g transform="translate(%d,0)">%s</g>' % (xb, macro_panel("thermal")))
    parts.append('<g transform="translate(0,%d)">%s</g>' % (yb, zoom_panel("mech")))
    parts.append('<g transform="translate(%d,%d)">%s</g>' % (xb, yb, zoom_panel("thermal")))
    parts.append('<line x1="378" y1="480" x2="0" y2="%d" stroke="#111" stroke-width="3" stroke-dasharray="4 7"/>' % yb)
    parts.append('<line x1="460" y1="480" x2="%d" y2="%d" stroke="#111" stroke-width="3" stroke-dasharray="4 7"/>' % (PW, yb))
    parts.append('<line x1="%d" y1="480" x2="%d" y2="%d" stroke="#f5c400" stroke-width="3" stroke-dasharray="4 7"/>' % (xb + 378, xb, yb))
    parts.append('<line x1="%d" y1="480" x2="%d" y2="%d" stroke="#f5c400" stroke-width="3" stroke-dasharray="4 7"/>' % (xb + 460, xb + PW, yb))
    for lab, x, y in [("(a)", AW / 2, AH + 36), ("(b)", xb + AW / 2, AH + 36), ("(c)", PW / 2, yb + PH + 42), ("(d)", xb + PW / 2, yb + PH + 42)]:
        parts.append(text(x, y, lab, 30, "#000", halo=None))
    parts.append("</svg>")
    return "\n".join(parts)


if __name__ == "__main__":
    svg = build()
    open("proto.svg", "w", encoding="utf-8").write(svg)
    import fitz
    doc = fitz.open("proto.svg")
    pix = doc[0].get_pixmap(dpi=72)
    pix.save("proto.png")
    print("svg %d bytes, png %dx%d" % (len(svg), pix.width, pix.height))
