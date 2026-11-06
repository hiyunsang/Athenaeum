# -*- coding: utf-8 -*-
"""Claude 채팅이 보는 폴더 (2026-11-07)

사용자: '아테네움의 데이터베이스를 폴더째 공유해서 클로드 chat 에서도 쓸 수 있게 하는 거 어때. 현재 아테네움에서 논의한 게 다 저장이 되어 있으면
그 폴더를 프로젝트에서 사용해서 … 클로드에서 따로 더 이야기할 수 있게(특히 연구노트). 용량 문제가 있다면 클로드가 보는 폴더는 전부 md 파일로
변환하던가, 라이브러리식으로 필요한 정보만 쏙쏙 잘 뺄 수 있게'

원본은 그대로 나눌 수 없다 — 논문 PDF 7 GB · 번역 1,009만 자 · 요약 456만 자에, 나머지는 Athenaeum 이 통째로 덮어쓰는 JSON 이라 다른 프로그램이 건드리면 깨진다.
그래서 Athenaeum 이 **읽기 좋은 md 사본**을 폴더 하나(기본 <자료 폴더>\\Claude용)에 만들어 둔다. 한쪽 방향(Athenaeum → 폴더)이고 토큰은 들지 않는다.
Claude 채팅은 「읽어보기.md」 와 차례 파일을 먼저 읽고 필요한 파일만 연다 (도서관의 목록 → 책).

  읽어보기.md                      Claude 가 맨 먼저 읽는 안내 (폴더의 짜임 · 찾는 차례 · 답할 때 지킬 것 · 남기는 법)
  연구노트\\_차례.md · <주제>\\요지.md(카드·정한 것·대화의 차례·최근 대화) · 대화.md(전체 — 2만 자를 넘으면 대화 1.md, 대화 2.md …) · 계획.md(큰 물음·틀·설계서·반론 …) · 서론.md
  공부\\_차례.md · <장>\\개요.md(차례·절마다 논지·답하지 못한 것·참고문헌) · <n 절 제목>.md · 근거 문장.md · 물은 것.md
  논문\\_차례.md(연도별) · _라벨.md(묶음 → 하위 분류 → 라벨) → 라벨\\<라벨>.md(그 논문들과 한 줄 요약) · _한줄요약 1.md …(전체, 2만 자 남짓씩) · <연도_저널_저자_제목>.md(요약 + 메모·질문)
  번역\\<같은 이름>.md             전문 번역 ([sN] 표식은 빼고, 수식 그림은 색인에 전사가 있으면 LaTeX 로)
  원고\\_차례.md · <제목>\\원고.md · 대화.md · 논증.md
  연구실.md · 탐색 기록.md
  Athenaeum에 남기기\\             Claude 채팅이 정리한 글을 여기에 쓰면 그 연구 노트의 대화에 'Claude 채팅에서 가져온 글' 로 붙는다 (되가져오기)

원칙
 - 내보내는 글은 그 모듈의 함수로 만든다 (notes._md_*·_study_ref_block, study.md_parts, ms._mirror_body) — 화면의 「md 복사」 와 같은 글.
 - 바뀐 것만 다시 쓴다: 항목마다 원본 파일의 mtime·크기(sig)를 `_상태.json` 에 적어 두고, 글이 같으면 파일을 건드리지 않는다.
 - 지우는 것은 내가 만든 파일(상태에 적힌 경로)뿐이다. 폴더에 다른 파일이 있어도 건드리지 않는다. 폴더는 비어 있거나 내가 만든 것이어야 고를 수 있다.
 - 설정은 설정.json 의 shelf {on, dir}. 켜 두면 30초마다 자료 폴더의 파일 이름·시각·크기만 훑어(_stamp, 0.03초쯤) 달라졌을 때만 맞춘다(397편에서 0.4초).
 - 파일 하나는 Claude 가 한 번에 읽을 만한 크기로: 차례는 2만 자 남짓으로 나누고(라벨마다 · 한 줄 요약 묶음), 긴 대화는 2만 자 묶음으로 나눠 요지에 차례를 둔다.
   나눌 수 없는 큰 파일(전문 번역 · 근거 문장 · 계획 · 원고)은 머리에 '이 파일의 차례'(제목과 줄 번호)를 달아 필요한 절만 읽게 한다(_finish).
   실제 서재(397편 · 라벨 294개)에서 처음엔 _한줄요약.md 10만 자 · _라벨별.md 7만 자 · 가장 긴 연구 노트의 대화.md 275 KB(13만 자)였다.
"""
import hashlib
import io
import json
import os
import re
import threading
import time

cfg = {}
VERSION = 2                 # 만드는 글의 꼴이 바뀌면 올린다 → 다음 맞춤 때 전부 다시 만든다
OUT_NAME = "Claude용"
INBOX = "Athenaeum에 남기기"
INBOX_DONE = "_넣음"
INBOX_HELP = "쓰는 법.md"
STATE = "_상태.json"
EVERY = int(os.environ.get("ATHENAEUM_SHELF_EVERY") or 30)      # 초 — 켜 두었을 때 살피는 간격 (환경 변수는 시험용)
TURNS_PART = 20000          # 자 — 대화를 이만큼씩 나눈다. 한국어는 글자 수 ≈ 토큰 수라, 파일 읽기 도구의 한 번 상한(2만 5천 토큰쯤) 안쪽으로
BIG = 20000                 # 자 — 이보다 큰 파일에는 머리에 '이 파일의 차례'(제목과 줄 번호)를 단다
LONG_LINE = 1800            # 자 — 이보다 긴 줄은 문장 끝에서 나눈다 (읽기 도구가 2000자 넘는 줄을 자를 수 있다)
_LOCK = threading.RLock()
_ST = {"thread": None, "stop": False, "running": False, "last": None, "err": "", "held": [], "imported": []}
_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_DATA_DIRS = ("논문모음", "번역", "메모", "원고", "공부", "연구노트", "단어장", "관련맵", "수집", "백업", "글꼴", "paper-search", "tools", "dist")


def init(**kw):
    cfg.update(kw)
    cfg["ROOT"] = os.path.dirname(cfg["ARCHIVE"])


def start():
    """서버가 켜질 때(main) 한 번 — 살피는 스레드를 띄운다. 서버를 import 만 하는 도구(일괄 생성·시험 틀)에서는 돌지 않는다."""
    _ensure_loop()


# ---------- 설정 ----------
def conf():
    try:
        s = (cfg["settings"]() or {}).get("shelf") or {}
    except Exception:
        s = {}
    d = str(s.get("dir") or "").strip()
    return {"on": bool(s.get("on")), "dir": os.path.normpath(d) if d else os.path.join(cfg["ROOT"], OUT_NAME), "custom": bool(d)}


def _save_conf(**kw):
    s = dict(cfg["settings"]() or {})
    sh = dict(s.get("shelf") or {})
    sh.update(kw)
    s["shelf"] = sh
    cfg["save_settings"](s)


def _inside(child, parent):
    c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _dir_ok(d):
    """이 폴더에 써도 되는가 → (True, '') | (False, 까닭). 자료 폴더의 안이나 위는 안 되고, 이미 있는 폴더는 비어 있거나 내가 만든 것이어야 한다."""
    if not d or not os.path.isabs(d):
        return False, "폴더의 전체 경로를 적어 주세요"
    root = cfg["ROOT"]
    if _inside(root, d):
        return False, "Athenaeum 폴더 자체나 그 위의 폴더는 쓸 수 없습니다"
    if os.path.splitdrive(os.path.abspath(d))[1].strip("\\/") == "":
        return False, "드라이브의 맨 위는 쓸 수 없습니다"
    for name in _DATA_DIRS:
        if _inside(d, os.path.join(root, name)):
            return False, "Athenaeum 의 자료 폴더(%s) 안에는 둘 수 없습니다" % name
    if os.path.isfile(d):
        return False, "그 이름의 파일이 이미 있습니다"
    if os.path.isdir(d):
        try:
            names = os.listdir(d)
        except OSError as e:
            return False, "폴더를 열 수 없습니다: %s" % str(e)[:80]
        if names and STATE not in names:
            return False, "빈 폴더이거나 Athenaeum 이 만든 폴더여야 합니다 (다른 파일이 들어 있습니다)"
    return True, ""


# ---------- 작은 도구 ----------
def _clean(name, n=70):
    s = re.sub(r"\s+", " ", _BAD.sub(" ", str(name or ""))).strip().lstrip("_").strip()
    s = s[:n].rstrip(" .")
    if not s or s.upper() in ("CON", "PRN", "AUX", "NUL"):
        s = "이름 없음"
    return s


def _read(p):
    try:
        with io.open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _sig(*paths):
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append("%d:%d" % (st.st_mtime_ns, st.st_size))
        except OSError:
            out.append("-")
    return "|".join(out)


def _day(t):
    if not t:
        return ""
    t = t / 1000.0 if t > 1e11 else t      # 화면이 적은 시각(밀리초)도 받는다
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _one(s, n=200):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


_SENT_END = re.compile(r"(?:(?<=[.!?。][\"'”’)\]])|(?<=[.!?。]))(?=\s)")      # 문장 끝(닫는 따옴표·괄호까지) 바로 뒤의 자리


def _wrap_long(text):
    """LONG_LINE 보다 긴 줄을 문장 끝에서 나눈다(md 에서는 같은 문단으로 이어진다). 표의 행·제목·코드 줄은 그대로."""
    if not any(len(ln) > LONG_LINE for ln in text.split("\n")):
        return text
    out = []
    for ln in text.split("\n"):
        if len(ln) <= LONG_LINE or ln.startswith("    ") or ln.lstrip().startswith(("|", "#", "```", "$$")):
            out.append(ln)
            continue
        cur = ""
        for piece in _SENT_END.split(ln):
            if cur and len(cur) + len(piece) > 1200:
                out.append(cur.rstrip())
                cur = piece.lstrip()
            else:
                cur += piece
        if cur:
            out.append(cur.rstrip())
    return "\n".join(out)


def _finish(text):
    """항목 파일을 쓰기 전의 손질: 긴 줄을 나누고, 큰 파일(BIG 자 넘음)에는 머리에 '이 파일의 차례' — 제목과 그 줄 번호.
    줄 번호가 있으면 Claude 가 파일을 통째로 읽지 않고 그 절만 읽을 수 있다(파일 읽기는 줄 단위로 끊어 읽는다)."""
    text = _wrap_long(text)
    if len(text) < BIG:
        return text
    lines = text.split("\n")
    heads = [(i, ln) for i, ln in enumerate(lines) if re.match(r"^#{2,3} \S", ln)]
    if len(heads) > 70:
        heads = [(i, ln) for i, ln in heads if ln.startswith("## ")] or heads
    heads = heads[:150]
    if len(heads) < 3:
        return text
    at = heads[0][0]
    add = len(heads) + 2
    toc = ["## 이 파일의 차례 (줄 번호 — 필요한 부분만 읽습니다)"] + ["- %d줄: %s%s" % (i + 1 + add, "  " if ln.startswith("### ") else "", _one(re.sub(r"^#+ ", "", ln), 90)) for i, ln in heads] + [""]
    return "\n".join(lines[:at] + toc + lines[at:])


def _put(out, rel, text):
    """out\\rel 에 쓴다. 글이 같으면 건드리지 않는다 → 썼으면 True"""
    fp = os.path.join(out, rel)
    data = text.replace("\r\n", "\n").encode("utf-8")
    try:
        with open(fp, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (fp, os.getpid(), threading.get_ident())
    with open(tmp, "wb") as f:
        f.write(data)
    for i in range(6):
        try:
            os.replace(tmp, fp)
            return True
        except PermissionError:      # 누가(Claude 채팅·백신) 읽는 순간
            time.sleep(0.05 * (i + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise PermissionError("쓰지 못했습니다: " + rel)


def _remove(out, rel):
    """내가 만든 파일 하나를 지운다 (상태에 적힌 상대 경로만). 폴더가 비면 그 폴더도."""
    rel = str(rel or "")
    if not rel or os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        return False
    fp = os.path.join(out, rel)
    if not _inside(fp, out) or not os.path.isfile(fp):
        return False
    try:
        os.remove(fp)
    except OSError:
        return False
    d = os.path.dirname(fp)
    while _inside(d, out) and os.path.normcase(os.path.abspath(d)) != os.path.normcase(os.path.abspath(out)):
        try:
            os.rmdir(d)      # 비어 있을 때만 지워진다
        except OSError:
            break
        d = os.path.dirname(d)
    return True


def _load_state(out):
    try:
        with io.open(os.path.join(out, STATE), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("items"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"v": 0, "items": {}}


def _mods():
    import notes, study, manuscript as ms
    return notes, study, ms


# ---------- 논문 ----------
def _papers():
    """서재의 논문 → [{file, stem, title, year, journal, author, labels, review, top, has_summary, has_translation, line}] (넣은 순)"""
    tags = cfg["load_json"](cfg["TAGS_PATH"], {}) or {}
    try:
        top = set(cfg["pref_abbrs"]() or [])
    except Exception:
        top = set()
    out = []
    for f in cfg["archive_pdfs"]():
        meta = cfg["parse_name"](f)
        t = tags.get(f) or {}
        labels = list(t.get("labels") or [])
        title = t.get("title") or meta.get("title") or os.path.splitext(f)[0]
        sp, tp = cfg["gen_path"](f, "summary"), cfg["gen_path"](f, "translation")
        p = {"file": f, "stem": os.path.splitext(f)[0], "title": title, "year": str(meta.get("year") or ""), "journal": str(meta.get("journal") or ""),
             "author": str(meta.get("author") or ""), "labels": labels, "sp": sp, "tp": tp,
             "has_summary": os.path.isfile(sp), "has_translation": os.path.isfile(tp)}
        p["top"] = p["journal"] in top
        try:
            p["review"] = bool(cfg["is_review"](title, labels + list(t.get("suggested") or [])))
        except Exception:
            p["review"] = False
        try:
            p["line"] = cfg["paper_line"](f) if p["has_summary"] else ""
        except Exception:
            p["line"] = ""
        out.append(p)
    return out


def _paper_files(p):
    f, stem = p["file"], p["stem"]
    out = ["# " + p["title"], "",
           "- 저널 · 연도 · 첫 저자: %s%s · %s · %s%s" % ("★ " if p["top"] else "", p["journal"], p["year"], p["author"], " · 리뷰 논문" if p["review"] else ""),
           "- 라벨: " + (", ".join(p["labels"]) if p["labels"] else "(없음)"),
           "- 원문 PDF: " + os.path.join(cfg["ARCHIVE"], f)]
    if p["has_translation"]:
        out.append("- 전문 번역: `번역\\%s.md` (수치·조건·방법을 정확히 봐야 할 때)" % stem)
    out.append("")
    if p["has_summary"]:
        body = re.sub(r"\A\s*#\s[^\n]*\n", "", _read(p["sp"]), count=1).strip()
        out += ["## 요약 (Athenaeum 의 Claude 가 원문을 읽고 쓴 것)", "", re.sub(r"(?m)^(#{2,4}) ", lambda m: "#" + m.group(1) + " ", body), ""]
    else:
        out += ["(아직 요약을 만들지 않은 논문입니다. Athenaeum 의 읽기 화면에서 만들 수 있습니다.)", ""]
    try:
        nd = cfg["load_notes"](f) or {}
    except Exception:
        nd = {}
    notes_, qa = nd.get("notes") or [], nd.get("qa") or []
    if notes_:
        out += ["## 연구자의 메모", ""]
        for x in sorted(notes_, key=lambda x: str(x.get("t") or "")):
            out.append("- (%s) %s%s" % (x.get("t") or "", ("「%s」 에 대해: " % _one(x["quote"], 240)) if x.get("quote") else "", str(x.get("text") or "").strip()))
        out.append("")
    if qa:
        out += ["## 이 논문을 놓고 물은 것 (답은 Athenaeum 의 Claude)", ""]
        for x in sorted(qa, key=lambda x: str(x.get("t") or "")):
            out += ["### %s — %s" % (x.get("t") or "", _one(x.get("q"), 160))]
            if x.get("quote"):
                out.append("> 고른 글: " + _one(x["quote"], 400))
            out += ["", "**물음**: " + str(x.get("q") or "").strip(), "", "**답**: " + str(x.get("a") or "").strip(), ""]
    return {"논문\\%s.md" % stem: "\n".join(out)}, {"notes_n": len(notes_) + len(qa)}


def _tr_files(p):
    _n, study, _m = _mods()
    f, stem = p["file"], p["stem"]
    body = re.sub(r"\A\s*#\s[^\n]*\n", "", _read(p["tp"]), count=1)
    eqs = {}
    try:
        d = study._ei().load(f) or {}
        for e in d.get("eqs") or []:
            if isinstance(e, dict) and e.get("img") and e.get("latex"):
                eqs[e["img"]] = e
    except Exception:
        eqs = {}

    def eq(m):
        e = eqs.get(m.group(1))
        if not e:
            return "(수식 — 원문 PDF 참조)"
        return "$$%s$$%s%s" % (re.sub(r"\s+", " ", str(e["latex"])), (" (식 %s)" % e["n"]) if e.get("n") else "", " (전사가 불완전할 수 있음)" if e.get("partial") else "")

    body = re.sub(r"\[\[EQ:([^\]\s]+)\]\]", eq, body)
    body = re.sub(r"\[s\d+\] ?", "", body).strip()
    head = ["# %s — 전문 번역" % p["title"], "",
            "> %s%s · %s · %s · 요약과 메모는 `논문\\%s.md`" % ("★ " if p["top"] else "", p["journal"], p["year"], p["author"], stem),
            "> Athenaeum 의 Claude 가 원문을 한국어로 옮긴 글입니다. 수식은 그림에서 전사한 LaTeX 이거나 '(수식 — 원문 PDF 참조)' 로 남아 있습니다. 수치가 중요하면 원문 PDF 로 확인합니다.", ""]
    return {"번역\\%s.md" % stem: "\n".join(head) + body + "\n"}, {}


# ---------- 연구 노트 ----------
def _turn_parts(notes, th):
    """대화를 2만 자 안팎의 묶음으로 → [(첫 턴, 끝 턴, md 줄)] (턴 번호는 1부터). 묶음은 Claude 의 답에서 시작하지 않는다(물음과 답이 갈리지 않게)."""
    blocks = []                                    # 물음(이나 가져온 글) 하나와 그 뒤의 답들 = 한 덩이 [첫 턴, 끝 턴, md 줄, 크기]
    for i, m in enumerate(th):
        md = notes._md_turns([m])
        n = sum(len(x) + 1 for x in md)
        if blocks and m.get("role") == "claude":
            blocks[-1][1], blocks[-1][3] = i + 1, blocks[-1][3] + n
            blocks[-1][2] += md
        else:
            blocks.append([i + 1, i + 1, list(md), n])
    parts, cur, size, a, b = [], [], 0, 1, 0
    for a2, b2, md, n in blocks:
        if cur and size + n > TURNS_PART:
            parts.append((a, b, cur))
            cur, size, a = [], 0, a2
        cur += md
        size += n
        b = b2
    if cur:
        parts.append((a, b, cur))
    return parts


def _turn_toc(th, parts):
    """요지에 싣는 대화의 차례 — 묶음마다 날짜와 연구자의 물음 한 줄씩. 앞선 대화가 필요할 때 그 묶음만 열게."""
    L = ["## 대화의 차례 (오래된 것부터 — 필요한 묶음만 엽니다)"]
    for k, (a, b, _md) in enumerate(parts):
        seg = th[a - 1:b]
        L.append("### `대화 %d.md` — %s ~ %s · %d~%d번째 말" % (k + 1, _day(seg[0].get("t")), _day(seg[-1].get("t")), a, b))
        for m in seg:
            if m.get("role") == "claude":
                continue
            L.append("- %s %s%s" % (time.strftime("%m-%d %H:%M", time.localtime(m.get("t") or 0)), "[Claude 채팅에서 가져온 글] " if m.get("role") == "chat" else "", _one(m["text"], 100)))
    return L + [""]


def _note_files(nt, name):
    notes, _s, _m = _mods()
    base = "연구노트\\%s\\" % name
    th = [m for m in nt.get("thread") or [] if m.get("text")]
    asks = sum(1 for m in th if m.get("role") == "user")
    ans = sum(1 for m in th if m.get("role") == "claude")
    chat = sum(1 for m in th if m.get("role") == "chat")
    long_ = len(th) > 8 or sum(len(m["text"]) for m in th) > 14000
    parts = _turn_parts(notes, th) if long_ else []
    many = len(parts) > 1
    recent = []                                                   # 요지에 싣는 최근 대화: 여섯 턴까지, 9천 자 안에서(적어도 둘)
    for m in reversed(th):
        if len(recent) >= 6 or (len(recent) >= 2 and sum(len(x["text"]) for x in recent) + len(m["text"]) > 9000):
            break
        recent.insert(0, m)
    plan = notes._md_plan(nt) + notes._md_objections(nt)
    intro = notes._md_intro(nt)
    isask = (nt.get("kind") or "topic") == "ask"
    last_t = max([nt.get("updated") or 0, nt.get("t") or 0] + [m.get("t") or 0 for m in th])
    files = ["요지.md (이 파일)"] + ([("대화 1.md ~ 대화 %d.md (대화 전체 — 아래 「대화의 차례」 를 보고 필요한 묶음만)" % len(parts)) if many else ("대화.md (대화 전체 %d턴)" % len(th))] if long_ else []) + (["계획.md (큰 물음 · 틀 · 설계서 · 점검 · 반론)"] if plan else []) + (["서론.md (서론 구조와 쓴 문단)"] if intro else [])
    head = ["# " + (nt.get("title") or ""), "",
            "- 종류: " + ("물음 (분야에 관해 묻고 배우는 대화)" if isask else "연구 주제 (상태: %s)" % notes.STATUS_KO.get(nt.get("status"), "아이디어")),
            "- 만든 날 %s · 마지막으로 바뀐 날 %s" % (_day(nt.get("t")), _day(last_t)),
            "- 대화: 연구자의 물음 %d · Athenaeum 의 Claude 가 한 답 %d%s" % (asks, ans, (" · Claude 채팅에서 가져온 글 %d" % chat) if chat else ""),
            "- 이 폴더의 파일: " + " · ".join(files),
            "- 이 주제에 글을 남길 때 첫 줄: `노트: %s`  (남기는 법은 맨 위 폴더의 `읽어보기.md`)" % name, ""]
    picks = [t for t in (notes._forest_text(nt.get("forest"), brief=True), notes._trees_text(nt.get("trees"), brief=True), notes._framing_text(nt.get("framing"), brief=True)) if t]
    body = notes._md_card(nt)
    if picks:
        body += ["## 연구자가 고른 방향 (자세한 것은 계획.md)"] + picks + [""]
    if th and long_:
        if many:
            body += _turn_toc(th, parts)
        body += ["## 최근 대화 (마지막 %d턴 — 앞부분은 %s)" % (len(recent), "위 차례의 묶음에" if many else "대화.md")] + notes._md_turns(recent)
    elif th:
        body += ["## 대화"] + notes._md_turns(th)
    out = {base + "요지.md": "\n".join(head + body)}
    for k, (a, b, md) in enumerate(parts):
        out[base + ("대화 %d.md" % (k + 1) if many else "대화.md")] = "\n".join(
            ["# %s — 대화%s" % (nt.get("title") or "", (" %d/%d (%d~%d번째 말)" % (k + 1, len(parts), a, b)) if many else " 전체"), "",
             "> 연구자와 Athenaeum 의 Claude 가 나눈 토의입니다(오래된 것부터). 요지와 대화의 차례는 `요지.md`.", ""] + md)
    if plan:
        out[base + "계획.md"] = "\n".join(["# %s — 계획" % (nt.get("title") or ""), "", "> 큰 물음 · 작은 물음 · 연구의 틀 · 실험 설계서 · 점검(식별력 표 · 약속과 답 · 공백 검증) · 심사위원의 반론. Athenaeum 의 Claude 가 세운 것이고, 고른 것과 고친 것은 연구자의 것입니다.", ""] + plan)
    if intro:
        out[base + "서론.md"] = "\n".join(["# %s — 서론" % (nt.get("title") or ""), ""] + intro)
    lastq = next((m["text"] for m in reversed(th) if m.get("role") == "user"), "")
    meta = {"kind": "ask" if isask else "topic", "status": notes.STATUS_KO.get(nt.get("status"), "아이디어"), "asks": asks, "chat": chat, "t": last_t,
            "has": [x for x, ok in (("카드", nt.get("card")), ("정한 것", nt.get("decisions")), ("계획", plan), ("서론", intro)) if ok], "lastq": _one(lastq, 110)}
    return out, meta


# ---------- 공부 ----------
def _study_files(st, name):
    notes, study, _m = _mods()
    base = "공부\\%s\\" % name
    ch = st.get("chapter") or {}
    parts = study.md_parts(st)
    stt = st.get("stats") or {}
    sec_files = [("%d %s.md" % (n, _clean(title, 60)), n, title, md) for n, title, md in parts["sections"]]
    head = ["# " + (ch.get("title") or st.get("topic") or ""), ""]
    if ch.get("scope"):
        head += ["> " + _one(ch["scope"], 600), ""]
    head += ["- 서재의 논문 %d편으로 씀 · %s · 문장 %s%s%s" % (len(parts["refs"]), _day(st.get("t")), stt.get("units", "?"), (" · 그림 %s" % stt["figs"]) if stt.get("figs") else "", (" · 식 %s" % stt["eqs"]) if stt.get("eqs") else ""),
             "- Athenaeum 의 「공부」 가 연구자의 서재에 있는 논문만으로 쓴 글입니다. 사실 문장은 논문의 원문 문장에 묶여 있고 원문과 대조했습니다(대조도 Claude 의 판정). 문장 끝의 [번호] 는 아래 참고문헌 번호입니다.", "",
             "## 이 폴더의 파일"] + ["- `%s`" % fn for fn, _n2, _t, _md in sec_files] + ["- `근거 문장.md` — 문장의 근거인 논문의 원문 문장(영어)"] + (["- `물은 것.md` — 이 장을 놓고 연구자가 물은 것과 답"] if parts["chats"] else []) + [""]
    try:
        ref_block = notes._study_ref_block(st, letter="C", cap=10 ** 9)[0]
    except Exception:
        ref_block = ""
    body = []
    if ref_block:
        # 연구 노트가 물음에 붙이는 것과 같은 글(논지 · 식 · 용어). 머리의 제목·범위 줄과 '답하지 못한 것'(줄여 적은 것)은 여기 따로 있으니 빼고, 제목은 한 단계 내린다
        k = ref_block.find("\n## ")
        ref_block = ref_block[k + 1:] if k >= 0 else ref_block
        ref_block = re.sub(r"(?s)^## 이 서재로는 답하지 못한 것\n.*?(?=^## |\Z)", "", ref_block, flags=re.M)
        ref_block = re.sub(r"(?m)^## ", "### ", ref_block).strip()
        body += ["## 절마다의 논지 (C절.번호 = 그 절의 논지)", "", ref_block, ""]
    if parts["summary"]:
        body += ["## 핵심 정리", "", parts["summary"], ""]
    if ch.get("gaps"):
        body += ["## 이 서재로는 답하지 못한 것 (전부)", ""] + ["- " + str(g) for g in ch["gaps"]] + [""]
    body += ["## 참고문헌 ([번호] · 서재의 파일 이름 — `논문\\<이름>.md` 에 요약)", ""] + ["[%d] %s%s" % (n, ref, ("  · 파일: " + os.path.splitext(f)[0]) if f else "") for n, ref, f in parts["refs"]] + [""]
    out = {base + "개요.md": "\n".join(head + body)}
    for fn, _n2, _t, md in sec_files:
        out[base + fn] = md
    if parts["evidence"]:
        out[base + "근거 문장.md"] = "# %s — 근거 문장\n\n> 장의 문장이 기대는 논문의 원문 문장입니다(영어 그대로). 괄호 안은 문장 번호와 쪽.\n\n%s" % (ch.get("title") or "", parts["evidence"])
    if parts["chats"]:
        out[base + "물은 것.md"] = "# %s — 물은 것\n\n> 이 장을 놓고 연구자가 Athenaeum 의 Claude 에게 물은 것과 답입니다.\n\n%s" % (ch.get("title") or "", parts["chats"])
    meta = {"t": st.get("t") or 0, "papers": len(parts["refs"]), "secs": len(parts["sections"]), "scope": _one(ch.get("scope"), 200), "chats": len(st.get("chats") or [])}
    return out, meta


# ---------- 원고 ----------
def _ms_files(doc, name):
    _n, _s, ms = _mods()
    base = "원고\\%s\\" % name
    title = str((doc.get("front") or {}).get("title") or doc.get("title") or "원고")
    body = ms._mirror_body(doc)
    dirs = [str(d.get("path")) for d in doc.get("source_dirs") or [] if d.get("path")]
    memos = [m for m in doc.get("memos") or [] if isinstance(m, dict) and (m.get("thread") or m.get("note"))]
    arg = ""
    if doc.get("argument"):
        try:
            arg = ms.argument_text(doc, cap=10 ** 6) or ""
        except Exception:
            arg = ""
    head = ["# " + title, "",
            "> 연구자가 Athenaeum 에서 쓰고 있는 논문의 지금 글입니다(읽기 전용 사본). 글을 바꾸려면 바꿀 글을 제안하고, 연구자가 Athenaeum 에서 반영합니다.",
            "- 투고할 저널: " + str(((doc.get("meta") or {}).get("journal")) or "(정하지 않음)")]
    if dirs:
        head.append("- 작업 자료 폴더(결정·문헌 노트 — 연구자와 Claude 가 같이 쓰는 곳): " + " · ".join(dirs))
    head.append("- 이 폴더의 파일: 원고.md (이 파일)" + (" · 대화.md (글을 놓고 Claude 와 나눈 대화 %d개)" % len(memos) if memos else "") + (" · 논증.md (물음·주장·결과의 짜임)" if arg else ""))
    out = {base + "원고.md": "\n".join(head + ["", body or "(아직 글이 없습니다)", ""])}
    if memos:
        heads = {n.get("id"): n.get("heading") or "" for n in doc.get("outline") or []}
        L = ["# %s — 대화" % title, "", "> 원고의 글을 고르거나 절·원고 전체를 놓고 연구자가 Athenaeum 의 Claude 에게 물은 것입니다(오래된 것부터). 대안 글은 싣지 않았습니다.", ""]
        for m in sorted(memos, key=lambda m: m.get("t") or 0):
            t = m.get("t") or 0
            t = t / 1000.0 if t > 1e11 else t
            L.append("## %s — %s" % (_day(t), "초록·머리부" if m.get("node") == "front" else (heads.get(m.get("node")) or "원고 전체")))
            if m.get("quote"):
                L.append("> 고른 글: " + _one(m["quote"], 500))
            if m.get("note"):
                L.append("메모: " + str(m["note"]).strip())
            L.append("")
            for x in m.get("thread") or []:
                if not isinstance(x, dict) or x.get("quiet") or not str(x.get("text") or "").strip():
                    continue
                L += ["**%s**: %s" % ("나" if x.get("role") == "user" else "Claude", str(x["text"]).strip()), ""]
        out[base + "대화.md"] = "\n".join(L)
    if arg:
        out[base + "논증.md"] = "# %s — 논증\n\n> Athenaeum 의 Claude 가 원고의 글에서 세운 짜임입니다(연구 물음 · 공백 · 결과 · 주장 · 서론의 약속과 답 · 문제).\n\n%s\n" % (title, arg)
    chars = sum(len(str(n.get("draft") or "")) + len(str(n.get("draft_en") or "")) for n in doc.get("outline") or [])
    return out, {"t": doc.get("updated") or doc.get("created") or 0, "secs": len(doc.get("outline") or []), "chars": chars, "memos": len(memos), "journal": str(((doc.get("meta") or {}).get("journal")) or "")}


# ---------- 그 밖: 연구실 · 탐색 기록 ----------
def _lab_files():
    notes, _s, _m = _mods()
    d = notes.lab_get() or {}
    if not (str(d.get("text") or "").strip() or str(d.get("field") or "").strip() or str(d.get("page") or "").strip()):
        return {}, {}
    L = ["# 우리 연구실", "", "> 연구자가 Athenaeum 의 연구 노트에 적어 둔 것입니다. 실험·측정을 이야기할 때 여기 있는 장비로 할 수 있는지 먼저 가립니다. 여기 없는 장비·측정은 외부에 맡겨야 하는 것으로 봅니다.", ""]
    if str(d.get("text") or "").strip():
        L += ["## 장비와 할 수 있는 것", str(d["text"]).strip(), ""]
    if str(d.get("field") or "").strip():
        L += ["## 분야 지형 (핵심 저자·그룹, 저널의 등급, 우리 자리)", str(d["field"]).strip(), ""]
    if str(d.get("page") or "").strip():
        L += ["## 연구실 홈페이지에서 읽은 글%s" % ((" (" + str(d["url"]) + ")") if d.get("url") else ""), str(d["page"]).strip()[:12000], ""]
    return {"연구실.md": "\n".join(L)}, {}


def _hist_files():
    d = cfg["load_json"](os.path.join(cfg["HIST_DIR"], "_목록.json"), {}) or {}
    items = d.get("items") if isinstance(d, dict) else d
    items = [x for x in (items or []) if isinstance(x, dict)]
    if not items:
        return {}, {}
    L = ["# 탐색 기록", "", "> 연구자가 Athenaeum 의 논문 탐색(OpenAlex·Scopus)으로 찾아본 주제입니다. 결과 편수와 그 가운데 서재에 있는 편수.", ""]
    for x in sorted(items, key=lambda x: -(x.get("t") or 0))[:60]:
        subs = [str(s if isinstance(s, str) else (s.get("name") or s.get("title") or "")) for s in (x.get("subs") or x.get("topics") or x.get("groups") or [])][:8]
        L.append("- %s 「%s」 — %s편(서재에 %s편)%s%s%s" % (_day(x.get("t")), _one(x.get("q"), 160), x.get("n", "?"), x.get("owned", "?"),
                                                       (" · 저널: " + ", ".join(str(j) for j in (x.get("journals") or [])[:5])) if x.get("journals") else "",
                                                       (" · 소주제: " + ", ".join(s for s in subs if s)) if any(subs) else "",
                                                       ("\n  뜻: " + _one(x["intent"], 200)) if x.get("intent") else ""))
    return {"탐색 기록.md": "\n".join(L) + "\n"}, {}


# ---------- 차례 · 읽어보기 ----------
def _index_files(papers, metas, out):
    """차례 파일들. metas = {key: {name, title, meta}} (연구 노트·공부·원고)"""
    notes, _s, _m = _mods()
    F = {}
    # 논문
    n = len(papers)
    by_year = {}
    for p in papers:
        by_year.setdefault(p["year"] or "연도 모름", []).append(p)
    L = ["# 서재의 논문 %d편" % n, "",
         "파일 이름은 `연도_저널_저자_제목` 입니다. 같은 이름으로 `논문\\<이름>.md`(요약 + 연구자의 메모·질문)와 `번역\\<이름>.md`(전문 번역)가 있습니다.",
         "★ = 연구자가 정한 우선 저널(이 분야의 최고 저널). 주제로 찾으려면 `_라벨.md`(라벨 목록) → `라벨\\<라벨>.md`(그 라벨의 논문과 한 줄 요약)를 봅니다.", ""]
    for y in sorted(by_year, reverse=True):
        L.append("## %s (%d편)" % (y, len(by_year[y])))
        for p in sorted(by_year[y], key=lambda p: (not p["top"], p["journal"], p["author"])):
            flags = ("★ " if p["top"] else "") + ("[리뷰] " if p["review"] else "")
            miss = [x for x, ok in (("요약 없음", p["has_summary"]), ("번역 없음", p["has_translation"])) if not ok]
            L.append("- %s%s%s" % (flags, p["stem"], (" (" + " · ".join(miss) + ")") if miss else ""))
        L.append("")
    F["논문\\_차례.md"] = "\n".join(L)
    # 주제로 찾기: 라벨 목록(묶음 → 하위 분류 → 라벨과 편수) → 라벨마다 파일 하나(그 논문들과 한 줄 요약). 한 파일이 작아서 필요한 것만 읽힌다
    groups = cfg["load_json"](cfg["LABELS_PATH"], {}) or {}
    sysd = groups.get("_체계") if isinstance(groups.get("_체계"), dict) else {}
    tree = sysd.get("트리") if isinstance(sysd.get("트리"), dict) else {}
    by_label = {}
    for p in papers:
        for lb in p["labels"]:
            by_label.setdefault(str(lb), []).append(p)
    order = lambda ps: sorted(ps, key=lambda p: (p["year"], p["top"], p["journal"], p["author"]), reverse=True)

    def gist(p, n=170):      # 한 줄에 이름과 요약을 같이 — 낱말로 찾았을 때 그 줄만 봐도 어느 논문인지 안다
        return "- %s%s%s%s" % ("★ " if p["top"] else "", "[리뷰] " if p["review"] else "", p["stem"], (" — " + _one(p["line"], n)) if p["line"] else "")

    lab_file, same = {}, {}
    for lb in sorted(by_label):
        lab_file[lb] = _clean(lb, 60)
        same.setdefault(lab_file[lb].lower(), []).append(lb)
    for labs in same.values():      # 파일 이름이 겹치는 라벨(대소문자만 다른 것 등)은 모두 뒤에 표식 — 순서에 따라 이름이 바뀌지 않게
        if len(labs) > 1:
            for lb in labs:
                lab_file[lb] = "%s (%s)" % (lab_file[lb][:54], hashlib.md5(lb.encode("utf-8")).hexdigest()[:4])
    item = lambda lb: "%s (%d)" % (lab_file[lb], len(by_label[lb]))
    L = ["# 라벨 목록 — 논문을 주제로 찾기", "",
         "연구자의 라벨 체계입니다(묶음 → 하위 분류 → 라벨). 라벨마다 `논문\\라벨\\<라벨>.md` 에 그 라벨이 붙은 논문과 한 줄 요약이 있습니다. 괄호 안은 편수.",
         "두 라벨에 다 걸리는 논문을 찾을 때는 편수가 적은 쪽 파일을 엽니다.", ""]
    seen = set()
    for g, labs in groups.items():
        if g.startswith("_") or not isinstance(labs, list):
            continue
        have = [str(lb) for lb in labs if by_label.get(str(lb))]
        if not have:
            continue
        L.append("## " + g)
        left = list(have)
        for sub in tree.get(g) or []:
            kids = [k for k in (sub.get("kids") or []) if k in left] if isinstance(sub, dict) else []
            if kids:
                L.append("- **%s**: %s" % (sub.get("name") or "", " · ".join(item(k) for k in kids)))
                left = [k for k in left if k not in kids]
        if left:
            L.append(("- **그 밖**: " if len(left) < len(have) else "- ") + " · ".join(item(k) for k in left))
        L.append("")
        seen.update(have)
    rest = sorted(lb for lb in by_label if lb not in seen)
    if rest:
        L += ["## 체계에 없는 라벨", "- " + " · ".join(item(lb) for lb in rest), ""]
    # 한 줄 요약 전체 — 라벨로 못 찾는 주제를 훑을 때. 2만 자 남짓씩 나눈다(최근 것부터). 파일 이름은 번호뿐이라 바뀌지 않고, 어느 해가 어디 있는지는 _라벨.md 끝에
    part, size, parts = [], 0, []
    for p in order([p for p in papers if p["line"]]):
        g2 = gist(p)
        if part and size + len(g2) + 1 > 22000:
            parts.append(part)
            part, size = [], 0
        part.append((p, g2))
        size += len(g2) + 1
    if part:
        parts.append(part)
    if parts:
        L += ["## 라벨로 못 찾을 때 — 한 줄 요약 전체",
              "논문마다 한 줄씩, 최근 것부터 나눠 담았습니다. 파일 안을 낱말로 찾는 도구가 있으면 이 파일들에서 찾습니다(한 줄에 논문 이름과 요약이 같이 있습니다)."]
        for i, part in enumerate(parts):
            ys = sorted(p["year"] for p, _g in part if p["year"])
            span = ("연도 모름" if not ys else (ys[0] + "년") if ys[0] == ys[-1] else "%s~%s년" % (ys[0], ys[-1])) + ((" · 연도 모름 %d편 포함" % (len(part) - len(ys))) if ys and len(ys) < len(part) else "")
            L.append("- `논문\\_한줄요약 %d.md` — %s · %d편" % (i + 1, span, len(part)))
            F["논문\\_한줄요약 %d.md" % (i + 1)] = "\n".join(["# 서재의 논문 — 한 줄 요약 %d/%d (%s · %d편)" % (i + 1, len(parts), span, len(part)), "",
                "논문마다 무엇을 어떤 재료·조건으로 했는지 한 줄입니다(최근 것부터). ★ = 우선 저널. 자세한 것은 `논문\\<이름>.md`.", ""] + [g2 for _p, g2 in part]) + "\n"
        L.append("")
    F["논문\\_라벨.md"] = "\n".join(L)
    gof = {str(lb): g for g, labs in groups.items() if isinstance(labs, list) and not g.startswith("_") for lb in labs}
    for lb, ps in by_label.items():
        cut = 170 if len(ps) <= 70 else (110 if len(ps) <= 110 else 60)      # 편수가 많은 라벨은 요약을 짧게 — 파일 하나가 2만 자 남짓
        F["논문\\라벨\\%s.md" % lab_file[lb]] = "\n".join(["# 라벨: %s%s — %d편" % (lb, (" (" + gof[lb] + ")") if gof.get(lb) else "", len(ps)), "",
            "최근 것부터. 파일 이름 그대로 `논문\\<이름>.md`(요약과 메모) · `번역\\<이름>.md`(전문 번역)를 엽니다. ★ = 우선 저널.", ""] + [gist(p, cut) for p in order(ps)]) + "\n"
    # 연구 노트
    nts = sorted(((k, v) for k, v in metas.items() if k.startswith("note:")), key=lambda kv: -(kv[1]["meta"].get("t") or 0))
    L = ["# 연구 노트 %d개" % len(nts), "", "연구자가 Athenaeum 의 Claude 와 연구 주제·분야를 놓고 나눈 토의입니다. 주제마다 폴더 하나 — 먼저 그 폴더의 `요지.md` 를 읽습니다(카드 · 정한 것 · 최근 대화).", ""]
    for kind, title in (("topic", "연구 주제"), ("ask", "물음 (분야에 관해 묻고 배우는 대화)")):
        rows = [(k, v) for k, v in nts if v["meta"].get("kind") == kind]
        if not rows:
            continue
        L.append("## " + title)
        for _k, v in rows:
            m = v["meta"]
            L.append("- `%s\\` — %s물음 %d · 마지막 %s%s%s" % (v["name"], (m.get("status") + " · ") if kind == "topic" else "", m.get("asks", 0), _day(m.get("t")),
                                                          (" · " + " · ".join(m["has"])) if m.get("has") else "", (" · 채팅에서 가져온 글 %d" % m["chat"]) if m.get("chat") else ""))
            if m.get("lastq"):
                L.append("  마지막 물음: " + m["lastq"])
        L.append("")
    F["연구노트\\_차례.md"] = "\n".join(L)
    # 공부
    sts = sorted(((k, v) for k, v in metas.items() if k.startswith("study:")), key=lambda kv: -(kv[1]["meta"].get("t") or 0))
    L = ["# 공부 %d장" % len(sts), "", "한 분야를 연구자의 서재에 있는 논문만으로 정리한 글입니다(교과서 한 장). 장마다 폴더 하나 — 먼저 `개요.md`(차례 · 절마다의 논지 · 답하지 못한 것 · 참고문헌), 그다음 필요한 절.", ""]
    for _k, v in sts:
        m = v["meta"]
        L += ["- `%s\\` — %s · 논문 %d편 · 절 %d%s" % (v["name"], _day(m.get("t")), m.get("papers", 0), m.get("secs", 0), (" · 물은 것 %d" % m["chats"]) if m.get("chats") else "")] + (["  " + m["scope"]] if m.get("scope") else [])
    F["공부\\_차례.md"] = "\n".join(L) + "\n"
    # 원고
    mss = sorted(((k, v) for k, v in metas.items() if k.startswith("ms:")), key=lambda kv: -(kv[1]["meta"].get("t") or 0))
    if mss:
        L = ["# 원고 %d편" % len(mss), "", "연구자가 Athenaeum 에서 쓰고 있는 논문입니다. 원고마다 폴더 하나 — `원고.md`(지금 글 전체) · `대화.md` · `논증.md`.", ""]
        for _k, v in mss:
            m = v["meta"]
            L.append("- `%s\\` — %s · 절 %d · 글 %s자%s%s" % (v["name"], _day(m.get("t")), m.get("secs", 0), format(m.get("chars", 0), ","), (" · 대화 %d" % m["memos"]) if m.get("memos") else "", (" · 저널 " + m["journal"]) if m.get("journal") else ""))
        F["원고\\_차례.md"] = "\n".join(L) + "\n"
    # 읽어보기
    n_sum, n_tr = sum(1 for p in papers if p["has_summary"]), sum(1 for p in papers if p["has_translation"])
    recent = sorted(((v["meta"].get("t") or 0, k, v) for k, v in metas.items()), reverse=True)[:8]
    kind_ko = {"note": "연구노트", "study": "공부", "ms": "원고"}
    try:
        rl = (cfg["load_readlog"]() or {}).get("papers") or {}
    except Exception:
        rl = {}
    read = sorted(((v.get("last") or 0, f) for f, v in rl.items() if isinstance(v, dict) and v.get("last")), reverse=True)[:8]
    R = ["# Athenaeum 서재 — Claude 가 읽는 사본", "",
         "이 폴더는 연구실 PC 의 Athenaeum(논문을 모으고, 읽고, 공부하고, 쓰는 프로그램)이 자동으로 만들어 두는 **읽기 전용 사본**입니다.",
         "원본은 Athenaeum 안에 있습니다. 여기 파일은 Athenaeum 이 다시 쓰므로 고쳐도 남지 않습니다(남기는 길은 맨 아래).",
         "",
         "지금 들어 있는 것: 논문 %d편(요약 %d · 전문 번역 %d) · 연구 노트 %d · 공부 %d장 · 원고 %d편" % (n, n_sum, n_tr, len(nts), len(sts), len(mss)),
         "",
         "## 폴더",
         "- `연구노트\\` — 연구 주제·물음마다 폴더 하나. 연구자가 Athenaeum 의 Claude 와 나눈 토의입니다.",
         "  `_차례.md`(주제 목록) → `<주제>\\요지.md`(카드 · 연구자가 정한 것 · 대화의 차례 · 최근 대화) · `대화.md`(대화 전체 — 길면 `대화 1.md`, `대화 2.md` … 로 나뉩니다) · `계획.md`(큰 물음 · 틀 · 설계서 · 반론) · `서론.md`",
         "- `공부\\` — 한 분야를 서재의 논문만으로 정리한 글. `_차례.md` → `<장>\\개요.md` → 절마다 파일(`1 ….md`) · `근거 문장.md`",
         "- `논문\\` — 서재의 논문. `_차례.md`(전부, 연도별) · `_라벨.md`(라벨 목록) → `라벨\\<라벨>.md`(그 라벨의 논문과 한 줄 요약) · `<연도_저널_저자_제목>.md`(요약 + 연구자의 메모·질문)",
         "- `번역\\` — 전문 번역(한국어). 파일 이름은 `논문\\` 과 같습니다.",
         "- `원고\\` — 쓰고 있는 논문. `<제목>\\원고.md` · `대화.md`",
         "- `연구실.md` — 우리 연구실의 장비와 할 수 있는 것 · `탐색 기록.md` — 지난 논문 탐색",
         "- `%s\\` — 이 대화에서 정리한 것을 Athenaeum 으로 보내는 자리(맨 아래)" % INBOX,
         "",
         "## 찾는 차례",
         "1. 연구 주제를 이어서 이야기한다 → `연구노트\\_차례.md` 에서 주제를 찾아 그 폴더의 `요지.md` 를 읽습니다. 앞선 대화가 필요하면 요지의 「대화의 차례」 에서 그 물음이 든 묶음만 엽니다.",
         "2. 어떤 논문이 있는지 → 주제로 찾을 때는 `논문\\_라벨.md` 에서 라벨을 고르고 `논문\\라벨\\<라벨>.md` 를 엽니다(그 논문들과 한 줄 요약). 저자·연도로 찾을 때는 `논문\\_차례.md`. 라벨로 못 찾으면 `논문\\_한줄요약 1.md` …(어느 해가 어느 파일인지는 `_라벨.md` 끝에).",
         "3. 논문 한 편의 내용 → `논문\\<이름>.md`. 수치·조건·방법을 정확히 봐야 하면 `번역\\<이름>.md`.",
         "4. 한 분야의 정리 → `공부\\_차례.md` → 그 장의 `개요.md` → 필요한 절.",
         "",
         "전부 읽지 않습니다. 차례를 보고 필요한 파일만 엽니다. 전문 번역은 큽니다(파일 하나에 2만~10만 자) — 요약으로 모자랄 때만 엽니다.",
         "큰 파일은 머리에 「이 파일의 차례」(제목과 줄 번호)가 있습니다. 통째로 읽지 말고 그 줄부터 필요한 절만 읽습니다.",
         "파일 안을 낱말로 찾는 도구가 있으면 `논문\\_한줄요약 *.md`(논문 이름과 요약이 한 줄에)나 `번역\\` 에서 찾고, 걸린 파일만 엽니다.",
         "",
         "## 답할 때",
         "- 서재의 논문은 (첫 저자 연도)로 가리킵니다. 서재에서 확인한 것과 일반 지식을 갈라 말하고, 서재에 없는 것은 \"서재 밖\" 이라고 밝힙니다.",
         "- ★ 는 연구자가 정한 우선 저널(이 분야의 최고 저널)입니다. 그 밖의 저널에 실린 논문 한 편의 결론은 조건을 붙인 보기로 다룹니다.",
         "- 요약과 번역은 Athenaeum 의 Claude 가 만든 것입니다. 중요한 수치는 전문 번역에서 다시 확인하고, 그래도 의심스러우면 원문 PDF 를 보라고 말합니다.",
         "- 공부 장의 문장 끝 [번호] 는 그 장의 참고문헌 번호입니다. \"Claude 의 생각\" 은 서재로 확인한 것이 아니라 풀이입니다.",
         "- 연구 노트의 \"연구자가 정한 것\" 은 이미 정해진 방향입니다. 그 안에서 답하고, 바꾸자고 하려면 까닭을 댑니다.",
         "- 카드·설계서·원고는 연구자의 문서입니다. 고쳐 쓰지 않고 고칠 곳을 제안합니다.",
         "- 실험과 측정은 `연구실.md` 에 있는 장비로 할 수 있는지부터 가립니다.",
         ""]
    if recent:
        R += ["## 최근에 바뀐 것"] + ["- %s `%s\\%s\\`" % (_day(t), kind_ko.get(k.split(":")[0], ""), v["name"]) for t, k, v in recent] + [""]
    if read:
        R += ["## 연구자가 최근에 읽은 논문"] + ["- %s %s" % (_day(t), os.path.splitext(f)[0]) for t, f in read] + [""]
    R += ["## Athenaeum 에 남기기",
          "이 대화에서 정리한 것을 연구자가 Athenaeum 에 남기라고 하면, `%s\\` 폴더에 md 파일을 하나 씁니다. Athenaeum 이 30초 안에 가져가 그 연구 노트의 대화에 \"Claude 채팅에서 가져온 글\" 로 붙입니다." % INBOX,
          "쓰는 형식은 그 폴더의 `%s` 에 있습니다. 첫 줄에 `노트: <연구노트 폴더 이름>` 을 적는 것이 전부입니다." % INBOX_HELP, ""]
    F["읽어보기.md"] = "\n".join(R)
    ex = nts[0][1]["name"] if nts else "연구 노트 이름"      # 보기로 드는 이름 — 가장 최근에 바뀐 노트
    F[INBOX + "\\" + INBOX_HELP] = "\n".join([
        "# Athenaeum 에 남기기", "",
        "이 폴더에 md 파일을 하나 쓰면 Athenaeum 이 30초 안에 가져가, 그 연구 노트의 대화에 \"Claude 채팅에서 가져온 글\" 로 붙입니다.",
        "가져간 파일은 `%s\\` 로 옮겨집니다. Athenaeum 안의 Claude 는 다음 물음부터 그 글을 지난 대화로 읽습니다." % INBOX_DONE, "",
        "## 형식", "첫 줄에 어느 연구 노트인지 적고, 한 줄 띄운 뒤 글을 씁니다.", "",
        "    노트: %s" % ex, "", "    (정리한 글)", "",
        "- 노트 이름은 `연구노트\\_차례.md` 에 있는 폴더 이름 그대로 적습니다.",
        "- 새 주제로 남기려면 `노트: 새 노트 — 제목` 으로 적습니다.",
        "- 파일 이름은 자유입니다(날짜와 주제를 적어 두면 나중에 찾기 좋습니다). 이름이 `_` 로 시작하는 파일은 가져가지 않습니다.", "",
        "## 무엇을 적나",
        "- 이 대화에서 정한 것, 새로 알게 된 것, 다음에 할 일. 대화 전체를 옮기지 않고 연구자가 나중에 읽을 한 장으로 씁니다.",
        "- 서재의 논문은 (첫 저자 연도)로, 서재에 없는 지식은 \"서재 밖:\" 으로 갈라 적습니다.",
        "- 연구자가 남기라고 할 때만 씁니다.", ""])
    return F


# ---------- 되가져오기: Claude 채팅이 쓴 글 → 연구 노트의 대화 ----------
_HDR = re.compile(r"^\s*(?:#+\s*)?[*_`]*\s*(?:연구\s*)?노트\s*[*_`]*\s*[:：]\s*(.+?)\s*$")


def _norm(s):
    return re.sub(r"[\s\W_]+", "", str(s or "").lower(), flags=re.U)


def _inbox(out, names):
    """「Athenaeum에 남기기」 의 md 를 그 연구 노트의 대화에 붙이고 `_넣음` 으로 옮긴다. names = {폴더 이름: 노트 id} → 붙인 수"""
    notes, _s, _m = _mods()
    d = os.path.join(out, INBOX)
    if not os.path.isdir(d):
        return 0
    held, done, busy = [], 0, False
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if f.startswith("_") or f == INBOX_HELP or not f.lower().endswith((".md", ".txt")) or not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        if time.time() - st.st_mtime < 6 or st.st_size == 0:      # 아직 쓰는 중일 수 있다 — 다음 번에
            busy = True
            continue
        if st.st_size > 400000:
            held.append({"name": f, "why": "글이 너무 깁니다(40만 바이트 넘음) — 요지만 남겨 주세요"})
            continue
        text = _read(p).replace("\r", "").lstrip("﻿")
        lines, target, body = text.split("\n"), "", ""
        for i, ln in enumerate(lines[:12]):
            m = _HDR.match(ln)
            if m:
                target, body = m.group(1).strip().strip("*`「」\"'"), "\n".join(lines[i + 1:]).strip()
                break
        if not target:
            held.append({"name": f, "why": "첫 줄에 `노트: <이름>` 이 없습니다"})
            continue
        if len(body) < 2:
            held.append({"name": f, "why": "글이 비어 있습니다"})
            continue
        nid, new = None, re.match(r"^새\s*노트\s*(?:[—–\-:：]\s*(.*))?$", target)
        if new:
            title = (new.group(1) or "").strip() or body.split("\n")[0][:80]
            r = notes.new_note({"kind": "topic", "title": title})
            nid = (r.get("note") or {}).get("id")
        else:
            items = notes.list_notes()
            if re.match(r"^nt_[0-9_]+$", target) and any(x["id"] == target for x in items):
                nid = target
            elif target in names:
                nid = names[target]
            else:
                v = _norm(target)
                hit = [x for x in items if _norm(x["title"]) == v] or [x for x in items if v and (v in _norm(x["title"]) or _norm(x["title"]) in v)]
                if len(hit) == 1:
                    nid = hit[0]["id"]
                elif len(hit) > 1:
                    held.append({"name": f, "why": "「%s」 에 맞는 노트가 여럿입니다 — 차례의 폴더 이름 그대로 적어 주세요" % target[:60]})
                    continue
        if not nid:
            held.append({"name": f, "why": "「%s」 라는 연구 노트를 찾지 못했습니다" % target[:60]})
            continue
        r = notes.chat_import(nid, body, src=f)
        if r.get("busy"):
            busy = True
            continue                                              # 답을 기다리는 중 — 다음 번에
        if not r.get("ok"):
            held.append({"name": f, "why": r.get("error") or "붙이지 못했습니다"})
            continue
        dd = os.path.join(d, INBOX_DONE)
        os.makedirs(dd, exist_ok=True)
        try:
            os.replace(p, os.path.join(dd, time.strftime("%Y%m%d_%H%M%S_") + f))
        except OSError:
            pass
        done += 1
        _ST["imported"] = ([{"name": f, "note": r.get("title") or "", "t": time.time()}] + _ST["imported"])[:20]
    _ST["held"] = held
    _ST["held_busy"] = busy      # 아직 못 가져간 글이 있다 → 원본이 그대로여도 다음 번에 다시 본다
    return done


# ---------- 맞추기 ----------
def _uniq(rows):
    """[(key, title)] → {key: 폴더 이름}. 같은 제목이 둘이면 둘 다 뒤에 id 의 끝을 붙인다(순서에 따라 이름이 바뀌지 않게)."""
    base = {k: _clean(t) for k, t in rows}
    cnt = {}
    for b in base.values():
        cnt[b.lower()] = cnt.get(b.lower(), 0) + 1
    return {k: (b if cnt[b.lower()] == 1 else "%s (%s)" % (b[:60], re.sub(r"\D", "", k)[-6:] or k[-6:])) for k, b in base.items()}


def sync(force=False):
    """폴더를 지금 자료에 맞춘다 → {ok, changed, removed, files, bytes, sec, errors} | {error}"""
    with _LOCK:
        c = conf()
        out = c["dir"]
        ok, why = _dir_ok(out)
        if not ok:
            return {"error": why}
        t0 = time.time()
        _ST["running"] = True
        try:
            return _sync(out, force, t0)
        finally:
            _ST["running"] = False


def _sync(out, force, t0):
    notes, study, ms = _mods()
    os.makedirs(os.path.join(out, INBOX), exist_ok=True)
    state = _load_state(out)
    if state.get("v") != VERSION:
        force = True
    old = state.get("items") or {}
    errors, plan = [], {}

    # 0) 되가져오기 — 먼저 넣어야 이번 맞춤에 그 글이 같이 나간다
    try:
        names_old = {v.get("name"): k.split(":", 1)[1] for k, v in old.items() if k.startswith("note:") and v.get("name")}
        imported = _inbox(out, names_old)
    except Exception as e:
        imported = 0
        errors.append("남기기 폴더: " + str(e)[:160])

    # 1) 무엇을 만들까 — 항목마다 (sig, 제목, 만드는 함수). 원본이 그대로면 읽지 않는다
    def add(key, sig, title, load, build):
        plan[key] = {"sig": "%d|%s" % (VERSION, sig), "title": title, "load": load, "build": build}

    papers = _papers()
    for p in papers:
        add("paper:" + p["stem"], "%s|%s|%s|%s|%d%d" % (_sig(p["sp"], cfg["notes_path"](p["file"])), p["title"], ",".join(p["labels"]), p["journal"], p["top"], p["review"]), p["title"], None, (lambda p=p: _paper_files(p)))
        if p["has_translation"]:
            add("tr:" + p["stem"], "%s|%s" % (_sig(p["tp"], os.path.join(cfg["GEN_DIR"], "수식색인", p["stem"] + ".json")), p["title"]), p["title"], None, (lambda p=p: _tr_files(p)))
    ndir = os.path.join(cfg["ROOT"], "연구노트")
    for f in sorted(os.listdir(ndir)) if os.path.isdir(ndir) else []:
        if f.startswith("_") or not f.endswith(".json"):
            continue
        nid = f[:-5]
        add("note:" + nid, _sig(os.path.join(ndir, f)), None, (lambda nid=nid: notes._load(nid)), _note_files)
    try:
        sts = [x for x in study.list_studies() if x.get("status") == "done"]
    except Exception as e:
        sts = []
        errors.append("공부 목록: " + str(e)[:120])
    sdir = os.path.join(cfg["ROOT"], "공부")
    for x in sts:
        add("study:" + x["id"], _sig(os.path.join(sdir, x["id"] + ".json")), None, (lambda sid=x["id"]: study._load(sid)), _study_files)
    mdir = os.path.join(cfg["ROOT"], "원고")
    for f in sorted(os.listdir(mdir)) if os.path.isdir(mdir) else []:
        if not f.startswith("ms_") or not f.endswith(".json") or f.startswith("ms_test"):
            continue
        add("ms:" + f[:-5], _sig(os.path.join(mdir, f)), None, (lambda mid=f[:-5]: ms.load_ms(mid)), _ms_files)

    # 2) 제목(폴더 이름) — 바뀐 것만 원본을 읽는다
    loaded = {}
    for key, it in plan.items():
        if it["load"] is None:
            continue
        o = old.get(key)
        if o and o.get("sig") == it["sig"] and o.get("title") is not None and not force:
            it["title"] = o["title"]
            continue
        try:
            obj = it["load"]()
        except Exception as e:
            obj = None
            errors.append("%s: %s" % (key, str(e)[:120]))
        if not isinstance(obj, dict):
            it["skip"] = True
            continue
        loaded[key] = obj
        if key.startswith("study:"):
            if not (obj.get("chapter") or {}).get("sections"):
                it["skip"] = True
                continue
            it["title"] = str((obj.get("chapter") or {}).get("title") or obj.get("topic") or key)
        elif key.startswith("ms:"):
            it["title"] = str((obj.get("front") or {}).get("title") or obj.get("title") or key)
        else:
            it["title"] = str(obj.get("title") or key)
    plan = {k: v for k, v in plan.items() if not v.get("skip")}
    names = {}
    for kind in ("note:", "study:", "ms:"):
        names.update(_uniq([(k, v["title"]) for k, v in plan.items() if k.startswith(kind)]))

    # 3) 만든다
    new, metas, changed, removed, wrote = {}, {}, 0, 0, 0
    for key, it in plan.items():
        o = old.get(key)
        name = names.get(key)
        same = (o and o.get("sig") == it["sig"] and o.get("name") == name and not force and all(os.path.isfile(os.path.join(out, r)) for r in o.get("files") or []))
        if same:
            new[key] = o
        else:
            try:
                if it["load"] is None:
                    files, meta = it["build"]()
                else:
                    obj = loaded.get(key) or it["load"]()
                    files, meta = it["build"](obj, name)
                w = 0
                for rel, text in files.items():
                    w += 1 if _put(out, rel, _finish(text)) else 0
                for r in (o or {}).get("files") or []:
                    if r not in files and _remove(out, r):
                        removed += 1
                new[key] = {"sig": it["sig"], "files": sorted(files), "title": it["title"], "name": name, "meta": meta}
                wrote += w
                changed += 1 if w else 0
            except Exception as e:
                errors.append("%s: %s" % (key, str(e)[:160]))
                if o:
                    new[key] = o
                continue
        if name:
            metas[key] = {"name": name, "title": new[key].get("title"), "meta": new[key].get("meta") or {}}
    for key, o in old.items():                 # 없어진 것(지운 노트·논문) — 내가 만든 파일만 지운다
        if key not in plan and not key.startswith("x:"):
            for r in o.get("files") or []:
                if _remove(out, r):
                    removed += 1

    # 4) 한 개짜리 · 차례 · 읽어보기 (언제나 다시 만들어 보고, 글이 다를 때만 쓴다)
    extra = {}
    for label, fn in (("연구실", _lab_files), ("탐색 기록", _hist_files)):
        try:
            extra.update(fn()[0])
        except Exception as e:
            errors.append("%s: %s" % (label, str(e)[:120]))
    try:
        extra.update(_index_files(papers, metas, out))
    except Exception as e:
        errors.append("차례: " + str(e)[:160])
    for rel, text in extra.items():
        try:
            wrote += 1 if _put(out, rel, text) else 0
        except Exception as e:
            errors.append("%s: %s" % (rel, str(e)[:120]))
    ox = old.get("x:extra") or {}
    for r in ox.get("files") or []:
        if r not in extra and _remove(out, r):
            removed += 1
    new["x:extra"] = {"sig": "", "files": sorted(extra)}

    # 바뀐 것이 하나도 없으면 상태 파일도 다시 쓰지 않는다 (30초마다 도는 일이라 — 클라우드 폴더에 두었을 때 쓸데없이 올라가지 않게)
    dirty = bool(wrote or removed or imported or force or len(new) != len(old) or any(new[k] != old.get(k) for k in new) or not state.get("counts"))
    if dirty:
        files = sum(len(v.get("files") or []) for v in new.values())
        size = 0
        for v in new.values():
            for r in v.get("files") or []:
                try:
                    size += os.path.getsize(os.path.join(out, r))
                except OSError:
                    pass
        counts = {"papers": len(papers), "translations": sum(1 for k in new if k.startswith("tr:")), "notes": sum(1 for k in new if k.startswith("note:")),
                  "studies": sum(1 for k in new if k.startswith("study:")), "ms": sum(1 for k in new if k.startswith("ms:")), "files": files, "bytes": size}
        state = {"v": VERSION, "t": time.time(), "items": new, "counts": counts, "inbox_n": int(state.get("inbox_n") or 0) + imported}
        tmp = os.path.join(out, STATE + ".%d.tmp" % os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(out, STATE))
    else:
        counts = state.get("counts") or {}
    res = {"ok": True, "t": time.time(), "changed": changed, "wrote": wrote, "removed": removed, "imported": imported, "sec": round(time.time() - t0, 2), "errors": errors[:12], "counts": counts}
    if wrote or removed or imported or errors or not _ST.get("last"):
        _ST["last"] = res
    else:
        _ST["last"] = dict(_ST["last"], t=res["t"], sec=res["sec"], counts=counts, errors=[])
    _ST["err"] = ""
    return res


def _stamp(out):
    """원본이 바뀌었는지 가볍게 — 자료 폴더의 파일 이름·시각·크기와 남기기 폴더에 든 글. 같으면 맞춤을 통째로 건너뛴다(397편에서 0.4초 → 0.03초쯤).
    읽기 기록은 넣지 않는다(읽는 동안 30초마다 바뀐다 — 읽어보기의 '최근에 읽은 논문' 은 다른 것이 바뀔 때 같이 맞춰진다)."""
    h = hashlib.md5()
    root = cfg["ROOT"]
    memo = os.path.dirname(cfg["notes_path"]("x.pdf"))
    for d, ext in ((cfg["ARCHIVE"], ".pdf"), (cfg["GEN_DIR"], ".md"), (os.path.join(cfg["GEN_DIR"], "수식색인"), ".json"), (memo, ".json"),
                   (os.path.join(root, "연구노트"), ".json"), (os.path.join(root, "공부"), ".json"), (os.path.join(root, "원고"), ".json"), (os.path.join(out, INBOX), "")):
        try:
            with os.scandir(d) as it:
                rows = sorted((e.name, e.stat().st_mtime_ns, e.stat().st_size) for e in it if e.name.lower().endswith(ext) and e.is_file())
        except OSError:
            rows = []
        h.update(("%s:%d\n" % (d, len(rows))).encode("utf-8"))
        for r in rows:
            h.update(("%s|%d|%d\n" % r).encode("utf-8", "replace"))
    h.update(_sig(cfg["TAGS_PATH"], cfg["LABELS_PATH"], os.path.join(cfg["BASE"], "설정.json"), os.path.join(cfg["BASE"], "수집설정.json"),
                  os.path.join(cfg["HIST_DIR"], "_목록.json"), os.path.join(out, STATE)).encode("utf-8"))
    return h.hexdigest()


def _loop():
    while not _ST["stop"]:
        try:
            c = conf()
            if c["on"]:
                st = _stamp(c["dir"])
                _ST["checked"] = time.time()
                if st != _ST.get("stamp") or _ST.get("retry"):
                    r = sync()
                    if r.get("error"):
                        _ST["err"] = r["error"]
                    _ST["retry"] = bool(r.get("error") or r.get("errors") or _ST.get("held_busy"))      # 못 만든 것이 있으면 다음에 다시
                    # 맞추기 **전**의 모습을 기억한다 — 맞추는 동안 저장된 노트를 놓치지 않게. 맞춤이 상태 파일을 썼으면 다음 번에 한 번 더 돌고(바뀐 것 없음) 거기서 멎는다
                    _ST["stamp"] = st
                else:
                    _ST["skips"] = int(_ST.get("skips") or 0) + 1
        except Exception as e:
            _ST["err"] = str(e)[:200]
        for _ in range(EVERY):
            if _ST["stop"]:
                return
            time.sleep(1)


def _ensure_loop():
    t = _ST.get("thread")
    if t and t.is_alive():
        return
    _ST["stop"] = False
    t = threading.Thread(target=_loop, name="shelf-sync", daemon=True)
    _ST["thread"] = t
    t.start()


# ---------- 화면 ----------
def project_prompt(d):
    """Claude 프로젝트의 지침 칸에 붙일 글"""
    return ("이 프로젝트는 내 연구 서재(Athenaeum)와 이어져 있습니다. 서재의 사본이 이 PC 의 다음 폴더에 md 파일로 있습니다.\n\n    %s\n\n"
            "대화를 시작하면 먼저 그 폴더의 「읽어보기.md」 를 읽고, 거기 적힌 대로 차례 파일에서 필요한 파일만 찾아 읽으세요. 폴더 전체를 읽지 않습니다.\n"
            "연구 주제를 이어서 이야기할 때는 「연구노트」 폴더에서 그 주제의 「요지.md」 를 먼저 읽습니다.\n"
            "이 폴더의 파일은 고치지 않습니다. 내가 이 대화에서 정리한 것을 Athenaeum 에 남기라고 하면, 「%s」 폴더에 그 폴더의 「%s」 에 적힌 형식대로 md 파일을 하나 씁니다.\n") % (d, INBOX, INBOX_HELP)


def view():
    c = conf()
    st = _load_state(c["dir"]) if os.path.isdir(c["dir"]) else {}
    return {"on": c["on"], "dir": c["dir"], "custom": c["custom"], "exists": os.path.isfile(os.path.join(c["dir"], STATE)), "running": _ST["running"],
            "last": _ST.get("last"), "synced": st.get("t") or 0, "counts": st.get("counts") or {}, "inbox_n": int(st.get("inbox_n") or 0),
            "held": _ST.get("held") or [], "imported": (_ST.get("imported") or [])[:5], "err": _ST.get("err") or "", "prompt": project_prompt(c["dir"]), "every": EVERY,
            "checked": _ST.get("checked") or 0, "skips": int(_ST.get("skips") or 0)}


def control(body):
    op = body.get("op")
    out = {}
    if op == "on":
        on = bool(body.get("on"))
        if on:
            ok, why = _dir_ok(conf()["dir"])
            if not ok:
                return dict(view(), result={"error": why})
        _save_conf(on=on)
        if on:
            _ensure_loop()
            threading.Thread(target=sync, name="shelf-first", daemon=True).start()
        out = {"ok": True}
    elif op == "dir":
        d = str(body.get("dir") or "").strip().strip('"')
        if d:
            d = os.path.normpath(d)
            ok, why = _dir_ok(d)
            if not ok:
                return dict(view(), result={"error": why})
        _save_conf(dir=d)
        if conf()["on"]:
            threading.Thread(target=sync, name="shelf-move", daemon=True).start()
        out = {"ok": True}
    elif op == "sync":
        out = sync(force=bool(body.get("force")))
    elif op == "open":
        d = conf()["dir"]
        if os.path.isdir(d):
            try:
                os.startfile(d)
                out = {"ok": True}
            except Exception as e:
                out = {"error": str(e)[:120]}
        else:
            out = {"error": "아직 폴더를 만들지 않았습니다"}
    else:
        out = {"error": "unknown"}
    return dict(view(), result=out)


def handle_get(h, url):
    return h._send(200, view())


def handle_post(h, body):
    try:
        return h._send(200, control(body))
    except Exception as e:
        return h._send(200, dict(view(), result={"error": str(e)[:300]}))
