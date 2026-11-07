# -*- coding: utf-8 -*-
"""정렬표의 쪽(p) 다시 맞추기 — 번역\\<stem>.번역.정렬.json 의 문장마다 PDF 쪽을 pagealign 으로 다시 찾는다 (2026-11-08).

사용: python tools\\realign_pages.py                 → 바꾸지 않고 통계만 (전체)
      python tools\\realign_pages.py --write         → p 를 고쳐 쓴다 (t·para 는 그대로; 임시 파일에 쓴 뒤 바꿔치기)
      python tools\\realign_pages.py --only Fang_Slip-line Yeung   → 이름 조각이 든 것만
      python tools\\realign_pages.py --limit 20      → 앞 20편만
파일마다의 결과는 번역\\_정렬쪽맞춤.json 에 {stem: {t, n, before, after, filled, changed, kept}} 로 남는다(--write 때만).
서버가 켜져 있어도 된다 — 읽기 화면은 열 때마다 /api/align 으로 파일을 다시 읽는다."""
import argparse
import glob
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "paper-search"))
import pagealign  # noqa: E402

TR = os.path.join(ROOT, "번역")
ARCHIVE = os.path.join(ROOT, "논문모음")
LOG = os.path.join(TR, "_정렬쪽맞춤.json")


def page_texts(pdf):
    import fitz
    doc = fitz.open(pdf)
    try:
        return [pg.get_text() for pg in doc]
    finally:
        doc.close()


def run_one(path, write):
    stem = os.path.basename(path)[:-len(".번역.정렬.json")]
    pdf = os.path.join(ARCHIVE, stem + ".pdf")
    if not os.path.isfile(pdf):
        return stem, None, "PDF 없음"
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception as e:
        return stem, None, "정렬표 못 읽음 " + str(e)[:60]
    if not isinstance(d, dict):
        return stem, None, "꼴이 다름"
    try:
        st = pagealign.realign_table(d, page_texts(pdf))
    except Exception as e:
        return stem, None, "PDF 못 읽음 " + str(e)[:60]
    if write and (st["filled"] or st["changed"]):
        tmp = "%s.tmp%d" % (path, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, path)
    return stem, st, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(TR, "*.번역.정렬.json")))
    if a.only:
        files = [f for f in files if any(o in os.path.basename(f) for o in a.only)]
    if a.limit:
        files = files[:a.limit]
    log = {}
    if a.write and os.path.isfile(LOG):
        try:
            log = json.load(io.open(LOG, encoding="utf-8"))
        except Exception:
            log = {}
    tot = {"files": 0, "n": 0, "before": 0, "after": 0, "filled": 0, "changed": 0, "kept": 0, "skipped": 0}
    t0 = time.time()
    for i, f in enumerate(files):
        stem, st, err = run_one(f, a.write)
        if st is None:
            tot["skipped"] += 1
            print("  건너뜀 %s — %s" % (stem[:60], err))
            continue
        tot["files"] += 1
        for k in ("n", "before", "after", "filled", "changed", "kept"):
            tot[k] += st[k]
        if a.write:
            log[stem] = dict(st, t=time.time())
        if a.only or a.limit or st["changed"] >= 20 or (st["n"] and st["before"] / st["n"] < 0.6):
            print("%3d/%d  %s  문장 %d · 쪽 %d → %d · 채움 %d · 바뀜 %d · 옛값 둔 것 %d" % (
                i + 1, len(files), stem[:58], st["n"], st["before"], st["after"], st["filled"], st["changed"], st["kept"]))
        elif (i + 1) % 25 == 0:
            print("  … %d/%d (%.0f초)" % (i + 1, len(files), time.time() - t0))
    if a.write:
        tmp = LOG + ".tmp%d" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)
        os.replace(tmp, LOG)
    if tot["n"]:
        print("\n%d편 · 문장 %d · 쪽 있는 문장 %d (%.0f%%) → %d (%.0f%%) · 채움 %d · 바뀜 %d · 옛값 둔 것 %d · 건너뜀 %d · %.0f초%s" % (
            tot["files"], tot["n"], tot["before"], tot["before"] / tot["n"] * 100, tot["after"], tot["after"] / tot["n"] * 100,
            tot["filled"], tot["changed"], tot["kept"], tot["skipped"], time.time() - t0, "" if a.write else "  (바꾸지 않음 — --write 로 적용)"))


if __name__ == "__main__":
    main()
