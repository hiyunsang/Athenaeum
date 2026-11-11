# -*- coding: utf-8 -*-
"""QR 코드 그리기 (표준 라이브러리만) — 「다른 기기 연결 도우미」(/connect)가 휴대폰에 줄 주소를 QR 로 보인다.

    matrix(text)  → 칸의 격자(행마다 0·1 의 목록), 들어가지 않으면 None
    svg(text)     → SVG 글, 들어가지 않으면 None

바이트 방식 · 판 1~6(21~41칸) · 오류 정정 M(안 들어가면 L)만 다룬다 — 주소 한 줄(M 106바이트, L 134바이트까지)이면 충분하다.
판 7 이상에 필요한 판 정보 칸과 여러 정렬 무늬, 블록 크기가 서로 다른 조합(Q·H)은 다루지 않는다. 규격은 ISO/IEC 18004.
시험: 스크래치 qr_test.py — 알려진 값(오류 정정 코드워드 · 형식 정보 15비트)과 대조하고, 그린 것을 OpenCV 의 QR 읽기로 되읽는다."""

_TOTAL = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172}                                   # 판마다 전체 코드워드 수
_EC = {"L": {1: (7, 1), 2: (10, 1), 3: (15, 1), 4: (20, 1), 5: (26, 1), 6: (18, 2)},      # (블록마다 오류 정정 코드워드, 블록 수)
       "M": {1: (10, 1), 2: (16, 1), 3: (26, 1), 4: (18, 2), 5: (24, 2), 6: (16, 4)}}
_FMT = {"L": 1, "M": 0}                                                                    # 형식 정보의 오류 정정 수준 2비트

# GF(256) — 원시 다항식 x^8 + x^4 + x^3 + x^2 + 1 (0x11D)
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_remainder(data, n):
    """리드-솔로몬 오류 정정 코드워드 n개 — data(x)·x^n 을 생성 다항식 ∏(x − α^i) 로 나눈 나머지"""
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        gen = nxt
    rem = [0] * n
    for d in data:
        f = d ^ rem[0]
        rem = rem[1:] + [0]
        for j in range(n):
            rem[j] ^= _mul(gen[j + 1], f)
    return rem


def _data_codewords(ecc, v):
    ec, blocks = _EC[ecc][v]
    return _TOTAL[v] - ec * blocks


def capacity(ecc, v):
    """그 판·수준에 들어가는 바이트 수 (방식 4비트 + 길이 8비트를 뺀 것)"""
    return (_data_codewords(ecc, v) * 8 - 12) // 8


def _pick(nbytes):
    for ecc in ("M", "L"):
        for v in range(1, 7):
            if nbytes <= capacity(ecc, v):
                return ecc, v
    return None


def _codewords(data, ecc, v):
    """자료 → 채움 → 블록마다 오류 정정 → 섞어 놓은 최종 코드워드"""
    ec, blocks = _EC[ecc][v]
    ndata = _data_codewords(ecc, v)
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(4, 4)                  # 바이트 방식
    put(len(data), 8)          # 판 1~9 의 글자 수 칸은 8비트
    for b in data:
        put(b, 8)
    put(0, min(4, ndata * 8 - len(bits)))      # 끝 표시(0 이 넷까지)
    while len(bits) % 8:
        bits.append(0)
    cw = []
    for i in range(0, len(bits), 8):
        val = 0
        for b in bits[i:i + 8]:
            val = (val << 1) | b
        cw.append(val)
    k = 0
    while len(cw) < ndata:                      # 남는 자리는 0xEC, 0x11 을 번갈아
        cw.append((0xEC, 0x11)[k % 2])
        k += 1
    per = ndata // blocks                       # 판 1~6 의 L·M 은 블록 크기가 모두 같다
    dblocks = [cw[i * per:(i + 1) * per] for i in range(blocks)]
    eblocks = [_rs_remainder(b, ec) for b in dblocks]
    out = []
    for i in range(per):
        for b in dblocks:
            out.append(b[i])
    for i in range(ec):
        for b in eblocks:
            out.append(b[i])
    return out


def format_bits(ecc, mask):
    """형식 정보 15비트 (오류 정정 수준 2 + 마스크 3 + BCH 10, 0x5412 와 XOR)"""
    data = (_FMT[ecc] << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


_MASKS = (lambda x, y: (x + y) % 2 == 0,
          lambda x, y: y % 2 == 0,
          lambda x, y: x % 3 == 0,
          lambda x, y: (x + y) % 3 == 0,
          lambda x, y: (x // 3 + y // 2) % 2 == 0,
          lambda x, y: x * y % 2 + x * y % 3 == 0,
          lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
          lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)


def _frame(v):
    """기능 무늬(위치 찾기 · 타이밍 · 정렬 · 어두운 칸)와 형식 정보 자리를 그린 격자와 '기능 칸' 표"""
    n = 17 + 4 * v
    m = [[0] * n for _ in range(n)]
    fn = [[False] * n for _ in range(n)]

    def setf(y, x, dark):
        m[y][x] = 1 if dark else 0
        fn[y][x] = True

    for y0, x0 in ((0, 0), (0, n - 7), (n - 7, 0)):          # 위치 찾기 무늬 7×7 과 둘레의 흰 띠
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                y, x = y0 + dy, x0 + dx
                if 0 <= y < n and 0 <= x < n:
                    inside = 0 <= dy <= 6 and 0 <= dx <= 6
                    setf(y, x, inside and (dy in (0, 6) or dx in (0, 6) or (2 <= dy <= 4 and 2 <= dx <= 4)))
    for i in range(8, n - 8):                                 # 타이밍 무늬 (6행 · 6열)
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    if v >= 2:                                                # 정렬 무늬 5×5 — 판 2~6 은 하나뿐
        a = 4 * v + 10
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                setf(a + dy, a + dx, max(abs(dy), abs(dx)) != 1)
    for i in range(9):                                        # 형식 정보 자리 (나중에 채운다)
        if not fn[8][i]:
            setf(8, i, False)
        if not fn[i][8]:
            setf(i, 8, False)
    for i in range(8):
        setf(8, n - 1 - i, False)
        setf(n - 1 - i, 8, False)
    setf(n - 8, 8, True)                                      # 늘 어두운 칸
    return n, m, fn


def _draw_format(m, n, ecc, mask):
    bits = format_bits(ecc, mask)

    def bit(i):
        return (bits >> i) & 1

    for i in range(6):                 # 첫 벌 — 왼쪽 위 무늬 둘레
        m[i][8] = bit(i)
    m[7][8] = bit(6)
    m[8][8] = bit(7)
    m[8][7] = bit(8)
    for i in range(9, 15):
        m[8][14 - i] = bit(i)
    for i in range(8):                 # 둘째 벌 — 오른쪽 위 · 왼쪽 아래 무늬 곁
        m[8][n - 1 - i] = bit(i)
    for i in range(8, 15):
        m[n - 15 + i][8] = bit(i)
    m[n - 8][8] = 1


def _penalty(m, n):
    score = 0
    lines = [row[:] for row in m] + [[m[y][x] for y in range(n)] for x in range(n)]
    for line in lines:                 # 같은 색이 다섯 넘게 이어짐 · 위치 찾기 무늬를 닮은 꼴
        run = 1
        for i in range(1, n):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
        s = "".join("1" if c else "0" for c in line)
        score += 40 * (s.count("10111010000") + s.count("00001011101"))
    for y in range(n - 1):             # 2×2 가 한 색
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(row) for row in m)
    score += int(abs(dark * 100.0 / (n * n) - 50) // 5) * 10
    return score


def matrix(text, ecc=None, mask=None):
    """글(주소 한 줄)을 QR 격자로. 들어가지 않으면 None. ecc·mask 는 시험용 — 보통은 알아서 고른다."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if not data:
        return None
    if ecc:
        v = next((k for k in range(1, 7) if len(data) <= capacity(ecc, k)), None)
        if v is None:
            return None
    else:
        pick = _pick(len(data))
        if not pick:
            return None
        ecc, v = pick
    cw = _codewords(data, ecc, v)
    n, base, fn = _frame(v)
    i = 0
    nbits = len(cw) * 8
    right = n - 1
    while right >= 1:                  # 오른쪽 아래에서 시작해 두 열씩 위아래로 오가며 채운다 (6열은 타이밍 무늬라 건너뜀)
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in range(n):
            y = n - 1 - vert if upward else vert
            for j in range(2):
                x = right - j
                if not fn[y][x] and i < nbits:
                    base[y][x] = (cw[i >> 3] >> (7 - (i & 7))) & 1
                    i += 1
        right -= 2
    best = None
    for k in ([mask] if mask is not None else range(8)):
        m = [row[:] for row in base]
        f = _MASKS[k]
        for y in range(n):
            for x in range(n):
                if not fn[y][x] and f(x, y):
                    m[y][x] ^= 1
        _draw_format(m, n, ecc, k)
        p = _penalty(m, n)
        if best is None or p < best[0]:
            best = (p, m)
    return best[1]


def svg(text, px=6, quiet=4, dark="#000000", light="#ffffff"):
    """QR 을 SVG 로 — 어디에 놓여도 읽히게 늘 흰 바탕에 검은 칸, 둘레에 빈 띠(quiet)를 둔다"""
    m = matrix(text)
    if m is None:
        return None
    n = len(m)
    size = (n + 2 * quiet) * px
    d = []
    for y in range(n):
        x = 0
        while x < n:
            if m[y][x]:
                x0 = x
                while x < n and m[y][x]:
                    x += 1
                d.append("M%d %dh%dv1h-%dz" % (x0 + quiet, y + quiet, x - x0, x - x0))
            else:
                x += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" shape-rendering="crispEdges">'
            '<rect width="100%%" height="100%%" fill="%s"/><path fill="%s" d="%s"/></svg>') % (n + 2 * quiet, n + 2 * quiet, size, size, light, dark, "".join(d))
