# -*- coding: utf-8 -*-
"""
Athenaeum 로고 생성기 (2026-09-06 확정안: 헤어라인 왼 다리 + 두꺼운 오른 다리 + 가로대 자리의 스와시 획, 앤티크 골드)

python make_logo.py  →  이 폴더에
  mark.svg / mark-ink.svg   투명 배경 마크 (헤더·인쇄용)
  lockup.svg                마크 + 가는 줄 + 자간 넓힌 ATHENAEUM (홈 화면 첫 인상용)
  favicon.svg               배경 없는 골드 A, 글자 몸통이 정중앙 (브라우저 탭) — 사용자가 원판/네모 대신 투명을 선택
  icon-16/32/48/180/192/256/512.png, athenaeum.ico   (16px 은 스와시가 뭉개지므로 굵은 로마체 A 로 대체)
  icon-disc.svg             참고용: 잉크 원판 버전
  touch-180.png             아이폰 홈 화면 아이콘 — 불투명 아이보리 바탕 (아이폰은 투명을 검게 채운다, 2026-11-08)
  maskable-192/512.png      안드로이드 홈 화면(manifest 의 purpose maskable) — 같은 바탕, A 는 가운데 80% 원 안에
python make_logo.py touch  →  홈 화면 아이콘 셋만 다시 만든다 (투명 아이콘 PNG 는 건드리지 않는다 — 전체를 돌리면 같은 그림이라도 바이트가 달라져 git 이 바뀐 것으로 본다)
글꼴 없이 전부 경로(path)로 그려서 어디서나 같은 모양. ATHENAEUM 글자만 Georgia(윈도·맥·iOS 기본 탑재).
"""
import io, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GOLD, INK, YELLOW = "#C9A227", "#1c1c1a", "#F5C518"
IVORY = "#F5F0E4"   # 홈 화면 아이콘의 바탕 — 앤티크 골드와 어울리는 종이색 (사용자: '하얗거나 아이보리거나', 2026-11-08)
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


BODY_CX = (80 + 338) / 2.0   # 마크 글자 몸통(세리프 포함)의 가로 중심. 스와시(→380)는 빼고 글자만 정중앙에 놓는다 (사용자 결정 2026-09-06)


def alone_svg(color, scale=1.14, simple=False):
    """확정 아이콘: 배경 없이 A 만, 글자 몸통을 정사각 캔버스 정중앙에. simple=True 는 16px 용 굵은 A.
    scale 1.14 = 몸통을 중앙에 둔 채 스와시 끝(x=380)이 캔버스 밖으로 잘리지 않는 최대 크기."""
    if simple:
        body = "<path fill='%s' d='%s'/>" % (color, bold_A_d()); cx = 202
    else:
        a, s = mark_d()
        body = "<path fill='%s' d='%s'/><path fill='%s' d='%s'/>" % (color, a, color, s); cx = BODY_CX
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'>"
            "<g transform='translate(%.1f,%.1f) scale(%.3f)'>%s</g></svg>") % (200 - cx * scale, 200 - 200 * scale, scale, body)


def touch_svg(color, bg, scale=0.9):
    """홈 화면 아이콘: 불투명 바탕 위에 A 를 안쪽으로. 아이폰은 apple-touch-icon 의 투명을 검게 채우고 모서리는 스스로 둥글게 깎으므로
    바탕을 꽉 채우고 둥근 모서리는 그리지 않는다. scale 0.9 = 몸통이 정중앙, 스와시 끝이 오른쪽 가장자리에서 46/400 안쪽(iOS 의 깎이는 모서리 밖).
    안드로이드 maskable 은 0.7 — 가운데 지름 80% 원(안전 영역) 안에 스와시 끝·세리프 모서리까지 든다."""
    a, s = mark_d()
    body = "<path fill='%s' d='%s'/><path fill='%s' d='%s'/>" % (color, a, color, s)
    return ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'><rect width='400' height='400' fill='%s'/>"
            "<g transform='translate(%.1f,%.1f) scale(%.3f)'>%s</g></svg>") % (bg, 200 - BODY_CX * scale, 200 - 200 * scale, scale, body)


def write(name, text):
    io.open(os.path.join(HERE, name), "w", encoding="utf-8").write(text)


def render_png(svg_text, size, name, alpha=True):
    import fitz
    doc = fitz.open("svg", svg_text.encode("utf-8"))
    page = doc[0]
    z = size / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=alpha)
    pix.save(os.path.join(HERE, name))


def touch_icons():
    """홈 화면 아이콘 셋 (2026-11-08): 아이폰은 apple-touch-icon 의 투명을 검게 채운다 → 불투명 아이보리 바탕(알파 채널 없이).
    안드로이드는 manifest 의 maskable 아이콘 — 바탕은 같고 A 는 가운데 80% 원 안에(0.7)."""
    render_png(touch_svg(GOLD, IVORY), 180, "touch-180.png", alpha=False)
    for n in (192, 512):
        render_png(touch_svg(GOLD, IVORY, 0.7), n, "maskable-%d.png" % n, alpha=False)


def main():
    write("mark.svg", mark_svg(GOLD))
    write("mark-ink.svg", mark_svg(INK))
    write("lockup.svg", lockup_svg(GOLD, INK))
    write("lockup-dark.svg", lockup_svg(GOLD, "#f2efe6"))
    # 아이콘: 배경 없는 골드 A (글자 몸통 정중앙). 원판 버전은 disc_svg 로 남겨 둠(미사용)
    write("favicon.svg", alone_svg(GOLD))
    write("icon-disc.svg", disc_svg(GOLD, INK))
    for n in (32, 48, 180, 192, 256, 512):
        render_png(alone_svg(GOLD), n, "icon-%d.png" % n)
    render_png(alone_svg(GOLD, 1.35, simple=True), 16, "icon-16.png")
    render_png(alone_svg(GOLD, 1.35, simple=True), 24, "icon-24.png")
    try:
        from PIL import Image
        imgs = {n: Image.open(os.path.join(HERE, "icon-%d.png" % n)).convert("RGBA") for n in (16, 24, 32, 48, 256)}
        imgs[256].save(os.path.join(HERE, "athenaeum.ico"), format="ICO",
                       sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)],
                       append_images=[imgs[16], imgs[24], imgs[32], imgs[48]])
    except ImportError:
        print("PIL 없음: .ico 생략")
    touch_icons()
    print("logo files written to", HERE)


if __name__ == "__main__":
    if sys.argv[1:] == ["touch"]:
        touch_icons()
        print("touch-180.png, maskable-192/512.png written to", HERE)
    else:
        main()
