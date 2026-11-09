# -*- coding: utf-8 -*-
"""자동 번역 — 새로 들어온 논문은 알아서 전문 번역을 만들고, 옛 판 번역은 밤에 차례로 다시 만든다.
(2026-11-08, 사용자: '내가 안 보는 동안에도 새로 논문 있으면 알아서 전문 번역 만들고, 너무 옛 번역 있으면 알아서 다시 생성하게 해 줘')
(2026-11-09, 사용자: '새로 다운받는 논문들은 바로바로 요약이랑 번역 만들어' — 새 논문은 들어온 지 20초 뒤부터, 요약(summary 를 켰을 때)은 번역을 기다리지 않고
 그 옆에서 나란히 만든다: 번역 칸 cur · 요약 칸 cur_sum. 번역은 있는데 요약이 없는 새 논문도 요약을 만든다.)

설정 — 설정.json 의 autotr: on(새 논문) · summary(새 논문은 요약도) · redo(옛 판 다시) · before('이 날 이전에 만든 번역 = 옛 판', YYYY-MM-DD)
       · per_night(하룻밤에 다시 만들 편수) · per_day_new(하루에 번역할 새 논문 편수) · hours([22, 8] — 옛 판은 이 시간대에만)
상태 — 번역\\_자동번역.json: since(켠 때 − 하루: 그 뒤 들어온 논문이 '새 논문') · seen{파일: 처음 본 때} · queue[](「모두 넣기」 로 넣은 옛 미번역 논문)
       · cur(지금 도는 것) · done[](최근 200) · fails{stem: {n, t, err}} · night{key, n} · day{key, n}
원칙 — 번역은 한 번에 하나(요약은 그 옆에서 하나), 사용자가 시킨 요약·번역이 둘 넘게 돌거나 학습·공부가 도는 밤(옛 판)에는 기다린다. Claude 호출에 문제가 있으면(로그인·한도) 쉰다.
       옛 판을 다시 만들 때는 옛 파일을 번역\\_옛판\\<stem>\\ 에 두고 생성은 끝에서만 파일을 쓰므로, 실패하면 옛 번역이 그대로 남는다.
       끝나면 옛→새 문장 번호 대응표(.번역.옛번호.json)를 적고 서버의 on_retranslated 로 주석(학습)·바탕의 본보기·수식 색인·캐시를 맞춘다.
       공부·학습·단어장이 가리키는 문장(?sent=)은 번호가 바뀌어도 원문 글(?q=)로 다시 찾는다(reader.html · m.html).
서버가 init(...) 으로 경로·함수를 주입하고 main() 에서 start() 를 부른다. 화면은 ⚙ 설정 → AI 「번역 자동으로」(GET/POST /api/autotr).
"""
import datetime
import json
import os
import re
import shutil
import threading
import time

cfg = {}
STATE_NAME = "_자동번역.json"
OLD_DIR = "_옛판"
EVERY = int(os.environ.get("ATHENAEUM_AUTOTR_EVERY") or 20)       # 초 — 살피는 간격 (환경 변수는 시험용; 2026-11-09 45 → 20: 새 논문은 바로바로)
_DEFAULT = {"on": False, "summary": False, "redo": False, "before": "2026-10-24", "per_night": 8, "per_day_new": 12, "hours": [22, 8]}
_LOCK = threading.RLock()
_ST = {"thread": None, "err": "", "pause_until": 0.0, "redo_cache": None, "counting": False, "started": 0}
_LIMIT_RX = re.compile(r"한도|limit|usage|rate|429|too many|quota|overloaded", re.I)
_PERM_RX = re.compile(r"스캔본|글자를 추출할 수 없|설치되어 있지|찾을 수 없습니다", re.I)     # 다시 해도 같은 결과인 실패 — 더 시도하지 않는다
_SETTLE = 20           # 초 — 서재에 들어온 뒤 이만큼은 기다린다 (수집이 파일을 옮겨 놓는 동안; 2026-11-09 120 → 20)
_SETTLE_SUM = 150      # 초 — 요약은 라벨이 붙을 때까지 기다리되(리뷰/연구 요약이 라벨로 갈린다) 길어도 이만큼만


def init(**kw):
    cfg.update(kw)
    cfg["OLD"] = os.path.join(cfg["GEN_DIR"], OLD_DIR)
    cfg["STATE"] = os.path.join(cfg["GEN_DIR"], STATE_NAME)
    cfg["LOG"] = os.path.join(cfg["GEN_DIR"], "야간작업_기록.txt")


def start():
    """서버가 켜질 때(main) 한 번 — 살피는 스레드. 서버를 import 만 하는 도구(시험 틀·일괄 생성)에서는 돌지 않는다."""
    if _ST["thread"] and _ST["thread"].is_alive():
        return
    _ST["started"] = time.time()
    t = threading.Thread(target=_loop, name="autotr", daemon=True)
    _ST["thread"] = t
    t.start()


# ---------- 설정 ----------
def settings():
    s = dict(_DEFAULT)
    try:
        v = (cfg["settings"]() or {}).get("autotr") or {}
        if isinstance(v, dict):
            s.update({k: v[k] for k in v if k in _DEFAULT})
    except Exception:
        pass
    s["on"], s["summary"], s["redo"] = bool(s.get("on")), bool(s.get("summary")), bool(s.get("redo"))
    if _ts(s.get("before")) is None:
        s["before"] = _DEFAULT["before"]
    try:
        s["per_night"] = max(1, min(40, int(s.get("per_night") or 8)))
    except Exception:
        s["per_night"] = 8
    try:
        s["per_day_new"] = max(1, min(60, int(s.get("per_day_new") or 12)))
    except Exception:
        s["per_day_new"] = 12
    try:
        h = s.get("hours") or [22, 8]
        s["hours"] = [int(h[0]) % 24, int(h[1]) % 24]
    except Exception:
        s["hours"] = [22, 8]
    return s


def save_settings(body):
    s = settings()
    was_on = s["on"]
    for k in ("on", "summary", "redo"):
        if k in body:
            s[k] = bool(body[k])
    if "before" in body and _ts(str(body.get("before") or "")) is not None:
        s["before"] = str(body["before"]).strip()[:10]
    for k, lo, hi in (("per_night", 1, 40), ("per_day_new", 1, 60)):
        if k in body:
            try:
                s[k] = max(lo, min(hi, int(body[k])))
            except Exception:
                pass
    if "hours" in body and isinstance(body["hours"], list) and len(body["hours"]) == 2:
        try:
            s["hours"] = [int(body["hours"][0]) % 24, int(body["hours"][1]) % 24]
        except Exception:
            pass
    d = dict(cfg["settings"]() or {})
    d["autotr"] = s
    cfg["save_settings"](d)
    if s["on"] and not was_on:            # 막 켰다 — 어제부터 들어온 논문을 '새 논문' 으로 본다
        with _LOCK:
            st = _state()
            st["since"] = time.time() - 86400
            _save_state(st)
    _ST["redo_cache"] = None
    return s


def _ts(day):
    try:
        return time.mktime(datetime.datetime.strptime(str(day or "").strip()[:10], "%Y-%m-%d").timetuple())
    except Exception:
        return None


def _in_window(hours):
    a, b = hours
    h = datetime.datetime.now().hour
    return (a <= h < b) if a < b else (h >= a or h < b)


def _nightkey(now):
    """밤은 자정을 넘어도 한 밤 — 정오를 경계로 날을 센다"""
    return time.strftime("%Y-%m-%d", time.localtime(now - 12 * 3600))


def _daykey(now):
    return time.strftime("%Y-%m-%d", time.localtime(now - 4 * 3600))      # 읽기 기록과 같은 새벽 4시 경계


# ---------- 상태 파일 ----------
def _state():
    d = cfg["load_json"](cfg["STATE"], None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("since", None)
    d.setdefault("seen", {})
    d.setdefault("queue", [])
    d.setdefault("cur", None)
    d.setdefault("cur_sum", None)        # 새 논문의 요약 — 번역(cur) 옆에서 나란히 돈다
    d.setdefault("done", [])
    d.setdefault("fails", {})
    d.setdefault("night", {"key": "", "n": 0})
    d.setdefault("day", {"key": "", "n": 0})
    return d


def _save_state(st):
    try:
        cfg["save_json"](cfg["STATE"], st)
    except Exception as e:
        _ST["err"] = ("상태 저장: %s" % e)[:200]


def _log(msg):
    try:
        with open(cfg["LOG"], "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S") + "  [자동 번역] " + msg + "\n")
    except Exception:
        pass


# ---------- 파일 ----------
def _stem(f):
    return os.path.splitext(os.path.basename(str(f or "")))[0]


def _short(f):
    m = re.match(r"^(\d{4})_[^_]+_([^_]+)_", str(f or ""))
    return (m.group(2) + " " + m.group(1)) if m else str(f or "")[:24]


def _pdfs():
    try:
        return cfg["archive_pdfs"]()
    except Exception:
        return []


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


def _is_korean(f):
    """원문이 한국어인 PDF(번역본이 논문 이름으로 들어온 것) — 번역할 것이 아니다"""
    try:
        return bool(cfg["is_korean"](cfg["paper_sents"](f)))
    except Exception:
        return False


def _fail_ok(st, stem, now):
    fl = (st.get("fails") or {}).get(stem)
    if not fl:
        return True
    if fl.get("n", 0) >= 90:
        return False                                   # 다시 해도 같은 결과(스캔본·한국어 원문)
    if fl.get("n", 0) >= 3 and now - (fl.get("t") or 0) < 14 * 86400:
        return False                                   # 세 번 실패 — 두 주 뒤에 다시
    return now - (fl.get("t") or 0) > 3600             # 실패한 뒤 한 시간은 쉰다


def _mark_fail(st, stem, err, perm=False):
    fl = dict((st.get("fails") or {}).get(stem) or {})
    fl["n"] = 90 if perm else fl.get("n", 0) + 1
    fl["t"] = time.time()
    fl["err"] = str(err or "")[:200]
    st.setdefault("fails", {})[stem] = fl


def _fkey(f, kind="translation"):
    """실패 기록의 열쇠 — 번역은 stem, 요약은 stem|요약 (요약의 실패가 번역을 막지 않게)"""
    return _stem(f) + ("|요약" if kind == "summary" else "")


# ---------- 후보 ----------
def _track_seen(st, now):
    """서재의 파일마다 처음 본 때 — 처음 돌 때는 파일의 시각으로(옛 논문이 모두 '새 논문' 이 되지 않게), 그 뒤 들어온 것은 지금으로"""
    seen = st.setdefault("seen", {})
    pdfs = _pdfs()
    have = set(pdfs)
    first = not seen
    for f in pdfs:
        if f not in seen:
            seen[f] = _mtime(os.path.join(cfg["ARCHIVE"], f)) if first else now
    for f in list(seen):
        if f not in have:
            seen.pop(f, None)
    st["queue"] = [f for f in (st.get("queue") or []) if f in have]
    return pdfs


def _new_candidates(st, s, now, pdfs=None, check=True, kind="translation"):
    """새로 만들 논문 — since 뒤에 들어온 것(들어온 뒤 _SETTLE 이 지난 것, 새것부터) + (번역만) 「모두 넣기」 로 넣어 둔 것(차례대로).
    kind="summary" 면 요약이 없는 새 논문 — 라벨이 붙었거나 들어온 지 _SETTLE_SUM 이 지난 것(번역이 이미 있어도 든다)."""
    pdfs = pdfs if pdfs is not None else _pdfs()
    since = st.get("since") or 1e18
    fresh, queued = [], []
    qset = set(st.get("queue") or []) if kind == "translation" else set()
    fails = st.get("fails") or {}
    labeled = cfg.get("labeled")
    for f in pdfs:
        if os.path.isfile(cfg["gen_path"](f, kind)):
            continue
        stem = _stem(f)
        if not _fail_ok(st, _fkey(f, kind), now):
            continue
        if kind == "summary" and (fails.get(stem) or {}).get("n", 0) >= 90:
            continue                                       # 번역할 수 없는 논문(스캔본·한국어 원문)은 요약도 하지 않는다
        t = (st.get("seen") or {}).get(f) or 0
        if f in qset:
            queued.append(f)
        elif t >= since:
            settled = now - t >= _SETTLE
            if settled and kind == "summary" and now - t < _SETTLE_SUM and labeled:
                try:
                    settled = bool(labeled(f))             # 라벨이 아직이면 조금 더 기다린다
                except Exception:
                    settled = True
            if settled or not check:
                fresh.append((t, f))                       # 아직 가라앉지 않은 것 — 세는 데는 넣고 고르는 데는 안 넣는다
    fresh.sort(key=lambda x: -x[0])
    out = [f for _, f in fresh] + [f for f in (st.get("queue") or []) if f in set(queued)]
    if check:
        kept = []
        for f in out:
            if _is_korean(f):
                _mark_fail(st, _stem(f), "원문이 한국어다 (번역본 PDF)", perm=True)
                st["queue"] = [x for x in st["queue"] if x != f]
                continue
            kept.append(f)
        out = kept
    return out


def _redo_list(st, s, now):
    """옛 판 번역(before 이전에 만든 것) — ★ 읽을 논문 → 최근 120일 안에 읽은 것(최근순) → 나머지(오래된 것부터). 10분 캐시(파일 330개를 읽는다)"""
    c = _ST["redo_cache"]
    if c and now - c[0] < 600 and c[1] == s["before"]:
        return list(c[2])
    before = _ts(s["before"]) or 0
    try:
        stars = (cfg["load_stars"]() or {}).get("papers") or {}
    except Exception:
        stars = {}
    try:
        rl = (cfg["load_readlog"]() or {}).get("papers") or {}
    except Exception:
        rl = {}
    items = []
    for f in _pdfs():
        p = cfg["gen_path"](f, "translation")
        mt = _mtime(p)
        if not mt or mt >= before:
            continue
        if not _fail_ok(st, _stem(f), now):
            continue
        if _is_korean(f):
            continue
        star = stars.get(f) if isinstance(stars.get(f), dict) else None
        last = ((rl.get(f) or {}).get("last") or 0) if isinstance(rl.get(f), dict) else 0
        if star:
            key = (0, star.get("t") or 0)
        elif now - last < 120 * 86400:
            key = (1, -last)
        else:
            key = (2, mt)
        items.append((key, f))
    items.sort(key=lambda x: x[0])
    out = [f for _, f in items]
    _ST["redo_cache"] = (now, s["before"], out)
    return list(out)


def _count_redo_bg():
    """설정 창이 열렸을 때 옛 판 편수를 배경에서 센다(처음 한 번 수 초)"""
    if _ST["counting"]:
        return
    _ST["counting"] = True

    def run():
        try:
            with _LOCK:
                _redo_list(_state(), settings(), time.time())
        finally:
            _ST["counting"] = False
    threading.Thread(target=run, daemon=True).start()


# ---------- 시작·마무리 ----------
def _start(st, f, mode, now, kind="translation"):
    stem = _stem(f)
    cur = {"file": f, "mode": mode, "kind": kind, "t0": now}
    if mode == "redo" and kind == "translation":
        d = os.path.join(cfg["OLD"], stem)
        try:
            os.makedirs(d, exist_ok=True)
            for src, dst in ((cfg["gen_path"](f, "translation"), "번역.md"), (cfg["align_path"](f), "번역.정렬.json"), (cfg["info_path"](f), "번역.정보.json")):
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(d, dst))
            cur["old"] = d
            cur["old_mtime"] = _mtime(cfg["gen_path"](f, "translation"))
        except Exception as e:
            _ST["err"] = ("옛 판 두기: %s" % e)[:200]
            _log("옛 판을 두지 못해 건너뜀: %s — %s" % (f[:60], e))
            _mark_fail(st, stem, "옛 판을 두지 못함: %s" % e)
            return False
    r = cfg["gen_start"](f, kind, force=(mode == "redo"), by="auto-" + mode)
    if r not in ("started", "running"):
        _mark_fail(st, _fkey(f, kind), "시작하지 못함(%s)" % r)
        return False
    st["cur_sum" if kind == "summary" else "cur"] = cur
    if kind == "translation":
        if mode == "redo":
            st["night"]["n"] = st["night"].get("n", 0) + 1
        else:
            st["day"]["n"] = st["day"].get("n", 0) + 1
    else:
        st["day"]["ns"] = st["day"].get("ns", 0) + 1
    if _ST["redo_cache"]:
        _ST["redo_cache"] = (_ST["redo_cache"][0], _ST["redo_cache"][1], [x for x in _ST["redo_cache"][2] if x != f])
    _log("%s 시작: %s" % ({"redo": "옛 판 다시", "new": "새 논문 번역", "summary": "새 논문 요약"}.get(mode if kind == "translation" else "summary", mode), f[:70]))
    return True


def _finalize(st, s, now, slot="cur"):
    """그 칸(cur 번역 · cur_sum 요약)에서 도는 것이 끝났나 — 끝났으면 기록한다(옛 판이면 문장 번호 대응표 + 서버 훅).
    요약은 번역 뒤에 잇지 않는다 — _tick 이 요약 칸을 따로 채운다(2026-11-09)."""
    cur = st.get(slot) or {}
    f, kind, mode = cur.get("file", ""), cur.get("kind", "translation"), cur.get("mode", "new")
    js = cfg["job_state"](f, kind) or {}
    if js.get("status") == "running":
        if now - (cur.get("t0") or now) > 4 * 3600:       # 생성은 40분 상한이 있다 — 여기까지 왔으면 작업이 사라진 것
            _log("네 시간이 지나도 끝나지 않아 접음: %s" % f[:60])
            st[slot] = None
        return
    path = cfg["gen_path"](f, kind)
    ok = js.get("status") != "error" and os.path.isfile(path) and _mtime(path) >= (cur.get("t0") or 0) - 2
    if js.get("status") == "error":
        cfg["job_clear"](f, kind)                       # 옛 번역이 그대로인데 홈이 '실패' 로 보이지 않게 — 기록은 여기 남는다
    rec = {"file": f, "mode": mode, "kind": kind, "t": now, "sec": int(now - (cur.get("t0") or now)), "ok": bool(ok)}
    if ok:
        if kind == "translation" and mode == "redo":
            try:
                old = cfg["load_json"](os.path.join(cur.get("old") or "", "번역.정렬.json"), None) or {}
                new = cfg["load_json"](cfg["align_path"](f), None) or {}
                m, lost = _sidmap(old, new)
                cfg["save_json"](cfg["oldnum_path"](f), {"t": now, "old_t": cur.get("old_mtime"), "n_old": sum(1 for k in old if str(k).isdigit()), "n_new": sum(1 for k in new if str(k).isdigit()), "lost": lost, "map": m})
                rec["mapped"], rec["lost"] = len(m), lost
            except Exception as e:
                m = {}
                rec["maperr"] = str(e)[:120]
            try:
                cfg["on_retranslated"](f, m)
            except Exception as e:
                _ST["err"] = ("되맞춤: %s" % e)[:200]
        _log("%s 끝 (%d분): %s%s" % ("옛 판 다시" if mode == "redo" else ("새 논문 요약" if kind == "summary" else "새 논문 번역"), rec["sec"] // 60, f[:70],
                                   (" — 문장 %d개 가운데 %d개가 새 번호로 이어짐" % (rec.get("mapped", 0) + rec.get("lost", 0), rec.get("mapped", 0))) if mode == "redo" and kind == "translation" else ""))
        st[slot] = None
    else:
        err = str(js.get("error") or "결과 파일이 없습니다")[:200]
        rec["err"] = err
        if _LIMIT_RX.search(err):
            _ST["pause_until"] = now + 1800                 # 한도 — 30분 쉬고, 그 논문 탓은 아니다
            _log("한도에 걸려 30분 쉼: %s — %s" % (f[:60], err))
        else:
            _mark_fail(st, _fkey(f, kind), err, perm=bool(_PERM_RX.search(err)))
            _log("%s 실패: %s — %s" % ("요약" if kind == "summary" else "번역", f[:60], err))
        st[slot] = None
    st["done"] = ([rec] + (st.get("done") or []))[:200]


def _sidmap(old, new):
    """옛 정렬표 → 새 정렬표의 문장 번호 대응 — 원문 글자만 남겨 같은 문장을 찾고(통째로, 아니면 앞 40자), 여럿이면 차례에 맞는 것. → (대응 {옛: 새}, 못 찾은 수)"""
    import pagealign
    nn, p24, ntext = {}, {}, {}
    for k in sorted((k for k in new if str(k).isdigit()), key=int):
        t = pagealign.norm(((new.get(k) or {}).get("t") if isinstance(new.get(k), dict) else "") or "")
        if len(t) >= 12:
            nn.setdefault(t, []).append(int(k))
            ntext[int(k)] = t
            if len(t) >= 24:
                p24.setdefault(t[:24], []).append(int(k))
    m, lost, last = {}, 0, 0
    for k in sorted((k for k in old if str(k).isdigit()), key=int):
        t = pagealign.norm(((old.get(k) or {}).get("t") if isinstance(old.get(k), dict) else "") or "")
        if len(t) < 12:
            continue                                   # 제목 조각·짧은 줄은 세지 않는다
        ids = nn.get(t) or []
        if not ids and len(t) >= 24:                   # 통째로 같은 것이 없으면: 앞 24자가 같고 앞 40자까지 한쪽이 다른 쪽의 머리인 것 (끝만 달라진 문장)
            ids = [i for i in p24.get(t[:24], []) if ntext[i].startswith(t[:40]) or t.startswith(ntext[i][:40])]
        if not ids:
            lost += 1
            continue
        cand = [i for i in ids if i >= last] or ids
        pick = min(cand, key=lambda i: abs(i - last))
        m[str(k)] = str(pick)
        last = pick
    return m, lost


# ---------- 살피기 ----------
def _rollover(st, now):
    nk, dk = _nightkey(now), _daykey(now)
    if st["night"].get("key") != nk:
        st["night"] = {"key": nk, "n": 0}
    if st["day"].get("key") != dk:
        st["day"] = {"key": dk, "n": 0}


def _tick():
    s = settings()
    now = time.time()
    with _LOCK:
        st = _state()
        pdfs = _track_seen(st, now)
        _rollover(st, now)
        if st.get("cur"):
            _finalize(st, s, now, "cur")
        if st.get("cur_sum"):
            _finalize(st, s, now, "cur_sum")
        if s["on"] and not st.get("since"):
            st["since"] = now - 86400
        # 쉬는 중(한도)·Claude 문제·사람이 시킨 작업이 둘 넘게 도는 동안은 새로 시작하지 않는다
        free = now >= _ST["pause_until"] and not cfg["claude_error"]() and cfg["engine_ok"]() and cfg["jobs_running"](auto=False) <= 1
        if free and (s["on"] or s["redo"]) and not st.get("cur"):
            f, mode = None, None
            if s["on"] and st["day"].get("n", 0) < s["per_day_new"]:
                c = _new_candidates(st, s, now, pdfs)
                if c:
                    f, mode = c[0], "new"
            if f is None and s["redo"] and _in_window(s["hours"]) and st["night"].get("n", 0) < s["per_night"] and not cfg["others_busy"]():
                c = _redo_list(st, s, now)
                if c:
                    f, mode = c[0], "redo"
            if f:
                _start(st, f, mode, now)
        # 새 논문의 요약 — 번역을 기다리지 않고 그 옆에서 (지금 번역하는 논문의 요약부터)
        if free and s["on"] and s["summary"] and not st.get("cur_sum") and st["day"].get("ns", 0) < s["per_day_new"]:
            c = _new_candidates(st, s, now, pdfs, kind="summary")
            if c:
                curf = (st.get("cur") or {}).get("file")
                _start(st, curf if curf in c else c[0], "new", now, kind="summary")
        _save_state(st)


def _loop():
    time.sleep(40)          # 서버가 켜진 직후(수집·색인이 뜨는 동안)는 비켜 준다
    while True:
        try:
            _tick()
            _ST["err"] = ""
        except Exception as e:
            _ST["err"] = str(e)[:200]
        time.sleep(EVERY)


# ---------- 화면 ----------
def view():
    s = settings()
    now = time.time()
    with _LOCK:
        st = _state()
        pdfs = _pdfs()
        missing = [f for f in pdfs if not os.path.isfile(cfg["gen_path"](f, "translation"))]
        fails = st.get("fails") or {}
        new_pending = len(_new_candidates(st, s, now, pdfs, check=False)) if s["on"] else 0
        c = _ST["redo_cache"]
        old = len(c[2]) if c and c[1] == s["before"] and now - c[0] < 600 else None
        if old is None:
            _count_redo_bg()
        def slot(name):
            c0 = st.get(name)
            if not c0:
                return None
            js = cfg["job_state"](c0.get("file", ""), c0.get("kind", "translation")) or {}
            return dict(c0, short=_short(c0.get("file")), stage=js.get("stage", ""), elapsed=js.get("elapsed"), done=js.get("done"), total=js.get("total"), status=js.get("status"))
        cur, cur_sum = slot("cur"), slot("cur_sum")
        last = (st.get("done") or [None])[0]
        if last:
            last = dict(last, short=_short(last.get("file")))
        today_all = [x for x in (st.get("done") or []) if _daykey(x.get("t") or 0) == _daykey(now)]
        today = [x for x in today_all if x.get("kind") == "translation"]
        new_pending_sum = len(_new_candidates(st, s, now, pdfs, check=False, kind="summary")) if s["on"] and s["summary"] else 0
        return {"settings": s, "in_window": _in_window(s["hours"]), "cur": cur, "cur_sum": cur_sum, "last": last,
                "new_pending_sum": new_pending_sum, "today_sum_ok": sum(1 for x in today_all if x.get("kind") == "summary" and x.get("ok")),
                "missing": len([f for f in missing if (fails.get(_stem(f)) or {}).get("n", 0) < 90]), "queued": len(st.get("queue") or []), "new_pending": new_pending,
                "old": old, "night": st["night"].get("n", 0) if st["night"].get("key") == _nightkey(now) else 0, "day": st["day"].get("n", 0) if st["day"].get("key") == _daykey(now) else 0,
                "today_ok": sum(1 for x in today if x.get("ok")), "today_fail": sum(1 for x in today if not x.get("ok")),
                "fails": sum(1 for v in fails.values() if v.get("n", 0) >= 3), "paused": now < _ST["pause_until"], "pause_until": _ST["pause_until"], "err": _ST["err"],
                "since": st.get("since"), "recent": [dict(x, short=_short(x.get("file"))) for x in (st.get("done") or [])[:8]]}


def control(body):
    op = str(body.get("op") or "settings")
    if op == "settings":
        save_settings(body)
        return view()
    now = time.time()
    with _LOCK:
        st = _state()
        if op == "queue_missing":          # 전에 들어온 번역 없는 논문도 모두 — 하루 상한(per_day_new)대로 차례로
            pdfs = _track_seen(st, now)
            q = list(st.get("queue") or [])
            for f in pdfs:
                if not os.path.isfile(cfg["gen_path"](f, "translation")) and f not in q and _fail_ok(st, _stem(f), now):
                    q.append(f)
            st["queue"] = q
            _save_state(st)
            return dict(view(), result={"queued": len(q)})
        if op == "clear_fails":
            st["fails"] = {}
            _save_state(st)
            _ST["redo_cache"] = None
            return view()
        if op == "run_now":                # 시간대·상한과 무관하게 한 편 — 시험·확인용 (상한에는 센다)
            if st.get("cur"):
                return dict(view(), result={"error": "이미 하나 만들고 있습니다"})
            if cfg["claude_error"]():
                return dict(view(), result={"error": cfg["claude_error"]()})
            s = settings()
            mode = "redo" if body.get("mode") == "redo" else "new"
            f = str(body.get("file") or "").strip()
            if f:
                f = os.path.basename(f)
                if not os.path.isfile(os.path.join(cfg["ARCHIVE"], f)):
                    return dict(view(), result={"error": "그 논문이 서재에 없습니다"})
                if mode == "redo" and not os.path.isfile(cfg["gen_path"](f, "translation")):
                    mode = "new"
            else:
                _rollover(st, now)
                c = _redo_list(st, s, now) if mode == "redo" else (_new_candidates(st, s, now) or [x for x in _pdfs() if not os.path.isfile(cfg["gen_path"](x, "translation")) and _fail_ok(st, _stem(x), now)])
                if not c:
                    return dict(view(), result={"error": "다시 만들 옛 판 번역이 없습니다" if mode == "redo" else "번역할 논문이 없습니다"})
                f = c[0]
            ok = _start(st, f, mode, now)
            _save_state(st)
            return dict(view(), result={"started": f if ok else "", "error": "" if ok else "시작하지 못했습니다"})
    return dict(view(), result={"error": "bad op"})


def handle_get(h, url):
    return h._send(200, dict(view(), remote=bool(getattr(h, "_remote", False))))


def handle_post(h, body):
    try:
        return h._send(200, control(body))
    except Exception as e:
        return h._send(200, dict(view(), result={"error": str(e)[:300]}))
