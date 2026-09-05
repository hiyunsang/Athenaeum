# -*- coding: utf-8 -*-
"""선삭/직교 절삭 변형 구역 도식 생성기 (파라메트릭).

render(params) -> SVG 문자열.  size(params) -> (W, H)
층 구조: 물리량(온도장) → 형상(수치에서 계산) → 결정립(Voronoi + 변위장) → 표현(팔레트·그라데이션·후광) → 주석(라벨)

온도장 (Geo.temp_field): 2차원 이동 선열원(Rosenthal/Jaeger) 근사의 중첩
  T ∝ exp(u/L)·K0(r/L),  L = 2α/v (px),  u = 재료 흐름 방향 좌표 (하류 +)
  → 등온선이 절삭 방향(+y)으로 길게 늘어나고 공구 앞쪽은 압축됨.
  (a) 날끝 이동 열원  (b) 경사면-칩 접촉 선열원(칩 흐름 -x로 이류)
  (c) 여유면-가공면 접촉 선열원(여유면 따라 감쇠)  (d) 칩 내부 잔류열 + PSZ 전단열
  → 각 열원의 피크로 정규화 → 가중 합 → 0~1 → 컬러맵(cmap)
"""
import math, random
import numpy as np
from scipy.spatial import Voronoi
from scipy.special import k0e

DEFAULTS = {
    "theme": "both",          # mech | thermal | both
    "panels": "all",          # all | zoom | macro
    "lang": "en",             # 라벨 언어 en | ko
    "tsz_top": 130,           # TSZ 폭(위)
    "tsz_bottom": 95,         # TSZ 폭(아래)
    "shear": 140,             # 표면 쓸림 세기 (px)
    "clearance": 18,          # 여유면 기울기 (아래로 갈수록 오른쪽 px)
    "surface_tilt": 55,       # 가공면 기울기 (px)
    "tip_radius": 34,         # 날끝 둥글기
    "grain": 62,              # 바탕 결정립 간격
    "seed": 7,
    "t_max": 900, "t_min": 25,          # 온도장 범위 (°C) - 열적 관점 색의 근거
    "contact": True,          # 접촉면(경사면·여유면) 마찰열 띠 표시
    "labels": {},             # 라벨 덮어쓰기 {"ssz": "...", ...}
    # --- 선택(추가) 파라미터 ---
    "cmap": "jet",            # 열 컬러맵: jet(navy→red) | hot(pale salmon→dark red, 참고 그림 스타일)
    "therm_len": 260,         # 열 확산 길이 L = 2α/v (px). 작을수록 등온선이 날끝에 집중·길게 늘어남
    "cool_len": 900,          # 가공면을 따라 내려가며 식는 길이 (px)
    "compress": 12,           # TSZ 표면 방향 수평 압축 (px)
    "ssz_shear": 20,          # 경사면 인접층(SSZ) 추가 전단 (px)
    "frag_depth": 12,         # 표면 파편화 깊이 (px): 이 안의 셀은 추가 씨앗으로 잘게 쪼개짐
    "field_res": 40,          # 열 배경장 격자 열 수 (행 수는 비율로 자동)
    "glow": True,             # SVG 필터(후광·내부 그림자·배경장 블러) 사용
}

LABELS = {
    "en": {"ssz": "SSZ", "psz": "PSZ", "tsz": "TSZ", "workpiece": "Workpiece", "insert": "Cutting Insert", "chip": "Chip",
           "mech": "Mechanical Perspective", "thermal": "Thermal Perspective", "spindle": ["Spindle", "Rotation"],
           "flank": ["Flank -", "Workpiece", "Interaction"], "strain": ["Surface", "Straining", "in TSZ"],
           "deformed": ["Deformed", "Material", "Layer"], "hot": ["Hot Metal", "enters TSZ"],
           "dissip": ["Heat", "dissipation", "in TSZ"], "cool": ["Surface", "Cooling"], "legend": "T [°C]"},
    "ko": {"ssz": "SSZ", "psz": "PSZ", "tsz": "TSZ", "workpiece": "공작물", "insert": "절삭 인서트", "chip": "칩",
           "mech": "기계적 관점", "thermal": "열적 관점", "spindle": ["주축", "회전"],
           "flank": ["여유면-", "공작물", "접촉"], "strain": ["표면", "변형", "(TSZ)"],
           "deformed": ["변형", "재료층"], "hot": ["고온 금속", "TSZ 유입"],
           "dissip": ["TSZ 내", "열 소산"], "cool": ["표면", "냉각"], "legend": "T [°C]"},
}

PW, PH = 690, 1210
AW, AH = 690, 670
GAP = 20
SERIF = "Georgia, 'Times New Roman', serif"
RAKE_Y = 245
TIP = (280.0, 300.0)
AXIS_BOX = (525, 110, 655, 225)      # 확대 패널 좌표축 상자 (충돌 회피용)


def lerp(a, b, t): return a + (b - a) * t
def clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))
def hexc(rgb): return "#%02x%02x%02x" % tuple(int(round(clamp(v, 0, 255))) for v in rgb)


def smoothstep(t):
    t = clamp(t); return t * t * (3.0 - 2.0 * t)


def _sstep(a):
    t = np.clip(a, 0.0, 1.0); return t * t * (3.0 - 2.0 * t)


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return hexc([lerp(a[i], b[i], t) for i in range(3)])


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# 온도 컬러맵
CMAPS = {
    # navy → blue → green → yellow → orange → red → dark red
    "jet": ["#0b1f6e", "#1f6fd6", "#6fc3d8", "#8fd06a", "#f1e23a", "#f5a623", "#e8231a", "#9a0a0a"],
    # pale salmon → red → dark red (참고 그림의 열적 관점 스타일)
    "hot": ["#fbe9df", "#f8cbb4", "#f3a483", "#eb7250", "#e0402a", "#c81a12", "#8e0909"],
}
CMAP = CMAPS["jet"]


def cmap(t, name="jet"):
    cm = CMAPS.get(name, CMAP)
    t = clamp(t) * (len(cm) - 1)
    i = min(int(t), len(cm) - 2)
    return mix(cm[i], cm[i + 1], t - i)


def clip_poly(poly, box):
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
    x0, y0, x1, y1 = box
    ring = []
    for x in np.arange(x0 - pad, x1 + pad + 1, 40):
        ring += [(x, y0 - pad), (x, y1 + pad)]
    for y in np.arange(y0 - pad, y1 + pad + 1, 40):
        ring += [(x0 - pad, y), (x1 + pad, y)]
    vor = Voronoi(np.array(list(seeds) + ring, dtype=float))
    cells = []
    for i in range(len(seeds)):
        reg = vor.regions[vor.point_region[i]]
        if not reg or -1 in reg:
            cells.append(None); continue
        poly = clip_poly([tuple(vor.vertices[j]) for j in reg], box)
        cells.append(poly if len(poly) >= 3 else None)
    return cells


def path(pts, close=True):
    return "M " + " L ".join("%.1f %.1f" % (x, y) for x, y in pts) + (" Z" if close else "")


def text(x, y, s, size, fill, weight="normal", halo="#fff", anchor="middle", italic=False, halo_w=None, glow=None):
    """텍스트 + 후광. glow=색 이면 흐린(blur) 넓은 후광을 한 겹 더 깔아 부드러운 헤일로."""
    st = "font-family:%s;font-size:%dpx;font-weight:%s;%s" % (SERIF, size, weight, "font-style:italic;" if italic else "")
    hw = halo_w if halo_w is not None else max(3, size * 0.16)
    s = esc(s)
    out = ""
    if glow:
        out += ('<text x="%.1f" y="%.1f" text-anchor="%s" style="%s" fill="%s" stroke="%s" stroke-width="%.1f" '
                'stroke-linejoin="round" opacity="0.85" filter="url(#fText)">%s</text>' % (x, y, anchor, st, glow, glow, hw * 2.2, s))
    if halo:
        out += ('<text x="%.1f" y="%.1f" text-anchor="%s" style="%s" fill="%s" stroke="%s" stroke-width="%.1f" '
                'stroke-linejoin="round">%s</text>' % (x, y, anchor, st, halo, halo, hw, s))
    out += '<text x="%.1f" y="%.1f" text-anchor="%s" style="%s" fill="%s">%s</text>' % (x, y, anchor, st, fill, s)
    return out


def lines(x, y0, words, size, fill, halo, halo_w, dy=33, glow=None):
    return "".join(text(x, y0 + dy * i, w, size, fill, halo=halo, halo_w=halo_w, glow=glow) for i, w in enumerate(words))


def text_w(s, size):
    # 대략적 폭: ASCII 0.55em, CJK 1.0em
    return sum((1.0 if ord(ch) > 0x2E7F else 0.55) * size for ch in str(s))


def tbox(x, y, words, size, anchor="middle"):
    """라벨 경계 상자 (근사). words: 문자열 또는 줄 목록."""
    words = list(words) if isinstance(words, (list, tuple)) else [words]
    w = max(text_w(s, size) for s in words); h = len(words) * 1.15 * size
    x0 = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
    y0 = y - 0.8 * size
    return (x0, y0, x0 + w, y0 + h)


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place(items, obstacles, step=40, max_steps=5):
    """탐욕적 라벨 배치: 장애물(구역 라벨·좌표축 상자·앉힌 라벨)과 겹치면 40px씩 아래로 (최대 5회)."""
    out = []
    for it in items:
        y = it["y"]; n = 0
        while n < max_steps and any(overlaps(tbox(it["x"], y, it["words"], it["size"]), o) for o in obstacles):
            y += step; n += 1
        dy = y - it["y"]
        out.append(lines(it["x"], y, it["words"], it["size"], it["fill"], it["halo"], it["hw"], glow=it.get("glow")))
        if it.get("arrow"):
            x1, y1, x2, y2, col, w, hd = it["arrow"]
            out.append(arrow(x1, y1 + dy, x2, y2, col, w, hd))
        obstacles.append(tbox(it["x"], y, it["words"], it["size"]))
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


def legend(x, y, p, L):
    """온도 범례: 연속 컬러바(cmap과 동일) + t_min…t_max 눈금."""
    n = 6; bar = 216
    out = ['<rect x="%d" y="%d" width="116" height="%d" fill="#fff" stroke="#000" stroke-width="2"/>' % (x, y, bar + 74),
           text(x + 58, y + 30, L["legend"], 22, "#000", halo=None),
           '<rect x="%d" y="%d" width="30" height="%d" fill="url(#gLegend)" stroke="#000" stroke-width="1"/>' % (x + 20, y + 46, bar)]
    for i in range(n):
        tt = 1 - i / (n - 1); yy = y + 46 + i * bar / (n - 1)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#000" stroke-width="1.2"/>' % (x + 50, yy, x + 57, yy))
        out.append(text(x + 62, yy + 5, "%d" % round(p["t_min"] + (p["t_max"] - p["t_min"]) * tt), 15, "#000", halo=None, anchor="start"))
    return "".join(out)


class Geo:
    """확대 패널 형상 + 온도장: 수치(params)에서 계산"""

    # 열원 가중치: 날끝, 경사면(칩 접촉), 여유면(가공면 접촉), 칩 잔류열, PSZ 전단열
    WEIGHTS = (1.0, 0.85, 0.92, 0.42, 0.50)

    def __init__(self, p):
        self.p = p
        self._peaks = None
        self._norm = 1.0
        self._calibrate()

    def surface_x(self, y):
        return TIP[0] + (y - TIP[1]) * (self.p["surface_tilt"] / (PH - TIP[1]))

    def flank_x(self, y):
        return TIP[0] + (y - TIP[1]) * (self.p["clearance"] / (PH - TIP[1]))

    def tsz_w(self, y):
        return lerp(self.p["tsz_top"], self.p["tsz_bottom"], clamp((y - TIP[1]) / (PH - TIP[1])))

    def psz_x(self, y):
        """PSZ 바깥(미변형 재료 쪽) 경계 x (y < RAKE_Y). 스칼라·배열 모두 허용."""
        return TIP[0] + 60 + (RAKE_Y - y) * 0.55

    # ---- 온도장 ----
    def _kern(self, X, Y, sx, sy, ax, ay):
        """Rosenthal 2D 이동 선열원: exp(u/L)·K0(r/L). (ax,ay)=재료 흐름 단위벡터, u=하류 좌표.
        k0e(x)=e^x·K0(x) 를 써서 exp((u-r)/L)·k0e(r/L) 로 안정적으로 계산 (u ≤ r)."""
        L = self.p["therm_len"]; r0 = max(4.0, self.p["tip_radius"])
        dx = X - sx; dy = Y - sy
        r = np.hypot(dx, dy); rr = np.maximum(r, r0)
        u = np.minimum(dx * ax + dy * ay, rr)
        return np.exp((u - rr) / L) * k0e(rr / L)

    def _sources(self, X, Y):
        p = self.p
        X = np.asarray(X, dtype=float); Y = np.asarray(Y, dtype=float)
        r0 = max(4.0, p["tip_radius"])
        cool = np.exp(-np.maximum(Y - TIP[1], 0.0) / p["cool_len"])          # 가공면 따라 냉각
        # (a) 날끝 이동 열원, 재료는 +y 로 흐름 → 등온선이 아래로 길게
        t_tip = self._kern(X, Y, TIP[0], TIP[1], 0.0, 1.0) * cool
        # (b) 경사면-칩 접촉 선열원: 날끝에서 멀어질수록 감쇠, 칩은 -x 로 흐름
        t_rake = np.zeros_like(X); wsum = 0.0
        for sx in np.linspace(TIP[0] - r0, 8.0, 14):
            q = math.exp(-(TIP[0] - sx) / 150.0)
            t_rake += q * self._kern(X, Y, sx, RAKE_Y, -1.0, 0.0); wsum += q
        t_rake /= wsum
        # (c) 여유면-가공면 접촉 선열원: 여유면 따라 감쇠, 재료 +y 이류
        t_flank = np.zeros_like(X); wsum = 0.0
        for sy in np.linspace(TIP[1] + r0, PH, 18):
            q = math.exp(-(sy - TIP[1]) / 440.0)
            t_flank += q * self._kern(X, Y, self.surface_x(sy), sy, 0.0, 1.0); wsum += q
        t_flank = t_flank / wsum * cool
        # (d) 칩: PSZ 를 통과한 재료는 이미 뜨겁다 (경사면 근처가 더 뜨거움) + PSZ 띠의 전단열
        h = RAKE_Y - Y
        above = _sstep(h / 30.0)
        xps = self.psz_x(Y)
        in_chip = 1.0 - _sstep((X - xps + 40.0) / 120.0)
        t_chip = above * in_chip * (0.55 + 0.45 * np.exp(-np.maximum(h, 0.0) / 400.0))
        band = _sstep((X - 190.0) / 90.0) * (1.0 - _sstep((X - xps + 30.0) / 90.0))
        t_psz = above * band
        return (t_tip, t_rake, t_flank, t_chip, t_psz)

    def _combine(self, S):
        return sum(w * s / pk for w, s, pk in zip(self.WEIGHTS, S, self._peaks))

    def _calibrate(self):
        xs = np.linspace(0, PW, 60); ys = np.linspace(0, PH, 100)
        X, Y = np.meshgrid(xs, ys); X = X.ravel(); Y = Y.ravel()
        vis = ~((Y > RAKE_Y - 1) & (X < self.surface_x(Y) + 1))      # 공구·공기층 제외 (보이는 재료만)
        S = self._sources(X, Y)
        self._peaks = [max(float(s[vis].max()), 1e-9) for s in S]
        self._norm = max(float(self._combine(S)[vis].max()), 1e-9)

    def temp_field(self, X, Y):
        """정규화 온도장 0~1 (배열)."""
        t = self._combine(self._sources(X, Y)) / self._norm
        return np.clip(t, 0.0, 1.0) ** 0.85 * 0.97 + 0.03

    def temp(self, x, y):
        return float(self.temp_field(np.array([x], dtype=float), np.array([y], dtype=float))[0])

    def temp_c(self, x, y):
        return self.p["t_min"] + (self.p["t_max"] - self.p["t_min"]) * self.temp(x, y)


def make_seeds(g, p, rnd):
    S = []
    gs = p["grain"]
    for y in np.arange(-40, PH + 60, gs):
        for x in np.arange(-40, PW + 60, gs):
            S.append((x + rnd.uniform(-gs * 0.35, gs * 0.35), y + rnd.uniform(-gs * 0.35, gs * 0.35), "base"))
    for y in np.arange(TIP[1] + 6, PH + 30, 30):
        w = g.tsz_w(y); sx = g.surface_x(y); d = 4.0
        while d < w:
            S.append((sx + d + rnd.uniform(-2, 2), y + rnd.uniform(-9, 9), "tsz"))
            d += lerp(7, 15, d / w)
    fd = max(2.0, p["frag_depth"])
    for y in np.arange(TIP[1] + 14, PH, 9):
        S.append((g.surface_x(y) + rnd.uniform(1.5, fd), y + rnd.uniform(-4, 4), "frag"))
    # 파편화: 표면에서 frag_depth 이내의 씨앗 주변에 추가 씨앗 → 여유면에 붙은 작은 파편 결정립
    extra = []
    for x, y, k in S:
        if k in ("tsz", "frag") and y > TIP[1] + 10:
            d = x - g.surface_x(y)
            if 0 <= d < fd:
                for _ in range(2):
                    extra.append((g.surface_x(y) + rnd.uniform(1.0, fd), y + rnd.uniform(-7, 7), "frag"))
    S += extra
    for y in np.arange(150, RAKE_Y, 12):
        for x in np.arange(0, 240, 42):
            S.append((x + rnd.uniform(-10, 10), y + rnd.uniform(-3, 3), "ssz"))
    for y in np.arange(-20, RAKE_Y, 34):
        for x in np.arange(240, 470, 30):
            S.append((x + rnd.uniform(-8, 8), y + rnd.uniform(-8, 8), "psz"))
    return S


def displace(g, p, x, y):
    """변위장 (연속·단사): 격자 정점에 적용.
    TSZ: 표면 전단 δy = shear·exp(-d/δ)·ramp(y), δ = TSZ 폭/2, 표면 쪽 수평 압축 δx = -compress·exp(-d/δ)·ramp.
         ramp 는 날끝 아래로 내려가며 누적 전단이 커지는 것을 흉내.
    칩(경사면 위): 경사면에 평행한 단순 전단 δx = k(x)·h, k 는 SSZ(0.70)→PSZ(0.45) 로 부드럽게,
         PSZ 바깥 경계 너머로 부드럽게 0. 경사면 인접층에 추가 SSZ 전단."""
    if y >= RAKE_Y - 2:
        d = x - g.surface_x(y); w = g.tsz_w(y)
        delta = max(8.0, w * 0.5)
        f = math.exp(-max(d, 0.0) / delta)
        ramp = 1.0 - math.exp(-max(y - RAKE_Y, 0.0) / 90.0)
        return x - p["compress"] * f * ramp, y + p["shear"] * f * ramp
    h = RAKE_Y - y
    k = 0.45 + 0.25 * (1.0 - smoothstep((x - 160.0) / 200.0))
    fall = 1.0 - smoothstep((x - g.psz_x(y) + 60.0) / 200.0)
    ssz = p["ssz_shear"] * (1.0 - math.exp(-h / 28.0)) * (1.0 - smoothstep((x - 180.0) / 150.0))
    return x + fall * k * h + ssz, y


def region_of(g, seed):
    x, y, _ = seed
    if y < RAKE_Y:
        if x < 235:
            return "ssz"
        if x < g.psz_x(y):
            return "psz"
        return "base"
    d = x - g.surface_x(y)
    if d < -2:
        return "hidden"
    if d < g.tsz_w(y):
        return "tsz"
    return "base"


def surface_dist(g, x, y):
    """씨앗에서 활성 표면(가공면 / 경사면 / 날끝)까지 거리."""
    if y >= RAKE_Y:
        return max(0.0, x - g.surface_x(y))
    d = math.hypot(x - TIP[0], y - TIP[1])
    if x < TIP[0]:
        d = min(d, RAKE_Y - y)
    return d


def stroke_w(g, seed, reg):
    x, y, _ = seed
    sw = 0.8 + 1.1 * math.exp(-surface_dist(g, x, y) / 260.0)     # 표면에서 멀수록 가늘게
    if reg == "frag":
        sw *= 0.55
    elif reg == "tsz":
        sw *= 0.75
    return sw


def grain_color(g, theme, seed, region, rnd, t=None):
    x, y, _ = seed
    if theme == "mech":
        if region == "tsz":
            d = (x - g.surface_x(y)) / g.tsz_w(y); tt = clamp((y - TIP[1]) / (PH - TIP[1]))
            c = mix("#1b4f9e", "#7db4ea", clamp(0.15 + d * 0.9 - tt * 0.25))
            return mix(c, "#0e2f6b", tt * 0.45)
        if region == "frag":
            return rnd.choice(["#2f63b5", "#3b74c9", "#5b90d8", "#1b4f9e"])
        if region == "ssz":
            return rnd.choice(["#6a3fa3", "#8657b8", "#a47cd0", "#b998dc"])
        if region == "psz":
            return rnd.choice(["#f2bf2f", "#f7cf55", "#fbdd82", "#f5c842"])
        return rnd.choice(["#ffffff", "#f3f3f3", "#e4e4e4", "#d3d3d3", "#bfbfbf", "#a8a8a8"])
    # 열적 관점: 색 = 온도장 값 → cmap (범례와 동일 사상)
    if t is None:
        t = g.temp(x, y)
    base = cmap(t, g.p["cmap"])
    return mix(base, rnd.choice(["#ffffff", "#000000"]), rnd.uniform(0, 0.05))


def field_layer(g, p):
    """연속 온도장 배경: 거친 격자 사각형(+블러) → 결정립 사이에서도 색이 연속으로 읽힘."""
    nx = max(10, int(p["field_res"])); cw = PW / nx; ch = cw
    ny = int(math.ceil(PH / ch)); pad = 2
    xs = (np.arange(-pad, nx + pad) + 0.5) * cw; ys = (np.arange(-pad, ny + pad) + 0.5) * ch
    X, Y = np.meshgrid(xs, ys)
    T = g.temp_field(X.ravel(), Y.ravel()).reshape(X.shape)
    cm = p["cmap"]
    out = ['<g clip-path="url(#cPanel)"><g%s>' % (' filter="url(#fField)"' if p["glow"] else "")]
    for j in range(X.shape[0]):
        for i in range(X.shape[1]):
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                       % ((i - pad) * cw - 0.4, (j - pad) * ch - 0.4, cw + 0.8, ch + 0.8, cmap(T[j, i], cm)))
    out.append("</g></g>")
    return out


def contact_layer(g, p, dark, faint=False):
    """접촉면 마찰열 띠: 여유면-가공면(가공면 따라), 경사면-칩(경사면 따라). 넓은 후광 + 얇고 밝은 코어."""
    sfx = "T" if dark else "M"
    surf = path([(g.surface_x(y), y) for y in np.arange(TIP[1], PH + 1, 30)], close=False)
    rake = "M %.1f %d L 0 %d" % (TIP[0] - p["tip_radius"], RAKE_Y, RAKE_Y)
    op = 0.4 if faint else 1.0
    gw, gop = (24, 0.75) if dark else (16, 0.5)      # 기계적 관점은 흰 하이라이트를 절제 (TSZ 파랑이 씻기지 않게)
    out = []
    for d_, gid in ((surf, "gContact"), (rake, "gRake")):
        if p["glow"]:
            out.append('<path d="%s" fill="none" stroke="url(#%s%s)" stroke-width="%d" stroke-linecap="round" opacity="%.2f" filter="url(#fGlow)"/>'
                       % (d_, gid, sfx, gw, gop * op))
        if not faint:
            out.append('<path d="%s" fill="none" stroke="url(#%s%s)" stroke-width="%d" stroke-linecap="round" opacity="0.95"/>'
                       % (d_, gid, sfx, 5 if dark else 3))
    if p["glow"]:
        out.append('<circle cx="%.1f" cy="%.1f" r="%d" fill="%s" opacity="%.2f" filter="url(#fGlow)"/>'
                   % (TIP[0], TIP[1], 16 if dark else 11, "#fff4b0" if dark else "#ffffff", (0.85 if dark else 0.6) * op))
    return out


def zoom_panel(p, theme, L):
    rnd = random.Random(p["seed"])
    g = Geo(p)
    box = (0, 0, PW, PH)
    seeds = make_seeds(g, p, rnd)
    cells = voronoi_cells([(s[0], s[1]) for s in seeds], (-60, -60, PW + 60, PH + 60))
    dark = theme == "thermal"
    out = ['<rect x="0" y="0" width="%d" height="%d" fill="%s"/>' % (PW, PH, "#dde8f3" if not dark else "#3a3a3a")]
    temps = None
    if dark:
        out += field_layer(g, p)
        temps = g.temp_field(np.array([s[0] for s in seeds]), np.array([s[1] for s in seeds]))
    for i, (seed, poly) in enumerate(zip(seeds, cells)):
        if poly is None:
            continue
        reg = region_of(g, seed)
        if reg == "hidden":
            continue
        pts = clip_poly([displace(g, p, x, y) for x, y in poly], box)
        if len(pts) < 3:
            continue
        sw = stroke_w(g, seed, reg)
        col = grain_color(g, theme, seed, reg, rnd, None if temps is None else float(temps[i]))
        if dark:
            out.append('<path d="%s" fill="%s" fill-opacity="0.8" stroke="#000" stroke-opacity="0.48" stroke-width="%.1f" stroke-linejoin="round"/>'
                       % (path(pts), col, sw))
        else:
            out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="%.1f" stroke-linejoin="round"/>' % (path(pts), col, sw))
    # 접촉면 마찰열 띠 (공구 아래층: 공작물 쪽으로 번지는 후광)
    if p.get("contact"):
        out += contact_layer(g, p, dark)
    if not dark:
        out.append('<ellipse cx="300" cy="235" rx="120" ry="60" fill="url(#gTip)" opacity="0.55"/>')
    air = [TIP] + [(g.surface_x(y), y) for y in np.arange(TIP[1] + 40, PH + 1, 60)] + \
          [(g.surface_x(PH), PH), (g.flank_x(PH), PH)] + [(g.flank_x(y), y) for y in np.arange(PH - 60, TIP[1], -60)]
    out.append('<path d="%s" fill="%s"/>' % (path(air), "#e6eef7" if not dark else "#3a3a3a"))
    r = p["tip_radius"]
    tool = "M 0 %d L %d %d Q %d %d %d %d " % (RAKE_Y, TIP[0] - r, RAKE_Y, TIP[0] + 2, RAKE_Y, TIP[0] + 4, TIP[1] + 14)
    tool += " ".join("L %.1f %.1f" % (g.flank_x(y), y) for y in np.arange(TIP[1] + 60, PH + 1, 60))
    tool += " L %.1f %d L 0 %d Z" % (g.flank_x(PH), PH, PH)
    out.append('<path d="%s" fill="url(#%s)"/>' % (tool, "gToolM" if not dark else "gToolT"))
    if p["glow"]:   # 공구 모서리 내부 그림자
        cid = "cTool_%s" % theme
        out.append('<clipPath id="%s"><path d="%s"/></clipPath>' % (cid, tool))
        out.append('<g clip-path="url(#%s)"><path d="%s" fill="none" stroke="#000" stroke-width="22" opacity="%.2f" filter="url(#fInner)"/></g>'
                   % (cid, tool, 0.22 if dark else 0.30))
    out.append('<path d="%s" fill="none" stroke="#111" stroke-width="3.5" stroke-linejoin="round"/>' % tool)
    if p.get("contact"):   # 공구 위층: 모서리에 걸친 희미한 후광
        out += contact_layer(g, p, dark, faint=True)
    glow_on = p["glow"]
    if not dark:
        navy, cream = "#0d2b6b", "#fdf0c0"
        zone = [(115, 200, L["ssz"], "#6a3fa3"), (360, 165, L["psz"], "#e8b400"), (385, 455, L["tsz"], "#2a6fd0")]
        out.append(text(390, 48, L["mech"], 28, "#000", halo="#fff", halo_w=7))
        for zx, zy, zs, zc in zone:
            out.append(text(zx, zy, zs, 60, zc, weight="bold", halo="#fff", halo_w=10, glow="#fff" if glow_on else None))
        out.append(text(610, 300, L["workpiece"], 28, "#000", halo="#fff", halo_w=7))
        out.append(text(225, 288, "O", 34, navy, weight="bold", halo="#fff", halo_w=6))
        out.append(text(115, 1178, L["insert"], 28, navy, halo=cream, halo_w=6))
        out.append(text(215, 745, "V", 34, navy, halo=cream, halo_w=5, italic=True) + text(238, 758, "C", 22, navy, halo=cream, halo_w=4))
        out.append(arrow(215, 665, 215, 830, navy, 6, 20))
        obst = [tbox(zx, zy, zs, 60) for zx, zy, zs, _ in zone] + [AXIS_BOX, tbox(390, 48, L["mech"], 28),
                tbox(610, 300, L["workpiece"], 28), (190, 655, 250, 840), tbox(115, 1178, L["insert"], 28)]
        items = [dict(x=120, y=335, words=L["flank"], size=26, fill=navy, halo=cream, hw=5, arrow=(220, 355, 275, 318, navy, 5, 16)),
                 dict(x=110, y=495, words=L["strain"], size=26, fill=navy, halo=cream, hw=5, arrow=(200, 520, 292, 512, navy, 5, 16)),
                 dict(x=115, y=1010, words=L["deformed"], size=26, fill=navy, halo=cream, hw=5, arrow=(210, 1060, 348, 1105, navy, 5, 16))]
        out += place(items, obst)
    else:
        red, dred = "#c8121a", "#5a0709"
        zone = [(115, 200, L["ssz"]), (360, 165, L["psz"]), (385, 455, L["tsz"])]
        out.append(text(390, 48, L["thermal"], 28, "#000", halo="#fff", halo_w=7))
        for zx, zy, zs in zone:
            out.append(text(zx, zy, zs, 60, "#fff", weight="bold", halo=red, halo_w=9, glow=dred if glow_on else None))
        out.append(text(610, 300, L["workpiece"], 28, "#000", halo="#fff", halo_w=7))
        out.append(text(225, 288, "O", 34, "#fff", weight="bold", halo="#7a0c10", halo_w=5))
        out.append(text(115, 1178, L["insert"], 28, "#fff", halo="#333", halo_w=6))
        out.append(text(215, 745, "V", 34, "#fff", halo=dred, halo_w=4, italic=True) + text(238, 758, "C", 22, "#fff", halo=dred, halo_w=3))
        out.append(arrow(215, 665, 215, 830, "#fff", 6, 20))
        obst = [tbox(zx, zy, zs, 60) for zx, zy, zs in zone] + [AXIS_BOX, tbox(390, 48, L["thermal"], 28),
                tbox(610, 300, L["workpiece"], 28), (190, 655, 250, 840), tbox(115, 1178, L["insert"], 28)]
        items = [dict(x=120, y=330, words=L["hot"], size=26, fill="#fff", halo=dred, hw=4, glow=dred if glow_on else None, arrow=(220, 345, 292, 300, "#fff", 6, 18)),
                 dict(x=115, y=480, words=L["dissip"], size=26, fill="#fff", halo=dred, hw=4, glow=dred if glow_on else None, arrow=(200, 505, 292, 512, "#fff", 6, 18)),
                 dict(x=120, y=985, words=L["cool"], size=26, fill="#fff", halo=dred, hw=4, glow=dred if glow_on else None, arrow=(215, 1005, 352, 1082, "#fff", 6, 18))]
        out += place(items, obst)
        if p["panels"] == "zoom":   # 단독 확대 패널이면 범례를 함께 (all 이면 거시 패널에 있음)
            out.append(legend(PW - 130, 880, p, L))
    out.append(axis_box(525, 110))
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="none" stroke="#000" stroke-width="7"/>' % (PW, PH))
    return "\n".join(out)


def chip_outline():
    inner = [(408, 372), (350, 352), (290, 318), (230, 272), (170, 218), (110, 155), (60, 88), (25, 20), (0, -30)]
    outer_pts = [(120, -60), (190, -5), (255, 55), (312, 122), (358, 195), (392, 270), (410, 330), (413, 372)]
    teeth = []
    for i in range(len(outer_pts) - 1):
        (x1, y1), (x2, y2) = outer_pts[i], outer_pts[i + 1]
        if i >= len(outer_pts) - 3:
            teeth.append((x1, y1)); continue
        seg = math.hypot(x2 - x1, y2 - y1); n = max(1, int(seg / 38))
        nx, ny = (y2 - y1) / seg, -(x2 - x1) / seg
        for k in range(n):
            t0, t1 = k / n, (k + 1) / n
            a = (lerp(x1, x2, t0), lerp(y1, y2, t0)); b = (lerp(x1, x2, t1), lerp(y1, y2, t1))
            depth = 34 * (0.6 + 0.4 * (1 - i / len(outer_pts)))
            teeth += [a, (lerp(a[0], b[0], 0.65) + nx * depth, lerp(a[1], b[1], 0.65) + ny * depth)]
    teeth.append(outer_pts[-1])
    return inner + teeth


def macro_panel(p, theme, L):
    dark = theme == "thermal"
    W, H = AW, AH
    out = ['<rect x="0" y="0" width="%d" height="%d" fill="%s"/>' % (W, H, "#dfe9f3" if not dark else "#3d3d3d")]
    wp = [(455, -10), (446, 120), (434, 250), (420, 372), (432, 480), (447, 580), (462, 680), (W + 10, 680), (W + 10, -10)]
    out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="3"/>' % (path(wp), "url(#gWpM)" if not dark else "url(#gWpT)"))
    out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="3" stroke-linejoin="round"/>'
               % (path(chip_outline()), "url(#gChipM)" if not dark else "url(#gChipT)"))
    tool = [(-10, 375), (398, 375), (410, 378), (400, 500), (388, 680), (-10, 680)]
    out.append('<path d="%s" fill="%s" stroke="#111" stroke-width="3"/>' % (path(tool), "url(#gToolAM)" if not dark else "url(#gToolAT)"))
    hot = cmap(0.93, p["cmap"])
    zc = ("#f4c542", "#7b4fb0", "#2a6fd0") if not dark else (hot,) * 3
    out.append('<path d="M 408 372 Q 372 300 368 232 Q 392 232 402 300 Q 414 340 416 372 Z" fill="%s" stroke="#111" stroke-width="2"/>' % zc[0])
    out.append('<path d="M 285 372 Q 340 352 400 368 Q 350 380 285 375 Z" fill="%s" stroke="#111" stroke-width="2"/>' % zc[1])
    out.append('<path d="M 418 378 L 424 380 Q 434 430 432 470 Q 426 430 418 378 Z" fill="%s" stroke="#111" stroke-width="2"/>' % zc[2])
    if dark and p["glow"]:
        out.append('<circle cx="410" cy="374" r="16" fill="#fff4b0" opacity="0.8" filter="url(#fGlow)"/>')
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
    tc = "#000" if not dark else "#fff"; hl = "#fff" if not dark else None
    out.append(lines(300, 55, L["spindle"], 28, tc, hl, 6))
    out.append(text(150, 240, L["chip"], 30, tc, halo=hl, halo_w=6))
    out.append(text(120, 430, L["insert"], 28, "#0d2b6b" if not dark else "#fff", halo="#fdf0c0" if not dark else None, halo_w=6))
    out.append(text(585, 505, L["workpiece"], 28, tc, halo=hl, halo_w=6))
    if not dark:
        out.append('<rect x="430" y="298" width="66" height="30" fill="#f4c542"/>' + text(463, 322, L["psz"], 24, "#000", halo=None))
        out.append('<rect x="268" y="322" width="64" height="30" fill="#7b4fb0"/>' + text(300, 346, L["ssz"], 24, "#fff", halo=None))
        out.append('<rect x="440" y="408" width="66" height="30" fill="#2a6fd0"/>' + text(473, 432, L["tsz"], 24, "#fff", halo=None))
        out.append(text(385, 410, "O", 28, "#000", halo="#fff", halo_w=5))
        out.append(axis_box(525, 95))
    else:
        out.append(text(455, 318, L["psz"], 26, "#d0121a", halo="#fff", halo_w=6))
        out.append(text(300, 344, L["ssz"], 26, "#d0121a", halo="#fff", halo_w=6))
        out.append(text(470, 428, L["tsz"], 26, "#d0121a", halo="#fff", halo_w=6))
        out.append(text(385, 410, "O", 28, "#fff", halo="#7a0c10", halo_w=5))
        out.append(legend(560, 40, p, L))       # 범례: 실제 온도 범위 표기 (cmap 과 동일 사상)
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="none" stroke="#000" stroke-width="7"/>' % (W, H))
    return "\n".join(out)


def defs(p):
    cm = p["cmap"]

    def c(t):
        return cmap(t, cm)

    def stops(pairs):
        return "".join('<stop offset="%.3f" stop-color="%s"%s/>' % (o, col, (' stop-opacity="%.2f"' % op) if op is not None else "")
                       for o, col, op in pairs)

    leg = stops([(i / 7.0, c(i / 7.0), None) for i in range(8)])
    # 확대 패널 공구(열): 날끝 중심 방사 그라데이션 = 컬러맵 (사용자 좌표계)
    toolT = stops([(0, c(1.0), None), (0.06, c(0.95), None), (0.18, c(0.8), None), (0.36, c(0.6), None), (0.62, c(0.35), None), (1, c(0.12), None)])
    # 접촉면 띠 (열): 노랑-흰색 → 빨강 → 투명, (기계): 흰색 하이라이트 → 투명
    hotband = stops([(0, "#fffbe0", 1.0), (0.22, "#ffc24a", 0.95), (0.55, "#ff3b1a", 0.7), (1, "#ff3b1a", 0.0)])
    whiteband = stops([(0, "#ffffff", 0.95), (0.5, "#ffffff", 0.55), (1, "#ffffff", 0.0)])
    # 거시 패널 (열): 컬러맵에서 유도
    wpT = stops([(0, c(1.0), None), (0.12, c(0.88), None), (0.26, c(0.7), None), (0.42, c(0.45), None), (0.7, c(0.2), None), (1, c(0.06), None)])
    chipT = stops([(0, c(0.72), None), (0.6, c(0.86), None), (1, c(0.95), None)])
    toolAT = stops([(0, c(1.0), None), (0.25, c(0.9), None), (0.5, c(0.6), None), (0.75, c(0.3), None), (1, c(0.1), None)])
    d = ['<defs>',
         '<linearGradient id="gToolM" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fbe089"/><stop offset="0.5" stop-color="#f6cd4e"/><stop offset="1" stop-color="#f2bb2a"/></linearGradient>',
         '<radialGradient id="gToolT" gradientUnits="userSpaceOnUse" cx="%.1f" cy="%.1f" r="1000">%s</radialGradient>' % (TIP[0], TIP[1], toolT),
         '<radialGradient id="gTip" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#ffffff" stop-opacity="0.9"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>',
         '<linearGradient id="gContactM" gradientUnits="userSpaceOnUse" x1="0" y1="%.1f" x2="0" y2="%d">%s</linearGradient>' % (TIP[1], PH, whiteband),
         '<linearGradient id="gContactT" gradientUnits="userSpaceOnUse" x1="0" y1="%.1f" x2="0" y2="%d">%s</linearGradient>' % (TIP[1], PH, hotband),
         '<linearGradient id="gRakeM" gradientUnits="userSpaceOnUse" x1="%.1f" y1="0" x2="0" y2="0">%s</linearGradient>' % (TIP[0], whiteband),
         '<linearGradient id="gRakeT" gradientUnits="userSpaceOnUse" x1="%.1f" y1="0" x2="0" y2="0">%s</linearGradient>' % (TIP[0], hotband),
         '<linearGradient id="gLegend" x1="0" y1="1" x2="0" y2="0">%s</linearGradient>' % leg,
         '<linearGradient id="gToolAM" x1="0" y1="0" x2="0.3" y2="1"><stop offset="0" stop-color="#fbe089"/><stop offset="1" stop-color="#f2bb2a"/></linearGradient>',
         '<radialGradient id="gToolAT" cx="0.98" cy="0.02" r="1.1">%s</radialGradient>' % toolAT,
         '<linearGradient id="gWpM" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#cfcfcf"/><stop offset="1" stop-color="#e6e6e6"/></linearGradient>',
         '<radialGradient id="gWpT" cx="0.0" cy="0.56" r="0.95">%s</radialGradient>' % wpT,
         '<linearGradient id="gChipM" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#c8c8c8"/><stop offset="1" stop-color="#e2e2e2"/></linearGradient>',
         '<linearGradient id="gChipT" x1="0" y1="0" x2="1" y2="1">%s</linearGradient>' % chipT,
         '<clipPath id="cPanel"><rect x="0" y="0" width="%d" height="%d"/></clipPath>' % (PW, PH)]
    if p["glow"]:
        d += ['<filter id="fGlow" filterUnits="userSpaceOnUse" x="-200" y="-200" width="%d" height="%d"><feGaussianBlur stdDeviation="7"/></filter>' % (PW + 400, PH + 400),
              '<filter id="fText" x="-25%" y="-60%" width="150%" height="220%"><feGaussianBlur stdDeviation="3.5"/></filter>',
              '<filter id="fInner" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="8"/></filter>',
              '<filter id="fField" x="-5%" y="-5%" width="110%" height="110%"><feGaussianBlur stdDeviation="9"/></filter>']
    d.append('</defs>')
    return "\n".join(d)


def _params(params):
    p = dict(DEFAULTS); p.update({k: v for k, v in (params or {}).items() if v is not None and v != ""})
    for k in ("tsz_top", "tsz_bottom", "shear", "clearance", "surface_tilt", "tip_radius", "grain", "seed", "t_max", "t_min",
              "therm_len", "cool_len", "compress", "ssz_shear", "frag_depth", "field_res"):
        try:
            p[k] = float(p[k]) if k not in ("seed",) else int(p[k])
        except (TypeError, ValueError):
            p[k] = DEFAULTS[k]
    p["therm_len"] = max(20.0, p["therm_len"]); p["cool_len"] = max(20.0, p["cool_len"])
    p["contact"] = str(p.get("contact", True)).lower() not in ("false", "0", "no", "")
    p["glow"] = str(p.get("glow", True)).lower() not in ("false", "0", "no", "")
    if p.get("cmap") not in CMAPS:
        p["cmap"] = DEFAULTS["cmap"]
    return p


def size(params):
    p = _params(params)
    themes = 2 if p["theme"] == "both" else 1
    W = AW * themes + GAP * (themes - 1)
    if p["panels"] == "zoom":
        return W, PH
    if p["panels"] == "macro":
        return W, AH
    return W, AH + 50 + PH + 60


def render(params):
    p = _params(params)
    L = dict(LABELS.get(p["lang"], LABELS["en"])); L.update(p.get("labels") or {})
    themes = ["mech", "thermal"] if p["theme"] == "both" else [p["theme"]]
    W, H = size(p)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
             defs(p), '<rect width="%d" height="%d" fill="#fff"/>' % (W, H)]
    labels = iter("abcdefgh")
    y_zoom = 0 if p["panels"] == "zoom" else AH + 50
    for i, th in enumerate(themes):
        xb = i * (AW + GAP)
        if p["panels"] in ("all", "macro"):
            parts.append('<g transform="translate(%d,0)">%s</g>' % (xb, macro_panel(p, th, L)))
            parts.append(text(xb + AW / 2, AH + 36, "(%s)" % next(labels), 30, "#000", halo=None) if p["panels"] == "all" else "")
    for i, th in enumerate(themes):
        xb = i * (AW + GAP)
        if p["panels"] in ("all", "zoom"):
            parts.append('<g transform="translate(%d,%d)">%s</g>' % (xb, y_zoom, zoom_panel(p, th, L)))
            if p["panels"] == "all":
                col = "#111" if th == "mech" else "#f5c400"
                parts.append('<line x1="%d" y1="480" x2="%d" y2="%d" stroke="%s" stroke-width="3" stroke-dasharray="4 7"/>' % (xb + 378, xb, y_zoom, col))
                parts.append('<line x1="%d" y1="480" x2="%d" y2="%d" stroke="%s" stroke-width="3" stroke-dasharray="4 7"/>' % (xb + 460, xb + PW, y_zoom, col))
                parts.append(text(xb + PW / 2, y_zoom + PH + 42, "(%s)" % next(labels), 30, "#000", halo=None))
    parts.append("</svg>")
    return "\n".join(parts)


if __name__ == "__main__":
    import sys, io
    out = sys.argv[1] if len(sys.argv) > 1 else "turning.svg"
    io.open(out, "w", encoding="utf-8").write(render({}))
    print("wrote", out, size({}))
