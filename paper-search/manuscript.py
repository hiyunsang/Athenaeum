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
        txt = n.get(key) or ""
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
    body = [_para(doc.get("title", ""), "Title")]
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
    if refs:
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
def _q(url, k, d=""):
    return urllib.parse.parse_qs(url.query).get(k, [d])[0]


def handle_get(h, url):
    p = url.path
    if p == "/ms":
        with open(os.path.join(cfg["BASE"], "manuscript.html"), "rb") as f:
            return h._send(200, f.read(), "text/html; charset=utf-8")
    if p == "/api/ms/list":
        return h._send(200, {"items": list_ms()})
    if p == "/api/ms":
        d = load_ms(_q(url, "id"))
        return h._send(200, d if d else {"error": "없음"})
    if p.startswith("/fig/"):
        name = os.path.basename(p)
        fp = os.path.join(cfg["FIG_DIR"], name)
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
        if p == "/api/ms/claude":
            doc = load_ms(body.get("id"))
            mode = body.get("mode")
            if mode == "review_outline":
                return h._send(200, review_outline(doc))
            if mode == "glossary":
                return h._send(200, {"terms": build_glossary(doc)})
            text = claude_paragraph(doc, body.get("node"), mode)
            return h._send(200, {"text": text})
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
