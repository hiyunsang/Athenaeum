# -*- coding: utf-8 -*-
"""Athenaeum 원고(작성) 모듈.

원고 = MAENG_paper\원고\<id>.json 한 파일. 근거 카드(출처: 논문·문장 번호·쪽) → 개요(주장) → 문단 초안(한국어) → 영문화 → .docx.
그림: 절삭 도식 생성기(schematic.turning) 로 SVG 를 만들고 Edge headless 로 PNG 렌더.
server.py 가 init() 으로 경로와 Claude 호출 함수를 넘겨 준다 (순환 import 방지).
"""
import os, io, re, json, time, zipfile, subprocess, threading, urllib.parse
import difflib
import wordsync
import mathtex

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
        if not f.endswith(".json") or f.startswith("_"):   # _본보기캐시.json 같은 보조 파일은 원고가 아니다
            continue
        d = cfg["load_json"](os.path.join(cfg["MS_DIR"], f), None)
        if not d or not isinstance(d, dict) or "outline" not in d:
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
# 캡션: 'Fig. 3. …' · '그림 3 …'. 번호에 조사가 바로 붙거나('Fig. 10과 Fig. 11은 …') 번호 뒤가 소문자 낱말이면('Fig. 3 shows …') 본문 문장이다
_CAP_RE = re.compile(r"^(Fig\.?|Figure|Table|그림|표)\s*(\d+)(?![\d가-힣])\s*([.:)]?)\s*(.*)$", re.I)


def _docx_para_md(p):
    """<w:p> 하나를 마크다운 문장으로 (굵게/기울임·첨자 유지, 수식은 $LaTeX$) — 규칙은 wordsync 에 있다(워드 반영과 공유)."""
    return wordsync.para_md(p)


def import_docx(path, title=None, dry=False, trace=None):
    """워드 원고 → 새 원고. 번호 절 제목 → 개요, 문단 → 초안, Fig. N 캡션+그림 → 그림 목록, [n] 참고문헌 → refs_text,
    제목·저자·초록·키워드 → front, 공저자 주석 → comments. 영어 원고는 meta.lang = en.
    dry = 저장·그림 추출 없이 읽기만(워드 반영이 '워드에 지금 든 글'을 볼 때). trace(dict) 를 주면 문단마다 원문 위치와 어디로 갔는지를 담는다."""
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
    bm = re.search(r"<w:body>(.*)</w:body>", docxml, re.S)
    body = bm.group(1) if bm else docxml
    tables_md, tables_xml, eq_md, eq_xml = [], [], [], []

    def _tbl(m):
        eq = wordsync.eq_table_md(m.group(0))
        if eq:   # 번호 붙은 문단 수식을 담은 표 → '$$…$$ (3)' 한 문단으로 (워드 반영 때는 표 그대로 되돌린다)
            eq_md.append(eq); eq_xml.append(m.group(0))
            return "<w:p><w:r><w:t>\u27e6EQ %d\u27e7</w:t></w:r></w:p>" % (len(eq_md) - 1)
        rows = []
        for tr in re.findall(r"<w:tr[ >].*?</w:tr>", m.group(0), re.S):
            cells = [" ".join(x for x in (re.sub(r"\*+", "", _docx_para_md(cp)).strip() for cp in re.findall(r"<w:p[ >].*?</w:p>", tc, re.S)) if x)
                     for tc in re.findall(r"<w:tc[ >].*?</w:tc>", tr, re.S)]
            if any(cells):
                rows.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
        tables_md.append("\n".join(rows)); tables_xml.append(m.group(0))
        return "<w:p><w:r><w:t>\u27e6TABLE %d\u27e7</w:t></w:r></w:p>" % (len(tables_md) - 1)

    body = re.sub(r"<w:tbl>.*?</w:tbl>", _tbl, body, flags=re.S)
    pms = list(re.finditer(r"<w:p[ >].*?</w:p>", body, re.S))
    items = []   # (text_md, image_rid or None, raw)
    for pm in pms:
        p = pm.group(0)
        md = _docx_para_md(p)
        rid = re.search(r'r:embed="([^"]+)"', p)
        items.append((md, rid.group(1) if rid else None, p))
    roles = []   # (문단 번호, 종류, 대상, 글) — 종류: head·body·table·abstract·title·figcap
    if trace is not None:
        trace.update({"docxml": docxml, "body_span": bm.span(1) if bm else (0, len(docxml)), "body": body, "tables": tables_xml, "eqtables": eq_xml, "eq_idx": set(),
                      "spans": [pm.span() for pm in pms], "raw": [it[2] for it in items], "roles": roles})
    all_text = " ".join(t for t, _, _ in items)
    is_en = len(re.findall(r"[A-Za-z]", all_text)) > len(re.findall(r"[가-힣]", all_text)) * 3
    if dry:
        doc = {"id": "_dry", "title": title or "", "created": time.time(), "updated": time.time(), "meta": {"journal": "", "kind": "research", "lang": "ko"},
               "cards": [], "outline": [], "figures": [], "glossary": [], "versions": []}
    else:
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
        me = re.match(r"^\u27e6EQ (\d+)\u27e7$", t)
        if me:   # 수식 표 → 그 절의 본문 문단
            if cur is not None and not in_refs:
                t = eq_md[int(me.group(1))]
                cur["draft"] = (cur["draft"] + "\n\n" + t).strip()
                roles.append((idx, "body", cur["id"], t))
                if trace is not None:
                    trace["eq_idx"].add(idx)
            continue
        # 판정용 평문: 워드에서 제목·캡션이 굵게/기울임으로 들어오면 **1. Introduction** 꼴이므로 서식 표시를 벗기고 본다
        plain = re.sub(r"\*+", "", t).strip()
        mt = re.match(r"^\u27e6TABLE (\d+)\u27e7$", plain)
        if mt:   # 표는 그 절의 글에 표 글로 넣는다
            if cur is not None and not in_refs and tables_md[int(mt.group(1))]:
                cur["draft"] = (cur["draft"] + "\n\n" + tables_md[int(mt.group(1))]).strip()
                roles.append((idx, "table", cur["id"], tables_md[int(mt.group(1))]))
            continue
        cap = _CAP_RE.match(plain)
        if cap and len(plain) > 7 and (cap.group(3) or not re.match(r"[a-z(,]", cap.group(4))):
            kind = "table" if cap.group(1).lower().startswith("t") or cap.group(1) == "표" else "fig"
            num = int(cap.group(2)); text = cap.group(4).strip()
            if kind == "fig":
                fig = {"id": _next_id(doc["figures"], "f"), "num": num, "caption": "" if is_en else text, "caption_en": text if is_en else "",
                       "source": {"type": "docx", "file": os.path.basename(path)}, "png": "", "svg": ""}
                roles.append((idx, "figcap", num, text))
                near = [r for (i2, r) in fig_pending if idx - 4 <= i2 <= idx + 1]
                if near:
                    target = rid2t.get(near[-1], "")
                    mpath = "word/" + target if not target.startswith("/") else target.lstrip("/")
                    if mpath in names and not dry:
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
            roles.append((idx, "head", cur["id"], plain))
            continue
        if re.match(r"^(acknowledg|declaration|funding|data availability|credit author|appendix|supplementary)", plain, re.I) and len(plain) < 60:
            cur = {"id": _next_id(doc["outline"], "n"), "level": 1, "heading": plain, "claim": "", "cards": [], "draft": "", "draft_en": "", "status": "drafted"}
            doc["outline"].append(cur); mode = "body"; in_refs = False
            roles.append((idx, "head", cur["id"], plain))
            continue
        if mode == "front":
            f = doc["front"]
            low = plain.lower()
            ma = re.match(r"^(abstract|초록|요약|국문\s*초록)\s*[:.\-]?\s*", plain, re.I)
            if ma and (len(plain) < 12 or plain[ma.end() - 1] in ":.- " or low.startswith("abstract")):
                abs_mode = True; rest = plain[ma.end():].strip(" :.-")
                if rest:
                    f["abstract"] = rest
                    roles.append((idx, "abstract", None, rest))
                continue
            if re.match(r"^(key\s?words|키워드|주요어|핵심어|주제어)(?![가-힣A-Za-z])", low):
                abs_mode = False
                kw = re.split(r"[;,]\s*", re.sub(r"^(key\s?words|키워드|주요어|핵심어|주제어)\s*[:.]?\s*", "", plain, flags=re.I))
                f["keywords"] = [k.strip() for k in kw if k.strip()]; continue
            if low.startswith("highlights") or plain.startswith("하이라이트"):
                abs_mode = False; mode = "highlights"; continue
            if abs_mode:
                f["abstract"] = (f["abstract"] + "\n\n" + plain).strip()
                roles.append((idx, "abstract", None, plain)); continue
            if not f["title"]:
                f["title"] = plain
                roles.append((idx, "title", None, plain)); continue
            f["authors"] = (f["authors"] + "\n" + plain).strip()
            continue
        if mode == "highlights":
            if len(plain) < 200 and not _HEAD_RE.match(plain):
                doc["front"]["highlights"].append(plain.lstrip("•-– ").strip()); continue
            mode = "body"
        if in_refs or cur is None:
            continue
        cur["draft"] = (cur["draft"] + "\n\n" + t).strip()
        roles.append((idx, "body", cur["id"], t))
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
    if not dry:
        save_ms(doc)
    return doc


def merge_docx(doc, path):
    """워드 파일을 다시 읽어 **지금 원고의 본문만** 갱신한다: 절 번호(1., 2.1)나 절 제목이 같은 절의 글을 바꾸고, 새 절은 끼워 넣는다.
    근거 카드·주장·리비전·그림은 그대로. 이전 개요는 판 이력에 남긴다. 워드와 여기를 오가며 쓸 때."""
    tmp = import_docx(path)
    try:
        os.remove(_path(tmp["id"]))   # 읽기용으로 만든 임시 원고는 지운다
    except OSError:
        pass
    if (tmp.get("meta", {}).get("lang") or "ko") != (doc.get("meta", {}).get("lang") or "ko"):
        return {"error": "워드 파일의 언어(%s)가 이 원고(%s)와 달라 덮어쓰지 않았습니다. 「새 원고로」 가져오세요." %
                ("영어" if tmp["meta"].get("lang") == "en" else "한국어", "영어" if doc.get("meta", {}).get("lang") == "en" else "한국어")}
    num = lambda h: (re.match(r"^\s*(\d+(?:\.\d+)*)", h or "") or [None, None])[1]
    key = lambda h: _tokens(re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", h or ""))
    doc.setdefault("versions", [])
    doc["versions"] = (doc["versions"] + [{"t": doc.get("updated", time.time()), "outline": json.loads(json.dumps(doc["outline"]))}])[-20:]
    stat = {"updated": 0, "same": 0, "added": 0}
    used, last_idx = set(), -1
    for tn in tmp["outline"]:
        match = None
        for i, n in enumerate(doc["outline"]):
            if i in used:
                continue
            if (num(tn["heading"]) and num(tn["heading"]) == num(n.get("heading"))) or (key(tn["heading"]) and key(tn["heading"]) == key(n.get("heading"))):
                match = i; break
        if match is None:
            new = dict(tn, id=_next_id(doc["outline"], "n"))
            last_idx += 1
            doc["outline"].insert(last_idx, new)
            used = {(i + 1 if i >= last_idx else i) for i in used} | {last_idx}
            stat["added"] += 1
            continue
        n = doc["outline"][match]; used.add(match); last_idx = match
        n["heading"] = tn["heading"]; n["level"] = tn.get("level", n.get("level", 1))
        if _norm_ws(tn.get("draft")) != _norm_ws(n.get("draft")):
            n["draft"] = tn.get("draft", ""); stat["updated"] += 1
            if n.get("status") == "todo":
                n["status"] = "drafted"
        else:
            stat["same"] += 1
    doc["outline"] = [n for i, n in enumerate(doc["outline"]) if i in used or n.get("heading") not in ("서론", "실험 방법", "결과 및 고찰", "결론")
                      or (n.get("draft") or n.get("draft_en") or n.get("claim") or n.get("cards"))]
    f, tf = doc.setdefault("front", {}), tmp.get("front") or {}
    for k in ("title", "authors", "abstract", "keywords", "highlights"):
        if tf.get(k):
            f[k] = tf[k]
    if tmp.get("refs_text"):
        doc["refs_text"] = tmp["refs_text"]
    old = {(c.get("author"), _norm_ws(c.get("text"))): c for c in doc.get("comments", [])}
    for c in tmp.get("comments", []):   # 새 주석으로 바꾸되, 같은 주석의 논의·답변 메모는 이어 간다
        o = old.get((c.get("author"), _norm_ws(c.get("text"))))
        if o:
            for k in ("thread", "reply", "status"):
                if o.get(k):
                    c[k] = o[k]
    doc["comments"] = tmp.get("comments", [])
    have = {fg.get("num") for fg in doc.get("figures", [])}
    doc.setdefault("figures", []).extend(fg for fg in tmp.get("figures", []) if fg.get("num") not in have)
    doc["figures"].sort(key=lambda fg: fg.get("num") or 0)
    doc["source_docx"] = path
    save_ms(doc)
    return dict(stat, id=doc["id"], nodes=len(doc["outline"]), comments=len(doc["comments"]))


# ---------- 워드 반영: 여기서 고친 글을 가져온 워드 파일에 (서식·그림·수식은 그대로, 바뀐 문단만) ----------
_MATH_ANY = re.compile(r"\$\$.+?\$\$|\$(?!\s)[^$\n]+?(?<![\s\\])\$|\u27e6[^\u27e7]*\u27e7", re.S)
_LEGACY = re.compile(r"\u27e6[^\u27e7]*\u27e7")
_SYNC_BLUE = "1F4FD1"
_SYNC_LOCK = threading.Lock()


_TEX_NORM = {}


def _eqv(text):
    """견줄 때 쓰는 꼴: 수식은 워드 수식으로 바꿨다 되돌린 표준 표기로(같은 수식을 다르게 적어도 같게), 수식 앞뒤 빈칸은 무시."""
    def sub(m):
        tok = m.group(0)
        if tok.startswith("\u27e6"):
            return "\x01" + tok + "\x01"
        tex = tok.strip("$").strip()
        if tex not in _TEX_NORM:
            _TEX_NORM[tex] = mathtex.omml_to_latex(mathtex.to_omml(tex)) or tex
        return "\x01" + _TEX_NORM[tex] + "\x01"
    return re.sub(r"\s*\x01\s*", "\x01", _norm_ws(_MATH_ANY.sub(sub, text or "")))


def _legacy_pairs(raw):
    """문단 원문의 수식들 → [(예전 표기 ⟦글자⟧, 지금 표기 $LaTeX$)] 순서대로."""
    out = []
    for e in re.findall(r"<m:oMathPara[ >].*?</m:oMathPara>|<m:oMath[ >].*?</m:oMath>", raw or "", re.S):
        tok = wordsync.math_token(e)
        for one in re.findall(r"<m:oMath[ >].*?</m:oMath>", e, re.S):
            flat = mathtex.omml_flat(one)
            if flat:
                out.append(("\u27e6" + flat + "\u27e7", tok))
    return out


def _upgrade_text(text, pairs, fallback):
    """글 속의 ⟦글자⟧ 를 그 문단의 워드 수식 순서(pairs)에 맞춰 $LaTeX$ 로. 못 맞추면 전체 표(fallback)에서."""
    if "\u27e6" not in (text or ""):
        return text
    k = [0]

    def sub(m):
        tok = m.group(0)
        for q in range(k[0], len(pairs)):
            if pairs[q][0] == tok and pairs[q][1].startswith("$"):
                k[0] = q + 1
                return pairs[q][1]
        return fallback.get(tok, tok)
    return _LEGACY.sub(sub, text)


def _align(O, N):
    """워드 문단 글 O 와 지금 문단 글 N 을 맞춘다 → (짝 {o: n}, 새 문단 [(앞의 o 또는 -1, n)], 없어진 o 목록).
    수식 표기(⟦…⟧ ↔ $…$)만 다른 문단은 같은 것으로 본다."""
    canon = lambda t: re.sub(r"\s*\u27e6\u27e7\s*", "\u27e6\u27e7", _norm_ws(_MATH_ANY.sub("\u27e6\u27e7", t or "")))
    co, cn = [canon(x) for x in O], [canon(x) for x in N]
    pairs, ins, gone = {}, [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, co, cn, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                pairs[i1 + k] = j1 + k
        elif tag == "delete":
            gone += range(i1, i2)
        elif tag == "insert":
            ins += [(i1 - 1, j) for j in range(j1, j2)]
        else:   # 바뀐 구간: 닮은 것끼리 순서를 지키며 짝짓고, 그 사이에 남은 것은 자리 순서대로
            anchors, j = [], j1
            for i in range(i1, i2):
                best, bj = 0.0, None
                for q in range(j, j2):
                    sm = difflib.SequenceMatcher(None, co[i], cn[q], autojunk=False)
                    r = sm.ratio() if sm.real_quick_ratio() >= 0.5 and sm.quick_ratio() >= 0.5 else 0.0
                    if r > best:
                        best, bj = r, q
                if bj is not None and best >= 0.5:
                    anchors.append((i, bj)); j = bj + 1
            oi, nj = i1, j1
            for ai, aj in anchors + [(i2, j2)]:
                os_, ns_ = list(range(oi, ai)), list(range(nj, aj))
                for k in range(min(len(os_), len(ns_))):   # 통째로 고쳐 쓴 문단: 제자리에서 바꾼다
                    pairs[os_[k]] = ns_[k]
                gone += os_[len(ns_):]
                last = os_[min(len(os_), len(ns_)) - 1] if os_ and ns_ else (oi - 1)
                ins += [(last, q) for q in ns_[len(os_):]]
                if ai < i2:
                    pairs[ai] = aj
                oi, nj = ai + 1, aj + 1
    return pairs, ins, gone


def _match_nodes(tmp, doc):
    """워드의 절 ↔ 지금 원고의 절: 절 번호나 제목이 같으면, 아니면 글이 많이 닮았으면. → {tmp 절 id: 원고 절}"""
    num = lambda h: (re.match(r"^\s*(\d+(?:\.\d+)*)", h or "") or [None, None])[1]
    key = lambda h: _tokens(re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", h or ""))
    used, out = set(), {}
    for tn in tmp["outline"]:   # 1) 제목이 그대로인 절
        for n in doc.get("outline", []):
            if n["id"] not in used and _norm_ws(n.get("heading")) == _norm_ws(tn["heading"]):
                out[tn["id"]] = n; used.add(n["id"]); break
    for tn in tmp["outline"]:   # 2) 번호 또는 제목 낱말이 같고, 3) 글이 닮은 절
        if tn["id"] in out:
            continue
        cands = [n for n in doc.get("outline", []) if n["id"] not in used]
        hit = next((n for n in cands if (key(tn["heading"]) and key(tn["heading"]) == key(n.get("heading")))), None) \
            or next((n for n in cands if num(tn["heading"]) and num(tn["heading"]) == num(n.get("heading"))), None)
        if hit is None and len(tn.get("draft") or "") > 200:
            a = _norm_ws(tn["draft"])[:3000]
            best = max(cands, key=lambda n: difflib.SequenceMatcher(None, a, _norm_ws(n.get("draft"))[:3000], autojunk=False).quick_ratio(), default=None)
            if best is not None and difflib.SequenceMatcher(None, a, _norm_ws(best.get("draft"))[:3000], autojunk=False).ratio() >= 0.6:
                hit = best
        if hit is not None:
            out[tn["id"]] = hit; used.add(hit["id"])
    return out


def _sync_plan(doc):
    """가져온 워드 파일을 다시 읽어, 지금 원고와 달라진 곳을 문단 단위로 찾는다.
    → (trace, tmp, edits) — edits = [{idx, kind: edit|delete|insert, text, like, where, pairs, seq}] (idx = 워드 문단 번호)"""
    src = doc.get("source_docx") or ""
    tr = {}
    tmp = import_docx(src, dry=True, trace=tr)
    roles, raws = tr["roles"], tr["raw"]
    fb = {}
    for raw in raws:
        for old, new in _legacy_pairs(raw):
            if new.startswith("$"):
                fb.setdefault(old, new)
    edits, notes = [], {"tables": 0, "skipped": 0, "word_only": [], "new_nodes": 0, "swallowed": []}
    old_cap = re.compile(r"^(Fig\.?|Figure|그림)\s*(\d+)\.?\s*(.*)$", re.I)   # 예전 캡션 판정 (본문 문장도 삼켰다)
    fig_caps = {(fg.get("num"), _norm_ws(_MATH_ANY.sub("\u27e6\u27e7", (fg.get("caption") or fg.get("caption_en") or "")))): fg for fg in doc.get("figures", [])}

    def swallowed(text):
        """이 워드 문단이 예전 가져오기 때 그림 캡션으로 잘못 들어가 원고 본문에 없는 것인가 → 그 가짜 그림"""
        m = old_cap.match(re.sub(r"\*+", "", text).strip())
        return fig_caps.get((int(m.group(2)), _norm_ws(_MATH_ANY.sub("\u27e6\u27e7", m.group(3).strip())))) if m else None
    unstar = lambda raw: re.sub(r"\*+", "", wordsync.para_md(raw)).strip()
    body_like = next((raws[i] for i, k, _, t in roles if k == "body" and len(t) > 80 and wordsync.patchable(raws[i])), "")
    tr["body_like"] = body_like
    head_like = {}
    lvl = {n["id"]: n.get("level", 1) for n in tmp["outline"]}
    for i, k, ref, _ in roles:
        if k == "head":
            head_like.setdefault(lvl.get(ref, 1), raws[i])

    def part_change(idx, old_text, new_text, where):
        """문단의 일부(초록 머리 뒤의 글, 캡션의 번호 뒤 글, 제목)가 바뀜 → 그 부분만 갈아 끼운 문단 글."""
        base = unstar(raws[idx])
        k = base.find(old_text)
        if k < 0 or not wordsync.patchable(raws[idx]):
            notes["skipped"] += 1
            return
        edits.append({"idx": idx, "kind": "edit", "text": base[:k] + new_text + base[k + len(old_text):], "where": where, "inherit": True})

    def align_block(rl, new_text, where):
        """rl = 이 구역의 역할들 [(idx, 종류, 글)], new_text = 지금 글. 문단을 맞춰 바뀐 것을 edits 에."""
        flat = []   # (역할 번호, 글, 옛 표기 글)
        for r, (idx, kind, text) in enumerate(rl):
            leg = wordsync.para_md(raws[idx], legacy=True) if kind == "body" and idx not in tr["eq_idx"] else text
            lp = leg.split("\n\n")
            for k, part in enumerate(text.split("\n\n")):
                flat.append((r, part, lp[k] if k < len(lp) else part))
        N = [x.strip() for x in re.split(r"\n\s*\n", new_text or "") if x.strip()]
        pairs, ins, gone = _align([f[1] for f in flat], N)
        last_part = {}
        for k, f in enumerate(flat):
            last_part[f[0]] = k
        for r, (idx, kind, text) in enumerate(rl):
            mine = [k for k, f in enumerate(flat) if f[0] == r]
            seq, same = [], True
            for k in mine:
                if k in pairs:
                    seq.append(N[pairs[k]])
                    if _norm_ws(N[pairs[k]]) not in (_norm_ws(flat[k][1]), _norm_ws(flat[k][2])) and _eqv(N[pairs[k]]) != _eqv(flat[k][1]):
                        same = False
                else:
                    same = False
                inner = [N[j] for a, j in ins if a == k and k != last_part[r]]
                if inner:
                    seq += inner; same = False
            if same:
                continue
            if idx in tr["eq_idx"] and seq and seq[0].lstrip().startswith("|"):
                continue   # 예전 가져오기는 이 수식 표를 '| 수식 | 번호 |' 글로 넣었다 — 고친 게 아니다
            if kind == "table":
                notes["tables"] += 1
                continue
            if not wordsync.patchable(raws[idx]):
                notes["skipped"] += 1
                continue
            if not seq:
                fake = swallowed(text) if kind == "body" else None
                if fake is not None:   # 사용자가 지운 게 아니라 가져오기가 놓친 문단 — 워드에 그대로 두고 알린다
                    prev = next((N[pairs[k]] for k in range(mine[0] - 1, -1, -1) if k in pairs), "")
                    notes["swallowed"].append({"node": where, "text": text, "after": prev, "fig": fake.get("id"), "num": fake.get("num")})
                    continue
                edits.append({"idx": idx, "kind": "delete", "where": where})
            else:
                edits.append({"idx": idx, "kind": "edit", "text": _upgrade_text("\n\n".join(seq), _legacy_pairs(raws[idx]), fb), "where": where})
        for a, j in ins:   # 문단 뒤(또는 구역 맨 앞)에 들어온 새 문단
            if a >= 0 and a != last_part[flat[a][0]]:
                continue   # 문단 안쪽에 끼운 것은 위에서 처리
            yield_after = rl[flat[a][0]][0] if a >= 0 else None
            edits.append({"idx": yield_after, "kind": "insert", "text": _upgrade_text(N[j], [], fb), "like": body_like, "where": where, "seq": j})
        return [e for e in edits if e.get("where") == where and e["kind"] == "insert" and e["idx"] is None]

    match = _match_nodes(tmp, doc)
    last_idx = {}   # tmp 절 id → 그 절의 마지막 문단 번호 (새 문단·새 절을 넣을 자리)
    head_idx = {}
    for i, k, ref, _ in roles:
        if k in ("head", "body", "table"):
            last_idx[ref] = i
        if k == "head":
            head_idx[ref] = i
    prev_anchor = None
    order = {n["id"]: k for k, n in enumerate(doc.get("outline", []))}
    matched_ids = {n["id"] for n in match.values()}
    for tn in tmp["outline"]:
        n = match.get(tn["id"])
        if n is None:
            notes["word_only"].append(tn["heading"])
            prev_anchor = last_idx.get(tn["id"], prev_anchor)
            continue
        if _norm_ws(n.get("heading")) != _norm_ws(tn["heading"]) and tn["id"] in head_idx and wordsync.patchable(raws[head_idx[tn["id"]]]):
            edits.append({"idx": head_idx[tn["id"]], "kind": "edit", "text": n.get("heading", ""), "where": n["id"], "inherit": True})
        rl = [(i, k, t) for i, k, ref, t in roles if k in ("body", "table") and ref == tn["id"]]
        key = "draft" if (n.get("draft") or "").strip() or not (n.get("draft_en") or "").strip() else "draft_en"
        firsts = align_block(rl, n.get(key) or "", n["id"])
        for e in firsts:   # 절 맨 앞에 넣을 문단 → 절 제목 문단 뒤
            e["idx"] = head_idx.get(tn["id"], prev_anchor)
        prev_anchor = last_idx.get(tn["id"], prev_anchor)
        # 이 절 바로 뒤에 새로 생긴 절들 (워드에 없던 것) — 글이 있는 것만
        k = order[n["id"]] + 1
        while k < len(doc["outline"]) and doc["outline"][k]["id"] not in matched_ids:
            nn = doc["outline"][k]
            txt = (nn.get("draft") or nn.get("draft_en") or "").strip()
            if txt and prev_anchor is not None:
                notes["new_nodes"] += 1
                edits.append({"idx": prev_anchor, "kind": "insert", "text": nn.get("heading", ""), "like": head_like.get(nn.get("level", 1)) or head_like.get(1, ""), "where": nn["id"], "seq": 1000 + k * 100, "inherit": True})
                for q, para in enumerate(x.strip() for x in re.split(r"\n\s*\n", txt) if x.strip()):
                    edits.append({"idx": prev_anchor, "kind": "insert", "text": _upgrade_text(para, [], fb), "like": body_like, "where": nn["id"], "seq": 1000 + k * 100 + 1 + q})
            k += 1
    # 머리부: 제목·초록
    f = doc.get("front") or {}
    for i, k, ref, t in roles:
        if k == "title" and (f.get("title") or "").strip() and _norm_ws(f["title"]) != _norm_ws(t):
            part_change(i, t, f["title"].strip(), "front")
    arl = [(i, "abstract", t) for i, k, ref, t in roles if k == "abstract"]
    if arl and (f.get("abstract") or "").strip():
        N = [x.strip() for x in re.split(r"\n\s*\n", f["abstract"]) if x.strip()]
        pairs, ins, gone = _align([t for _, _, t in arl], N)
        for r, (i, _, t) in enumerate(arl):
            if r in pairs and _norm_ws(N[pairs[r]]) != _norm_ws(t):
                part_change(i, t, N[pairs[r]], "front")
            elif r not in pairs and r > 0:
                edits.append({"idx": i, "kind": "delete", "where": "front"})
        for a, j in ins:
            if a >= 0:
                edits.append({"idx": arl[a][0], "kind": "insert", "text": N[j], "like": raws[arl[a][0]], "where": "front", "seq": j})
    # 그림 캡션
    is_en = tmp["meta"].get("lang") == "en"
    caps = {}   # 그림 번호 → 그 번호의 캡션들 (같은 번호가 둘이면 나온 순서대로 짝짓는다)
    for fg in doc.get("figures", []):
        caps.setdefault(fg.get("num"), []).append((fg.get("caption_en") if is_en else fg.get("caption")) or "")
    seen = {}
    for i, k, num, t in roles:
        if k != "figcap":
            continue
        q = seen.get(num, 0); seen[num] = q + 1
        mine = caps.get(num) or []
        want = next((c for c in mine if _norm_ws(_upgrade_text(c, _legacy_pairs(raws[i]), fb)) == _norm_ws(t)), None)   # 그대로인 캡션이 있으면 안 바뀐 것
        if want is None and q < len(mine) and len(mine) == sum(1 for r in roles if r[1] == "figcap" and r[2] == num) and mine[q].strip():
            part_change(i, t, _upgrade_text(mine[q].strip(), _legacy_pairs(raws[i]), fb), "fig%s" % num)
    return tr, tmp, edits, notes


def sync_default_path(doc):
    return os.path.join(cfg["MS_DIR"], "워드반영", os.path.basename(doc.get("source_docx") or "원고.docx"))


def sync_docx(doc, mark=False, check_only=False):
    """지금 원고의 글을 가져온 워드 파일 사본에 반영한다. 원본(가져오기 폴더의 파일)은 건드리지 않고 매번 거기서부터 다시 만든다 →
    여러 번 돌려도 어긋남이 쌓이지 않는다. 반영하는 것: 본문 문단·절 제목·초록·제목·그림 캡션. 표·참고문헌·키워드는 워드 것 그대로."""
    import xml.etree.ElementTree as ET
    src = doc.get("source_docx") or ""
    if not src or not os.path.isfile(src):
        return {"error": "가져온 워드 파일이 없습니다 (「워드 가져오기」로 들여온 원고만 워드에 반영할 수 있습니다)" + ((" — " + src) if src else "")}
    ws = doc.get("word_sync") or {}
    out = (ws.get("path") or "").strip().strip('"') or sync_default_path(doc)
    if not out.lower().endswith(".docx"):
        return {"error": "반영할 파일 이름이 .docx 로 끝나야 합니다"}
    if os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(src)):
        return {"error": "가져온 원본과 같은 파일에는 쓸 수 없습니다 (원본은 기준으로 남겨 둡니다). 다른 이름이나 폴더를 고르세요"}
    if not os.path.isdir(os.path.dirname(out) or "."):
        if ws.get("path"):
            return {"error": "그 폴더가 없습니다: " + os.path.dirname(out)}
        if not check_only:   # 살펴보기만 할 때는 폴더도 만들지 않는다
            os.makedirs(os.path.dirname(out), exist_ok=True)
    t0 = time.time()
    tr, tmp, edits, notes = _sync_plan(doc)
    color = _SYNC_BLUE if mark else None
    body, spans, raws = tr["body"], tr["spans"], tr["raw"]
    ops, stat, fields = [], {"edit": 0, "insert": 0, "delete": 0}, 0
    for e in edits:
        if e["idx"] is None:
            continue
        a, b = spans[e["idx"]]
        if e["kind"] == "edit" and e["idx"] in tr["eq_idx"]:   # 수식 표: 통째로 다시 만든다
            ops.append((a, 1, 0, b, wordsync.new_paragraph(e["text"], tr.get("body_like") or "", color)))
        elif e["kind"] == "edit":
            new, info = wordsync.patch_paragraph(raws[e["idx"]], e["text"], color, e.get("inherit", False))
            if new is None:
                continue
            fields += info["fields"]
            ops.append((a, 1, 0, b, new))
        elif e["kind"] == "delete":
            ops.append((a, 1, 0, b, "" if e["idx"] in tr["eq_idx"] else wordsync.emptied_paragraph(raws[e["idx"]])))
        else:
            ops.append((b, 0, e.get("seq", 0), b, wordsync.new_paragraph(e["text"], e.get("like") or "", color, e.get("inherit", False))))
        stat[e["kind"]] += 1
    res = {"changed": stat["edit"], "inserted": stat["insert"], "deleted": stat["delete"], "fields": fields, "tables": notes["tables"],
           "skipped": notes["skipped"], "word_only": notes["word_only"][:8], "new_nodes": notes["new_nodes"], "swallowed": notes["swallowed"], "path": out,
           "cards": len(re.findall(r"\[c\d+\]", " ".join(e.get("text") or "" for e in edits)))}
    if check_only:
        return res
    ops.sort(key=lambda o: (o[0], o[1], o[2]))
    parts, cur = [], 0
    for a, _, _, b, new in ops:
        if a < cur:
            continue   # 같은 문단을 두 번 고치려는 경우 — 첫 것만
        parts.append(body[cur:a]); parts.append(new); cur = b
    parts.append(body[cur:])
    body = "".join(parts)
    for k, tx in enumerate(tr["tables"]):   # 가져올 때 자리 표시로 바꿔 둔 표를 제자리에
        body = body.replace("<w:p><w:r><w:t>\u27e6TABLE %d\u27e7</w:t></w:r></w:p>" % k, tx, 1)
    for k, tx in enumerate(tr["eqtables"]):
        body = body.replace("<w:p><w:r><w:t>\u27e6EQ %d\u27e7</w:t></w:r></w:p>" % k, tx, 1)
    docxml = tr["docxml"]
    bs, be = tr["body_span"]
    new_xml = docxml[:bs] + body + docxml[be:]
    if "<m:oMath" in new_xml and "xmlns:m=" not in new_xml[:new_xml.find(">", new_xml.find("<w:document"))]:
        new_xml = new_xml.replace("<w:document ", '<w:document xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ', 1)
    try:
        ET.fromstring(new_xml.encode("utf-8"))
    except ET.ParseError as ex:   # 깨진 문서는 절대 쓰지 않는다
        return {"error": "워드 문서를 만들다 구조가 어긋나 멈췄습니다 (파일은 그대로) — " + str(ex)[:120]}
    log_p = os.path.join(cfg["MS_DIR"], "_워드반영기록.json")
    log = cfg["load_json"](log_p, {}) or {}
    k = os.path.normcase(os.path.abspath(out))
    if os.path.isfile(out) and k not in log:   # 내가 만든 적 없는 파일을 처음 덮어쓸 때는 옆에 백업
        bak = "%s.백업_%s.docx" % (out[:-5], time.strftime("%Y%m%d_%H%M%S"))
        import shutil
        shutil.copy2(out, bak)
        res["backup"] = bak
    try:
        wordsync.write_docx(src, out, new_xml)
    except PermissionError:
        return dict(res, locked=True, error="워드가 그 파일을 열고 있어 쓰지 못했습니다 — 워드에서 파일을 닫으면 다음 저장 때 반영됩니다")
    log[k] = {"t": time.time(), "backup": res.get("backup") or (log.get(k) or {}).get("backup", "")}
    cfg["save_json"](log_p, log)
    res.update(ok=True, t=time.time(), sec=round(time.time() - t0, 2))
    return res


def upgrade_equations(doc):
    """예전에 가져와 글자만 남은 수식 ⟦…⟧ 를, 가져온 워드 파일을 다시 읽어 고칠 수 있는 수식 $LaTeX$ 로 바꾼 글을 돌려준다 (저장은 화면이 한다)."""
    src = doc.get("source_docx") or ""
    if not src or not os.path.isfile(src):
        return {"error": "가져온 워드 파일이 없어 수식을 다시 읽을 수 없습니다"}
    tr = {}
    tmp = import_docx(src, dry=True, trace=tr)
    roles, raws = tr["roles"], tr["raw"]
    fb = {}
    for raw in raws:
        for old, new in _legacy_pairs(raw):
            if new.startswith("$"):
                fb.setdefault(old, new)
    match = _match_nodes(tmp, doc)
    out = {"nodes": {}, "captions": {}, "abstract": None, "count": 0}
    cnt = lambda t: len(_LEGACY.findall(t or "")) - len(re.findall(r"\u27e6TABLE \d+\u27e7", t or ""))
    for tn in tmp["outline"]:
        n = match.get(tn["id"])
        if n is None:
            continue
        rl = [(i, t) for i, k, ref, t in roles if k == "body" and ref == tn["id"]]
        for key in ("draft", "draft_en"):
            text = n.get(key) or ""
            if "\u27e6" not in text:
                continue
            N = [x for x in re.split(r"(\n\s*\n)", text)]   # 구분자 유지
            paras = [x for x in N[0::2]]
            pairs, _, _ = _align([wordsync.para_md(raws[i], legacy=True) for i, _ in rl], [p.strip() for p in paras])
            back = {j: o for o, j in pairs.items()}
            new = [_upgrade_text(p, _legacy_pairs(raws[rl[back[j]][0]]) if j in back else [], fb) for j, p in enumerate(paras)]
            N[0::2] = new
            text2 = "".join(N)
            if text2 != text:
                out["nodes"].setdefault(n["id"], {})[key] = text2
                out["count"] += cnt(text) - cnt(text2)
    for n in doc.get("outline", []):   # 워드와 짝이 안 맞은 절은 전체 표로
        for key in ("draft", "draft_en"):
            text = n.get(key) or ""
            if "\u27e6" in text and key not in out["nodes"].get(n["id"], {}):
                text2 = _upgrade_text(text, [], fb)
                if text2 != text:
                    out["nodes"].setdefault(n["id"], {})[key] = text2
                    out["count"] += cnt(text) - cnt(text2)
    ab = (doc.get("front") or {}).get("abstract") or ""
    if "\u27e6" in ab and _upgrade_text(ab, [], fb) != ab:
        out["abstract"] = _upgrade_text(ab, [], fb); out["count"] += cnt(ab) - cnt(out["abstract"])
    capraw = {num: raws[i] for i, k, num, t in roles if k == "figcap"}
    for fg in doc.get("figures", []):
        for key in ("caption", "caption_en"):
            t = fg.get(key) or ""
            if "\u27e6" in t:
                t2 = _upgrade_text(t, _legacy_pairs(capraw.get(fg.get("num"), "")), fb)
                if t2 != t:
                    out["captions"].setdefault(fg["id"], {})[key] = t2; out["count"] += cnt(t) - cnt(t2)
    total = (sum(cnt(n.get("draft")) + cnt(n.get("draft_en")) for n in doc.get("outline", [])) + cnt(ab)
             + sum(cnt(fg.get("caption")) + cnt(fg.get("caption_en")) for fg in doc.get("figures", [])))
    out["left"] = total - out["count"]
    return out


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
    if "$" in (node.get("draft") or ""):
        prompt += "\n\n(주의: 글 속의 $…$ · $$…$$ 는 LaTeX 수식이다. 고치라는 말이 없으면 한 글자도 바꾸지 말고 그 자리에 둔다.)"
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


def _runs(text, color=None):
    """**굵게**, *기울임*, ^위첨자^, _아래첨자_, $수식$(LaTeX → 워드 수식) 을 런으로. color = 'RRGGBB' 면 글자색."""
    return wordsync.runs_xml(wordsync.md_units(text), "", color)


def _body_para(text, color=None):
    """본문 문단. '$$ 수식 $$ (3)' 처럼 문단 수식뿐인 글은 수식 배치로 (번호가 있으면 테두리 없는 표)."""
    m = wordsync.DISP_EQ.match(text.strip())
    if m:
        return wordsync.display_block(m.group(1).strip(), m.group(2) or "", "", color)
    return _para(text, color=color)


def _para(text, style=None, align=None, color=None):
    ppr = ""
    if style or align:
        ppr = "<w:pPr>%s%s</w:pPr>" % ('<w:pStyle w:val="%s"/>' % style if style else "", '<w:jc w:val="%s"/>' % align if align else "")
    return "<w:p>%s%s</w:p>" % (ppr, _runs(text, color))


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


def export_docx(doc, lang="ko", mark=None):
    """mark = 리비전 라운드 → 그 라운드의 base(심사 의견을 받은 시점의 본문)와 달라진 문단을 파란색으로 표시한 원고."""
    paras, refs = citations(doc, lang)
    _ck = lambda p: _norm_ws(re.sub(r"\[(?:c?\d+(?:\s*[,\u2013\-]\s*c?\d+)*)\]", "", p))   # 인용 번호는 빼고 비교
    base_key = "draft_en" if lang == "en" else "draft"

    def _is_new(n, p):
        if not mark:
            return False
        b = (mark.get("base") or {}).get(n["id"])
        if b is None:
            return True   # 그 뒤에 생긴 절
        old = {_ck(x) for x in re.split(r"\n\s*\n", b.get(base_key) or b.get("draft") or "") if x.strip()}
        return _ck(p) not in old
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
        old_abs = {_ck(x) for x in re.split(r"\n\s*\n", (((mark or {}).get("base") or {}).get("_front") or {}).get("abstract") or "") if x.strip()}
        for p in re.split(r"\n\s*\n", f["abstract"]):
            if p.strip(): body.append(_para(p.strip(), color="1F4FD1" if mark and _ck(p.strip()) not in old_abs else None))
    if f.get("keywords"):
        body.append(_para("Keywords: " + "; ".join(f["keywords"])))
    for n, txt in paras:
        body.append(_para(n.get("heading", ""), "Heading1" if n.get("level", 1) == 1 else "Heading2"))
        for p in [x.strip() for x in re.split(r"\n\s*\n", txt) if x.strip()]:
            body.append(_body_para(p, color="1F4FD1" if _is_new(n, p) else None))
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
                'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
                'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
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
    out = os.path.join(cfg["EXPORT_DIR"], "%s_%s%s_%s.docx" % (safe, "EN" if lang == "en" else "KO", "_수정표시" if mark else "", time.strftime("%Y%m%d_%H%M")))
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
              "- 논의에서 정해진 방향이 있으면 그것을 따른다. 카드 번호 [cN] 과 수식($…$ 안의 LaTeX)은 그대로 둔다.\n- 고친 문단만 출력. 설명·제목·따옴표 금지.\n\n"
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


# ---------- 본보기: 잘 쓴 논문들이 그 부분을 어떻게 썼나 (초록·제목·서론·방법·결과·논의·결론) ----------
MOVES = ["배경", "공백", "목적", "방법", "결과", "의의"]
# 부분마다: 이름, 나누는 단위(sent = 문장, para = 문단, title = 제목 목록), 역할
SECTION_KINDS = {
    "abstract": {"name": "초록", "level": "sent", "roles": MOVES},
    "title": {"name": "제목", "level": "title", "roles": []},
    "intro": {"name": "서론", "level": "para", "roles": ["배경", "선행연구", "공백", "목적·접근", "기여·구성"]},
    "methods": {"name": "방법", "level": "para", "roles": ["재료·시편", "장치·셋업", "조건·절차", "측정·분석", "모델·해석"]},
    "results": {"name": "결과·논의", "level": "para", "roles": ["관찰", "비교", "해석·기구", "문헌 비교", "요약·전환"]},
    "conclusion": {"name": "결론", "level": "sent", "roles": ["요약", "결과", "의의", "한계", "향후"]},
}
_ROLE_HELP = {
    "abstract": "배경(분야·중요성·일반 사실), 공백(기존 연구의 한계·남은 문제·필요성), 목적(이 연구가 하려는 것·제안), 방법(실험·모델·조건·재료·측정), 결과(발견·관찰·수치), 의의(기여·응용·결론적 함의)",
    "conclusion": "요약(무엇을 했는지 다시), 결과(핵심 발견·수치), 의의(기여·응용), 한계(제한·가정), 향후(앞으로 할 일)",
    "intro": "배경(분야·중요성), 선행연구(기존 연구 소개·정리), 공백(한계·남은 문제), 목적·접근(이 연구가 하려는 것과 방법의 개요), 기여·구성(기여 요약·논문 구성 안내)",
    "methods": "재료·시편(재료·시편 준비), 장치·셋업(장비·실험 장치 구성), 조건·절차(가공 조건·실험 순서), 측정·분석(측정·관찰·데이터 처리), 모델·해석(수치·해석 모델)",
    "results": "관찰(결과·관찰 제시), 비교(조건 사이 비교), 해석·기구(원인·메커니즘 설명), 문헌 비교(선행 연구와 대조), 요약·전환(정리·다음으로 넘어감)",
}
_STOP = set(("the and for with from that this these those into onto over under between during using based study studies paper results "
             "result method methods effect effects analysis model models new novel approach high low different various also than which were "
             "been have has had their there here such both more most less very when while where within without through toward towards").split())
_ABBR = r"(?:e\.g|i\.e|et al|Figs?|Eqs?|approx|vs|ca|resp|Refs?|No)\."
_SEC_RX = {"conclusion": r"conclu|concluding|closing remarks|^summary", "intro": r"introduc",
           "results": r"result|discussion", "methods": r"experiment|method|material|set-?up|procedure|approach|modell?ing|simulation|numerical|preparation"}
_END_RX = r"acknowledg|credit authorship|declaration|data availability|references|appendix|funding|supplementary|nomenclature"
_MINE_RX = {"intro": r"서론|introduc", "methods": r"방법|실험|재료|장치|method|experiment|material|set-?up",
            "results": r"결과|고찰|논의|result|discussion", "conclusion": r"결론|conclu"}


def _sentences(text):
    """영어·한국어를 문장으로. 약어(e.g., et al., Fig.)와 소수점의 마침표는 문장 끝으로 보지 않는다."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return []
    t = re.sub(_ABBR, lambda m: m.group(0).replace(".", "\u00a7"), t)
    t = re.sub(r"(\d)\.(\d)", "\\1\u00a7\\2", t)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[\"\u201c\uac00-\ud7a3])", t)
    return [p.replace("\u00a7", ".").strip() for p in parts if p.strip()]


def _topic_words(doc, limit=10):
    import collections
    f = doc.get("front") or {}
    text = " ".join([doc.get("title", ""), f.get("title", ""), " ".join(f.get("keywords") or []), f.get("abstract", "")] +
                    [n.get("heading", "") for n in (doc.get("outline") or [])[:12]])
    words = re.findall(r"[A-Za-z][A-Za-z\-]{3,}", text.lower())
    c = collections.Counter(w for w in words if w not in _STOP)
    return [w for w, _ in c.most_common(limit)]


def _journal_full(abbr):
    return re.sub(r"\s*\([^)]*\)\s*$", "", JOURNAL_NAMES.get(abbr, abbr)).strip()


def _library_candidates(journals, topic):
    """내 서재에서 그 저널 논문을 주제 가까운 순·최근 순으로."""
    tags = cfg["load_json"](cfg["TAGS_PATH"], {}) if cfg.get("TAGS_PATH") else {}
    try:
        names = os.listdir(cfg["ARCHIVE"])
    except OSError:
        names = []
    tset, cands = set(topic), []
    for f in names:
        m = re.match(r"^(\d{4})_([^_]+)_([^_]+)_(.+)\.pdf$", f, re.I)
        if not m or m.group(2) not in journals:
            continue
        title = (tags.get(f) or {}).get("title") or m.group(4)
        tw = set(re.findall(r"[a-z][a-z\-]{3,}", title.lower()))
        cands.append((len(tw & tset) * 3 + (int(m.group(1)) - 2000) * 0.05, {"src": "library", "file": f, "journal": m.group(2), "year": int(m.group(1)), "author": m.group(3), "title": title}))
    cands.sort(key=lambda x: -x[0])
    return [c for _, c in cands]


def _pdf_sections(name):
    """보유 PDF 본문을 1단계 절로 나눈다: [(종류 또는 None, 절 제목, [{sub, text}])]. 그림·표 모음·감사의 글·참고문헌 뒤는 버린다."""
    try:
        body = cfg["paper_body"](name) if cfg.get("paper_body") else ""
    except Exception:
        body = ""
    secs, cur, sub = [], None, ""
    for p in re.split(r"\n\s*\n", body or ""):
        one = re.sub(r"\s+", " ", p).strip()
        if not one:
            continue
        if one.startswith("Figures and Tables"):
            break
        m1 = re.match(r"^(\d{1,2})\.?\s+([A-Z][^.]{2,80})$", one)
        m2 = re.match(r"^\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\.?\s+([A-Z].{2,120})$", one)
        plain = re.match(r"^(introduction|conclusions?|results and discussion|discussion|summary)$", one, re.I)
        if (m1 and len(one) < 90) or plain:
            head = (m1.group(2) if m1 else one).strip(); low = head.lower()
            if re.search(_END_RX, low):
                cur = None; continue
            kind = next((k for k in ("conclusion", "intro", "results", "methods") if re.search(_SEC_RX[k], low)), None)
            cur = [kind, head, []]; secs.append(cur); sub = ""
            continue
        if re.search(r"^(" + _END_RX + r")", one.lower()) and len(one) < 60:
            cur = None; continue
        if cur is None:
            continue
        if m2 and len(one) < 140:
            sub = m2.group(1).strip(); continue
        if one.startswith("[[EQ:") or len(one.split()) < 20:
            continue
        cur[2].append({"sub": sub, "text": one})
    return secs


def _section_paras(name, kind):
    """그 종류의 절들(방법은 여러 절일 수 있음)의 문단을 모아서."""
    out = []
    for k, head, paras in _pdf_sections(name):
        if k == kind:
            out += [dict(p, sub=p["sub"] or head) for p in paras]
    return out


def _cites(text):
    return len(re.findall(r"\[\d+(?:\s*[,\u2013\-]\s*\d+)*\]", text or ""))


def _library_exemplars(kind, journals, topic, n):
    out = []
    for c in _library_candidates(journals, topic)[: n * 4]:
        if kind == "abstract":
            a = cfg["extract_abstract"](c["file"]) if cfg.get("extract_abstract") else ""
            if 80 <= len(a.split()) <= 450:
                out.append(dict(c, text=a))
        elif kind == "title":
            out.append(dict(c, text=c["title"]))
        else:
            paras = _section_paras(c["file"], kind)
            words = sum(len(p["text"].split()) for p in paras)
            if (kind == "conclusion" and 50 <= words <= 1200) or (kind != "conclusion" and len(paras) >= 2):
                out.append(dict(c, paras=paras, text="\n\n".join(p["text"] for p in paras)))
        if len(out) >= n:
            break
    return out


def _scopus_query(key, q, want):
    import requests as rq
    r = rq.get("https://api.elsevier.com/content/search/scopus", params={"query": q, "count": min(25, max(want, 1) * 3), "view": "COMPLETE", "sort": "-citedby-count"},
               headers={"X-ELS-APIKey": key, "Accept": "application/json", "User-Agent": "Athenaeum/0.9"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError("Scopus %d" % r.status_code)
    return (r.json().get("search-results") or {}).get("entry") or []


def _scopus_exemplars(kind, journals, topic, n):
    """Scopus(Elsevier 키): 저널마다 고르게(한 번에 섞으면 IJMTM 만 나옴), 2018년 이후, 주제어로 좁혀 피인용 순. 모자라면 그 저널 피인용 상위로. 초록·제목만 가능."""
    key = cfg["elsevier_key"]() if cfg.get("elsevier_key") else ""
    if not key:
        return [], "Elsevier API 키가 없어 Scopus 는 건너뜀 (⚙ 설정)"
    if n <= 0:
        return [], ""
    per = max(2, -(-n // max(1, len(journals))))
    out, errs = [], []
    for j in journals:
        base = 'SRCTITLE("%s") AND PUBYEAR > 2017' % _journal_full(j)
        got, seen = [], set()
        for q in ([base + " AND TITLE-ABS-KEY(" + " OR ".join(topic[:6]) + ")"] if topic else []) + [base]:
            if len(got) >= per:
                break
            try:
                entries = _scopus_query(key, q, per)
            except Exception as e:
                errs.append("%s: %s" % (j, str(e)[:40])); break
            for e in entries:
                if "error" in e or len(got) >= per:
                    break
                title = e.get("dc:title") or ""
                if not title or title in seen:
                    continue
                a = re.sub(r"\s+", " ", e.get("dc:description") or "").strip()
                a = re.sub(r"^abstract\s*", "", a, flags=re.I)
                a = re.sub(r"\s*(©|\(c\))\s*\d{4}.*$", "", a, flags=re.I)   # 끝의 저작권 표기
                if kind == "abstract" and not (80 <= len(a.split()) <= 450):
                    continue
                seen.add(title)
                got.append({"src": "scopus", "doi": e.get("prism:doi") or "", "journal": j, "year": int((e.get("prism:coverDate") or "0")[:4] or 0),
                            "author": ((e.get("dc:creator") or "").split(",")[0]).strip(), "title": title, "cit": int(e.get("citedby-count") or 0),
                            "text": a if kind == "abstract" else title})
        out += got
    return out[:max(n, per * len(journals))], (" · ".join(errs) if errs else "")


_MOVE_CUES = [("공백", r"\b(however|remains? (unclear|unknown|limited|challenging)|lack of|little is known|few studies|not (yet )?been|challeng)"),
              ("목적", r"\b(this (study|paper|work|research)|in this (study|paper|work)|we (propose|present|investigate|develop|aim|report)|aims? to|the (aim|objective|purpose))"),
              ("의의", r"\b(provides? (a |new )?(insight|guidance|basis|understanding)|contribut|implication|can be (used|applied|extended)|this work (offers|provides)|these findings)"),
              ("결과", r"\b(results? (show|indicate|reveal|demonstrate)|(was|were) (observed|found)|found that|increase[sd]?|decrease[sd]?|reduc|improv|%)"),
              ("방법", r"\b(experiment|simulat|finite element|modell?ing|were (conducted|performed|measured|carried)|characteri[sz]|measur|using)")]


def _heuristic_roles(kind, units):
    """Claude 가 안 될 때: 초록은 단서 규칙, 나머지는 위치로 어림."""
    roles = SECTION_KINDS[kind]["roles"]
    out = []
    for i, u in enumerate(units):
        if kind == "abstract":
            low = u.lower(); mv = None
            for name, rx in _MOVE_CUES:
                if re.search(rx, low):
                    mv = name; break
            out.append(mv or ("배경" if i < 2 else ("의의" if i == len(units) - 1 else "결과")))
        else:
            k = min(len(roles) - 1, int(i * len(roles) / max(1, len(units))))
            out.append(roles[k])
    return out


def _exemplar_cache_path():
    return os.path.join(cfg["MS_DIR"], "_본보기캐시.json")


def _label(kind, texts):
    """{키: 단위 목록(문장 또는 문단)} → {키: [[역할, 요지], …]}. 캐시 먼저, 나머지는 Claude(sonnet) 한 번에. 안 되면 규칙.
    문단은 앞 90단어만 보여 역할과 한국어 한 줄 요지를 받는다. 문장은 역할만(요지는 빈 문자열)."""
    import hashlib, io
    level = SECTION_KINDS[kind]["level"]; roles = SECTION_KINDS[kind]["roles"]
    try:
        cache = json.load(io.open(_exemplar_cache_path(), encoding="utf-8"))
    except Exception:
        cache = {}
    res, todo = {}, {}
    for k, units in texts.items():
        h = hashlib.md5((kind + "\n" + "\n".join(units)).encode("utf-8")).hexdigest()
        v = cache.get(h)
        if kind == "abstract" and v is None:   # 예전 캐시(초록, 역할만) 호환
            v = cache.get(hashlib.md5("\n".join(units).encode("utf-8")).hexdigest())
            v = [[x, ""] for x in v] if isinstance(v, list) and v and isinstance(v[0], str) else None
        if isinstance(v, list) and len(v) == len(units):
            res[k] = v
        elif units:
            todo[k] = (h, units)
        else:
            res[k] = []
    if todo and cfg.get("claude_text"):
        if level == "para":
            blocks = ["<%s>\n" % k + "\n".join("[%d] %s" % (i + 1, " ".join(u.split()[:90])) for i, u in enumerate(units)) for k, (h, units) in todo.items()]
            fmt = "JSON 한 줄만 출력: {\"<키>\": [[\"역할\", \"이 문단이 하는 일을 한국어 한 줄(25자 안팎)\"], ...]} — 배열 길이는 그 글의 문단 수와 같게."
            what = "문단"
        else:
            blocks = ["<%s>\n" % k + "\n".join("[%d] %s" % (i + 1, u) for i, u in enumerate(units)) for k, (h, units) in todo.items()]
            fmt = "JSON 한 줄만 출력: {\"<키>\": [\"역할\", ...]} — 배열 길이는 그 글의 문장 수와 같게."
            what = "문장"
        prompt = ("아래는 논문의 '%s' 부분들이다. %s마다 수사적 역할을 하나씩 붙여라. 역할은 다음 중 하나다:\n%s\n%s\n\n" %
                  (SECTION_KINDS[kind]["name"], what, _ROLE_HELP[kind], fmt)) + "\n\n".join(blocks)
        out = cfg["claude_text"](prompt, timeout=300, model="sonnet") or ""
        m = re.search(r"\{.*\}", out, re.S)
        parsed = {}
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = {}
        for k, (h, units) in todo.items():
            v = parsed.get(k)
            if isinstance(v, list) and len(v) == len(units):
                if level == "para":
                    v = [[x[0], str(x[1])[:60]] if isinstance(x, list) and len(x) >= 2 and x[0] in roles else None for x in v]
                else:
                    v = [[x, ""] if x in roles else None for x in v]
                if all(v):
                    res[k] = v; cache[h] = v
    for k, (h, units) in todo.items():
        if k not in res:
            res[k] = [[r, ""] for r in _heuristic_roles(kind, units)]
    try:
        with io.open(_exemplar_cache_path(), "w", encoding="utf-8") as fp:
            json.dump(cache, fp, ensure_ascii=False)
    except Exception:
        pass
    return res


def _pattern(roles):
    out = []
    for m in roles:
        if out and out[-1][0] == m:
            out[-1][1] += 1
        else:
            out.append([m, 1])
    return " → ".join(m + (" ×%d" % c if c > 1 else "") for m, c in out)


def _my_section(doc, kind):
    """내 원고에서 그 부분: 초록·제목은 머리부, 나머지는 개요에서 제목이 맞는 1단계 절과 그 아래 소절의 초안(영문이 있으면 영문)."""
    f = doc.get("front") or {}
    if kind == "abstract":
        return (f.get("abstract") or "").strip()
    if kind == "title":
        return (f.get("title") or doc.get("title") or "").strip()
    outl, on, parts = doc.get("outline") or [], False, []
    for nd in outl:
        if nd.get("level", 1) == 1:
            on = bool(re.search(_MINE_RX[kind], (nd.get("heading") or "").lower()))
        if on:
            t = (nd.get("draft_en") or "").strip() or (nd.get("draft") or "").strip()
            if t:
                parts.append(t)
    return "\n\n".join(parts)


def _units(kind, text, paras=None):
    if SECTION_KINDS[kind]["level"] == "para":
        return [p["text"] for p in paras] if paras is not None else [p.strip() for p in re.split(r"\n\s*\n", text or "") if len(p.split()) >= 12]
    return _sentences(text)


def exemplars(body):
    """잘 쓴 논문의 본보기 + 단위(문장·문단)마다 역할 + 전형적 구조 요약 + 내 원고 비교."""
    doc = load_ms(body.get("id")) or {"title": "", "front": {}, "outline": []}
    kind = body.get("section") if body.get("section") in SECTION_KINDS else "abstract"
    spec = SECTION_KINDS[kind]
    journals = [str(j).strip() for j in (body.get("journals") or []) if str(j).strip()] or ["IJMTM", "JMPT", "IJEM"]
    source = body.get("source") if body.get("source") in ("both", "library", "scopus") else "both"
    if kind not in ("abstract", "title"):
        source = "library"   # 본문은 전문이 필요 — 내 서재 PDF 에서만
    n = max(3, min(20, int(body.get("n") or (6 if spec["level"] == "para" else 10))))
    topic = _topic_words(doc)
    items, notes = [], []
    if source in ("both", "library"):
        items += _library_exemplars(kind, journals, topic, n if source == "library" else (n + 1) // 2)
    if source in ("both", "scopus"):
        sc, err = _scopus_exemplars(kind, journals, topic, n if source == "scopus" else n - len(items))
        if err:
            notes.append(err)
        seen = {re.sub(r"\W+", "", it["title"].lower())[:60] for it in items}
        items += [x for x in sc if re.sub(r"\W+", "", x["title"].lower())[:60] not in seen]
    if kind not in ("abstract", "title") and not items:
        notes.append("고른 저널의 논문이 내 서재에 없거나, PDF 에서 '%s' 절을 찾지 못했습니다 (본문은 전문이 필요해 내 서재 PDF 에서만 봅니다)" % spec["name"])
    mine_text = _my_section(doc, kind)
    if spec["level"] == "title":   # 제목은 역할 없이 목록과 특징만
        for it in items:
            t = it.pop("text", it["title"]); it["words"] = len(t.split()); it["colon"] = ":" in t
        mine = {"title": mine_text, "words": len(mine_text.split()), "colon": ":" in mine_text} if mine_text else None
        summ = {"n": len(items), "avg_words": round(sum(it["words"] for it in items) / len(items), 1) if items else 0,
                "colon_share": round(100 * sum(1 for it in items if it["colon"]) / len(items)) if items else 0}
        return {"section": kind, "level": "title", "roles": [], "items": items, "mine": mine, "summary": summ, "topic": topic, "journals": journals, "notes": notes}
    texts = {"E%d" % i: _units(kind, it.get("text"), it.get("paras")) for i, it in enumerate(items)}
    if mine_text:
        texts["ME"] = _units(kind, mine_text)
    labels = _label(kind, texts) if texts else {}
    key = "paras" if spec["level"] == "para" else "sentences"
    for i, it in enumerate(items):
        us = texts["E%d" % i]; lb = labels.get("E%d" % i) or [[r, ""] for r in _heuristic_roles(kind, us)]
        if spec["level"] == "para":
            it["paras"] = [dict(p, role=l[0], gist=l[1]) for p, l in zip(it["paras"], lb)]
        else:
            it["sentences"] = [{"t": s, "m": l[0]} for s, l in zip(us, lb)]
        it["pattern"] = _pattern([l[0] for l in lb]); it["words"] = len((it.get("text") or "").split()); it["cites"] = _cites(it.get("text"))
        it.pop("text", None)
        if spec["level"] != "para":
            it.pop("paras", None)   # 문장 단위(결론)는 원문 문단이 필요 없음
    mine = None
    if mine_text:
        us = texts["ME"]; lb = labels.get("ME") or [[r, ""] for r in _heuristic_roles(kind, us)]
        rl = [l[0] for l in lb]
        mine = {"pattern": _pattern(rl), "words": len(mine_text.split()), "cites": _cites(mine_text), "missing": [r for r in spec["roles"] if r not in rl]}
        if spec["level"] == "para":
            mine["paras"] = [{"sub": "", "text": u, "role": l[0], "gist": l[1]} for u, l in zip(us, lb)]
        else:
            mine["sentences"] = [{"t": s, "m": l[0]} for s, l in zip(us, lb)]
    summ = {}
    if items:
        k = len(items)
        units_of = lambda it: [p["role"] for p in it.get("paras", [])] if spec["level"] == "para" else [s["m"] for s in it.get("sentences", [])]
        summ = {"n": k, "avg_words": round(sum(it["words"] for it in items) / k), "avg_units": round(sum(len(units_of(it)) for it in items) / k, 1),
                "avg_cites": round(sum(it["cites"] for it in items) / k, 1),
                "roles": {r: {"avg": round(sum(units_of(it).count(r) for it in items) / k, 1), "share": round(100 * sum(1 for it in items if r in units_of(it)) / k)} for r in spec["roles"]}}
    return {"section": kind, "level": spec["level"], "unit": "문단" if spec["level"] == "para" else "문장", "roles": spec["roles"],
            "items": items, "mine": mine, "summary": summ, "topic": topic, "journals": journals, "notes": notes}


# ---------- 고른 글: 원고에서 드래그한 부분을 놓고 묻거나 고쳐 달라고 하기 ----------
_SEL_EFFORT = {"xhigh": ("opus", "xhigh"), "high": ("opus", None), "fast": ("sonnet", None)}   # 화면의 엑스트라·보통·빠름


def ask_selection(doc, body):
    """저자가 고른 부분(quote)에 대해 묻거나("이 말이 맞나"), 고쳐 달라거나("더 간결하게"), 제 생각을 말하면("이렇게 바꾸면 어때")
    → {answer: 한국어 답·평가, alternatives: 고른 부분을 그대로 대체할 글들}. 대화는 화면이 메모에 남긴다."""
    quote = str(body.get("quote") or "").strip()
    question = str(body.get("question") or "").strip()
    if not quote or not question:
        return {"error": "고른 글과 물을 말이 필요합니다"}
    nid = body.get("node")
    node = _rev_node(doc, nid)
    if nid == "front":
        text = (doc.get("front") or {}).get("abstract") or ""
    else:
        text = (node or {}).get(body.get("key") or "draft") or ""
    para = next((p for p in re.split(r"\n\s*\n", text) if quote in p), "")
    if not para and quote in text:   # 고른 글이 문단을 넘는다
        k = text.find(quote)
        para = text[max(0, k - 1200):k + len(quote) + 1200]
    ctx = para.replace(quote, "\u27ea" + quote + "\u27eb", 1) if para else "(고른 글이 든 문단을 찾지 못함 — 그 사이 글이 바뀌었을 수 있다)"
    lang_en = doc.get("meta", {}).get("lang") == "en" or sum(1 for ch in quote if ord(ch) < 128) > len(quote) * 0.8
    hist = "\n".join("[%s] %s%s" % ("저자" if m.get("role") == "user" else "Claude", str(m.get("text") or "")[:1500],
                                    ("\n  (내놓은 대안: " + " / ".join(str(a)[:300] for a in m.get("alts") or []) + ")") if m.get("alts") else "")
                     for m in (body.get("thread") or [])[-8:])
    prompt = (
        "당신은 기계가공·재료 분야 국제 저널 논문의 공저자이자 교정자다. 저자가 원고에서 글의 한 부분을 골라 묻거나, 고쳐 달라고 하거나, 제 생각(\"이렇게 바꾸면 어때?\")을 말한다. JSON 으로만 답하라:\n"
        "{\"answer\": \"한국어로 짧게(2~5문장). 질문이면 답, 제안이면 그 제안에 대한 솔직한 평가와 이유\", \"alternatives\": [\"[고른 부분]을 그대로 대체할 글\"]}\n"
        "규칙:\n"
        "- alternatives 는 고쳐 쓰기를 바라거나 표현을 묻는 경우에만 1~3개. 뜻·사실·근거만 묻는 질문이면 빈 배열.\n"
        "- 각 대안은 [고른 부분]과 정확히 같은 범위를 대체한다. 앞뒤 글과 그대로 이어져야 하므로 고른 부분 밖의 글을 넣거나 빼지 마라. 글은 %s, 학술 문체.\n"
        "- 저자가 방향을 말했으면 첫 대안은 그 방향을 충실히 따른 것. 더 나은 길이 있다고 보면 그것을 둘째 대안으로 내고 answer 에서 이유를 말하라. 대안끼리는 실제로 달라야 한다.\n"
        "- 저자의 제안이 틀렸거나 글을 나쁘게 만든다고 보면 그렇게 말하라. 듣기 좋은 말을 하지 마라.\n"
        "- 원고에 없는 수치·결과·문헌을 지어내지 마라. 인용 번호 [n], 카드 번호 [cN], 수식($…$ 안의 LaTeX)은 고쳐 달라는 것이 아니면 그대로 둔다.\n\n"
        "논문 제목: %s\n절: %s\n\n[문단 — 고른 부분은 \u27ea \u27eb 사이]\n%s\n\n[고른 부분]\n%s\n%s\n[저자의 말]\n%s"
        % ("영어로" if lang_en else "한국어로", doc.get("title", ""), (node or {}).get("heading", ""), ctx[:6000], quote[:4000],
           ("\n[지금까지의 대화]\n" + hist + "\n") if hist else "", question[:2000]))
    model, effort = _SEL_EFFORT.get(body.get("effort") or "xhigh", _SEL_EFFORT["xhigh"])
    r = cfg["claude_json"](prompt, timeout=600, model=model, effort=effort)
    if not isinstance(r, dict) or not (r.get("answer") or r.get("alternatives")):
        return {"error": "Claude 응답이 없습니다" + _why()}
    alts = [str(a).strip() for a in (r.get("alternatives") or []) if str(a).strip() and _norm_ws(str(a)) != _norm_ws(quote)][:3]
    return {"answer": str(r.get("answer") or "").strip(), "alternatives": alts}


# ---------- 리비전: 심사 의견(편집자·심사위원)을 하나씩 같이 처리 ----------
# doc["revision"] = {"rounds": [{id, title, file, created, decision, raw, base: {절 id: {draft, draft_en}}, items: [...]}]}
# item = {id(R1.2), reviewer, no, kind(major|minor|praise), text(원문 그대로), status(todo|plan|applied|done), gist, work, suggest: [절 id],
#         plan(내가 정한 방침), nodes: [절 id], thread: [{role, text, t}], response(영문 답변), changes: [{node, key, before, after, t}]}
_REV_HEAD = re.compile(r"^\s*(?:#+\s*)?(?:comments? (?:from|of|by|to the author[s]? from) )?(reviewer|referee|editor|associate editor|handling editor|guest editor|area editor)\s*[#:]?\s*(\d+)?\b[^\n]{0,40}$", re.I)
_REV_TAIL = re.compile(r"\n\s*(?:(?:yours\s+)?(?:sincerely|faithfully)|(?:with\s+)?(?:best|kind|warm)\s+regards|reviewers?['’]?\s*comments?\s*:)", re.I)
_REV_INLINE = re.compile(r"^\s*(?:reviewer|referee)\s*#?\s*(\d+)\s*[:.\u2013-]\s*(?=\S)", re.I)   # 'Reviewer #1: This manuscript …' (Editorial Manager)
_REV_BUL = re.compile(r"^\s*(?:[-\u2022*\u00b7\u25aa]|\([a-z]\)|[a-z]\))\s+\S", re.I)
_REV_NUM = re.compile(r"^\s*(?:comment|point|question|remark|issue|q|c)?\s*[#(]?(\d{1,2})[.):]\s+\S", re.I)


def _file_text(name, raw):
    """심사 의견 파일(.docx·.pdf·.txt·.md) → 글. 문단은 빈 줄로."""
    ext = os.path.splitext(name or "")[1].lower()
    if ext == ".docx":
        import zipfile, io as _io
        z = zipfile.ZipFile(_io.BytesIO(raw))
        x = z.read("word/document.xml").decode("utf-8")
        body = re.search(r"<w:body>(.*)</w:body>", x, re.S)
        paras = re.findall(r"<w:p[ >].*?</w:p>", body.group(1) if body else x, re.S)
        return "\n\n".join(t for t in (re.sub(r"\*+", "", _docx_para_md(p)).strip() for p in paras) if t)
    if ext == ".pdf":
        import fitz
        d = fitz.open(stream=raw, filetype="pdf")
        try:
            return "\n\n".join(pg.get_text() for pg in d)
        finally:
            d.close()
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _why():
    """Claude 가 답하지 않은 이유(로그인 만료·한도)가 있으면 오류 글 뒤에 붙인다."""
    fn = cfg.get("claude_error")
    r = fn() if fn else ""
    return (" — " + r) if r else ""


def _rev_node(doc, nid):
    """절 찾기 — 없으면 None (_node 는 예외를 던진다: 개요에서 지워진 절에 걸린 수정 기록 때문에 죽으면 안 된다). 'front' = 머리부의 초록."""
    if nid == "front":
        return {"id": "front", "heading": "Abstract", "level": 1, "abstract": (doc.get("front") or {}).get("abstract", "")}
    for n in doc.get("outline", []):
        if n["id"] == nid:
            return n
    return None


def _rev_text(node):
    """(글이 든 키, 글) — 초록은 abstract, 절은 draft(비었으면 draft_en)."""
    if node.get("id") == "front":
        return "abstract", node.get("abstract") or ""
    key = "draft" if (node.get("draft") or "").strip() else "draft_en"
    return key, node.get(key) or ""


def _rev_heading(doc, nid):
    return (_rev_node(doc, nid) or {}).get("heading") or nid


_REV_LOCK = threading.Lock()


def _rev_commit(doc, rid, iid, apply):
    """Claude 를 기다리는 1분 남짓 동안 사용자가 본문·방침을 고쳐 저장했을 수 있다.
    요청 첫머리에 읽은 doc 을 그대로 저장하면 그 글을 덮어쓰므로, 파일을 다시 읽어 그 위에 결과만 얹는다. iid 가 없으면 라운드에."""
    with _REV_LOCK:
        fresh = load_ms(doc["id"]) or doc
        rnd = _round(fresh, rid)
        target = _rev_item(rnd, iid) if iid else rnd
        if target is None:
            return None
        apply(target)
        save_ms(fresh)
        return target


def _rev_label(reviewer):
    m = re.search(r"(\d+)", reviewer or "")
    low = (reviewer or "").lower()
    if "editor" in low:
        return "E" + (m.group(1) if m else "")
    return "R" + (m.group(1) if m else "1")


def _split_review_regex(text):
    """Claude 없이 나누기. 심사위원 머리('Reviewer #1' 한 줄, 또는 'Reviewer #1: 글…' 처럼 글과 한 줄)로 블록을 가르고,
    블록 안에서 번호 붙은 문단(1. / (1) / Comment 1:)마다, 번호가 없는 심사위원은 빈 줄 문단마다. 번호 앞 총평은 .0,
    글머리표 목록(Minor points: - … - …)은 표마다, 편집자 편지는 통째로 하나."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks, reviewer, cur = [], "Editor", []
    for p in paras:
        first = p.split("\n")[0]
        h = _REV_HEAD.match(first) if len(first) < 70 else None
        hi = None if h else _REV_INLINE.match(p)
        if h or hi:
            if cur:
                blocks.append((reviewer, cur))
            cur = []
            if h:
                who = h.group(1).lower()
                reviewer = ("Editor" if "editor" in who else "Reviewer") + (" " + h.group(2) if h.group(2) else ("" if "editor" in who else " 1"))
                rest = "\n".join(p.split("\n")[1:]).strip()
            else:
                reviewer, rest = "Reviewer " + hi.group(1), p[hi.end():].strip()
            if rest:
                cur.append(rest)
            continue
        cur.append(p)
    if cur:
        blocks.append((reviewer, cur))

    items = []

    def push(it):
        if it:
            it["text"] = re.sub(r"\n\s*(?:\S+\s+){0,3}\S+:\s*\Z", "", _REV_TAIL.split(it["text"])[0]).strip()   # 맺음말·다음 묶음 머리
            if len(it["text"]) >= 25:
                items.append(it)

    for reviewer, ps in blocks:
        numbered = sum(1 for p in ps if _REV_NUM.match(p)) >= 2
        if reviewer.startswith("Editor") and not numbered:   # 편집자 편지: 절차 안내가 섞여 있어 문단마다 나누면 잡음만 는다
            push({"reviewer": reviewer, "no": "0", "text": "\n\n".join(ps), "kind": ""})
            continue
        it, last, started = None, 0, False
        for p in ps:
            lines = p.split("\n")
            bl = [i for i, ln in enumerate(lines) if _REV_BUL.match(ln)]
            if len(bl) >= 2 and bl[0] <= 1:   # 글머리표 목록 → 표마다 하나 (번호는 이어서)
                push(it); it = None; started = True
                for k, i in enumerate(bl):
                    seg = "\n".join(lines[i:(bl[k + 1] if k + 1 < len(bl) else len(lines))]).strip()
                    last += 1
                    push({"reviewer": reviewer, "no": str(last), "text": seg, "kind": "minor"})
                continue
            m = _REV_NUM.match(p)
            if numbered and not m and not started:   # 번호 앞의 인사·총평: 하나로 모아 .0 (답변이 필요 없을 때가 많다)
                if it is None:
                    it = {"reviewer": reviewer, "no": "0", "text": p, "kind": "praise"}
                else:
                    it["text"] += "\n\n" + p
            elif m or not numbered:
                push(it); started = True
                last = int(m.group(1)) if m else last + 1
                it = {"reviewer": reviewer, "no": str(last), "text": p, "kind": ""}
            elif it is not None:
                it["text"] += "\n\n" + p
        push(it)
    return items


def _split_review_claude(text):
    """Claude 가 지적마다 '누구·번호·종류·첫 낱말들'만 알려 주면 원문에서 그 자리를 찾아 자른다 — 글을 다시 쓰게 하지 않아 빠르고 원문이 그대로 남는다."""
    r = cfg["claude_json"](
        "아래는 저널 심사 결과(편집자 편지와 심사위원 의견)다. 저자가 하나씩 답해야 할 지적의 경계를 찾아 JSON 으로만 답하라:\n"
        "{\"decision\": \"major revision|minor revision|reject and resubmit|accept|unknown\", "
        "\"items\": [{\"reviewer\": \"Editor 또는 Reviewer 1 …\", \"no\": \"그 심사위원 안에서의 번호\", \"kind\": \"major|minor|praise\", \"start\": \"그 지적이 시작하는 첫 8~12 낱말을 원문 그대로\"}]}\n"
        "규칙: 글에 나온 순서대로. 인사·절차 안내(마감일, 제출 방법)는 넣지 마라. 전체 평가·칭찬뿐인 문단은 kind=praise 로 하나만. "
        "한 번호 안에 요구가 여럿이어도 나누지 말고 하나로. 번호가 없으면 문단마다 하나. start 는 반드시 원문에 있는 글자 그대로(번호·기호 포함).\n\n[심사 결과]\n" + text[:45000],
        timeout=400) or {}
    raw_items = r.get("items") if isinstance(r, dict) else None
    if not raw_items:
        return [], ""
    norm_map, norm = [], []
    for i, ch in enumerate(text):   # 공백을 하나로 줄인 글과 원래 위치의 대응
        if ch.isspace():
            if norm and norm[-1] == " ":
                continue
            norm.append(" "); norm_map.append(i)
        else:
            norm.append(ch); norm_map.append(i)
    ntext = "".join(norm)
    cuts, pos = [], 0
    for it in raw_items:
        s = re.sub(r"\s+", " ", str(it.get("start") or "")).strip()
        if len(s) < 8:
            continue
        j = -1
        for probe in (s, s[:60], s[:35], s[:20]):
            j = ntext.find(probe, pos)
            if j >= 0:
                break
        if j < 0:
            continue
        cuts.append((norm_map[j], it)); pos = j + 5
    items = []
    for k, (start, it) in enumerate(cuts):
        end = cuts[k + 1][0] if k + 1 < len(cuts) else len(text)
        seg = text[start:end].strip()
        seg = re.split(r"\n\s*(?:reviewer|referee)\s*#?\s*\d+\s*[:\n]", seg, flags=re.I)[0].strip()   # 다음 심사위원 머리가 끝에 붙으면 뗀다
        seg = _REV_TAIL.split(seg)[0].strip()                     # 편지 맺음말·'Reviewers' comments:' 머리
        seg = re.sub(r"\n\s*(?:\S+\s+){0,3}\S+:\s*\Z", "", seg).strip()   # 끝에 붙은 다음 묶음 머리 ('Minor points:')
        if len(seg) >= 20:
            items.append({"reviewer": str(it.get("reviewer") or "Reviewer 1")[:40], "no": str(it.get("no") or "")[:6],
                          "kind": it.get("kind") if it.get("kind") in ("major", "minor", "praise") else "", "text": seg})
    return items, str(r.get("decision") or "")


def _round(doc, rid=None):
    rounds = (doc.get("revision") or {}).get("rounds") or []
    if not rounds:
        return None
    for r in rounds:
        if r["id"] == rid:
            return r
    return rounds[-1]


def _rev_item(rnd, iid):
    for it in (rnd or {}).get("items", []):
        if it["id"] == iid:
            return it
    return None


def rev_import(doc, name, raw=None, text=""):
    """심사 의견을 읽어 지적마다 나누고 새 라운드를 만든다. 그 시점의 원고 본문을 base 로 남겨 나중에 '수정 표시 원고'에 쓴다."""
    text = (text or "").strip() or _file_text(name, raw or b"").strip()
    text = re.sub(r"\r\n?", "\n", text)
    if len(text) < 40:
        return {"error": "심사 의견 글을 읽지 못했습니다 (빈 파일이거나 스캔 PDF)"}
    items, decision, how = [], "", "claude"
    try:
        items, decision = _split_review_claude(text)
    except Exception:
        items = []
    if len(items) < 2:
        items, how = _split_review_regex(text), "규칙"
    if not items:
        return {"error": "지적을 나누지 못했습니다. 글을 붙여 넣어 다시 시도해 보세요"}
    seen = {}
    for it in items:
        lab = _rev_label(it["reviewer"])
        seen[lab] = seen.get(lab, 0) + 1
        no = it.get("no") or str(seen[lab])
        iid = "%s.%s" % (lab, no)
        while any(x.get("id") == iid for x in items if x is not it):
            iid += "'"
        it.update({"id": iid, "no": no, "status": "todo", "gist": "", "work": "", "suggest": [], "plan": "", "nodes": [], "thread": [], "response": "", "changes": []})
    with _REV_LOCK:
        doc = load_ms(doc["id"]) or doc   # 나누는 동안 저장된 글 위에
        rev = doc.setdefault("revision", {"rounds": []})
        k = 1 + max([int(r["id"][1:]) for r in rev["rounds"] if r.get("id", "")[1:].isdigit()] or [0])   # 라운드를 지운 뒤에도 id 가 겹치지 않게
        rnd = {"id": "r%d" % k, "title": "%d차 심사" % (len(rev["rounds"]) + 1), "file": os.path.basename(name or "붙여 넣은 글"), "created": time.time(),
               "decision": decision, "raw": text[:80000], "split": how, "split_note": (_why().strip(" —") if how == "규칙" else ""),
               "base": {n["id"]: {"heading": n.get("heading", ""), "draft": n.get("draft", ""), "draft_en": n.get("draft_en", "")} for n in doc.get("outline", [])},
               "items": items}
        rnd["base"]["_front"] = {"abstract": (doc.get("front") or {}).get("abstract", "")}
        rev["rounds"].append(rnd)
        save_ms(doc)
    return {"round": rnd}


def _outline_brief(doc, n_chars=160):
    ab = _norm_ws((doc.get("front") or {}).get("abstract") or "")
    return ("front [Abstract]  — %s\n" % ab[:n_chars] if ab else "") + "\n".join("%s%s [%s] %s — %s" % ("  " if n.get("level", 1) > 1 else "", n["id"], n.get("heading", ""), "", _norm_ws(n.get("draft") or n.get("draft_en") or "")[:n_chars])
                     for n in doc.get("outline", []))


def rev_triage(doc, rid):
    """지적 전체를 훑어 뜻(한국어 한 줄)·작업 종류·고칠 절 후보·권하는 순서를 붙인다 — 무엇부터 어떻게 할지 협의의 출발점."""
    rnd = _round(doc, rid)
    if not rnd:
        return {"error": "라운드 없음"}
    lst = "\n\n".join("<%s> (%s) %s" % (it["id"], it["reviewer"], _norm_ws(it["text"])[:700]) for it in rnd["items"])
    r = cfg["claude_json"](
        "당신은 기계가공 분야 국제 저널 논문의 공저자다. 심사 의견에 어떻게 대응할지 정리한다. JSON 으로만 답하라:\n"
        "{\"items\": {\"<지적 id>\": {\"gist\": \"이 지적이 요구하는 것 한국어 한 줄(40자 안팎)\", \"work\": \"문장 수정|설명 보강|데이터·분석 추가|추가 실험|그림·표 수정|문헌 추가|반박·해명|답변만\", "
        "\"effort\": 1, \"nodes\": [\"고칠 절 id (개요에서, 최대 3개)\"]}}, "
        "\"order\": [\"먼저 처리하기를 권하는 순서대로 지적 id\"], \"note\": \"지적 사이의 연관·충돌, 편집자가 특히 강조한 것 (한국어 두세 문장, 없으면 빈 문자열)\"}\n"
        "effort 는 1(문장만) 2(분석·그림 손봄) 3(새 실험·큰 구조 변경). 절 id 는 아래 개요의 id 만 쓴다(초록을 고쳐야 하면 front). 칭찬뿐인 지적은 work=답변만.\n\n"
        "논문 제목: %s\n\n[원고 개요: id [절 제목] — 첫머리]\n%s\n\n[심사 의견]\n%s" % (doc.get("title", ""), _outline_brief(doc), lst), timeout=400)
    if not isinstance(r, dict) or not isinstance(r.get("items"), dict):
        return {"error": "정리 실패 (Claude 응답 없음)" + _why()}
    ids = {n["id"] for n in doc.get("outline", [])} | {"front"}

    def apply(rn):
        for it in rn["items"]:
            v = r["items"].get(it["id"]) or {}
            it["gist"] = str(v.get("gist") or it.get("gist") or "")[:120]
            it["work"] = str(v.get("work") or "")[:20]
            it["effort"] = v.get("effort") if v.get("effort") in (1, 2, 3) else 0
            it["suggest"] = [x for x in (v.get("nodes") or []) if x in ids][:3]
        rn["triage"] = {"t": time.time(), "order": [x for x in (r.get("order") or []) if _rev_item(rn, x)], "note": str(r.get("note") or "")[:600]}
    out = _rev_commit(doc, rnd["id"], None, apply)
    if out is None:
        return {"error": "라운드가 그 사이 지워졌습니다"}
    return {"round": out, "round_fields": ["triage"], "item_fields": ["gist", "work", "effort", "suggest"]}


def _rev_context(doc, rnd, it, max_nodes=3):
    ids = (it.get("nodes") or it.get("suggest") or [])[:max_nodes]
    lines = ["논문 제목: %s" % doc.get("title", ""), "투고 저널: %s" % (doc.get("meta", {}).get("journal") or "미정"),
             "", "[심사 의견 %s — %s]" % (it["id"], it["reviewer"]), it["text"][:5000]]
    if it.get("plan"):
        lines += ["", "[저자가 정한 방침]", it["plan"]]
    found = [n for n in (_rev_node(doc, nid) for nid in ids) if n]
    if found:
        for n in found:
            lines += ["", "[관련 절 %s: %s]" % (n["id"], n.get("heading", "")), (_rev_text(n)[1] or "(비어 있음)")[:3500]]
    else:
        lines += ["", "[원고 개요: id [절 제목] — 첫머리]", _outline_brief(doc, 220)]
    if it.get("changes"):
        lines += ["", "[이 지적으로 이미 반영한 수정]"] + ["- %s: %s" % (_rev_heading(doc, c["node"]), _change_brief(c, 400)) for c in it["changes"][-4:]]
    return "\n".join(lines)


def _change_brief(c, n):
    if c.get("type") == "delete":
        return "(문단 삭제) " + _norm_ws(c.get("before") or "")[:n // 2]
    return _norm_ws(c.get("after") or "")[:n]


def rev_discuss(doc, rid, iid, question=""):
    """지적 하나를 놓고 같이 논의. 첫 번에는 뜻·대응 방안·고칠 곳·필요한 것·답변 방향, 그 뒤로는 질문에 답. 한국어."""
    rnd = _round(doc, rid); it = _rev_item(rnd, iid)
    if not it:
        return {"error": "지적을 찾지 못했습니다"}
    thread = it.setdefault("thread", [])
    hist = _thread_text(it)
    if not thread and not question:
        ask = ("이 지적을 처음 검토한다. 다섯 항목을 한국어로 쓰고 각 항목은 줄 첫머리에 **뜻**, **대응 방안**, **고칠 곳**, **필요한 것**, **답변 방향** 이라고 표시해라.\n"
               "- 뜻: 심사위원이 실제로 문제 삼는 것이 무엇인지 한두 문장. 표면 요구 뒤의 우려(타당성·신규성·재현성 등)가 있으면 짚어라.\n"
               "- 대응 방안: 2~3가지를 번호로(수용해 고침 / 일부 수용 / 근거를 들어 해명 등). 각각 무엇을 어떻게 하는지 구체적으로, 끝에 어느 쪽을 권하는지와 이유.\n"
               "- 고칠 곳: 어느 절의 어느 문단·그림·표를 고치거나 더해야 하는지. 절 제목으로 가리켜라.\n"
               "- 필요한 것: 추가 실험·데이터·분석·문헌 중 무엇이 필요한지. 원고에 이미 있는 것으로 되면 '없음'.\n"
               "- 답변 방향: 답변서에 쓸 요지 한두 문장.\n"
               "원고에 없는 결과를 있는 것처럼 말하지 마라. 문단을 다시 쓰지는 마라 (그건 별도 기능이다). 맨 위에 제목 줄을 달지 말고 **뜻** 부터 바로 시작해라. 표는 쓰지 마라.")
    else:
        ask = ("지금까지의 논의를 이어서 아래 질문에 한국어로 답하라. 결론부터 짧게, 필요한 만큼만(대개 세 문단 안). 문장 예시가 필요하면 원고의 언어로 들어도 된다. "
               "제목 줄(#)과 표는 쓰지 말고 강조는 **굵게** 만.\n[질문]\n" + (question or "계속 논의해 주세요."))
    prompt = ("당신은 기계가공 분야 국제 저널 논문의 공저자로, 심사 의견에 어떻게 대응할지 저자와 함께 정한다. 솔직하고 구체적으로, 군말 없이. "
              "심사위원이 틀렸다고 보면 그렇게 말하되 정중히 해명하는 길도 같이 보여라.\n\n" + _rev_context(doc, rnd, it) +
              (("\n\n[지금까지의 논의]\n" + hist) if hist else "") + "\n\n" + ask)
    try:
        out = (cfg["claude"](prompt, timeout=300) or "").strip()
    except Exception as e:
        return {"error": str(e)[:200]}
    if not out:
        return {"error": "Claude 응답이 없습니다" + _why()}
    msgs = ([{"role": "user", "text": question, "t": time.time()}] if question else []) + [{"role": "claude", "text": out, "t": time.time()}]
    it = _rev_commit(doc, rnd["id"], iid, lambda t: t.setdefault("thread", []).extend(msgs))
    if it is None:
        return {"error": "지적이 그 사이 지워졌습니다"}
    return {"item": it, "fields": ["thread"]}


def rev_propose(doc, rid, iid, nid, note=""):
    """지적을 반영해 그 절의 문단을 고치는 안. 문단 번호로 고칠 것·새로 넣을 것을 받아 before/after 로 돌려준다 (반영은 화면에서)."""
    rnd = _round(doc, rid); it = _rev_item(rnd, iid); node = _rev_node(doc, nid)
    if not it or not node:
        return {"error": "지적 또는 절을 찾지 못했습니다"}
    key, text = _rev_text(node)
    paras = [x.strip() for x in re.split(r"\n\s*\n", text) if x.strip()]
    if not paras:
        return {"error": "이 절에는 아직 글이 없습니다"}
    sample = " ".join(paras)[:2000]
    lang_en = doc.get("meta", {}).get("lang") == "en" or sum(1 for ch in sample if ord(ch) < 128) > len(sample) * 0.8
    hist = _thread_text(it, 6)
    r = cfg["claude_json"](
        "당신은 기계가공 분야 국제 저널 논문의 공저자다. 아래 심사 의견을 반영해 [절]의 문단을 고쳐라. JSON 으로만 답하라:\n"
        "{\"edits\": [{\"para\": 문단 번호, \"text\": \"고친 문단 전체\"}], \"inserts\": [{\"after\": 문단 번호(맨 앞이면 0), \"text\": \"새 문단\"}], "
        "\"deletes\": [없앨 문단 번호], \"note\": \"무엇을 왜 고쳤는지 한국어 한두 문장\"}\n"
        "규칙:\n- 글은 %s로. 지적과 무관한 문단·문장은 건드리지 마라. 꼭 필요한 문단만 edits 에 넣는다.\n"
        "- edits 의 text 는 그 번호 문단 자신을 고친 글이어야 한다. 문단을 없애거나 합칠 때는 없어지는 번호를 deletes 에 넣고, 뒤 문단의 내용을 앞 번호로 밀어 옮겨 쓰지 마라. 문단 순서는 바꾸지 마라.\n"
        "- 원고에 없는 수치·결과·문헌을 지어내지 마라. 필요한 데이터가 없으면 그 자리에 %s 처럼 표시한다.\n"
        "- 지적과 무관한 수치는 절대 바꾸지 마라. 카드 번호 [cN], 인용 번호 [n], 그림 번호, 수식($…$ 안의 LaTeX)은 그대로 둔다.\n"
        "- 저자가 정한 방침과 논의에서 정해진 방향이 있으면 그것을 따른다. 고칠 것이 이 절에 없으면 edits·inserts·deletes 를 비우고 note 에 이유를 적어라.\n%s\n"
        % ("영어 학술 문체" if lang_en else "한국어 학술 문체", "(DATA NEEDED: …)" if lang_en else "(데이터 필요: …)",
           "- 이 절은 초록이다. 저널의 단어 수 제한이 있으니 길이를 거의 늘리지 말고, 넣는 만큼 덜 중요한 말을 줄여라.\n" if nid == "front" else "")
        + _rev_context(doc, rnd, dict(it, nodes=[]), 0).split("[원고 개요")[0]
        + "\n[절 %s: %s — 문단 번호]\n" % (nid, node.get("heading", "")) + "\n\n".join("[%d] %s" % (i + 1, p) for i, p in enumerate(paras))
        + (("\n\n[논의 요약]\n" + hist) if hist else "") + (("\n\n[추가 지시]\n" + note) if note else ""), timeout=400)
    if not isinstance(r, dict):
        return {"error": "Claude 응답이 없습니다" + _why()}

    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    gone = sorted({k - 1 for k in (num(x) for x in (r.get("deletes") or [])) if k and 0 < k <= len(paras)})
    if len(gone) >= len(paras):
        gone = []   # 절을 통째로 비우는 안은 받지 않는다
    edits = []   # 새 문단·삭제의 기준은 바로 앞 문단(before·prev). 앞 문단이 먼저 고쳐지거나 지워지면 화면이 남은 안의 기준을 바꿔 준다
    for e in r.get("edits") or []:
        k = num(e.get("para"))
        t = str(e.get("text") or "").strip()
        if k and 0 < k <= len(paras) and (k - 1) not in gone and t and _norm_ws(t) != _norm_ws(paras[k - 1]):
            edits.append({"type": "edit", "para": k, "before": paras[k - 1], "after": t})
    for e in r.get("inserts") or []:
        k = num(e.get("after"))
        t = str(e.get("text") or "").strip()
        if k is not None and 0 <= k <= len(paras) and t:
            edits.append({"type": "insert", "para": k, "before": paras[k - 1] if k >= 1 else "", "after": t})
    for k in gone:
        edits.append({"type": "delete", "para": k + 1, "before": paras[k], "after": "", "prev": paras[k - 1] if k >= 1 else ""})
    edits.sort(key=lambda e: (e["para"], {"edit": 0, "delete": 0, "insert": 1}[e["type"]]))
    return {"node": nid, "key": key, "heading": node.get("heading", ""), "edits": edits, "note": str(r.get("note") or "")[:500]}


def rev_response(doc, rid, iid, note=""):
    """답변서에 들어갈 이 지적의 답변(영문). 반영한 수정을 근거로, 어디를 어떻게 고쳤는지 가리킨다."""
    rnd = _round(doc, rid); it = _rev_item(rnd, iid)
    if not it:
        return {"error": "지적을 찾지 못했습니다"}
    changes = "\n".join("- Section '%s'%s: %s" % (_rev_heading(doc, c["node"]), " (paragraph removed)" if c.get("type") == "delete" else (" (new paragraph)" if c.get("type") == "insert" else " (revised paragraph)"),
                                                 _norm_ws(c.get("before") or "")[:300] if c.get("type") == "delete" else _norm_ws(c.get("after") or "")[:900]) for c in (it.get("changes") or [])[-6:])
    prompt = ("You are a co-author writing the point-by-point response letter for a manuscript under revision at an international manufacturing journal.\n"
              "Write the response to ONE reviewer comment, in English. Rules:\n"
              "- 1 to 3 short paragraphs. Open by acknowledging the point in one clause (no flattery, vary the wording), then state exactly what was done.\n"
              "- Point to where the change is (section title; say 'revised paragraph' or 'new paragraph'), and quote at most one or two key revised sentences in quotation marks.\n"
              "- Use only what is given below. Do NOT invent results, numbers, experiments, or references. If a needed item is missing, write [TO ADD: …].\n"
              "- If the authors chose not to change the manuscript, explain the reasoning respectfully and concretely.\n"
              "- Output only the response text. No heading, no 'Response:' label.\n\n"
              "Manuscript title: %s\n\n[Reviewer comment %s — %s]\n%s\n\n[Authors' decision (may be in Korean)]\n%s\n\n[Discussion summary (Korean)]\n%s\n\n[Changes made in the manuscript]\n%s%s"
              % (doc.get("title", ""), it["id"], it["reviewer"], it["text"][:5000], it.get("plan") or "(not stated)", _thread_text(it, 4) or "(none)",
                 changes or "(no change recorded yet)", ("\n\n[Extra instruction]\n" + note) if note else ""))
    fn = cfg.get("claude_text")
    out = (fn(prompt, timeout=240, model="opus") if fn else cfg["claude"](prompt, timeout=300)) or ""
    out = out.strip()
    if not out:
        return {"error": "Claude 응답이 없습니다" + _why()}
    it = _rev_commit(doc, rnd["id"], iid, lambda t: t.update({"response": out}))
    if it is None:
        return {"error": "지적이 그 사이 지워졌습니다"}
    return {"item": it, "fields": ["response"]}


def _changed_paras(doc, rnd, n, key):
    """base(심사 의견을 받은 시점) 와 달라진 문단의 집합 (공백 무시)."""
    base = (rnd.get("base") or {}).get(n["id"]) or {}
    old = {_norm_ws(x) for x in re.split(r"\n\s*\n", base.get(key) or "") if x.strip()}
    return {_norm_ws(x) for x in re.split(r"\n\s*\n", n.get(key) or "") if x.strip()} - old


def rev_export_response(doc, rid):
    """답변서(.docx): 심사위원마다, 지적(기울임) → 답변 → 원고에서 바뀐 글(파란색)."""
    import zipfile
    rnd = _round(doc, rid)
    if not rnd:
        return {"error": "라운드 없음"}
    body = [_para("Response to Reviewers", "Title"), _para(doc.get("front", {}).get("title") or doc.get("title", "")),
            _para("We thank the editor and the reviewers for their careful reading and constructive comments. Our point-by-point responses are given below. "
                  "Reviewer comments are shown in italics, and text revised in the manuscript is shown in blue.")]
    last = None
    for it in rnd["items"]:
        if it["reviewer"] != last:
            body.append(_para(it["reviewer"], "Heading1")); last = it["reviewer"]
        body.append(_para("**Comment %s**" % it["id"]))
        for p in [x.strip() for x in re.split(r"\n\s*\n", it["text"]) if x.strip()]:
            body.append(_para("\n".join("*" + ln.strip().replace("*", "") + "*" for ln in p.split("\n") if ln.strip())))
        resp = (it.get("response") or "").strip()
        body.append(_para("**Response:** " + (resp.split("\n\n")[0] if resp else ("We thank the reviewer for this comment." if it.get("kind") == "praise" else "[TO WRITE]"))))
        for p in resp.split("\n\n")[1:]:
            if p.strip():
                body.append(_para(p.strip()))
        if it.get("changes"):
            body.append(_para("**Changes in the manuscript:**"))
            for c in it["changes"]:
                body.append(_para("Section: " + _rev_heading(doc, c["node"])))
                if c.get("type") == "delete":
                    gone = _norm_ws(re.sub(r"\[c\d+\]", "", c.get("before") or ""))
                    body.append(_para("Removed paragraph: “" + gone[:140] + ("…" if len(gone) > 140 else "") + "”"))
                    continue
                for p in [x.strip() for x in re.split(r"\n\s*\n", c.get("after") or "") if x.strip()]:
                    body.append(_para(re.sub(r"\[c\d+\]", "", p), color="1F4FD1"))
    safe = re.sub(r'[\\/:*?"<>|]+', " ", doc.get("title", "원고")).strip()[:50] or "원고"
    out = os.path.join(cfg["EXPORT_DIR"], "%s_Response_%s_%s.docx" % (safe, rnd["id"], time.strftime("%Y%m%d_%H%M")))
    _write_simple_docx(out, body)
    return {"path": out}


def _write_simple_docx(out, body):
    import zipfile
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><w:body>%s'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>' % "".join(body))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", STYLES_XML)
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')


def rev_op(doc, body):
    op = body.get("op")
    rid = body.get("rid")
    if op == "import":
        import base64
        raw = base64.b64decode(body["b64"]) if body.get("b64") else None
        return rev_import(doc, body.get("name") or "", raw, body.get("text") or "")
    if op == "triage":
        return rev_triage(doc, rid)
    if op == "discuss":
        return rev_discuss(doc, rid, body.get("iid"), body.get("question", ""))
    if op == "propose":
        return rev_propose(doc, rid, body.get("iid"), body.get("node"), body.get("note", ""))
    if op == "response":
        return rev_response(doc, rid, body.get("iid"), body.get("note", ""))
    if op == "export_response":
        return rev_export_response(doc, rid)
    if op == "export_marked":
        rnd = _round(doc, rid)
        if not rnd:
            return {"error": "라운드 없음"}
        return {"path": export_docx(doc, body.get("lang", "ko"), mark=rnd)}
    if op == "delete_round":
        rev = doc.get("revision") or {}
        rev["rounds"] = [r for r in rev.get("rounds", []) if r["id"] != rid]
        save_ms(doc)
        return {"ok": True}
    return {"error": "알 수 없는 동작"}


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
            with _REV_LOCK:   # 리비전 결과를 파일에 얹는 동작(_rev_commit)과 겹치지 않게 한 번에 하나씩
                old = load_ms(d["id"]) or {}
                # 개요가 바뀌면 이전 개요를 판 이력에 남김 (최근 20개)
                if old.get("outline") and json.dumps(old.get("outline"), sort_keys=True) != json.dumps(d.get("outline"), sort_keys=True):
                    d.setdefault("versions", old.get("versions", []))
                    d["versions"] = (d["versions"] + [{"t": old.get("updated", time.time()), "outline": old["outline"]}])[-20:]
                upd = save_ms(d)["updated"]
            return h._send(200, {"ok": True, "updated": upd})
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
            if body.get("into"):   # 열려 있는 원고에 덮어쓰기 (본문만 갱신)
                cur_doc = load_ms(body["into"])
                if not cur_doc:
                    return h._send(400, {"error": "원고를 찾지 못했습니다"})
                return h._send(200, merge_docx(cur_doc, path))
            d = import_docx(path, body.get("title"))
            if body.get("journal"):
                d["meta"]["journal"] = body["journal"]; save_ms(d)
            return h._send(200, {"id": d["id"], "title": d["title"], "nodes": len(d["outline"]), "figures": len(d["figures"]),
                                 "refs": len(d.get("refs_text", [])), "comments": len(d.get("comments", []))})
        if p == "/api/ms/evidence":
            return h._send(200, find_evidence(load_ms(body.get("id")), body.get("node"), body.get("query", "")))
        if p == "/api/ms/check":
            return h._send(200, check_manuscript(load_ms(body.get("id"))))
        if p == "/api/ms/revision":    # 리비전: 심사 의견 가져오기·정리·논의·고칠 안·답변·내보내기
            doc = load_ms(body.get("id"))
            if not doc:
                return h._send(400, {"error": "원고 없음"})
            return h._send(200, rev_op(doc, body))
        if p == "/api/ms/ask_sel":   # 고른 글을 놓고 Claude 에게 묻기·고쳐 달라기
            doc = load_ms(body.get("id"))
            if not doc:
                return h._send(400, {"error": "원고 없음"})
            return h._send(200, ask_selection(doc, body))
        if p == "/api/ms/math":   # 수식 미리보기: LaTeX → MathML (브라우저가 직접 그린다)
            items = (body.get("items") or [])[:400]
            return h._send(200, {"mml": [mathtex.to_mathml(str(it.get("tex") or "")[:4000], bool(it.get("display"))) for it in items]})
        if p == "/api/ms/wordsync":   # 워드 반영: 여기서 고친 글을 가져온 워드 파일(사본)에
            doc = load_ms(body.get("id"))
            if not doc:
                return h._send(400, {"error": "원고 없음"})
            op = body.get("op") or "run"
            if op == "upgrade":
                return h._send(200, upgrade_equations(doc))
            if op == "open":
                path = ((doc.get("word_sync") or {}).get("path") or "").strip().strip('"') or sync_default_path(doc)
                if not os.path.isfile(path):
                    return h._send(200, {"error": "아직 만든 파일이 없습니다 — 먼저 「지금 반영」"})
                os.startfile(path if body.get("file") else os.path.dirname(path))
                return h._send(200, {"ok": True})
            with _SYNC_LOCK:
                return h._send(200, sync_docx(doc, mark=bool((doc.get("word_sync") or {}).get("mark")), check_only=(op == "check")))
        if p == "/api/ms/exemplars":   # 본보기: 잘 쓴 논문의 초록 구조
            return h._send(200, exemplars(body))
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
