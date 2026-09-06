# -*- coding: utf-8 -*-
"""Athenaeum 원고(작성) 모듈.

원고 = MAENG_paper\원고\<id>.json 한 파일. 근거 카드(출처: 논문·문장 번호·쪽) → 개요(주장) → 문단 초안(한국어) → 영문화 → .docx.
그림: 절삭 도식 생성기(schematic.turning) 로 SVG 를 만들고 Edge headless 로 PNG 렌더.
server.py 가 init() 으로 경로와 Claude 호출 함수를 넘겨 준다 (순환 import 방지).
"""
import os, io, re, json, time, zipfile, subprocess, urllib.parse

cfg = {}


def init(**kw):
    cfg.update(kw)
    cfg["MS_DIR"] = os.path.join(os.path.dirname(cfg["ARCHIVE"]), "원고")
    cfg["FIG_DIR"] = os.path.join(cfg["MS_DIR"], "그림")
    cfg["EXPORT_DIR"] = os.path.join(cfg["MS_DIR"], "내보내기")
    for d in (cfg["MS_DIR"], cfg["FIG_DIR"], cfg["EXPORT_DIR"]):
        os.makedirs(d, exist_ok=True)


# ---------- 저장 ----------
def _path(mid):
    return os.path.join(cfg["MS_DIR"], os.path.basename(mid) + ".json")


def list_ms():
    out = []
    for f in os.listdir(cfg["MS_DIR"]):
        if not f.endswith(".json"):
            continue
        d = cfg["load_json"](os.path.join(cfg["MS_DIR"], f), None)
        if not d:
            continue
        out.append({"id": d.get("id", f[:-5]), "title": d.get("title", ""), "updated": d.get("updated", 0),
                    "cards": len(d.get("cards", [])), "nodes": len(d.get("outline", [])), "figures": len(d.get("figures", []))})
    out.sort(key=lambda x: -x["updated"])
    return out


def load_ms(mid):
    return cfg["load_json"](_path(mid), None)


def save_ms(doc):
    doc["updated"] = time.time()
    cfg["save_json"](_path(doc["id"]), doc)
    return doc


def new_ms(title):
    mid = "ms_" + time.strftime("%Y%m%d_%H%M%S")
    doc = {"id": mid, "title": title or "제목 없는 원고", "created": time.time(), "updated": time.time(),
           "meta": {"journal": "", "kind": "research", "lang": "ko"},
           "idea": {"question": "", "assessment": "", "related": []},
           "cards": [], "outline": [
               {"id": "n1", "level": 1, "heading": "서론", "claim": "", "cards": [], "draft": "", "draft_en": "", "status": "todo"},
               {"id": "n2", "level": 1, "heading": "실험 방법", "claim": "", "cards": [], "draft": "", "draft_en": "", "status": "todo"},
               {"id": "n3", "level": 1, "heading": "결과 및 고찰", "claim": "", "cards": [], "draft": "", "draft_en": "", "status": "todo"},
               {"id": "n4", "level": 1, "heading": "결론", "claim": "", "cards": [], "draft": "", "draft_en": "", "status": "todo"}],
           "figures": [], "glossary": [], "versions": []}
    return save_ms(doc)


def _next_id(items, prefix):
    n = 0
    for it in items:
        m = re.match(r"^%s(\d+)$" % prefix, str(it.get("id", "")))
        if m:
            n = max(n, int(m.group(1)))
    return "%s%d" % (prefix, n + 1)


def paper_short(fname):
    """'2015_IJMTM_Goel_Diamond…pdf' → 'Goel 2015'"""
    m = re.match(r"^(\d{4})_([A-Za-z0-9&-]+)_([^_]+)_", fname or "")
    return ("%s %s" % (m.group(3), m.group(1))) if m else (fname or "")[:30]


def paper_meta(fname):
    tags = cfg["load_json"](cfg["TAGS_PATH"], {}) if cfg.get("TAGS_PATH") else {}
    t = tags.get(fname, {}) if isinstance(tags, dict) else {}
    m = re.match(r"^(\d{4})_([A-Za-z0-9&-]+)_([^_]+)_(.*)\.pdf$", fname or "")
    return {"file": fname, "year": m.group(1) if m else "", "journal": m.group(2) if m else "",
            "author": m.group(3) if m else "", "title": t.get("title") or (m.group(4) if m else fname),
            "doi": t.get("doi", "")}


def add_card(mid, text, source, note=""):
    doc = load_ms(mid)
    if not doc:
        raise RuntimeError("원고를 찾을 수 없습니다")
    source = source or {}
    card = {"id": _next_id(doc["cards"], "c"), "text": (text or "").strip(), "note": note or "", "tag": "",
            "source": {"file": source.get("file", ""), "kind": source.get("kind", ""), "sent": source.get("sent", ""),
                       "page": source.get("page")}, "src_text": "", "t": time.time()}
    # 문장 번호표에서 원문 문장·쪽 찾기 (번역에서 담은 카드)
    f = card["source"]["file"]
    if f and card["source"]["sent"]:
        ap = os.path.join(cfg["GEN_DIR"], os.path.splitext(f)[0] + ".번역.정렬.json")
        tb = cfg["load_json"](ap, {})
        ent = tb.get(str(card["source"]["sent"]).lstrip("s")) or tb.get(str(card["source"]["sent"]))
        if ent:
            card["src_text"] = ent.get("t", "")
            if card["source"]["page"] is None and ent.get("p") is not None:
                card["source"]["page"] = ent["p"] + 1
    doc["cards"].append(card)
    save_ms(doc)
    return card


# ---------- 워드(.docx) 가져오기 ----------
_HEAD_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z가-힣][^\n]{1,88})$")
_CAP_RE = re.compile(r"^(Fig\.?|Figure|Table)\s*(\d+)\.?\s*(.*)$", re.I)


def _docx_para_md(p):
    """<w:p> 하나를 마크다운 문장으로 (굵게/기울임 유지, 탭·줄바꿈 처리)"""
    out = []
    for r in re.findall(r"<w:r[ >].*?</w:r>", p, re.S):
        rpr = re.search(r"<w:rPr>(.*?)</w:rPr>", r, re.S)
        rpr = rpr.group(1) if rpr else ""
        b = bool(re.search(r"<w:b(?:\s[^>]*)?/>", rpr)) and 'w:val="0"' not in rpr and 'w:val="false"' not in rpr
        i = bool(re.search(r"<w:i(?:\s[^>]*)?/>", rpr)) and 'w:val="0"' not in rpr
        sup = 'w:val="superscript"' in rpr; sub = 'w:val="subscript"' in rpr
        pieces = []
        for m in re.finditer(r"<w:t[^>]*>([^<]*)</w:t>|<w:tab/>|<w:br/>", r):
            if m.group(0) == "<w:tab/>": pieces.append(" ")
            elif m.group(0) == "<w:br/>": pieces.append("\n")
            else: pieces.append(m.group(1))
        t = "".join(pieces)
        if not t:
            continue
        t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        if sup: t = "^" + t + "^"
        if sub: t = "_" + t + "_"
        out.append((t, b, i))
    # 같은 서식의 이웃 런 합치기
    merged = []
    for t, b, i in out:
        if merged and merged[-1][1] == b and merged[-1][2] == i:
            merged[-1] = (merged[-1][0] + t, b, i)
        else:
            merged.append((t, b, i))
    txt = ""
    for t, b, i in merged:
        core = t.strip()
        if not core:
            txt += t; continue
        lead = t[:len(t) - len(t.lstrip())]; trail = t[len(t.rstrip()):]
        if b and i: core = "***" + core + "***"
        elif b: core = "**" + core + "**"
        elif i: core = "*" + core + "*"
        txt += lead + core + trail
    return " ".join(txt.split(" ")).strip()


def import_docx(path, title=None):
    """워드 원고 → 새 원고. 번호 절 제목 → 개요, 문단 → 초안, Fig. N 캡션+그림 → 그림 목록, [n] 참고문헌 → refs_text,
    제목·저자·초록·키워드 → front, 공저자 주석 → comments. 영어 원고는 meta.lang = en."""
    import zipfile
    z = zipfile.ZipFile(path)
    names = z.namelist()
    docxml = z.read("word/document.xml").decode("utf-8")
    rels = z.read("word/_rels/document.xml.rels").decode("utf-8") if "word/_rels/document.xml.rels" in names else ""
    rid2t = {}
    for m in re.finditer(r"<Relationship\b([^>]*)/?>", rels):
        a = m.group(1)
        i = re.search(r'Id="([^"]+)"', a); t = re.search(r'Target="([^"]+)"', a)
        if i and t: rid2t[i.group(1)] = t.group(1)
    body = re.search(r"<w:body>(.*)</w:body>", docxml, re.S)
    body = body.group(1) if body else docxml
    paras = re.findall(r"<w:p[ >].*?</w:p>", body, re.S)
    items = []   # (text_md, image_rid or None, raw)
    for p in paras:
        md = _docx_para_md(p)
        rid = re.search(r'r:embed="([^"]+)"', p)
        items.append((md, rid.group(1) if rid else None, p))
    all_text = " ".join(t for t, _, _ in items)
    is_en = len(re.findall(r"[A-Za-z]", all_text)) > len(re.findall(r"[가-힣]", all_text)) * 3
    doc = new_ms(title or "")
    doc["outline"] = []
    doc["meta"]["lang"] = "en" if is_en else "ko"
    doc["front"] = {"title": "", "authors": "", "abstract": "", "keywords": [], "highlights": []}
    doc["refs_text"] = []
    doc["tables"] = []
    doc["comments"] = []
    cur = None; in_refs = False; mode = "front"; abs_mode = False
    fig_pending = []   # 최근 그림 rid (캡션과 짝지을 때)
    img_n = 0
    for idx, (md, rid, raw) in enumerate(items):
        t = md.strip()
        if rid and rid in rid2t:
            fig_pending.append((idx, rid))
        if not t:
            continue
        # 판정용 평문: 워드에서 제목·캡션이 굵게/기울임으로 들어오면 **1. Introduction** 꼴이므로 서식 표시를 벗기고 본다
        plain = re.sub(r"\*+", "", t).strip()
        cap = _CAP_RE.match(plain)
        if cap and len(plain) > 12:
            kind = "table" if cap.group(1).lower().startswith("t") else "fig"
            num = int(cap.group(2)); text = cap.group(3).strip()
            if kind == "fig":
                fig = {"id": _next_id(doc["figures"], "f"), "num": num, "caption": "" if is_en else text, "caption_en": text if is_en else "",
                       "source": {"type": "docx", "file": os.path.basename(path)}, "png": "", "svg": ""}
                near = [r for (i2, r) in fig_pending if idx - 4 <= i2 <= idx + 1]
                if near:
                    target = rid2t.get(near[-1], "")
                    mpath = "word/" + target if not target.startswith("/") else target.lstrip("/")
                    if mpath in names:
                        img_n += 1
                        ext = os.path.splitext(mpath)[1].lower()
                        base = "%s_img%d" % (doc["id"], img_n)
                        raw_b = z.read(mpath)
                        out_png = os.path.join(cfg["FIG_DIR"], base + ".png")
                        try:
                            from PIL import Image
                            im = Image.open(io.BytesIO(raw_b)); im.save(out_png); fig["png"] = base + ".png"
                            fig["w"], fig["h"] = im.size
                        except Exception:
                            if ext == ".png":
                                open(out_png, "wb").write(raw_b); fig["png"] = base + ".png"
                            else:
                                open(os.path.join(cfg["FIG_DIR"], base + ext), "wb").write(raw_b); fig["orig"] = base + ext
                    fig_pending = [x for x in fig_pending if x[1] != near[-1]]
                doc["figures"].append(fig)
            else:
                doc["tables"].append({"num": num, "caption": text})
            continue
        if re.match(r"^\[\d+\]\s*\S", plain):
            doc["refs_text"].append(plain); in_refs = True
            continue
        h = _HEAD_RE.match(plain)
        if h and not plain.endswith((".", ",", ";")) and len(plain.split()) <= 12:
            level = 1 if "." not in h.group(1) else 2
            if re.search(r"references|참고문헌", h.group(2), re.I):
                in_refs = True; cur = None; mode = "body"; continue
            cur = {"id": _next_id(doc["outline"], "n"), "level": level, "heading": h.group(1) + ". " + h.group(2).strip() if level == 1 else h.group(1) + " " + h.group(2).strip(),
                   "claim": "", "cards": [], "draft": "", "draft_en": "", "status": "drafted"}
            doc["outline"].append(cur); mode = "body"; in_refs = False
            continue
        if re.match(r"^(acknowledg|declaration|funding|data availability|credit author|appendix|supplementary)", plain, re.I) and len(plain) < 60:
            cur = {"id": _next_id(doc["outline"], "n"), "level": 1, "heading": plain, "claim": "", "cards": [], "draft": "", "draft_en": "", "status": "drafted"}
            doc["outline"].append(cur); mode = "body"; in_refs = False
            continue
        if mode == "front":
            f = doc["front"]
            low = plain.lower()
            if low.startswith("abstract"):
                abs_mode = True; rest = plain[8:].strip(" :.-")
                if rest: f["abstract"] = rest
                continue
            if low.startswith("keywords") or low.startswith("key words"):
                abs_mode = False
                kw = re.split(r"[;,]\s*", re.sub(r"^key\s?words\s*[:.]?\s*", "", plain, flags=re.I))
                f["keywords"] = [k.strip() for k in kw if k.strip()]; continue
            if low.startswith("highlights"):
                abs_mode = False; mode = "highlights"; continue
            if abs_mode:
                f["abstract"] = (f["abstract"] + "\n\n" + plain).strip(); continue
            if not f["title"]:
                f["title"] = plain; continue
            f["authors"] = (f["authors"] + "\n" + plain).strip()
            continue
        if mode == "highlights":
            if len(plain) < 200 and not _HEAD_RE.match(plain):
                doc["front"]["highlights"].append(plain.lstrip("•-– ").strip()); continue
            mode = "body"
        if in_refs or cur is None:
            continue
        cur["draft"] = (cur["draft"] + "\n\n" + t).strip()
    # 공저자 주석
    if "word/comments.xml" in names:
        cx = z.read("word/comments.xml").decode("utf-8")
        for m in re.finditer(r"<w:comment\b([^>]*)>(.*?)</w:comment>", cx, re.S):
            a, inner = m.group(1), m.group(2)
            cid = re.search(r'w:id="([^"]+)"', a); au = re.search(r'w:author="([^"]*)"', a); dt = re.search(r'w:date="([^"]*)"', a)
            ctext = " ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", inner)).strip()
            anchor = ""
            if cid:
                rng = re.search(r'<w:commentRangeStart w:id="%s"/>(.*?)<w:commentRangeEnd w:id="%s"/>' % (cid.group(1), cid.group(1)), body, re.S)
                if rng: anchor = " ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", rng.group(1))).strip()[:200]
            doc["comments"].append({"id": cid.group(1) if cid else "", "author": au.group(1) if au else "", "date": (dt.group(1) if dt else "")[:10],
                                    "text": ctext[:1000], "anchor": anchor})
    doc["figures"].sort(key=lambda f: f["num"])
    if not doc["title"] or doc["title"] == "제목 없는 원고":
        doc["title"] = (doc["front"]["title"] or os.path.splitext(os.path.basename(path))[0])[:120]
    doc["source_docx"] = path
    save_ms(doc)
    return doc


# ---------- Claude ----------
def _node(doc, nid):
    for n in doc["outline"]:
        if n["id"] == nid:
            return n
    raise RuntimeError("문단을 찾을 수 없습니다")


def _cards_text(doc, node):
    by = {c["id"]: c for c in doc["cards"]}
    out = []
    for cid in node.get("cards", []):
        c = by.get(cid)
        if not c:
            continue
        out.append("[%s] (%s%s) %s%s" % (c["id"], paper_short(c["source"].get("file", "")),
                                         (" p.%s" % c["source"]["page"]) if c["source"].get("page") else "",
                                         c["text"], (" — 메모: " + c["note"]) if c.get("note") else ""))
    return "\n".join(out)


def _prev_draft(doc, node, key="draft"):
    idx = [n["id"] for n in doc["outline"]].index(node["id"])
    for n in reversed(doc["outline"][:idx]):
        if n.get(key):
            return n[key][-400:]
    return ""


SECTION_HINT = {
    "서론": "배경 → 기존 연구의 빈틈 → 이 연구의 목적 순으로. 마지막 문장은 이 논문이 무엇을 하는지.",
    "방법": "무엇을 어떤 조건으로 어떻게 했는지. 재현 가능하게, 수치는 근거 카드에 있는 것만.",
    "결과": "관찰된 사실을 먼저, 해석은 뒤에. 그림·표를 언급할 자리는 (그림 N) 로 표시.",
    "고찰": "결과가 왜 그런지, 기존 연구와 어떻게 다른지, 한계는 무엇인지.",
    "결론": "주장을 3~5문장으로 압축. 새 정보 금지.",
}


def _hint(heading):
    for k, v in SECTION_HINT.items():
        if k in (heading or ""):
            return v
    return "학술 논문 문단의 일반 규칙을 따른다."


def claude_paragraph(doc, nid, mode):
    node = _node(doc, nid)
    cards = _cards_text(doc, node)
    head = "논문 제목: %s\n절: %s\n" % (doc.get("title", ""), node.get("heading", ""))
    prev = _prev_draft(doc, node)
    ctx = ("\n[앞 문단 끝부분 - 흐름 참고용, 다시 쓰지 말 것]\n" + prev + "\n") if prev else ""
    if mode == "draft":
        prompt = (
            "당신은 기계가공·재료 분야 논문을 쓰는 연구자다. 아래 '주장'을 아래 '근거 카드'만 사용해 학술 문체의 한국어 한 문단(4~8문장)으로 써라.\n"
            "규칙:\n- 카드에 없는 사실·수치·인용은 절대 쓰지 마라. 근거가 부족하면 그 부분은 '(근거 필요: …)' 로 표시하라.\n"
            "- 근거를 쓴 문장 끝에는 카드 번호를 [c3] 처럼 붙여라 (나중에 인용 번호로 바뀐다). 카드 여러 개면 [c3][c7].\n"
            "- 절 성격: " + _hint(node.get("heading")) + "\n- 문단만 출력. 제목·설명·따옴표 금지.\n\n" +
            head + "[주장]\n" + (node.get("claim") or "(주장이 비어 있음 - 카드들을 종합해 이 절에 맞는 주장을 세워 써라)") +
            "\n\n[근거 카드]\n" + (cards or "(카드 없음)") + ctx)
    elif mode == "rewrite":
        prompt = ("아래 한국어 논문 문단을 뜻은 그대로 두고 더 명확하고 자연스러운 학술 문체로 다시 써라. 카드 번호 [cN] 은 그 자리에 유지. 문단만 출력.\n\n"
                  + head + "[문단]\n" + node.get("draft", "") + "\n\n[근거 카드 - 사실 확인용]\n" + cards)
    elif mode == "shorten":
        prompt = ("아래 한국어 논문 문단을 뜻과 근거 번호 [cN] 을 유지하며 30~40% 줄여라. 문단만 출력.\n\n" + head + "[문단]\n" + node.get("draft", ""))
    elif mode == "plain":
        prompt = ("아래 한국어 논문 문단을 석사 1년차가 읽어도 이해되게 쉬운 말로 풀어 써라. 전문용어는 처음 나올 때 한 줄로 설명. 카드 번호 [cN] 유지. 문단만 출력.\n\n"
                  + head + "[문단]\n" + node.get("draft", ""))
    elif mode == "english":
        gl = "\n".join("- %s → %s" % (g.get("ko"), g.get("en")) for g in doc.get("glossary", []) if g.get("ko") and g.get("en"))
        prev_en = _prev_draft(doc, node, "draft_en")
        prompt = (
            "당신은 기계가공 분야 국제 저널 논문의 영문 교정자다. 아래 한국어 문단을 영어 학술 문단으로 옮겨라.\n"
            "규칙:\n- 아래 용어집의 영어 표기를 반드시 그대로 써라 (동의어로 바꾸지 마라).\n"
            "- 방법·결과는 과거형, 일반적 사실은 현재형. 같은 개념은 같은 단어. 한국어투 직역 금지. 1인칭 남용 금지.\n"
            "- 카드 번호 [cN] 은 그 자리에 그대로 둔다.\n- 문단만 출력.\n\n" + head +
            ("[용어집]\n" + gl + "\n\n" if gl else "") +
            (("[앞 문단 영문 - 문체·시제 참고용]\n" + prev_en + "\n\n") if prev_en else "") +
            "[한국어 문단]\n" + node.get("draft", ""))
    else:
        raise RuntimeError("알 수 없는 작업: " + str(mode))
    out = cfg["claude"](prompt, timeout=300).strip()
    out = re.sub(r"^```[a-z]*\n|\n```$", "", out).strip()
    return out


def review_outline(doc):
    lines = []
    for n in doc["outline"]:
        lines.append("%s %s [%s] 주장: %s | 카드 %d개 | 초안 %d자" % (
            "#" * n.get("level", 1), n.get("heading", ""), n["id"], n.get("claim") or "(없음)", len(n.get("cards", [])), len(n.get("draft", ""))))
    prompt = ("아래는 기계가공 논문 원고의 개요다. 논리 흐름을 검토해 JSON 으로만 답하라.\n"
              "{\"issues\": [{\"node\": \"n3\", \"type\": \"근거 없음|중복|흐름|빠짐|주장 불명확\", \"text\": \"한 문장 지적\"}], \"summary\": \"전체 평 2~3문장\"}\n\n"
              "논문 제목: %s\n[개요]\n%s" % (doc.get("title", ""), "\n".join(lines)))
    return cfg["claude_json"](prompt, timeout=240) or {"issues": [], "summary": "검토 실패 (Claude 응답 없음)"}


def build_glossary(doc):
    pairs = [(c["text"], c["src_text"]) for c in doc["cards"] if c.get("src_text")]
    if not pairs:
        return []
    body = "\n".join("KO: %s\nEN: %s\n" % (k[:300], e[:300]) for k, e in pairs[:60])
    prompt = ("아래는 같은 문장의 한국어 번역과 영어 원문 짝이다. 이 논문 원고에서 써야 할 전문용어 대응표를 뽑아라. "
              "한국어 용어 → 원문에서 실제로 쓰인 영어 표기(약어 포함). 일반 단어 제외, 15~40개. JSON 으로만: {\"terms\": [{\"ko\": \"\", \"en\": \"\"}]}\n\n" + body)
    r = cfg["claude_json"](prompt, timeout=240) or {}
    terms = [t for t in r.get("terms", []) if t.get("ko") and t.get("en")]
    doc["glossary"] = terms
    save_ms(doc)
    return terms


def idea_review(doc, question):
    q1 = cfg["claude_json"](
        "다음 연구 아이디어(한국어)를 OpenAlex 학술 검색에 넣을 영어 검색어 4~6개로 바꿔라. 핵심 개념 조합 위주, 각 2~5단어. "
        "JSON 으로만: {\"queries\": [\"...\"]}\n\n" + question, timeout=180) or {}
    queries = [q for q in q1.get("queries", []) if isinstance(q, str)][:6]
    found, seen = [], set()
    for q in queries:
        try:
            for r in (cfg["openalex_search"](q, "", 12) or []):
                key = r.get("id") or r.get("doi") or r.get("title")
                if key and key not in seen:
                    seen.add(key); r = dict(r); r["query"] = q; found.append(r)
        except Exception:
            continue
    listing = "\n".join("- (%s) %s | %s | 피인용 %s | %s" % (
        r.get("year", "?"), (r.get("title") or "")[:120], r.get("venue") or r.get("journal") or "", r.get("cit", r.get("cited", "?")),
        (r.get("abstract") or "")[:300]) for r in found[:40])
    q2 = cfg["claude_json"](
        "연구 아이디어와 기존 문헌 목록이다. 신규성을 판단해 JSON 으로만 답하라.\n"
        "{\"assessment\": \"이 아이디어가 새로운지, 어느 부분이 이미 연구됐는지 4~6문장(한국어)\", "
        "\"gaps\": \"아직 비어 있는 틈 2~3개(한국어, 줄바꿈 구분)\", "
        "\"overlap\": [{\"title\": \"가장 겹치는 논문 제목\", \"why\": \"왜 겹치는지 한 문장\"}]}\n\n"
        "[아이디어]\n" + question + "\n\n[문헌]\n" + listing, timeout=300) or {}
    doc["idea"] = {"question": question, "assessment": q2.get("assessment", ""), "gaps": q2.get("gaps", ""),
                   "overlap": q2.get("overlap", []), "queries": queries,
                   "related": [{"title": r.get("title"), "year": r.get("year"), "doi": r.get("doi"), "cit": r.get("cit", r.get("cited")),
                                "venue": r.get("venue") or r.get("journal") or "", "owned": r.get("owned")} for r in found[:40]]}
    save_ms(doc)
    return doc["idea"]


# ---------- 쓰는 중간 검토 ----------
def review_paragraph(doc, nid):
    """지금 쓰고 있는 문단 하나를 심사자 눈으로: 근거 없는 주장·논리·용어·수치·문장. 한국어 JSON."""
    node = _node(doc, nid)
    txt = node.get("draft") or ""
    if len(txt.strip()) < 40:
        return {"issues": [], "summary": "검토할 만큼 글이 없습니다."}
    cards = _cards_text(doc, node)
    prev = _prev_draft(doc, node)
    prompt = ("당신은 기계가공 분야 국제 저널의 심사자다. 아래 '문단'만 검토해 JSON 으로만 답하라.\n"
              "{\"issues\": [{\"type\": \"근거 없음|논리|용어|수치|문장|중복\", \"quote\": \"문제 구절(원문 그대로, 40자 이내)\", \"text\": \"무엇이 왜 문제이고 어떻게 고칠지 한 문장\"}], "
              "\"summary\": \"이 문단의 강점 1개와 가장 먼저 고칠 것 1개 (2문장, 한국어)\"}\n"
              "규칙: 최대 6개. 근거 카드에 있는 사실은 '근거 없음'으로 지적하지 마라. 문단이 이미 좋으면 issues 를 비워도 된다. 지적은 한국어로.\n\n"
              "논문 제목: %s\n절: %s\n주장: %s\n\n[근거 카드]\n%s\n\n%s[문단]\n%s" % (
                  doc.get("title", ""), node.get("heading", ""), node.get("claim") or "(없음)", cards or "(없음)",
                  ("[앞 문단 끝]\n" + prev + "\n\n") if prev else "", txt))
    r = cfg["claude_json"](prompt, timeout=240) or {"issues": [], "summary": "검토 실패 (응답 없음)"}
    node["review"] = {"t": time.time(), "result": r, "len": len(txt)}
    save_ms(doc)
    return r


# ---------- 내 논문 DB 에서 근거 찾기 ----------
def _translation_sentences(fname):
    """논문 하나의 (문장번호, 영어 원문, 쪽, 한국어 번역) 목록. 번역 정렬표 + 번역문의 [sN] 표식으로."""
    stem = os.path.splitext(fname)[0]
    tb = cfg["load_json"](os.path.join(cfg["GEN_DIR"], stem + ".번역.정렬.json"), None)
    if not tb:
        return []
    ko = {}
    try:
        md = io.open(os.path.join(cfg["GEN_DIR"], stem + ".번역.md"), encoding="utf-8").read()
        for m in re.finditer(r"\[s(\d+)\]\s*([^\[\n]{5,}?)(?=\s*\[s\d+\]|\n|$)", md):
            ko.setdefault(m.group(1), m.group(2).strip())
    except Exception:
        pass
    out = []
    for k, v in tb.items():
        t = v.get("t") or ""
        if len(t) < 30:
            continue
        out.append((k, t, v.get("p"), ko.get(k, "")))
    return out


def find_evidence(doc, nid, query=""):
    """주장·문단에서 검색 구절을 뽑아, 내 논문들의 번역 문장표(영어 원문)에서 근거 문장을 찾는다."""
    node = _node(doc, nid) if nid else None
    seed = (query or "").strip() or ((node.get("claim") or "") + "\n" + (node.get("draft") or "")[:600] if node else "")
    if not seed.strip():
        raise RuntimeError("주장이나 초안이 있어야 근거를 찾을 수 있습니다")
    q = cfg["claude_json"](
        "아래 글의 주장을 뒷받침하거나 반박할 근거를 영어 논문 본문에서 찾으려 한다. 검색용 영어 핵심 구절 5~8개를 뽑아라 "
        "(2~4단어, 전문용어 우선, 동의어·약어 포함). JSON 으로만: {\"phrases\": [\"...\"]}\n\n" + seed, timeout=120) or {}
    phrases = [p.lower() for p in q.get("phrases", []) if isinstance(p, str) and len(p) > 2][:8]
    if not phrases:
        phrases = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{4,}", seed)][:8]
    words = set()
    for p in phrases:
        words.update(w for w in re.findall(r"[a-z][a-z-]{2,}", p) if w not in ("the", "and", "with", "for", "from", "that", "this", "were", "was"))
    files = [f for f in os.listdir(cfg["GEN_DIR"]) if f.endswith(".번역.정렬.json")]
    hits = []
    for f in files:
        pdf = f[:-len(".번역.정렬.json")] + ".pdf"
        for sid, t, p, ko in _translation_sentences(pdf):
            low = t.lower()
            ph = sum(1 for x in phrases if x in low)
            wd = sum(1 for w in words if w in low)
            score = ph * 3 + wd
            if score >= 3:
                hits.append({"file": pdf, "short": paper_short(pdf), "sent": "s" + sid, "page": (p + 1) if p is not None else None,
                             "en": t[:400], "ko": ko[:300], "score": score})
    hits.sort(key=lambda h: -h["score"])
    # 논문당 최대 4개, 전체 15개
    per, out = {}, []
    for h in hits:
        if per.get(h["file"], 0) >= 4:
            continue
        per[h["file"]] = per.get(h["file"], 0) + 1
        out.append(h)
        if len(out) >= 15:
            break
    return {"phrases": phrases, "hits": out, "searched": len(files)}


# ---------- 저널 규격 ----------
JOURNALS = {
    "JMPT": {"name": "Journal of Materials Processing Technology (Elsevier)", "ref_style": "번호식 [n], Vancouver 계열 (Ernst H, Martellotti M. Title. Journal Year;Vol:pages)",
             "abstract_max_words": 250, "highlights": {"min": 3, "max": 5, "max_chars": 85}, "keywords_max": 6,
             "fig_width_mm": {"single": 90, "double": 190}, "fig_dpi": {"halftone": 300, "combination": 500, "line": 1000},
             "notes": "하이라이트 필수(3~5개, 공백 포함 85자 이내). 그림은 TIFF/EPS/JPEG, 본문 언급 순서대로 번호. 초록 단어 수 한도는 최신 가이드로 확인 필요."},
}

# 논문모음 파일명의 저널 약어(연도_약어_저자_제목) 에서 자주 쓰는 저널의 전체 이름. 수집설정.json 의 저널약어(사용자 지정)가 우선.
JOURNAL_NAMES = {
    "IJMTM": "International Journal of Machine Tools and Manufacture (Elsevier)", "JMPT": "Journal of Materials Processing Technology (Elsevier)",
    "JMP": "Journal of Manufacturing Processes (Elsevier)", "IJMS": "International Journal of Mechanical Sciences (Elsevier)",
    "PE": "Precision Engineering (Elsevier)", "AMT": "International Journal of Advanced Manufacturing Technology (Springer)",
    "WEAR": "Wear (Elsevier)", "JMSE": "Journal of Manufacturing Science and Engineering (ASME)", "Trib": "Tribology International (Elsevier)",
    "CIRPA": "CIRP Annals - Manufacturing Technology (Elsevier)", "CIRPJMST": "CIRP Journal of Manufacturing Science and Technology (Elsevier)",
    "IJPEM": "International Journal of Precision Engineering and Manufacturing (Springer)", "IJEM": "International Journal of Extreme Manufacturing (IOP)",
    "IJHMT": "International Journal of Heat and Mass Transfer (Elsevier)", "JMPS": "Journal of the Mechanics and Physics of Solids (Elsevier)",
    "MD": "Materials & Design (Elsevier)", "MST": "Materials Science and Technology", "PRA": "Physical Review Applied (APS)",
    "ATE": "Applied Thermal Engineering (Elsevier)", "AM": "Additive Manufacturing (Elsevier)", "MATERIALS": "Materials (MDPI)", "METALS": "Metals (MDPI)",
    "MICROMACHINES": "Micromachines (MDPI)", "COATINGS": "Coatings (MDPI)", "JMMP": "Journal of Manufacturing and Materials Processing (MDPI)",
    "PNAS": "Proceedings of the National Academy of Sciences", "FME": "Frontiers of Mechanical Engineering (Springer)",
}
ELSEVIER_GENERIC = {"ref_style": "번호식 [n] (저널 가이드 확인)", "abstract_max_words": 250, "highlights": {"min": 3, "max": 5, "max_chars": 85},
                    "keywords_max": 6, "fig_width_mm": {"single": 90, "double": 190}, "fig_dpi": {"halftone": 300, "combination": 500, "line": 1000},
                    "notes": "이 저널의 세부 규격은 아직 등록되지 않아 Elsevier 공통 규칙(초록 250단어·하이라이트 3~5개 85자·키워드 6개)으로 점검합니다. 정확한 한도는 저널 Guide for Authors 로 확인하세요."}


def journal_rules(abbr):
    """저널 약어 → 점검·머리부 규칙. 등록된 저널(JMPT)은 세부 규칙, 나머지는 일반 규칙 + 이름."""
    abbr = (abbr or "").strip()
    if abbr in JOURNALS:
        return dict(JOURNALS[abbr], registered=True)
    if not abbr:
        return dict(JOURNALS["JMPT"], name="저널 미지정 (JMPT 규칙으로 점검)", registered=False)
    j = dict(ELSEVIER_GENERIC); j["name"] = JOURNAL_NAMES.get(abbr, abbr) + " · 규격 미등록(일반 규칙)"; j["registered"] = False
    return j


def archive_journals():
    """논문모음 파일명에서 저널 약어별 편수. 원고 화면의 저널 선택 목록용."""
    import collections
    c = collections.Counter()
    try:
        for f in os.listdir(cfg["ARCHIVE"]):
            m = re.match(r"^\d{4}_([^_]+)_", f)
            if m and f.lower().endswith(".pdf"):
                c[m.group(1)] += 1
    except OSError:
        pass
    names = dict(JOURNAL_NAMES)
    try:
        import intake
        for full, ab in intake.Config().data.get("저널약어", {}).items():
            names.setdefault(ab, full)
    except Exception:
        pass
    return [{"abbr": k, "count": n, "name": names.get(k, ""), "registered": k in JOURNALS} for k, n in c.most_common()]


def _body_text(doc, key="draft"):
    return "\n\n".join(("## " + n.get("heading", "") + "\n" + (n.get(key) or n.get("draft") or "")) for n in doc["outline"])


def front_matter(doc):
    """본문에서 초록·키워드·하이라이트 생성 (저널 규격 반영)"""
    j = journal_rules((doc.get("meta") or {}).get("journal", ""))
    lang = (doc.get("meta") or {}).get("lang", "ko")
    body = _body_text(doc)[:60000]
    hl = j["highlights"]
    prompt = ("아래 논문 본문으로 투고용 머리부를 만들어라. JSON 으로만 답하라: "
              "{\"abstract\": \"...\", \"keywords\": [\"...\"], \"highlights\": [\"...\"], \"title_suggestions\": [\"...\"]}\n"
              "- abstract: %s, 배경 1문장·목적 1문장·방법 1~2문장·핵심 결과(수치 포함) 2~3문장·의의 1문장, %d단어 이내, 인용·약어 정의 없이.\n"
              "- keywords: %d개 이내, 본문에서 실제로 쓴 용어.\n- highlights: %d~%d개, 각각 공백 포함 %d자 이내의 완결된 문장(영어), 결과 중심.\n"
              "- title_suggestions: 현재 제목보다 정보량이 많은 대안 2개(영어).\n"
              "저널: %s\n\n[현재 제목] %s\n\n[본문]\n%s" % (
                  "영어" if lang == "en" else "영어(투고용)", j["abstract_max_words"], j["keywords_max"], hl["min"], hl["max"], hl["max_chars"],
                  j["name"], (doc.get("front") or {}).get("title") or doc.get("title", ""), body))
    r = cfg["claude_json"](prompt, timeout=400) or {}
    f = doc.setdefault("front", {"title": "", "authors": "", "abstract": "", "keywords": [], "highlights": []})
    f["abstract_ai"] = r.get("abstract", ""); f["keywords_ai"] = r.get("keywords", []); f["highlights_ai"] = r.get("highlights", [])
    f["title_suggestions"] = r.get("title_suggestions", [])
    save_ms(doc)
    return f


def check_manuscript(doc):
    """규칙 점검 (Claude 없이): 그림 번호·언급 순서, 참고문헌 인용 누락, 초록 길이, 하이라이트, 키워드"""
    j = journal_rules((doc.get("meta") or {}).get("journal", ""))
    issues = []
    body = _body_text(doc)
    # 그림: 본문 첫 언급 순서
    order = []
    for m in re.finditer(r"\bFigs?\.?\s*(\d+)(?:\s*(?:and|,|–|-|~)\s*(\d+))?", body):
        for g in (m.group(1), m.group(2)):
            if g and int(g) not in order: order.append(int(g))
    regs = sorted(f["num"] for f in doc.get("figures", []))
    if order != sorted(order):
        issues.append({"type": "그림 순서", "text": "본문 첫 언급 순서가 번호 순이 아님: %s" % " → ".join("Fig.%d" % k for k in order[:12])})
    for k in regs:
        if k not in order: issues.append({"type": "그림 미언급", "text": "Fig. %d 은 등록되어 있으나 본문에서 언급되지 않음" % k})
    for k in order:
        if k not in regs: issues.append({"type": "그림 없음", "text": "본문이 Fig. %d 을 언급하지만 그림 목록에 없음" % k})
    for f in doc.get("figures", []):
        cap = f.get("caption_en") or f.get("caption")
        if not cap: issues.append({"type": "캡션 없음", "text": "Fig. %d 캡션이 비어 있음" % f["num"]})
        if not f.get("png") and not f.get("orig"): issues.append({"type": "그림 파일 없음", "text": "Fig. %d 이미지 파일이 없음" % f["num"]})
    # 참고문헌
    cited = set()
    for m in re.finditer(r"\[(\d+(?:\s*[–-]\s*\d+)?(?:\s*,\s*\d+(?:\s*[–-]\s*\d+)?)*)\]", body):
        for part in re.split(r"\s*,\s*", m.group(1)):
            rng = re.match(r"(\d+)\s*[–-]\s*(\d+)", part)
            if rng: cited.update(range(int(rng.group(1)), int(rng.group(2)) + 1))
            elif part.strip().isdigit(): cited.add(int(part))
    nref = len(doc.get("refs_text", []))
    if nref:
        unc = [k for k in range(1, nref + 1) if k not in cited]
        if unc: issues.append({"type": "참고문헌 미인용", "text": "본문에 인용되지 않은 참고문헌: %s" % ", ".join("[%d]" % k for k in unc[:20])})
        over = sorted(k for k in cited if k > nref)
        if over: issues.append({"type": "인용 번호 초과", "text": "참고문헌 목록(%d편)보다 큰 번호 인용: %s" % (nref, ", ".join(map(str, over[:10])))})
        firsts = []
        for m in re.finditer(r"\[(\d+)", body):
            k = int(m.group(1))
            if k not in firsts: firsts.append(k)
        if firsts and firsts != sorted(firsts): issues.append({"type": "인용 순서", "text": "번호식은 첫 인용 순서대로 번호를 매겨야 함 (현재 %s…)" % ", ".join(map(str, firsts[:10]))})
    # 머리부
    f = doc.get("front") or {}
    if f.get("abstract"):
        w = len(f["abstract"].split())
        if w > j["abstract_max_words"]: issues.append({"type": "초록 길이", "text": "초록 %d단어 (한도 %d단어, 가이드 재확인)" % (w, j["abstract_max_words"])})
    else:
        issues.append({"type": "초록 없음", "text": "초록이 비어 있음 — 머리부 생성으로 만들 수 있음"})
    hl = f.get("highlights") or []
    if len(hl) < j["highlights"]["min"] or len(hl) > j["highlights"]["max"]:
        issues.append({"type": "하이라이트", "text": "%d개 (저널 요구 %d~%d개)" % (len(hl), j["highlights"]["min"], j["highlights"]["max"])})
    for h in hl:
        if len(h) > j["highlights"]["max_chars"]: issues.append({"type": "하이라이트 길이", "text": "%d자: %s…" % (len(h), h[:50])})
    if len(f.get("keywords") or []) > j["keywords_max"]: issues.append({"type": "키워드", "text": "%d개 (한도 %d)" % (len(f["keywords"]), j["keywords_max"])})
    words = len(re.sub(r"##[^\n]*", "", body).split())
    return {"journal": j["name"], "issues": issues, "stats": {"words": words, "sections": len(doc["outline"]), "figures": len(regs), "refs": nref, "cited": len(cited), "comments": len(doc.get("comments", []))}}


def full_review(doc):
    """규칙 점검 + Claude 전체 검토(용어 일관성·반복·논리 비약·근거 없는 주장)"""
    rules = check_manuscript(doc)
    body = _body_text(doc)[:70000]
    prompt = ("당신은 기계가공 분야 국제 저널의 꼼꼼한 심사자다. 아래 원고 전체를 읽고 JSON 으로만 답하라:\n"
              "{\"terminology\": [{\"variants\": [\"built-up edge\", \"BUE\", \"build-up edge\"], \"suggest\": \"통일 표기\"}], "
              "\"repetition\": [\"거의 같은 말이 반복되는 곳 (절 이름 + 요지)\"], "
              "\"logic\": [\"논리 비약·근거 없는 주장·수치 불일치 (절 이름 + 무엇이)\"], "
              "\"style\": [\"시제·태·문장 길이 문제 3~5개\"], \"overall\": \"전체 평 3문장(한국어)\"}\n"
              "각 항목은 한국어로 짧게, 최대 8개씩.\n\n[원고]\n" + body)
    r = cfg["claude_json"](prompt, timeout=600) or {}
    rules["claude"] = r
    doc["review"] = {"t": time.time(), "result": rules}
    save_ms(doc)
    return rules


# ---------- 인용 번호 ----------
def citations(doc, lang="ko"):
    """초안 속 [cN] → 참고문헌 번호 [k] (출처 논문 등장 순). (문단별 텍스트, 참고문헌 목록) 반환"""
    by = {c["id"]: c for c in doc["cards"]}
    order, refs = {}, []
    key = "draft_en" if lang == "en" else "draft"

    def repl(m):
        c = by.get("c" + m.group(1))          # 카드 id 는 'c3' 꼴, 정규식 그룹은 숫자만
        f = c["source"].get("file") if c else ""
        if not f:
            return ""
        if f not in order:
            order[f] = len(order) + 1
            refs.append(paper_meta(f))
        return "[%d]" % order[f]

    paras = []
    for n in doc["outline"]:
        txt = n.get(key) or n.get("draft") or ""      # 영문이 비어 있으면(가져온 영어 원고 등) 본문 초안 사용
        txt = re.sub(r"\[c(\d+)\]", lambda m: repl(m), txt)
        txt = re.sub(r"\](\s*)\[", "][", txt)
        paras.append((n, txt))
    return paras, refs


def ref_line(i, r):
    a = "%s (%s). %s. %s.%s" % (r["author"], r["year"], r["title"], r["journal"], (" https://doi.org/" + r["doi"]) if r.get("doi") else "")
    return "[%d] %s" % (i, a)


# ---------- .docx (표준 라이브러리만으로) ----------
def _xml(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _runs(text):
    """**굵게**, *기울임* 을 런으로"""
    out = []
    for tok in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not tok:
            continue
        if tok.startswith("**"):
            out.append('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>' % _xml(tok[2:-2]))
        elif tok.startswith("*"):
            out.append('<w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>' % _xml(tok[1:-1]))
        else:
            out.append('<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % _xml(tok))
    return "".join(out)


def _para(text, style=None, align=None):
    ppr = ""
    if style or align:
        ppr = "<w:pPr>%s%s</w:pPr>" % ('<w:pStyle w:val="%s"/>' % style if style else "", '<w:jc w:val="%s"/>' % align if align else "")
    return "<w:p>%s%s</w:p>" % (ppr, _runs(text))


def _image_para(rid, cx, cy, n):
    return ('<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="%d" cy="%d"/><wp:docPr id="%d" name="Figure %d"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="%d" name="fig%d.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>' % (cx, cy, n, n, n, n, rid, cx, cy))


STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="맑은 고딕"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr></w:style>
</w:styles>"""


def export_docx(doc, lang="ko"):
    paras, refs = citations(doc, lang)
    f = doc.get("front") or {}
    body = [_para(f.get("title") or doc.get("title", ""), "Title")]
    if f.get("authors"):
        for a in f["authors"].split("\n"):
            body.append(_para(a))
    if f.get("highlights"):
        body.append(_para("Highlights", "Heading1"))
        for h in f["highlights"]:
            body.append(_para("• " + h))
    if f.get("abstract"):
        body.append(_para("Abstract", "Heading1"))
        for p in re.split(r"\n\s*\n", f["abstract"]):
            if p.strip(): body.append(_para(p.strip()))
    if f.get("keywords"):
        body.append(_para("Keywords: " + "; ".join(f["keywords"])))
    for n, txt in paras:
        body.append(_para(n.get("heading", ""), "Heading1" if n.get("level", 1) == 1 else "Heading2"))
        for p in [x.strip() for x in re.split(r"\n\s*\n", txt) if x.strip()]:
            body.append(_para(p))
    media, rels = [], []
    figs = [f for f in doc.get("figures", []) if f.get("png") and os.path.isfile(os.path.join(cfg["FIG_DIR"], f["png"]))]
    if figs:
        body.append(_para("Figures" if lang == "en" else "그림", "Heading1"))
        for i, f in enumerate(figs, 1):
            path = os.path.join(cfg["FIG_DIR"], f["png"])
            rid = "rIdImg%d" % i
            try:
                from PIL import Image
                with Image.open(path) as im:
                    w, h = im.size
            except Exception:
                w, h = 1400, 1000
            cx = 5400000; cy = int(cx * h / max(1, w))
            media.append((i, path)); rels.append((rid, "media/image%d.png" % i))
            body.append(_image_para(rid, cx, cy, i))
            cap = f.get("caption_en") if lang == "en" else f.get("caption")
            body.append(_para("Fig. %d. %s" % (f.get("num", i), cap or ""), "Caption"))
    if doc.get("refs_text"):           # 워드에서 가져온 원문 참고문헌이 있으면 그대로 (번호 유지)
        body.append(_para("References" if lang == "en" else "참고문헌", "Heading1"))
        for r in doc["refs_text"]:
            body.append(_para(r))
    elif refs:
        body.append(_para("References" if lang == "en" else "참고문헌", "Heading1"))
        for i, r in enumerate(refs, 1):
            body.append(_para(ref_line(i, r)))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
                '<w:body>%s<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>'
                % "".join(body))
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                + "".join('<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="%s"/>' % (rid, t) for rid, t in rels)
                + '</Relationships>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                 '</Relationships>')
    safe = re.sub(r'[\\/:*?"<>|]+', " ", doc.get("title", "원고")).strip()[:60] or "원고"
    out = os.path.join(cfg["EXPORT_DIR"], "%s_%s_%s.docx" % (safe, "EN" if lang == "en" else "KO", time.strftime("%Y%m%d_%H%M")))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", STYLES_XML)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        for i, path in media:
            z.write(path, "word/media/image%d.png" % i)
    return out


# ---------- 그림: 도식 생성기 + Edge 렌더 ----------
def _edge():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.isfile(p):
            return p
    return None


def render_png(svg_path, png_path, w, h, scale=1):
    edge = _edge()
    if not edge:
        return False
    url = "file:///" + svg_path.replace("\\", "/")
    args = [edge, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--default-background-color=ffffffff",
            "--window-size=%d,%d" % (w, h), "--force-device-scale-factor=%d" % scale, "--screenshot=" + png_path, url]
    subprocess.run(args, capture_output=True, timeout=120, **cfg["no_window"]())
    return os.path.isfile(png_path)


def schematic_figure(doc, params, fig_id=None):
    from schematic import turning
    svg = turning.render(params)
    w, h = turning.size(params)
    fig = None
    if fig_id:
        fig = next((f for f in doc["figures"] if f["id"] == fig_id), None)
    if not fig:
        fig = {"id": _next_id(doc["figures"], "f"), "num": len(doc["figures"]) + 1, "caption": "", "caption_en": "",
               "source": {"type": "schematic"}}
        doc["figures"].append(fig)
    fig["source"] = {"type": "schematic", "template": "turning", "params": params}
    base = "%s_%s" % (doc["id"], fig["id"])
    svg_path = os.path.join(cfg["FIG_DIR"], base + ".svg")
    png_path = os.path.join(cfg["FIG_DIR"], base + ".png")
    io.open(svg_path, "w", encoding="utf-8").write(svg)
    ok = render_png(svg_path, png_path, w, h, 1)
    fig["svg"] = base + ".svg"
    fig["png"] = base + ".png" if ok else ""
    fig["w"], fig["h"] = w, h
    fig["t"] = time.time()
    save_ms(doc)
    return fig


def _gen():
    import sys, shutil
    if cfg["BASE"] not in sys.path:
        sys.path.insert(0, cfg["BASE"])
    from schematic import generate as gen
    gen.cfg["no_window"] = cfg["no_window"]
    gen.cfg["claude_exe"] = cfg.get("claude_exe") or shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")
    return gen


def save_ref(doc, name, data):
    """참고 그림(아이패드 스케치·PPT) 저장 → 이미지 파일명 목록"""
    gen = _gen()
    ref_dir = os.path.join(cfg["FIG_DIR"], "refs")
    base = "%s_%s" % (doc["id"], time.strftime("%H%M%S"))
    paths, note = gen.save_ref_image(data, name, ref_dir, base)
    return {"images": [os.path.basename(p) for p in paths], "note": note}


def claude_figure(doc, spec, refs=(), fig_id=None, feedback=None):
    """설명(+참고 그림) → Claude 가 lib 로 그리는 스크립트 → SVG/PNG. fig_id 가 있으면 그 그림의 스크립트를 수정 요청으로 고친다."""
    gen = _gen()
    ref_dir = os.path.join(cfg["FIG_DIR"], "refs")
    ref_paths = [os.path.join(ref_dir, os.path.basename(r)) for r in (refs or []) if os.path.isfile(os.path.join(ref_dir, os.path.basename(r)))]
    fig = next((f for f in doc["figures"] if f["id"] == fig_id), None) if fig_id else None
    prev = (fig or {}).get("source", {}).get("script") if fig else None
    if fig and not spec:
        spec = fig["source"].get("spec") or {}
        ref_paths = ref_paths or [os.path.join(ref_dir, r) for r in fig["source"].get("refs", []) if os.path.isfile(os.path.join(ref_dir, r))]
    log = []
    # 같은 분야의 최근 그림 스크립트를 출발점으로 (백지 설계보다 훨씬 빠르고 스타일도 이어짐)
    dom = (spec or {}).get("domain")
    prior = [f["source"].get("script") for f in sorted(doc["figures"], key=lambda f: -(f.get("t") or 0))
             if f.get("source", {}).get("type") == "claude" and f["source"].get("script") and (not dom or f["source"].get("spec", {}).get("domain") == dom)
             and f["id"] != (fig or {}).get("id")]
    code, svg, log = gen.generate(spec or {}, ref_paths, prev_script=prev, feedback=feedback, log=log, prior_scripts=prior)
    if not fig:
        fig = {"id": _next_id(doc["figures"], "f"), "num": len(doc["figures"]) + 1, "caption": "", "caption_en": "", "source": {}}
        doc["figures"].append(fig)
    hist = (fig.get("source") or {}).get("history", [])
    if prev:
        hist = (hist + [{"t": fig.get("t", 0), "script": prev, "feedback": feedback or ""}])[-10:]
    fig["source"] = {"type": "claude", "spec": spec or {}, "refs": [os.path.basename(p) for p in ref_paths], "script": code, "history": hist}
    base = "%s_%s" % (doc["id"], fig["id"])
    svg_path = os.path.join(cfg["FIG_DIR"], base + ".svg"); png_path = os.path.join(cfg["FIG_DIR"], base + ".png")
    io.open(svg_path, "w", encoding="utf-8").write(svg)
    io.open(os.path.join(cfg["FIG_DIR"], base + ".py"), "w", encoding="utf-8").write(code)
    w, h = gen.svg_size(svg)
    ok = render_png(svg_path, png_path, w, h, 1)
    fig["svg"] = base + ".svg"; fig["png"] = base + ".png" if ok else ""; fig["w"], fig["h"] = w, h; fig["t"] = time.time(); fig["log"] = log
    save_ms(doc)
    return fig


def export_figure(doc, fig_id, scale=3):
    """저널 제출용 고해상도 PNG (기본 3배 ≈ 300 dpi 상당)"""
    fig = next((f for f in doc["figures"] if f["id"] == fig_id), None)
    if not fig or not fig.get("svg"):
        raise RuntimeError("그림을 찾을 수 없습니다")
    svg_path = os.path.join(cfg["FIG_DIR"], fig["svg"])
    out = os.path.join(cfg["EXPORT_DIR"], "Fig%d_%s_x%d.png" % (fig.get("num", 0), doc["id"], scale))
    if not render_png(svg_path, out, fig.get("w", 1400), fig.get("h", 1990), scale):
        raise RuntimeError("Edge 렌더 실패")
    return out


# ---------- HTTP ----------

# ---------- 교수님·공저자 피드백 (워드 주석) 논의 ----------
def _norm_ws(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _tokens(s):
    """비교용 낱말 열: 소문자, 글자·숫자만 (기호·띄어쓰기 차이를 무시)."""
    return " ".join(re.findall(r"[^\W_]+", (s or "").lower()))


def _comment_node(doc, c):
    """주석이 달린 구절(anchor)이 들어 있는 절을 찾는다. 못 찾으면 None (그림·표·전체에 대한 지적).
    1) 구절 그대로 포함  2) 절 제목에 달린 주석  3) 낱말 4개 창(window)이 가장 많이 맞는 절 (주석 후 문장이 조금 고쳐진 경우)"""
    a = _norm_ws(c.get("anchor", "")).lower()
    if len(a) < 6:
        return None
    outline = doc.get("outline", [])
    for probe in (a[:80], a[:30]):
        for key in ("draft", "draft_en"):
            for n in outline:
                if probe in _norm_ws(n.get(key, "")).lower():
                    return n
    at = _tokens(a)
    for n in outline:
        ht = _tokens(n.get("heading"))
        if len(at) >= 8 and (at in ht or (len(ht) >= 8 and ht in at)):
            return n
    words = at.split()
    if len(words) < 4:
        return None
    wins = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
    best, best_hits = None, 0
    for n in outline:
        body = _tokens((n.get("draft") or "") + " " + (n.get("draft_en") or ""))
        hits = sum(1 for w in wins if w in body)
        if hits > best_hits:
            best, best_hits = n, hits
    if best is not None and best_hits >= max(1, int(len(wins) * 0.3)):
        return best
    return None


def _comment_threads(doc):
    """같은 구절에 연달아 달린 주석(지적 → 학생 답변)을 한 묶음으로."""
    threads, prev = [], None
    for c in doc.get("comments", []):
        a = _norm_ws(c.get("anchor"))
        if threads and prev is not None and a and a == _norm_ws(prev.get("anchor")):
            threads[-1].append(c)
        else:
            threads.append([c])
        prev = c
    return threads


def _comment_by_id(doc, cid):
    for c in doc.get("comments", []):
        if str(c.get("id")) == str(cid):
            return c
    return None


def _anchor_paragraph(node, key, anchor):
    """절 본문(빈 줄로 나뉜 문단들) 중 주석 구절이 든 문단의 (번호, 본문). 못 찾으면 (-1, 절 전체)."""
    text = node.get(key) or ""
    paras = [x for x in re.split(r"\n\s*\n", text) if x.strip()]
    a = _norm_ws(anchor).lower()
    if len(paras) <= 1 or len(a) < 6:
        return -1, text
    for probe in (a[:80], a[:30]):
        for i, ptxt in enumerate(paras):
            if probe in _norm_ws(ptxt).lower():
                return i, ptxt
    words = _tokens(a).split()
    if len(words) >= 4:
        wins = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
        best, best_hits = -1, 0
        for i, ptxt in enumerate(paras):
            body = _tokens(ptxt)
            hits = sum(1 for w in wins if w in body)
            if hits > best_hits:
                best, best_hits = i, hits
        if best >= 0 and best_hits >= max(1, int(len(wins) * 0.3)):
            return best, paras[best]
    return -1, text


def _feedback_context(doc, c):
    node = _comment_node(doc, c)
    para = ""
    if node:
        key = "draft" if node.get("draft") else "draft_en"
        _, para = _anchor_paragraph(node, key, c.get("anchor", ""))
    same = [x for x in doc.get("comments", []) if x is not c and _norm_ws(x.get("anchor")) and _norm_ws(x.get("anchor")) == _norm_ws(c.get("anchor"))]
    lines = ["논문 제목: %s" % doc.get("title", ""), "투고 저널: %s" % (doc.get("meta", {}).get("journal") or "미정")]
    if node:
        lines.append("절: %s" % node.get("heading", ""))
    lines += ["", "[주석이 달린 구절]", _norm_ws(c.get("anchor")) or "(구절 표시 없음 - 그림·표·전체에 대한 지적일 수 있음)",
              "", "[해당 문단]", para[:4000] or "(문단을 찾지 못함)",
              "", "[주석] %s (%s): %s" % (c.get("author"), c.get("date"), _norm_ws(c.get("text")))]
    for x in same:
        lines.append("[같은 구절의 다른 주석] %s (%s): %s" % (x.get("author"), x.get("date"), _norm_ws(x.get("text"))))
    return "\n".join(lines), node


def _thread_text(c, n=8):
    return "\n".join("[%s] %s" % ("나" if m.get("role") == "user" else "Claude", m.get("text", "")) for m in c.get("thread", [])[-n:])


def discuss_feedback(doc, cid, question=""):
    """주석 하나를 놓고 Claude 와 논의. 첫 번에는 뜻·대응 방안·필요한 것·답변 초안, 그 뒤로는 질문에 답. 한국어."""
    c = _comment_by_id(doc, cid)
    if not c:
        return {"error": "주석을 찾지 못했습니다"}
    ctx, node = _feedback_context(doc, c)
    thread = c.setdefault("thread", [])
    hist = _thread_text(c)
    cards = _cards_text(doc, node) if node else ""
    if not thread and not question:
        ask = ("이 주석을 처음 검토한다. 네 항목을 한국어로 쓰고, 각 항목은 줄 첫머리에 **뜻**, **대응 방안**, **필요한 것**, **답변 초안** 이라고 표시해라.\n"
               "- 뜻: 교수님이 요구하는 것이 무엇인지, 문단 문맥과 연결해 한두 문장.\n"
               "- 대응 방안: 2~3가지를 번호로. 각각 어떻게 고치는지 구체적으로, 마지막에 어느 쪽을 권하는지.\n"
               "- 필요한 것: 추가 실험·데이터·확인·문헌 중 무엇이 필요한지. 없으면 '없음'.\n"
               "- 답변 초안: 교수님께 드릴 답변 1~2문장 (한국어 존댓말).\n"
               "원고가 영어라도 답은 한국어로. 문단을 다시 쓰지는 마라 (그건 별도 기능이다).")
    else:
        ask = "지금까지의 논의를 이어서 아래 질문에 한국어로 답하라. 문장 예시가 필요하면 원고의 언어로 들어도 된다.\n[질문]\n" + (question or "계속 논의해 주세요.")
    prompt = ("당신은 기계가공 분야 논문 지도교수의 피드백을 학생과 함께 검토하는 공저자다. 솔직하고 구체적으로, 군말 없이.\n\n" +
              ctx + (("\n\n[이 문단의 근거 카드]\n" + cards) if cards else "") +
              (("\n\n[지금까지의 논의]\n" + hist) if hist else "") + "\n\n" + ask)
    out = (cfg["claude"](prompt, timeout=300) or "").strip()
    if not out:
        return {"error": "Claude 응답이 없습니다"}
    if question:
        thread.append({"role": "user", "text": question, "t": time.time()})
    thread.append({"role": "claude", "text": out, "t": time.time()})
    c["node"] = node["id"] if node else None
    save_ms(doc)
    return {"text": out, "node": c["node"], "thread": thread}


def revise_for_feedback(doc, cid, note=""):
    """주석을 반영해 해당 문단을 고쳐 본다. 원고 언어로 문단만. 없는 데이터는 표시만 한다."""
    c = _comment_by_id(doc, cid)
    if not c:
        return {"error": "주석을 찾지 못했습니다"}
    ctx, node = _feedback_context(doc, c)
    if not node:
        return {"error": "이 주석이 달린 문단을 찾지 못했습니다 (그림·표에 대한 지적일 수 있습니다)"}
    key = "draft" if node.get("draft") else "draft_en"
    pidx, para = _anchor_paragraph(node, key, c.get("anchor", ""))
    whole = node.get(key, "")
    lang_en = doc.get("meta", {}).get("lang") == "en" or (bool(para) and sum(1 for ch in para if ord(ch) < 128) > len(para) * 0.8)
    hist = _thread_text(c, 6)
    prompt = ("당신은 기계가공 분야 국제 저널 논문의 공저자다. 아래 주석을 반영해 '[해당 문단]' 하나를 고쳐 써라.\n"
              "규칙:\n- 문단은 %s로. 지적과 무관한 문장은 그대로 둔다. [해당 문단] 만 다루고 절의 다른 문단은 쓰지 마라.\n"
              "- 원고에 없는 수치·결과를 지어내지 마라. 필요한 데이터가 없으면 그 자리에 %s 처럼 표시한다.\n"
              "- 지적과 무관한 수치는 절대 바꾸지 마라. 수치가 서로 안 맞는 것을 발견하면 고치지 말고 문단 끝에 %s 처럼 한 줄로만 적어라.\n"
              "- 논의에서 정해진 방향이 있으면 그것을 따른다. 카드 번호 [cN] 이 있으면 그대로 둔다.\n- 고친 문단만 출력. 설명·제목·따옴표 금지.\n\n"
              % ("영어 학술 문체" if lang_en else "한국어 학술 문체",
                 "(DATA NEEDED: repetitions per condition)" if lang_en else "(데이터 필요: 조건별 반복 수)",
                 "(CHECK: 104 µm vs 0.8h₀ inconsistent)" if lang_en else "(확인: 104 µm 와 0.8h₀ 가 서로 안 맞음)")
              + ctx + (("\n\n[절의 나머지 문단 - 참고만, 다시 쓰지 말 것]\n" + whole[:3000]) if pidx >= 0 else "")
              + (("\n\n[논의 요약]\n" + hist) if hist else "") + (("\n\n[추가 지시]\n" + note) if note else ""))
    out = (cfg["claude"](prompt, timeout=300) or "").strip()
    out = re.sub(r"^```[a-z]*\n|\n```$", "", out).strip()
    if not out:
        return {"error": "Claude 응답이 없습니다"}
    # before = 바꿔 넣을 대상 문단 (절 전체가 아님). 화면은 이 문단만 치환한다.
    return {"text": out, "node": node["id"], "key": key, "before": para, "para_index": pidx, "partial": pidx >= 0}


def feedback_plan(doc):
    """주석 전체를 주제별로 묶어 우선순위·작업 종류를 매긴 처리 계획 (JSON)."""
    cs = doc.get("comments", [])
    if not cs:
        return {"error": "주석이 없습니다"}
    items = []
    for c in cs:
        n = _comment_node(doc, c)
        items.append("#%s %s (%s)%s: %s%s" % (c.get("id"), c.get("author"), c.get("date"), (" [절: %s]" % n["heading"]) if n else "",
                                            _norm_ws(c.get("text"))[:400], (" ← 구절: “%s”" % _norm_ws(c.get("anchor"))[:80]) if c.get("anchor") else ""))
    prompt = ("당신은 기계가공 분야 논문 지도교수의 피드백을 정리하는 공저자다. 아래 워드 주석들을 읽고 JSON 으로만 답하라:\n"
              "{\"themes\": [{\"title\": \"주제 (한국어, 8자 이내)\", \"ids\": [\"주석 번호\"], \"kind\": \"데이터 추가|실험 필요|문장 수정|그림·표 수정|구조|확인 질문\", "
              "\"priority\": 1, \"what\": \"무엇을 어떻게 할지 한두 문장 (한국어)\"}], \"first\": \"가장 먼저 할 일 한 문장\", \"note\": \"주석 사이의 연관·모순이 있으면 한두 문장, 없으면 빈 문자열\"}\n"
              "규칙: 학생 본인의 답변 주석은 원 지적과 같은 주제로 묶고 kind 는 원 지적 기준. priority 는 1(먼저)~3. 최대 8개 주제. 모든 주석 번호가 어느 주제에든 들어가야 한다.\n\n"
              "논문 제목: %s\n\n[주석]\n%s" % (doc.get("title", ""), "\n".join(items)))
    r = cfg["claude_json"](prompt, timeout=300) or {"themes": [], "first": "", "note": "정리 실패 (응답 없음)"}
    doc["feedback_plan"] = {"t": time.time(), "result": r}
    save_ms(doc)
    return r


def feedback_view(doc):
    out = []
    for grp in _comment_threads(doc):
        n = _comment_node(doc, grp[0])
        out.append({"node": n["id"] if n else None, "heading": n["heading"] if n else "", "items": grp})
    return {"threads": out, "plan": doc.get("feedback_plan")}


def _q(url, k, d=""):
    return urllib.parse.parse_qs(url.query).get(k, [d])[0]


def handle_get(h, url):
    p = url.path
    if p == "/ms":
        with open(os.path.join(cfg["BASE"], "manuscript.html"), "rb") as f:
            return h._send(200, f.read(), "text/html; charset=utf-8")
    if p == "/api/ms/list":
        return h._send(200, {"items": list_ms()})
    if p == "/api/ms/journals":
        return h._send(200, {"journals": archive_journals()})
    if p == "/api/ms":
        d = load_ms(_q(url, "id"))
        return h._send(200, d if d else {"error": "없음"})
    if p.startswith("/fig/"):
        name = os.path.basename(p)
        fp = os.path.join(cfg["FIG_DIR"], "refs", name) if p.startswith("/fig/refs/") else os.path.join(cfg["FIG_DIR"], name)
        if not os.path.isfile(fp):
            return h._send(404, {"error": "no file"})
        ctype = "image/svg+xml" if name.endswith(".svg") else "image/png"
        with open(fp, "rb") as f:
            return h._send(200, f.read(), ctype)
    return h._send(404, {"error": "unknown"})


def handle_post(h, body):
    p = h.path
    try:
        if p == "/api/ms/new":
            return h._send(200, new_ms(body.get("title", "")))
        if p == "/api/ms/save":
            d = body.get("data")
            if not d or not d.get("id"):
                return h._send(400, {"error": "data 필요"})
            old = load_ms(d["id"]) or {}
            # 개요가 바뀌면 이전 개요를 판 이력에 남김 (최근 20개)
            if old.get("outline") and json.dumps(old.get("outline"), sort_keys=True) != json.dumps(d.get("outline"), sort_keys=True):
                d.setdefault("versions", old.get("versions", []))
                d["versions"] = (d["versions"] + [{"t": old.get("updated", time.time()), "outline": old["outline"]}])[-20:]
            return h._send(200, {"ok": True, "updated": save_ms(d)["updated"]})
        if p == "/api/ms/delete":
            fp = _path(body.get("id", ""))
            if os.path.isfile(fp):
                os.remove(fp)
            return h._send(200, {"ok": True})
        if p == "/api/ms/card":
            return h._send(200, add_card(body.get("id"), body.get("text", ""), body.get("source"), body.get("note", "")))
        if p == "/api/ms/import":
            import base64
            name = os.path.basename(body.get("name") or "manuscript.docx")
            if body.get("b64"):
                up_dir = os.path.join(cfg["MS_DIR"], "가져오기"); os.makedirs(up_dir, exist_ok=True)
                path = os.path.join(up_dir, name)
                open(path, "wb").write(base64.b64decode(body["b64"]))
            else:
                path = body.get("path", "")
            if not os.path.isfile(path):
                return h._send(400, {"error": "파일을 찾을 수 없습니다: " + path})
            d = import_docx(path, body.get("title"))
            if body.get("journal"):
                d["meta"]["journal"] = body["journal"]; save_ms(d)
            return h._send(200, {"id": d["id"], "title": d["title"], "nodes": len(d["outline"]), "figures": len(d["figures"]),
                                 "refs": len(d.get("refs_text", [])), "comments": len(d.get("comments", []))})
        if p == "/api/ms/evidence":
            return h._send(200, find_evidence(load_ms(body.get("id")), body.get("node"), body.get("query", "")))
        if p == "/api/ms/check":
            return h._send(200, check_manuscript(load_ms(body.get("id"))))
        if p == "/api/ms/claude":
            doc = load_ms(body.get("id"))
            mode = body.get("mode")
            if mode == "review_outline":
                return h._send(200, review_outline(doc))
            if mode == "glossary":
                return h._send(200, {"terms": build_glossary(doc)})
            if mode == "front":
                return h._send(200, front_matter(doc))
            if mode == "review_para":
                return h._send(200, review_paragraph(doc, body.get("node")))
            if mode == "review_full":
                return h._send(200, full_review(doc))
            text = claude_paragraph(doc, body.get("node"), mode)
            return h._send(200, {"text": text})
        if p == "/api/ms/feedback":
            doc = load_ms(body.get("id"))
            if not doc:
                return h._send(400, {"error": "원고 없음"})
            op = body.get("op")
            if op == "discuss":
                return h._send(200, discuss_feedback(doc, body.get("cid"), body.get("question", "")))
            if op == "revise":
                return h._send(200, revise_for_feedback(doc, body.get("cid"), body.get("note", "")))
            if op == "plan":
                return h._send(200, feedback_plan(doc))
            return h._send(200, feedback_view(doc))
        if p == "/api/ms/idea":
            doc = load_ms(body.get("id"))
            return h._send(200, idea_review(doc, body.get("question", "")))
        if p == "/api/ms/export":
            doc = load_ms(body.get("id"))
            out = export_docx(doc, body.get("lang", "ko"))
            return h._send(200, {"path": out})
        if p == "/api/ms/open_folder":
            os.startfile(cfg["EXPORT_DIR"])
            return h._send(200, {"ok": True})
        if p == "/api/ms/figure/schematic":
            doc = load_ms(body.get("id"))
            return h._send(200, schematic_figure(doc, body.get("params") or {}, body.get("fig")))
        if p == "/api/ms/figure/refs":
            import base64
            doc = load_ms(body.get("id"))
            return h._send(200, save_ref(doc, body.get("name") or "ref.png", base64.b64decode(body.get("b64", ""))))
        if p == "/api/ms/figure/generate":
            doc = load_ms(body.get("id"))
            fig = claude_figure(doc, body.get("spec") or {}, body.get("refs") or [], body.get("fig"), body.get("feedback"))
            return h._send(200, fig)
        if p == "/api/ms/figure/export":
            doc = load_ms(body.get("id"))
            return h._send(200, {"path": export_figure(doc, body.get("fig"), int(body.get("scale", 3)))})
        if p == "/api/ms/figure/preview":
            from schematic import turning
            params = body.get("params") or {}
            svg = turning.render(params)
            return h._send(200, svg.encode("utf-8"), "image/svg+xml")
        return h._send(404, {"error": "unknown"})
    except Exception as e:
        return h._send(500, {"error": str(e)[:300]})
