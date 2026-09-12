# -*- coding: utf-8 -*-
"""
Athenaeum 단어장 — 담는 곳은 여기, 외우는 곳은 Anki.

두 갈래를 한 곳에 담는다.
  (1) 논문 단어 : 읽기 화면에서 구절을 드래그 → '단어' → 뜻·그 논문의 예문(영/한)·출처가 자동으로 붙는다
  (2) 시험 단어 : 외부 단어 목록을 붙여 넣으면 뜻을 채워 담는다 (뜻이 없으면 Claude 가 채움)

간격 반복(복습 일정)은 만들지 않는다. Anki 로 내보내 폰에서 외운다.
아테네움 안에는 방금 담은 것을 넘겨 보는 '훑어보기'만 둔다.

데이터: MAENG_paper\\단어장\\단어.json   내보내기: 단어장\\내보내기\\*.csv (Anki 용, 탭 구분 UTF-8)
server.py 가 init() 으로 주입하고 /vocab, /api/vocab* 를 이 모듈에 넘긴다.
"""

import io
import json
import os
import re
import time
import urllib.parse

cfg = {}


def init(**kw):
    cfg.update(kw)
    cfg["VOCAB_DIR"] = os.path.join(os.path.dirname(cfg["ARCHIVE"]), "단어장")
    cfg["WORDS_PATH"] = os.path.join(cfg["VOCAB_DIR"], "단어.json")
    cfg["VOCAB_EXPORT"] = os.path.join(cfg["VOCAB_DIR"], "내보내기")
    for d in (cfg["VOCAB_DIR"], cfg["VOCAB_EXPORT"]):
        os.makedirs(d, exist_ok=True)


# ---------- 저장 ----------

def load():
    d = cfg["load_json"](cfg["WORDS_PATH"], None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("words", [])
    d.setdefault("decks", ["논문단어", "시험단어"])
    return d


def save(d):
    d["updated"] = time.time()
    cfg["save_json"](cfg["WORDS_PATH"], d)
    return d


def _new_id(words):
    n = 1
    have = set(w.get("id") for w in words)
    while ("w%d" % n) in have:
        n += 1
    return "w%d" % n


def _norm(term):
    return re.sub(r"\s+", " ", (term or "")).strip().lower()


def find_word(d, term):
    t = _norm(term)
    for w in d["words"]:
        if _norm(w.get("term")) == t:
            return w
    return None


# ---------- 논문에서 담기 ----------

def _sentence_of(fname, sent_id, fallback=""):
    """번역 정렬표에서 그 문장의 영어 원문·쪽·한국어를 찾는다."""
    stem = os.path.splitext(fname)[0]
    tb = cfg["load_json"](os.path.join(cfg["GEN_DIR"], stem + ".번역.정렬.json"), None) or {}
    en = page = None
    ko = ""
    if sent_id and str(sent_id) in tb:
        v = tb[str(sent_id)]
        en, page = v.get("t"), v.get("p")
    if not en and fallback:
        # 문장 번호를 모를 때: 정렬표에서 그 구절이 든 문장을 찾는다
        key = _norm(fallback)[:60]
        for k, v in tb.items():
            if key and key in _norm(v.get("t") or ""):
                en, page, sent_id = v.get("t"), v.get("p"), k
                break
    if sent_id:
        try:
            md = io.open(os.path.join(cfg["GEN_DIR"], stem + ".번역.md"), encoding="utf-8").read()
            m = re.search(r"\[s%s\]\s*([^\[\n]{3,}?)(?=\s*\[s\d+\]|\n|$)" % re.escape(str(sent_id)), md)
            if m:
                ko = m.group(1).strip()
        except Exception:
            pass
    return en or fallback, page, ko, sent_id


def paper_title(fname):
    tags = cfg["load_json"](cfg["TAGS_PATH"], {}) or {}
    t = (tags.get(fname) or {}).get("title")
    if t:
        return t
    base = os.path.splitext(fname)[0].split("_")
    return base[-1] if base else fname


def lookup_meaning(term, context_en="", field=""):
    """단어 하나의 뜻을 문맥에 맞게. JSON {meaning, pos, gloss_en, note}."""
    prompt = (
        "당신은 %s 분야 논문을 읽는 한국인 대학원생을 돕는 사전이다. 아래 용어의 뜻을 JSON 한 줄로만 답하라.\n"
        "{\"meaning\": \"한국어 뜻 (짧게, 15자 이내. 학술 용어면 정착된 번역어를 쓴다)\", "
        "\"pos\": \"품사 (명사/동사/형용사/부사/구)\", "
        "\"gloss_en\": \"영어 한 줄 설명 (12단어 이내)\", "
        "\"note\": \"이 문맥에서 특별히 알아둘 점이 있으면 한국어 한 문장, 없으면 빈 문자열\"}\n"
        "규칙: 문맥에 쓰인 뜻만 고른다. 일반 단어면 일반 뜻으로, 전문 용어면 그 분야 뜻으로.\n\n"
        "[용어] %s\n[문맥] %s" % (field or "기계가공·재료", term, (context_en or "(없음)")[:600]))
    r = cfg["claude_json"](prompt, timeout=120) or {}
    return {"meaning": str(r.get("meaning", ""))[:60], "pos": str(r.get("pos", ""))[:12],
            "gloss_en": str(r.get("gloss_en", ""))[:120], "note": str(r.get("note", ""))[:200]}


def add_from_paper(body):
    """읽기 화면에서 드래그한 구절을 단어로 담는다. 이미 있으면 예문만 보탠다."""
    term = re.sub(r"\s+", " ", (body.get("term") or "")).strip(" .,;:()[]")
    if not term:
        return {"error": "담을 말이 비어 있습니다"}
    if len(term) > 80:
        return {"error": "너무 깁니다 (80자 이내). 단어나 짧은 구를 골라 주세요"}
    fname = os.path.basename(body.get("file") or "")
    en, page, ko, sent = _sentence_of(fname, body.get("sent"), body.get("context") or "")
    d = load()
    w = find_word(d, term)
    if w is None:
        info = lookup_meaning(term, en, body.get("field") or "")
        w = {"id": _new_id(d["words"]), "term": term, "meaning": info["meaning"], "pos": info["pos"],
             "gloss_en": info["gloss_en"], "note": info["note"], "examples": [],
             "deck": body.get("deck") or "논문단어", "tags": [], "source": "paper",
             "t": time.time(), "status": "new", "seen": 0, "last": 0}
        d["words"].append(w)
        made = True
    else:
        made = False
    if en:
        same = any((x.get("sent") == sent and x.get("file") == fname) for x in w["examples"])
        if not same and len(w["examples"]) < 5:
            w["examples"].append({"en": en[:600], "ko": ko[:600], "file": fname,
                                  "title": paper_title(fname)[:120], "sent": sent, "page": page})
    save(d)
    return {"ok": True, "word": w, "new": made, "total": len(d["words"])}


# ---------- 목록으로 담기 (시험 단어) ----------

def _parse_lines(text):
    """'단어', '단어<탭>뜻', '단어 - 뜻', '단어,뜻' 을 받아 (단어, 뜻) 목록으로."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        term, mean = line, ""
        for sep in ("\t", " - ", " – ", " — ", ":", ",", "="):
            if sep in line:
                a, b = line.split(sep, 1)
                if a.strip():
                    term, mean = a.strip(), b.strip()
                break
        term = term.strip(" .0123456789)]")
        if term:
            out.append((term[:80], mean[:60]))
    return out


def fill_meanings(pairs, field=""):
    """뜻이 빈 것들을 Claude 가 한 번에 채운다 (40개씩)."""
    need = [t for t, m in pairs if not m]
    got = {}
    for i in range(0, len(need), 40):
        chunk = need[i:i + 40]
        prompt = ("당신은 %s 분야를 공부하는 한국인 대학원생의 단어장을 만드는 사전이다. "
                  "아래 용어마다 뜻을 붙여 JSON 한 줄로만 답하라.\n"
                  "{\"items\": [{\"term\": \"입력한 그대로\", \"meaning\": \"한국어 뜻 (15자 이내)\", "
                  "\"pos\": \"품사\", \"gloss_en\": \"영어 한 줄 설명 (12단어 이내)\"}]}\n"
                  "규칙: 입력한 용어를 하나도 빠뜨리지 말고 순서대로. 전문 용어는 그 분야의 정착된 번역어를 쓴다.\n\n"
                  "[용어 %d개]\n%s" % (field or "기계가공·재료", len(chunk), "\n".join(chunk)))
        r = cfg["claude_json"](prompt, timeout=300) or {}
        for it in (r.get("items") or []):
            if isinstance(it, dict) and it.get("term"):
                got[_norm(it["term"])] = it
    return got


def add_bulk(body):
    pairs = _parse_lines(body.get("text") or "")
    if not pairs:
        return {"error": "단어를 찾지 못했습니다. 한 줄에 하나씩 적어 주세요"}
    if len(pairs) > 500:
        return {"error": "한 번에 500개까지입니다 (%d개 들어옴)" % len(pairs)}
    deck = (body.get("deck") or "시험단어").strip()
    tags = [t.strip() for t in (body.get("tags") or "").split(",") if t.strip()]
    filled = fill_meanings(pairs, body.get("field") or "") if body.get("ask_claude", True) else {}
    d = load()
    added = skipped = 0
    for term, mean in pairs:
        if find_word(d, term):
            skipped += 1
            continue
        info = filled.get(_norm(term), {})
        d["words"].append({"id": _new_id(d["words"]), "term": term,
                           "meaning": mean or str(info.get("meaning", ""))[:60],
                           "pos": str(info.get("pos", ""))[:12], "gloss_en": str(info.get("gloss_en", ""))[:120],
                           "note": "", "examples": [], "deck": deck, "tags": tags, "source": "list",
                           "t": time.time(), "status": "new", "seen": 0, "last": 0})
        added += 1
    if deck and deck not in d["decks"]:
        d["decks"].append(deck)
    save(d)
    return {"ok": True, "added": added, "skipped": skipped, "total": len(d["words"])}


# ---------- 고치기 ----------

def edit(body):
    d = load()
    ids = body.get("ids") or ([body["id"]] if body.get("id") else [])
    op = body.get("op")
    n = 0
    for w in d["words"]:
        if w.get("id") not in ids:
            continue
        n += 1
        if op == "delete":
            continue
        for k in ("term", "meaning", "pos", "gloss_en", "note", "deck"):
            if k in body:
                w[k] = str(body[k])[:600]
        if "tags" in body:
            w["tags"] = [t.strip() for t in str(body["tags"]).split(",") if t.strip()]
        if "status" in body:
            w["status"] = body["status"]
            w["seen"] = int(w.get("seen", 0)) + 1
            w["last"] = time.time()
        if body.get("del_example") is not None:
            i = int(body["del_example"])
            if 0 <= i < len(w.get("examples", [])):
                w["examples"].pop(i)
    if op == "delete":
        d["words"] = [w for w in d["words"] if w.get("id") not in ids]
    save(d)
    return {"ok": True, "n": n, "total": len(d["words"])}


# ---------- Anki 로 내보내기 ----------

def _html(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\t", " ").replace("\n", " ").replace("\r", " "))


def export_csv(body):
    """Anki 텍스트 가져오기용 파일. 탭 구분, UTF-8.
       mode=basic  : 앞면=단어 / 뒷면=뜻+예문+출처 (Anki 기본 노트 유형 'Basic' 그대로, 설정 필요 없음)
       mode=fields : 단어·뜻·예문·출처를 따로 (노트 유형을 한 번 만들어야 함)"""
    d = load()
    mode = body.get("mode") or "basic"
    decks = body.get("decks") or []
    only = body.get("only") or "all"        # all | new | paper | list
    words = [w for w in d["words"]
             if (not decks or w.get("deck") in decks)
             and (only != "new" or w.get("status") != "known")
             and (only not in ("paper", "list") or w.get("source") == only)]
    if not words:
        return {"error": "내보낼 단어가 없습니다"}
    deck_name = body.get("deck_name") or "Athenaeum"
    rows = []
    for w in words:
        ex = (w.get("examples") or [{}])[0]
        src = ""
        if ex.get("title"):
            src = "%s%s" % (ex["title"], (" p.%s" % ex["page"]) if ex.get("page") else "")
        tags = " ".join(["Athenaeum"] + [re.sub(r"\s+", "_", t) for t in (w.get("tags") or [])]
                        + [re.sub(r"\s+", "_", w.get("deck") or "")])
        if mode == "fields":
            rows.append([_html(w["term"]), _html(w.get("meaning")), _html(w.get("gloss_en")),
                         _html(ex.get("en")), _html(ex.get("ko")), _html(src), tags])
        else:
            back = "<b>%s</b>" % _html(w.get("meaning") or "")
            if w.get("pos"):
                back += " <span style=color:#888>(%s)</span>" % _html(w["pos"])
            if w.get("gloss_en"):
                back += "<br>%s" % _html(w["gloss_en"])
            if ex.get("en"):
                back += "<br><br><i>%s</i>" % _html(ex["en"])
            if ex.get("ko"):
                back += "<br>%s" % _html(ex["ko"])
            if w.get("note"):
                back += "<br><br>%s" % _html(w["note"])
            if src:
                back += "<br><span style=color:#888;font-size:12px>%s</span>" % _html(src)
            rows.append([_html(w["term"]), back, tags])
    head = ["#separator:tab", "#html:true", "#deck:" + deck_name,
            "#tags column:%d" % len(rows[0])]
    if mode == "fields":
        head.append("#notetype:Athenaeum 단어")
    out = "\n".join(head) + "\n" + "\n".join("\t".join(r) for r in rows) + "\n"
    name = "단어장_%s_%s.txt" % (mode, time.strftime("%Y%m%d_%H%M"))
    path = os.path.join(cfg["VOCAB_EXPORT"], name)
    io.open(path, "w", encoding="utf-8", newline="\n").write(out)
    return {"ok": True, "path": path, "name": name, "n": len(rows), "mode": mode}


# ---------- 라우트 ----------

def _q(url, k, d=""):
    return urllib.parse.parse_qs(url.query).get(k, [d])[0]


def handle_get(h, url):
    p = url.path
    if p == "/vocab":
        with open(os.path.join(cfg["BASE"], "vocab.html"), "rb") as f:
            return h._send(200, f.read(), "text/html; charset=utf-8")
    if p == "/api/vocab":
        d = load()
        return h._send(200, {"words": d["words"], "decks": sorted(set(d["decks"] + [w.get("deck") for w in d["words"] if w.get("deck")]))})
    if p == "/api/vocab/file":
        name = os.path.basename(_q(url, "name"))
        fp = os.path.join(cfg["VOCAB_EXPORT"], name)
        if not os.path.isfile(fp):
            return h._send(404, {"error": "no file"})
        with open(fp, "rb") as f:
            return h._send(200, f.read(), "text/plain; charset=utf-8")
    return h._send(404, {"error": "unknown"})


def handle_post(h, body):
    p = h.path
    try:
        if p == "/api/vocab/from_paper":
            return h._send(200, add_from_paper(body))
        if p == "/api/vocab/bulk":
            return h._send(200, add_bulk(body))
        if p == "/api/vocab/edit":
            return h._send(200, edit(body))
        if p == "/api/vocab/export":
            return h._send(200, export_csv(body))
        if p == "/api/vocab/open_folder":
            try:
                os.startfile(cfg["VOCAB_EXPORT"])
            except Exception:
                pass
            return h._send(200, {"ok": True})
        if p == "/api/vocab/lookup":
            return h._send(200, lookup_meaning(body.get("term", ""), body.get("context", ""), body.get("field", "")))
        return h._send(404, {"error": "unknown"})
    except Exception as e:
        return h._send(200, {"error": str(e)[:300]})
