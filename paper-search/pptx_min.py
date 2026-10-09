# -*- coding: utf-8 -*-
"""pptx_min — 표준 라이브러리만으로 .pptx 를 쓴다 (python-pptx 없이; 포터블판에 의존성을 더하지 않으려고).
공부의 「발표 자료」 가 쓴다: 제목 슬라이드 · 내용 슬라이드(제목 + 글머리표 + 오른쪽 그림 + 출처 줄) · 참고문헌 슬라이드, 발표자 메모(notes).
16:9. 글꼴은 맑은 고딕(설치된 PC 에서 그 글꼴로 보인다 — 파일에는 이름만 든다).

build(path, deck)  deck = {title, subtitle, slides: [{h, bullets: [str], fig: {path, caption} | None, foot: str, note: str}], refs: [str], footer: str}
"""
import io, os, re, struct, zipfile, time

EMU = 914400                                   # 1 inch
W, H = 12192000, 6858000                       # 16:9 (13.333 × 7.5 in)
FONT = "Malgun Gothic"
ACCENT = "1F5FBF"                              # 파랑 하나 (ui.css 의 강조색에 가깝게)
GRAY = "6B6B6B"
TEXT = "1A1A1A"
NS_P = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def esc(t):
    return str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def png_size(path):
    """PNG 의 가로·세로 (IHDR). 못 읽으면 (4, 3)."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", head[16:24])
            return max(1, w), max(1, h)
        if head[:2] == b"\xff\xd8":   # JPEG: SOF 를 찾는다
            with open(path, "rb") as f:
                data = f.read()
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return max(1, w), max(1, h)
                seg = struct.unpack(">H", data[i + 2:i + 4])[0]
                i += 2 + seg
    except Exception:
        pass
    return 4, 3


def inch(x):
    return int(round(x * EMU))


# ---------- 조각 ----------
def _rpr(sz, bold=False, color=TEXT, italic=False):
    return ('<a:rPr lang="ko-KR" altLang="en-US" sz="%d"%s%s dirty="0"><a:solidFill><a:srgbClr val="%s"/></a:solidFill>'
            '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface="%s"/></a:rPr>' % (sz * 100, ' b="1"' if bold else "", ' i="1"' if italic else "", color, FONT, FONT, FONT))


def _run(text, sz, bold=False, color=TEXT, italic=False):
    return "<a:r>%s<a:t>%s</a:t></a:r>" % (_rpr(sz, bold, color, italic), esc(text))


def _para(runs, align="l", bullet=False, level=0, space_after=0, line=None):
    ppr = '<a:pPr algn="%s"' % align
    if bullet:
        ppr += ' marL="%d" indent="-%d" lvl="%d"' % (inch(0.32) * (level + 1), inch(0.28), level)
    ppr += ">"
    if line:
        ppr += '<a:lnSpc><a:spcPct val="%d"/></a:lnSpc>' % int(line * 100000)   # 1/1000 % — 100% = 100000
    if space_after:
        ppr += '<a:spcAft><a:spcPts val="%d"/></a:spcAft>' % int(space_after * 100)
    ppr += ('<a:buClr><a:srgbClr val="%s"/></a:buClr><a:buFont typeface="Arial"/><a:buChar char="%s"/>' % (ACCENT, "•" if level == 0 else "–")) if bullet else "<a:buNone/>"
    ppr += "</a:pPr>"
    return "<a:p>%s%s</a:p>" % (ppr, runs)


def _sp(id_, name, x, y, w, h, paras, anchor="t", autofit=True, fill=None, line=None, wrap=True):
    body = ('<p:txBody><a:bodyPr wrap="%s" lIns="0" tIns="0" rIns="0" bIns="0" anchor="%s">%s</a:bodyPr><a:lstStyle/>%s</p:txBody>'
            % ("square" if wrap else "none", anchor, '<a:normAutofit/>' if autofit else "", paras or "<a:p><a:endParaRPr lang=\"ko-KR\"/></a:p>"))
    sppr = '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>%s%s</p:spPr>' % (
        x, y, w, h, ('<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % fill) if fill else "<a:noFill/>",
        ('<a:ln w="%d"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:ln>' % line) if line else "")
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>%s%s</p:sp>' % (id_, esc(name), sppr, body))


def _rect(id_, name, x, y, w, h, fill):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="%s"/></a:solidFill><a:ln><a:noFill/></a:ln></p:spPr>'
            '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ko-KR"/></a:p></p:txBody></p:sp>' % (id_, esc(name), x, y, w, h, fill))


def _pic(id_, name, rid, x, y, w, h):
    return ('<p:pic><p:nvPicPr><p:cNvPr id="%d" name="%s"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            '<p:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            '<p:spPr><a:xfrm><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>' % (id_, esc(name), rid, x, y, w, h))


def _slide_xml(shapes):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sld %s><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>' % (NS_P, "".join(shapes)))


def _notes_xml(text, slide_rid="rId1"):
    paras = "".join(_para(_run(t, 12), space_after=4) for t in (text or "").split("\n") if t.strip()) or "<a:p><a:endParaRPr lang=\"ko-KR\"/></a:p>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:notes %s><p:cSld><p:spTree>'
            '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
            '<p:txBody><a:bodyPr/><a:lstStyle/>%s</p:txBody></p:sp>'
            '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>' % (NS_P, paras))


# ---------- 고정 부품 (마스터·레이아웃·테마·노트 마스터) ----------
_THEME = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Athenaeum"><a:themeElements>'
          '<a:clrScheme name="Athenaeum"><a:dk1><a:srgbClr val="1A1A1A"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
          '<a:accent1><a:srgbClr val="%s"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="4472C4"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
          '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
          '<a:fontScheme name="Athenaeum"><a:majorFont><a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
          '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
          '<a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst>'
          '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
          '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
          '</a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>' % (ACCENT, FONT, FONT, FONT, FONT))

_MASTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldMaster %s><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>'
           '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
           '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
           '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
           '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="2800"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles></p:sldMaster>' % NS_P)

_LAYOUT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldLayout %s type="blank" preserve="1"><p:cSld name="Blank"><p:spTree>'
           '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
           '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>' % NS_P)

_NOTES_MASTER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:notesMaster %s><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>'
                 '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                 '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr>'
                 '<p:spPr><a:xfrm><a:off x="685800" y="1143000"/><a:ext cx="5486400" cy="3086100"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln w="12700"><a:solidFill><a:prstClr val="black"/></a:solidFill></a:ln></p:spPr></p:sp>'
                 '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="body" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr>'
                 '<p:spPr><a:xfrm><a:off x="685800" y="4400550"/><a:ext cx="5486400" cy="3600450"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="ko-KR"/></a:p></p:txBody></p:sp>'
                 '</p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
                 '<p:notesStyle><a:lvl1pPr><a:defRPr sz="1200"/></a:lvl1pPr></p:notesStyle></p:notesMaster>' % NS_P)

_RELS_ROOT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
              '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
              '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
              '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>')


def _rels(items):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join('<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/%s" Target="%s"/>' % (rid, typ, tgt) for rid, typ, tgt in items)
            + "</Relationships>")


def _fit(img_w, img_h, box_w, box_h):
    """그림을 상자 안에 비율대로 (가운데 맞춤 좌표는 호출 쪽에서)"""
    r = min(box_w / float(img_w), box_h / float(img_h))
    return int(img_w * r), int(img_h * r)


def _wrap_len(text, per_line):
    """글머리표 한 줄의 글자 수로 줄 수를 어림(글꼴 크기를 고르려고)"""
    return max(1, -(-len(text) // per_line))


# ---------- 본체 ----------
def build(path, deck):
    """deck 를 path(.pptx) 로 쓴다. 돌려주는 값: {slides: n, pics: n}"""
    slides_xml, slide_rels, media, notes = [], [], {}, []
    T = str(deck.get("title") or "발표 자료")
    sub = str(deck.get("subtitle") or "")
    footer = str(deck.get("footer") or "")
    m = [inch(0.6), inch(0.45)]   # 좌우·상하 여백

    # 1) 제목 슬라이드
    shapes = [_rect(2, "accent", 0, 0, inch(0.35), H, ACCENT),
              _sp(3, "title", inch(1.0), inch(2.3), W - inch(2.0), inch(1.9), _para(_run(T, 36, True), line=1.1), anchor="b"),
              _sp(4, "subtitle", inch(1.0), inch(4.35), W - inch(2.0), inch(1.2), "".join(_para(_run(s, 16, False, GRAY), space_after=4) for s in sub.split("\n") if s.strip()), anchor="t")]
    if footer:
        shapes.append(_sp(5, "foot", inch(1.0), H - inch(0.7), W - inch(2.0), inch(0.4), _para(_run(footer, 10, False, GRAY)), anchor="b"))
    slides_xml.append(_slide_xml(shapes)); slide_rels.append([]); notes.append(str(deck.get("title_note") or ""))

    # 2) 내용 슬라이드
    total = len(deck.get("slides") or [])
    for n, s in enumerate(deck.get("slides") or [], 1):
        shapes, rels = [], []
        sid = 2
        shapes.append(_sp(sid, "title", m[0], m[1], W - 2 * m[0], inch(0.9), _para(_run(s.get("h") or "", 26, True), line=1.05), anchor="b")); sid += 1
        shapes.append(_rect(sid, "rule", m[0], inch(1.42), inch(0.9), inch(0.04), ACCENT)); sid += 1
        fig = s.get("fig") if isinstance(s.get("fig"), dict) and s["fig"].get("path") and os.path.isfile(s["fig"]["path"]) else None
        body_w = W - 2 * m[0] - (inch(4.9) if fig else 0)
        bullets = [str(b) for b in (s.get("bullets") or []) if str(b).strip()]
        # 글자 크기: 줄 수에 맞춰 (대강 — 16:9 에서 18pt 는 한 줄에 한글 38자쯤, 그림이 있으면 24자쯤)
        per = 24 if fig else 40
        lines = sum(_wrap_len(b, per) for b in bullets)
        sz = 18 if lines <= 10 else 16 if lines <= 13 else 14
        paras = "".join(_para(_run(b, sz), bullet=True, space_after=7, line=1.12) for b in bullets)
        shapes.append(_sp(sid, "body", m[0], inch(1.65), body_w, H - inch(1.65) - inch(0.85), paras, anchor="t")); sid += 1
        if fig:
            iw, ih = png_size(fig["path"])
            box_w, box_h = inch(4.5), inch(3.9)
            w, h = _fit(iw, ih, box_w, box_h)
            x = W - m[0] - box_w + (box_w - w) // 2
            y = inch(1.65)
            rid = "rId%d" % (len(rels) + 2)
            ext = os.path.splitext(fig["path"])[1].lower() or ".png"
            mname = "image%d%s" % (len(media) + 1, ext)
            media[mname] = fig["path"]
            rels.append((rid, "image", "../media/" + mname))
            shapes.append(_pic(sid, "fig", rid, x, y, w, h)); sid += 1
            cap = str(fig.get("caption") or "")
            if cap:
                shapes.append(_sp(sid, "caption", W - m[0] - box_w, y + h + inch(0.08), box_w, inch(1.2), _para(_run(cap, 10, False, GRAY), line=1.1), anchor="t")); sid += 1
        foot = str(s.get("foot") or "")
        if foot:
            shapes.append(_sp(sid, "foot", m[0], H - inch(0.72), W - 2 * m[0] - inch(0.8), inch(0.45), _para(_run(foot, 10, False, GRAY), line=1.1), anchor="b")); sid += 1
        shapes.append(_sp(sid, "num", W - m[0] - inch(0.8), H - inch(0.6), inch(0.8), inch(0.3), _para(_run("%d / %d" % (n, total + 1), 10, False, GRAY), align="r"), anchor="b")); sid += 1
        slides_xml.append(_slide_xml(shapes)); slide_rels.append(rels); notes.append(str(s.get("note") or ""))

    # 3) 참고문헌 슬라이드 (길면 둘로)
    refs = [str(r) for r in (deck.get("refs") or []) if str(r).strip()]
    chunks = [refs[i:i + 12] for i in range(0, len(refs), 12)] or [[]]
    for ci, chunk in enumerate(chunks):
        shapes = [_sp(2, "title", m[0], m[1], W - 2 * m[0], inch(0.9), _para(_run("참고문헌" + (" (%d/%d)" % (ci + 1, len(chunks)) if len(chunks) > 1 else ""), 26, True)), anchor="b"),
                  _rect(3, "rule", m[0], inch(1.42), inch(0.9), inch(0.04), ACCENT),
                  _sp(4, "body", m[0], inch(1.65), W - 2 * m[0], H - inch(2.4), "".join(_para(_run(r, 11 if len(chunk) > 8 else 12), space_after=4, line=1.1) for r in chunk) or _para(_run("(없음)", 12, False, GRAY)), anchor="t")]
        slides_xml.append(_slide_xml(shapes)); slide_rels.append([]); notes.append("")
    nslides = len(slides_xml)

    # 4) 꾸러미
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>', '<Default Extension="xml" ContentType="application/xml"/>',
          '<Default Extension="png" ContentType="image/png"/>', '<Default Extension="jpg" ContentType="image/jpeg"/>', '<Default Extension="jpeg" ContentType="image/jpeg"/>',
          '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
          '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
          '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
          '<Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>',
          '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
          '<Override PartName="/ppt/theme/theme2.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
          '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>',
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
          '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>']
    for i in range(1, nslides + 1):
        ct.append('<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % i)
        ct.append('<Override PartName="/ppt/notesSlides/notesSlide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>' % i)
    ct.append("</Types>")

    pres = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentation %s saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:notesMasterIdLst><p:notesMasterId r:id="rId%d"/></p:notesMasterIdLst>'
            '<p:sldIdLst>%s</p:sldIdLst><p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/>'
            '<p:defaultTextStyle><a:defPPr><a:defRPr lang="ko-KR"/></a:defPPr></p:defaultTextStyle></p:presentation>'
            % (NS_P, nslides + 2, "".join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, i + 2) for i in range(nslides)), W, H))
    pres_rels = [("rId1", "slideMaster", "slideMasters/slideMaster1.xml")] + [("rId%d" % (i + 2), "slide", "slides/slide%d.xml" % (i + 1)) for i in range(nslides)] + \
                [("rId%d" % (nslides + 2), "notesMaster", "notesMasters/notesMaster1.xml"), ("rId%d" % (nslides + 3), "theme", "theme/theme1.xml"), ("rId%d" % (nslides + 4), "presProps", "presProps.xml")]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>Athenaeum</dc:creator><cp:lastModifiedBy>Athenaeum</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>' % (esc(T), now, now))
    app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Athenaeum</Application><Slides>%d</Slides></Properties>' % nslides)
    pres_props = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentationPr %s/>' % NS_P

    tmp = path + ".%d.tmp" % os.getpid()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", _RELS_ROOT)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", _rels(pres_rels))
        z.writestr("ppt/presProps.xml", pres_props)
        z.writestr("ppt/theme/theme1.xml", _THEME)
        z.writestr("ppt/theme/theme2.xml", _THEME)
        z.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "theme", "../theme/theme1.xml")]))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]))
        z.writestr("ppt/notesMasters/notesMaster1.xml", _NOTES_MASTER)
        z.writestr("ppt/notesMasters/_rels/notesMaster1.xml.rels", _rels([("rId1", "theme", "../theme/theme2.xml")]))
        for i, (sx, rels) in enumerate(zip(slides_xml, slide_rels), 1):
            z.writestr("ppt/slides/slide%d.xml" % i, sx)
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % i, _rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")] + rels + [("rId%d" % (len(rels) + 2), "notesSlide", "../notesSlides/notesSlide%d.xml" % i)]))
            z.writestr("ppt/notesSlides/notesSlide%d.xml" % i, _notes_xml(notes[i - 1]))
            z.writestr("ppt/notesSlides/_rels/notesSlide%d.xml.rels" % i, _rels([("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"), ("rId2", "slide", "../slides/slide%d.xml" % i)]))
        for mname, src in media.items():
            z.write(src, "ppt/media/" + mname)
    os.replace(tmp, path)
    return {"slides": nslides, "pics": len(media)}


if __name__ == "__main__":   # python pptx_min.py out.pptx  — 보기용 두 장
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "pptx_min_test.pptx"
    print(build(out, {"title": "시험 발표", "subtitle": "pptx_min 보기\n2026-10-09", "footer": "Athenaeum",
                      "slides": [{"h": "첫 슬라이드", "bullets": ["글머리표 하나", "둘 — 조금 더 긴 문장을 넣어 줄바꿈이 어떻게 되는지 본다, 숫자 12.5 µm 와 영어 built-up edge 도"], "foot": "출처: [1] Davis 2020", "note": "발표자 메모"}],
                      "refs": ["[1] Davis J. et al. (2020) …"]}))
