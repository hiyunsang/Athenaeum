# -*- coding: utf-8 -*-
"""번역 정렬표의 문장 → PDF 쪽 맞추기 (2026-11-08).

읽기 화면의 '원문 따라가기'는 정렬표(번역\\<stem>.번역.정렬.json)의 문장마다 적힌 쪽(p)에 기댄다. 전에는 번역할 때 문장 앞 60/35/20자를
쪽 글에서 찾아 처음 맞는 쪽으로 두었다 — 띄어쓰기·줄 끝 하이픈·합자(ﬁ)가 다르면 비고(서재 평균 16%, Fang 2005 는 58%), 짧은 머리는
엉뚱한 쪽(초록 문장이 결론 쪽)에 맞았다. 여기서는
  ① 읽기 화면의 normK 와 같은 규칙으로 글자·숫자만 남겨 견주고(NFKD 로 합자를 풀고, 하이픈·띄어쓰기·문장부호를 버린다)
  ② 문장이 통째로 든 쪽이 없으면 30자 조각(머리 · 20·40·60자 자리 · 꼬리)이 둘 이상 든 쪽을 후보로 삼고
  ③ 후보가 여럿이면 앞뒤 문장과 차례가 맞는 쪽을 고른다(동적 계획법 — 뒤로 가는 쪽에 벌점; 번역은 PDF 차례를 따르고 캡션·표만 끝에 몰린다)
  ④ 못 찾은 문장은 앞뒤의 아는 쪽이 같으면 그 쪽으로 채운다.
server.number_chunks(새 번역)와 tools/realign_pages.py(이미 만든 정렬표 다시 맞추기)가 같이 쓴다. 표준 라이브러리만."""
import re
import unicodedata

_KEEP = re.compile(r"[^a-z0-9À-ɏ가-힣]")


def norm(t):
    """읽기 화면 reader.html 의 normK 와 같은 규칙 — 바꾸면 거기도 같이."""
    return _KEEP.sub("", unicodedata.normalize("NFKD", t or "").lower())


def candidates(qn, page_norms):
    """정규화한 문장 qn 이 어느 쪽에 있나 → {쪽: 점수}. 통째로 든 쪽 3 · 조각 둘 이상 2 · (짧은 문장) 조각 하나 1. 12자 미만은 보지 않는다."""
    out = {}
    if len(qn) < 12:
        return out
    for i, s in enumerate(page_norms):
        if qn in s:
            out[i] = 3
    if out:
        return out
    chunks = [qn[k:k + 30] for k in (0, 20, 40, 60) if k + 30 <= len(qn)]
    if len(qn) >= 42:
        chunks.append(qn[-30:])
    if not chunks:
        chunks.append(qn[:30])
    for i, s in enumerate(page_norms):
        hits = sum(1 for c in chunks if c in s)
        if hits >= 2:
            out[i] = 2
        elif hits == 1 and len(chunks) <= 2:
            out[i] = 1
    return out


def assign(sents, page_texts, fill_gap=26):
    """문장 목록(번역 차례) → 쪽 번호 목록(0부터, 모르면 None).
    후보가 없는 문장은 차례만 이어 주고(None), 뒤로 가는 쪽은 1.5 + 0.7/쪽, 두 쪽 넘게 건너뛰는 것은 0.3/쪽의 벌점으로 앞뒤와 맞는 쪽을 고른다."""
    pn = [norm(p) for p in page_texts]
    P, N = len(pn), len(sents)
    if not P or not N:
        return [None] * N
    cands = [candidates(norm(s), pn) for s in sents]
    INF = float("inf")
    prev = [0.0] * P
    back = []
    for i in range(N):
        c = cands[i]
        cur = [INF] * P
        bk = [-1] * P
        for p in (c.keys() if c else range(P)):
            em = (3 - c[p]) if c else 2.5
            best, bq = INF, -1
            for q in range(P):
                if prev[q] == INF:
                    continue
                d = p - q
                tr = 0.0 if 0 <= d <= 1 else (0.3 * (d - 1) if d > 1 else 1.5 + 0.7 * (-d))
                v = prev[q] + tr
                if v < best:
                    best, bq = v, q
            cur[p] = best + em
            bk[p] = bq
        prev = cur
        back.append(bk)
    p = min(range(P), key=lambda x: prev[x])
    out = [None] * N
    for i in range(N - 1, -1, -1):
        out[i] = p if cands[i] else None
        if back[i][p] >= 0:
            p = back[i][p]
    if fill_gap:
        known = [(i, q) for i, q in enumerate(out) if q is not None]
        for (i0, p0), (i1, p1) in zip(known, known[1:]):
            if p0 == p1 and i1 - i0 <= fill_gap:
                for j in range(i0 + 1, i1):
                    out[j] = p0
    return out


def realign_table(table, page_texts):
    """정렬표 dict({번호: {t, p, para}})의 p 를 다시 맞춘다(제자리). 돌려주는 것: {n, before, after, filled, changed, kept}.
    새로 못 찾은 문장의 옛 p 는 지우지 않는다(kept)."""
    keys = sorted((k for k in table if str(k).isdigit()), key=int)
    sents = [str((table[k] or {}).get("t") or "") for k in keys]
    old = [(table[k] or {}).get("p") for k in keys]
    new = assign(sents, page_texts)
    filled = changed = kept = 0
    for k, o, n in zip(keys, old, new):
        if n is None:
            if o is not None:
                kept += 1
            continue
        if o is None:
            filled += 1
            table[k]["p"] = n
        elif o != n:
            changed += 1
            table[k]["p"] = n
    before = sum(1 for o in old if o is not None)
    after = sum(1 for k in keys if (table[k] or {}).get("p") is not None)
    return {"n": len(keys), "before": before, "after": after, "filled": filled, "changed": changed, "kept": kept}
