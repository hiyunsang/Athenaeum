# -*- coding: utf-8 -*-
"""워드(.docx) 문단을 글로 읽고, 바뀐 글만 원본 문단에 갈아 끼운다 — 표준 라이브러리만.

가져오기(manuscript.import_docx)와 워드 반영(manuscript.sync_docx)이 같은 규칙을 쓰도록 문단 → 글 변환을 여기에 모았다.
원칙: 원본 XML 은 건드리지 않은 곳을 한 글자도 바꾸지 않는다. 바뀐 문단도 앞뒤의 안 바뀐 런(서식·그림·수식·인용 필드)은 그대로 두고
가운데만 새 런으로 바꾼다.
"""
import difflib
import os
import re
import zipfile

import mathtex

_PIECE = re.compile(r"<m:oMathPara[ >].*?</m:oMathPara>|<m:oMath[ >].*?</m:oMath>|<w:r[ >].*?</w:r>", re.S)
_TXT = re.compile(r"<w:t[^>]*>([^<]*)</w:t>|<w:tab/>|<w:br/>")
_TAG = re.compile(r"<(/?)([A-Za-z_][\w:.-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)>", re.S)
_EMPH = re.compile(r"(\*\*\*[^*\n]+\*\*\*|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*\*)")
_SS = re.compile(r"(\^[^\^\s]{1,30}\^|(?<=\S)_[^_\s]{1,30}_)")
_OBJ = re.compile(r"<w:drawing|<w:pict|<w:object|<w:bookmarkStart|<w:bookmarkEnd|<w:commentRangeStart|<w:commentRangeEnd|<w:commentReference|"
                  r"<w:footnoteReference|<w:endnoteReference|<mc:AlternateContent")
# w:rPr 자식의 정해진 순서 (어긋나면 워드가 문서를 못 읽을 수 있다)
_RPR_ORDER = ("rStyle rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint noProof snapToGrid vanish webHidden "
              "color spacing w kern position sz szCs highlight u effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout specVanish oMath").split()


def unesc(t):
    return t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- 워드 → 글
def math_token(xml):
    """워드 수식(OMML) → `$LaTeX$`, 문단 수식(oMathPara)은 `$$…$$`. 변환이 안 되면 예전처럼 글자만 ⟦…⟧."""
    display = xml.startswith("<m:oMathPara")
    parts = re.findall(r"<m:oMath[ >].*?</m:oMath>", xml, re.S) if display else [xml]
    tex = [mathtex.omml_to_latex(p) for p in parts]
    if tex and all(tex) and not any("$" in t or "\n" in t for t in tex):
        body = tex[0] if len(tex) == 1 else "\\begin{aligned} " + " \\\\ ".join(tex) + " \\end{aligned}"
        return ("$$%s$$" if display else "$%s$") % body
    flat = mathtex.omml_flat(xml)
    return "⟦" + flat + "⟧" if flat else ""


def run_text(r):
    """<w:r> 하나 → (글, 굵게, 기울임, 위첨자, 아래첨자). 탭은 빈칸, 줄바꿈은 \\n."""
    rpr = re.search(r"<w:rPr>(.*?)</w:rPr>", r, re.S)
    rpr = rpr.group(1) if rpr else ""
    b = bool(re.search(r"<w:b(?:\s[^>]*)?/>", rpr)) and 'w:val="0"' not in rpr and 'w:val="false"' not in rpr
    i = bool(re.search(r"<w:i(?:\s[^>]*)?/>", rpr)) and 'w:val="0"' not in rpr
    sup = 'w:val="superscript"' in rpr
    sub = 'w:val="subscript"' in rpr
    pieces = []
    for m in _TXT.finditer(r):
        if m.group(0) == "<w:tab/>":
            pieces.append(" ")
        elif m.group(0) == "<w:br/>":
            pieces.append("\n")
        else:
            pieces.append(m.group(1))
    t = "".join(pieces).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return t, b, i, sup, sub


def para_md(p, legacy=False):
    """<w:p> 하나를 마크다운 글로: **굵게**, *기울임*, ^위첨자^, _아래첨자_, 수식은 $LaTeX$.
    legacy = 예전 가져오기의 표기(수식을 글자만 ⟦…⟧ 로) — 그때 가져온 원고와 견줄 때."""
    out = []
    for r in _PIECE.findall(p):
        if r.startswith("<m:"):
            if legacy:
                for one in re.findall(r"<m:oMath[ >].*?</m:oMath>", r, re.S):
                    flat = mathtex.omml_flat(one)
                    if flat:
                        out.append((" ⟦" + flat + "⟧ ", False, False))
                continue
            tok = math_token(r)
            if tok:
                out.append((" " + tok + " ", False, False))
            continue
        t, b, i, sup, sub = run_text(r)
        if not t:
            continue
        if sup:
            t = "^" + t + "^"
        if sub:
            t = "_" + t + "_"
        out.append((t, b, i))
    merged = []   # 같은 서식의 이웃 런 합치기
    for t, b, i in out:
        if merged and merged[-1][1] == b and merged[-1][2] == i:
            merged[-1] = (merged[-1][0] + t, b, i)
        else:
            merged.append((t, b, i))
    txt = ""
    for t, b, i in merged:
        core = t.strip()
        if not core:
            txt += t
            continue
        lead = t[:len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        if b and i:
            core = "***" + core + "***"
        elif b:
            core = "**" + core + "**"
        elif i:
            core = "*" + core + "*"
        txt += lead + core + trail
    return txt.strip()


def plain_of(xml):
    """XML 조각의 글(서식 표시 없이) — para_md 에서 *,^,_ 표시만 뺀 것과 같다."""
    out = []
    for r in _PIECE.findall(xml):
        if r.startswith("<m:"):
            tok = math_token(r)
            if tok:
                out.append(" " + tok + " ")
        else:
            out.append(run_text(r)[0])
    return "".join(out)


# ---------------------------------------------------------------- XML 조각 다루기
def xml_children(inner):
    """XML 조각의 맨 위 자식들 → [(태그, 원문)]. 자식 사이의 글(공백)은 버린다."""
    out, depth, start, name = [], 0, None, None
    for m in _TAG.finditer(inner):
        if m.group(1):
            depth -= 1
            if depth == 0 and start is not None:
                out.append((name, inner[start:m.end()]))
                start = None
            depth = max(depth, 0)
        elif m.group(4):
            if depth == 0:
                out.append((m.group(2), m.group(0)))
        else:
            if depth == 0:
                start, name = m.start(), m.group(2)
            depth += 1
    return out


def para_parts(raw):
    """<w:p …>…</w:p> → (여는 태그, pPr 원문, [자식 (태그, 원문)])"""
    m = re.match(r"<w:p(?:\s[^>]*)?>", raw)
    kids = xml_children(raw[m.end():raw.rfind("</w:p>")])
    if kids and kids[0][0] == "w:pPr":
        return m.group(0), kids[0][1], kids[1:]
    return m.group(0), "", kids


def patchable(raw):
    """글상자 등으로 문단이 겹친 조각은 정규식이 중간에서 자르므로 손대지 않는다."""
    return len(re.findall(r"<w:p[ >]", raw)) == 1 and "<w:txbxContent" not in raw and raw.endswith("</w:p>")


def _units(children):
    """문단의 자식을 '단위'로 — 필드(fldChar begin … end)는 쪼개면 깨지므로 한 덩어리. [{xml, plain, field}]"""
    units, k = [], 0
    while k < len(children):
        tag, raw = children[k]
        if tag == "w:r" and 'w:fldCharType="begin"' in raw:
            depth, j = 0, k
            while j < len(children):
                depth += children[j][1].count('w:fldCharType="begin"') - children[j][1].count('w:fldCharType="end"')
                if depth <= 0:
                    break
                j += 1
            j = min(j, len(children) - 1)
            xml = "".join(r for _, r in children[k:j + 1])
            units.append({"xml": xml, "plain": plain_of(xml), "field": True})
            k = j + 1
            continue
        units.append({"xml": raw, "plain": plain_of(raw), "field": tag == "w:fldSimple"})
        k += 1
    return units


# ---------------------------------------------------------------- 글 → 워드 런
def md_units(md):
    """마크다운 글 → 단위 목록. ('c', 글자, (굵게, 기울임, 위첨자, 아래첨자)) 또는 ('m', LaTeX, 문단 수식인가)."""
    out = []
    for kind, s in mathtex.split_math(md or ""):
        if kind != "text":
            out.append(("m", s, kind == "display"))
            continue
        for tok in _EMPH.split(s):
            if not tok:
                continue
            b = i = False
            if _EMPH.fullmatch(tok):
                if tok.startswith("***"):
                    b = i = True; tok = tok[3:-3]
                elif tok.startswith("**"):
                    b = True; tok = tok[2:-2]
                else:
                    i = True; tok = tok[1:-1]
            for part in _SS.split(tok):
                if not part:
                    continue
                sup = sub = False
                if _SS.fullmatch(part) or (part.startswith("_") and part.endswith("_") and len(part) > 2 and "_" not in part[1:-1] and " " not in part):
                    sup, sub = part[0] == "^", part[0] == "_"
                    part = part[1:-1]
                out.extend(("c", ch, (b, i, sup, sub)) for ch in part)
    return out


def unit_plain(u):
    if u[0] == "c":
        return u[1]
    return ("$$%s$$" if u[2] else "$%s$") % u[1]


def _rpr(base, fmt, color=None, keep=False):
    """기본 rPr(안쪽 XML)에 굵게·기울임·첨자·색을 얹어 정해진 순서로. keep = 기본의 굵게·기울임을 지우지 않는다(옆 글 모양을 이어받을 때)."""
    kids = [(t.split(":")[-1], raw) for t, raw in xml_children(base or "")]
    drop = (set() if keep else {"b", "bCs", "i", "iCs"}) | {"vertAlign"} | ({"color"} if color else set())
    kids = [(t, raw) for t, raw in kids if t not in drop]
    b, i, sup, sub = fmt
    have = {t for t, _ in kids}
    b, i = b and "b" not in have, i and "i" not in have
    if b:
        kids += [("b", "<w:b/>"), ("bCs", "<w:bCs/>")]
    if i:
        kids += [("i", "<w:i/>"), ("iCs", "<w:iCs/>")]
    if color:
        kids.append(("color", '<w:color w:val="%s"/>' % color))
    if sup or sub:
        kids.append(("vertAlign", '<w:vertAlign w:val="%s"/>' % ("superscript" if sup else "subscript")))
    order = {t: k for k, t in enumerate(_RPR_ORDER)}
    kids = sorted(enumerate(kids), key=lambda x: (order.get(x[1][0], 999), x[0]))
    inner = "".join(raw for _, (_, raw) in kids)
    return "<w:rPr>%s</w:rPr>" % inner if inner else ""


def runs_xml(units, base_rpr="", color=None, keep=False, mask=None):
    """단위 목록 → 워드 런 XML. 같은 서식의 이웃 글자는 한 런으로. mask = 단위마다 색을 입힐지(없으면 전부)."""
    out, buf, cur = [], [], None

    def flush():
        if not buf:
            return
        parts = "".join(buf).split("\n")
        body = "<w:br/>".join('<w:t xml:space="preserve">%s</w:t>' % esc(p) if p else "" for p in parts)
        out.append("<w:r>%s%s</w:r>" % (_rpr(base_rpr, cur[0], color if cur[1] else None, keep), body))
        del buf[:]
    for k, u in enumerate(units):
        if u[0] == "c":
            key = (u[2], bool(color) and (mask is None or mask[k]))
            if cur != key:
                flush(); cur = key
            buf.append(u[1])
        else:
            flush(); cur = None
            out.append(mathtex.to_omml(u[1], u[2]))
    flush()
    return "".join(out)


def _base_rpr(units_xml):
    """글이 든 첫 런의 rPr 안쪽 (글꼴·크기·언어를 이어받으려고)."""
    for xml in units_xml:
        for r in re.findall(r"<w:r[ >].*?</w:r>", xml, re.S):
            if "<w:t" in r:
                m = re.search(r"<w:rPr>(.*?)</w:rPr>", r, re.S)
                return m.group(1) if m else ""
    return ""


def patch_paragraph(raw, new_text, color=None, inherit=False):
    """문단 원문에 새 글을 반영한다. 글자가 그대로인 런(서식·그림·수식·인용 필드)은 원문 그대로 두고, 달라진 곳만 새 런으로 바꾼다.
    inherit = 새 글이 바로 앞 글의 모양(굵게 등)을 이어받는다 — 서식 표시 없이 글만 받는 제목·캡션용.
    → (새 원문 또는 None(글자가 같다), 정보 {fields: 일반 글자로 바뀐 필드 수})"""
    open_tag, ppr, children = para_parts(raw)
    units = _units(children)
    old = "".join(u["plain"] for u in units)
    lead = old[:len(old) - len(old.lstrip())] if old.strip() else ""
    trail = old[len(old.rstrip()):] if old.strip() else ""
    f0 = (False, False, False, False)
    nu = [("c", ch, f0) for ch in lead] + md_units(new_text) + [("c", ch, f0) for ch in trail]
    nlen = [len(unit_plain(u)) for u in nu]
    nstart, pos = [], 0
    for n in nlen:
        nstart.append(pos); pos += n
    new = "".join(unit_plain(u) for u in nu)
    if old == new:
        return None, {"fields": 0}
    blocks = [m for m in difflib.SequenceMatcher(None, old, new, autojunk=False).get_matching_blocks() if m.size]
    nb = set(nstart) | {len(new)}          # 새 글에서 단위(글자·수식)의 경계
    same = bytearray(len(new))             # 워드와 같은 글자 (색을 입히지 않는다). 우연히 맞은 한두 글자는 바뀐 것으로
    for m in blocks:
        if m.size >= 3:
            same[m.b:m.b + m.size] = b"\x01" * m.size

    def place(s0, e0):
        """옛 글의 [s0, e0) 이 통째로 같은 구간 안에 있으면 새 글에서의 시작 자리."""
        for m in blocks:
            if m.a <= s0 and e0 <= m.a + m.size:
                return s0 - m.a + m.b
            if m.a > s0:
                break
        return None

    para_base = _base_rpr([u["xml"] for u in units])
    state = {"ui": 0, "cur": 0, "base": None}

    def gen(upto):
        seg, mask = [], []
        while state["ui"] < len(nu) and nstart[state["ui"]] < upto:
            k = state["ui"]
            seg.append(nu[k]); mask.append(not all(same[nstart[k]:nstart[k] + nlen[k]]))
            state["ui"] += 1
        state["cur"] = max(state["cur"], upto)
        base = state["base"] if state["base"] is not None else para_base
        return runs_xml(seg, base, color, inherit, mask) if seg else ""

    out, fields, pos = [], 0, 0
    for u in units:
        s0, e0 = pos, pos + len(u["plain"])
        pos = e0
        p = place(s0, e0)
        if s0 == e0:   # 글자가 없는 것(그림·책갈피·주석 표시 …): 자리가 남아 있으면 그 자리에, 아니면 물건만 남긴다
            if p is not None and p >= state["cur"] and p in nb:
                out.append(gen(p)); out.append(u["xml"])
            elif _OBJ.search(u["xml"]):
                out.append(u["xml"])
            continue
        if p is not None and p >= state["cur"] and p in nb and (p + e0 - s0) in nb:
            out.append(gen(p)); out.append(u["xml"])
            while state["ui"] < len(nu) and nstart[state["ui"]] < p + e0 - s0:
                state["ui"] += 1
            state["cur"] = p + e0 - s0
            r = _base_rpr([u["xml"]])
            if r or "<w:t" in u["xml"]:
                state["base"] = r
        elif u["field"] and u["plain"].strip():
            fields += 1
    out.append(gen(len(new)))
    return open_tag + ppr + "".join(out) + "</w:p>", {"fields": fields}


DISP_EQ = re.compile(r"^\$\$(.+?)\$\$\s*(?:\(\s*([\w.\-]+)\s*\))?\s*[.,]?$", re.S)   # '$$ 수식 $$' 또는 '$$ 수식 $$ (3)' 뿐인 문단


def display_block(tex, num="", base_rpr="", color=None):
    """문단 수식. 번호가 있으면 테두리 없는 표(빈칸 | 수식 | 번호) — 워드 원고에서 흔히 쓰는 배치이고 수식이 제 크기로 나온다.
    (수식과 번호를 한 문단에 두면 워드가 수식을 줄 안 수식으로 작게 그린다)"""
    om = mathtex.to_omml(tex, True)
    if not num:
        return "<w:p>%s</w:p>" % om
    cell = lambda w, inner: '<w:tc><w:tcPr><w:tcW w:w="%d" w:type="pct"/><w:vAlign w:val="center"/></w:tcPr>%s</w:tc>' % (w, inner)
    numrun = "<w:r>%s<w:t>(%s)</w:t></w:r>" % (_rpr(base_rpr, (False, False, False, False), color), esc(num))
    return ('<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblLayout w:type="fixed"/></w:tblPr>'
            '<w:tblGrid><w:gridCol w:w="900"/><w:gridCol w:w="7200"/><w:gridCol w:w="900"/></w:tblGrid><w:tr>%s%s%s</w:tr></w:tbl>'
            % (cell(500, "<w:p/>"), cell(4000, "<w:p>%s</w:p>" % om), cell(500, '<w:p><w:pPr><w:jc w:val="right"/></w:pPr>%s</w:p>' % numrun)))


def eq_table_md(tbl):
    """워드 표가 '수식 | 번호' 한 줄짜리(위의 배치)면 '$$…$$ (3)' 글로, 아니면 None."""
    rows = re.findall(r"<w:tr[ >].*?</w:tr>", tbl, re.S)
    if len(rows) != 1:
        return None
    cells = [" ".join(x for x in (para_md(cp) for cp in re.findall(r"<w:p[ >].*?</w:p>", tc, re.S)) if x).strip()
             for tc in re.findall(r"<w:tc[ >].*?</w:tc>", rows[0], re.S)]
    nz = [c for c in cells if c]
    if not nz or len(nz) > 2:
        return None
    m = re.match(r"^\$\$?(.+?)\$?\$$", nz[0], re.S)
    if not m or "$" in m.group(1):
        return None
    if len(nz) == 2:
        n = re.match(r"^\(?\s*([\w.\-]{1,8})\s*\)?$", nz[1])
        if not n:
            return None
        return "$$%s$$ (%s)" % (m.group(1).strip(), n.group(1))
    return "$$%s$$" % m.group(1).strip()


def new_paragraph(text, like_raw="", color=None, inherit=False):
    """like_raw 문단의 모양(문단 속성·글꼴)을 본뜬 새 문단. 문단 수식뿐인 글은 수식 배치(display_block)로."""
    ppr = base = ""
    if like_raw:
        _, ppr, children = para_parts(like_raw)
        ppr = re.sub(r"<w:sectPr[ >].*?</w:sectPr>|<w:sectPr[^>]*/>", "", ppr, flags=re.S)   # 구역 나누기는 복제하지 않는다
        base = _base_rpr([r for _, r in children])
    m = DISP_EQ.match((text or "").strip())
    if m:
        return display_block(m.group(1).strip(), m.group(2) or "", base, color)
    return "<w:p>%s%s</w:p>" % (ppr, runs_xml(md_units(text), base, color, inherit))


def emptied_paragraph(raw):
    """문단을 지운다. 구역 나누기나 그림이 든 문단은 껍데기와 그 물건만 남긴다 → 남길 원문(없으면 빈 문자열)."""
    open_tag, ppr, children = para_parts(raw)
    objs = "".join(r for _, r in children if _OBJ.search(r) and not plain_of(r).strip())
    if "<w:sectPr" in ppr or re.search(r"<w:drawing|<w:pict|<w:object", objs):
        return open_tag + ppr + objs + "</w:p>"
    return ""


# ---------------------------------------------------------------- .docx 다시 쓰기
_STORED = (".png", ".jpg", ".jpeg", ".gif", ".zip", ".mp4")


def write_docx(src, out, document_xml):
    """src 의 word/document.xml 만 바꾼 사본을 out 에 쓴다 (임시 파일에 쓴 뒤 바꿔치기). 워드가 out 을 열고 있으면 PermissionError."""
    tmp = "%s.%d.tmp" % (out, os.getpid())
    try:
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for it in zin.infolist():
                data = document_xml.encode("utf-8") if it.filename == "word/document.xml" else zin.read(it.filename)
                stored = it.filename.lower().endswith(_STORED) or it.compress_type == zipfile.ZIP_STORED
                ni = zipfile.ZipInfo(it.filename, it.date_time)
                ni.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
                ni.external_attr = it.external_attr
                zout.writestr(ni, data, compresslevel=None if stored else (6 if it.filename.endswith((".xml", ".rels")) else 1))
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
