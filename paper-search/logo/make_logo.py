# -*- coding: utf-8 -*-
"""
Athenaeum 로고 생성기 (2026-09-06 확정안: 헤어라인 왼 다리 + 두꺼운 오른 다리 + 가로대 자리의 스와시 획, 앤티크 골드)

python make_logo.py  →  이 폴더에
  mark.svg / mark-ink.svg   투명 배경 마크 (헤더·인쇄용)
  lockup.svg                마크 + 가는 줄 + 자간 넓힌 ATHENAEUM (홈 화면 첫 인상용)
  favicon.svg               잉크 원판 위 골드 마크 (브라우저 탭)
  icon-16/32/48/180/192/256/512.png, athenaeum.ico   (16px 은 스와시가 뭉개지므로 굵은 로마체 A 로 대체)
글꼴 없이 전부 경로(path)로 그려서 어디서나 같은 모양. ATHENAEUM 글자만 Georgia(윈도·맥·iOS 기본 탑재).
"""
import io, os

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD, INK, YELLOW = "#C9A227", "#1c1c1a", "#F5C518"
APEX_Y, BASE = 60, 340          # 글자 꼭지 y, 밑선 y (viewBox 0 0 400 400 기준)


def edge_x(y, x_bot, apex=(200, APEX_Y), base=BASE):
    """꼭지에서 밑선의 x_bot 로 내려오는 직선 위의, 높이 y 에서의 x."""
    return apex[0] + (x_bot - apex[0]) * (y - apex[1]) / (base - apex[1])


def counter_apex(LO, LI, RI, RO):
    a = (200 - LO) / float(BASE - APEX_Y); b = (RO - 200) / float(BASE - APEX_Y)
    d = (RI - LI) / (a + b); y = BASE - d; x = LI + a * d
    return round(x, 1), round(y, 1)


def roman_A(LO, LI, RI, RO, bar=(228, 250), serif_spread=16, serif_h=18, bracket=True):
    """로마체 A. LO/LI = 왼 다리 바깥/안쪽 x(밑선), RI/RO = 오른 다리 안쪽/바깥 x(밑선).
    모든 조각을 화면 기준 같은 회전 방향으로 그린다 (겹치는 곳이 nonzero 규칙으로 구멍 나지 않게)."""
    cx, cy = counter_apex(LO, LI, RI, RO)
    parts = ["M200,%d L%s,%d L%s,%d L%s,%s L%s,%d L%s,%d Z" % (APEX_Y, RO, BASE, RI, BASE, cx, cy, LI, BASE, LO, BASE)]
    if bar:
        y0, y1 = bar
        parts.append("M%s,%d L%s,%d L%s,%d L%s,%d Z" % (round(edge_x(y0, LI) - 4, 1), y0, round(edge_x(y0, RI) + 4, 1), y0,
                                                      round(edge_x(y1, RI) + 4, 1), y1, round(edge_x(y1, LI) - 4, 1), y1))
    top = BASE - serif_h
    for xo, xi in ((LO, LI), (RO, RI)):
        lo, hi = sorted([xo, xi]); eo, ei = edge_x(top, lo), edge_x(top, hi)
        if bracket:
            parts.append("M%s,%d Q%s,%d %s,%d L%s,%d Q%s,%d %s,%d L%s,%d Z" % (
                lo - serif_spread, BASE, lo - serif_spread * 0.35, BASE - serif_h * 0.55, round(eo, 1), top,
                round(ei, 1), top, hi + serif_spread * 0.35, BASE - serif_h * 0.55, hi + serif_spread, BASE, lo - serif_spread, BASE))
        else:
            parts.append("M%s,%d L%s,%d L%s,%d L%s,%d Z" % (lo - serif_spread, BASE, lo - serif_spread, BASE - serif_h, hi + serif_spread, BASE - serif_h, hi + serif_spread, BASE))
    return " ".join(parts)


def swash_path(LO, thick=20, tip=(380, 140)):
    """왼 다리 속에서 시작해 가로대를 지나 오른 다리 밖으로 길게 뻗는 펜 획."""
    x0 = round(edge_x(262, LO) - 4, 1)
    top_y = 262 - thick
    return "M%s,%d C200,%d 292,214 %d,%d C300,222 226,264 %s,266 Z" % (x0, top_y, top_y - 6, tip[0], tip[1], x0)


def mark_d():
    """확정 마크: 헤어라인 왼 다리(11) + 두꺼운 오른 다리(52), 얇은 평 세리프, 스와시."""
    return roman_A(104, 115, 262, 314, bar=None, serif_spread=24, serif_h=7, bracket=False), swash_path(104)


def bold_A_d():
    """16px 용 단순판: 굵은 로마체 A."""
    return roman_A(84, 128, 258, 320, bar=(224, 254), serif_spread=14, serif_h=16)


MARK_VIEWBOX = "72 52 316 296"   # 마크의 실제 범위에 맞춘 뷰박스 (여백 최소)


def mark_svg(color):
    a, s = mark_d()
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='%s'><path fill='%s' d='%s'/><path fill='%s' d='%s'/></svg>"
            % (MARK_VIEWBOX, color, a, color, s))


def lockup_svg(color, text_color):
    a, s = mark_d()
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 900 380'>"
            "<g transform='translate(300,4) scale(0.75)'><path fill='%s' d='%s'/><path fill='%s' d='%s'/></g>"
            "<line x1='120' y1='300' x2='780' y2='300' stroke='%s' stroke-width='2'/>"
            "<text x='450' y='356' text-anchor='middle' font-family='Georgia, \"Times New Roman\", serif' font-size='50' letter-spacing='14' fill='%s'>ATHENAEUM</text>"
            "</svg>") % (color, a, color, s, color, text_color)


def disc_svg(color, bg, simple=False):
    """원판 아이콘. simple=True 면 굵은 A (아주 작은 크기용)."""
    if simple:
        body = "<path fill='%s' d='%s'/>" % (color, bold_A_d())
        scale, tx, ty = 0.70, 60, 66
    else:
        a, s = mark_d()
        body = "<path fill='%s' d='%s'/><path fill='%s' d='%s'/>" % (color, a, color, s)
        scale, tx, ty = 0.74, 42, 58
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'><circle cx='200' cy='200' r='200' fill='%s'/>"
            "<g transform='translate(%s,%s) scale(%s)'>%s</g></svg>") % (bg, tx, ty, scale, body)


def write(name, text):
    io.open(os.path.join(HERE, name), "w", encoding="utf-8").write(text)


def render_png(svg_text, size, name):
    import fitz
    doc = fitz.open("svg", svg_text.encode("utf-8"))
    page = doc[0]
    z = size / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=True)
    pix.save(os.path.join(HERE, name))


def main():
    write("mark.svg", mark_svg(GOLD))
    write("mark-ink.svg", mark_svg(INK))
    write("lockup.svg", lockup_svg(GOLD, INK))
    write("lockup-dark.svg", lockup_svg(GOLD, "#f2efe6"))
    write("favicon.svg", disc_svg(GOLD, INK))
    for n in (32, 48, 180, 192, 256, 512):
        render_png(disc_svg(GOLD, INK), n, "icon-%d.png" % n)
    render_png(disc_svg(GOLD, INK, simple=True), 16, "icon-16.png")
    render_png(disc_svg(GOLD, INK, simple=True), 24, "icon-24.png")
    try:
        from PIL import Image
        imgs = {n: Image.open(os.path.join(HERE, "icon-%d.png" % n)).convert("RGBA") for n in (16, 24, 32, 48, 256)}
        imgs[256].save(os.path.join(HERE, "athenaeum.ico"), format="ICO",
                       sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)],
                       append_images=[imgs[16], imgs[24], imgs[32], imgs[48]])
    except ImportError:
        print("PIL 없음: .ico 생략")
    print("logo files written to", HERE)


if __name__ == "__main__":
    main()
