# -*- coding: utf-8 -*-
"""수식 변환 — 표준 라이브러리만.

  to_mathml(tex, display)   LaTeX(부분집합) → MathML   (원고 화면 미리보기. 브라우저가 MathML 을 직접 그린다)
  to_omml(tex, display)     LaTeX(부분집합) → OMML     (워드 수식. 내보내기·워드 반영)
  omml_to_latex(xml)        OMML → LaTeX               (워드에서 가져오기)

가공 논문에 나오는 수식(분수·첨자·근호·합·적분·윗줄·점·괄호·행렬·경우 나누기·그리스 문자)을 다룬다.
모르는 명령은 변환을 멈추지 않고 그 이름을 글자로 남긴다(화면에서는 빨간 글자).
"""
import re
import unicodedata
import xml.etree.ElementTree as ET

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "varepsilon": "ε", "epsilon": "ϵ", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "varphi": "φ", "phi": "ϕ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ",
    "Psi": "Ψ", "Omega": "Ω",
}
OPS = {
    "times": "×", "cdot": "⋅", "div": "÷", "pm": "±", "mp": "∓", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "sim": "∼", "simeq": "≃", "cong": "≅", "equiv": "≡", "propto": "∝", "ll": "≪", "gg": "≫",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←", "leftrightarrow": "↔", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "Leftrightarrow": "⇔", "uparrow": "↑", "downarrow": "↓", "mapsto": "↦",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "perp": "⊥", "parallel": "∥", "angle": "∠",
    "cdots": "⋯", "ldots": "…", "dots": "…", "vdots": "⋮", "ddots": "⋱", "ast": "∗", "star": "⋆", "circ": "∘", "bullet": "∙",
    "mid": "∣", "forall": "∀", "exists": "∃", "neg": "¬", "wedge": "∧", "vee": "∨", "oplus": "⊕", "otimes": "⊗",
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉", "vert": "|", "Vert": "‖",
    "prime": "′", "colon": ":", "lt": "<", "gt": ">",
}
IDENT = {"infty": "∞", "partial": "∂", "nabla": "∇", "ell": "ℓ", "hbar": "ℏ", "degree": "°", "emptyset": "∅", "Re": "ℜ", "Im": "ℑ"}
BIG = {"sum": "∑", "prod": "∏", "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮", "bigcup": "⋃", "bigcap": "⋂"}
_LIMITS_BIG = set("∑∏⋃⋂")
FUNCS = ("sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth ln log lg exp lim max min sup inf det arg deg dim gcd "
         "sgn erf tr rank").split()
_LIMITS_FUNC = {"lim", "max", "min", "sup", "inf"}
# 강조 기호: 이름 → (MathML 글자, OMML 결합 문자)
ACCENT = {"hat": ("^", "\u0302"), "widehat": ("^", "\u0302"), "tilde": ("~", "\u0303"), "widetilde": ("~", "\u0303"),
          "bar": ("¯", "\u0304"), "dot": ("˙", "\u0307"), "ddot": ("¨", "\u0308"), "vec": ("→", "\u20d7"),
          "check": ("ˇ", "\u030c"), "breve": ("˘", "\u0306"), "acute": ("´", "\u0301"), "grave": ("`", "\u0300")}
SPACE = {",": 0.167, ":": 0.222, ">": 0.222, ";": 0.278, " ": 0.33, "quad": 1.0, "qquad": 2.0, "enspace": 0.5, "thinspace": 0.167}
_IGNORE = set("displaystyle textstyle scriptstyle scriptscriptstyle limits nolimits nonumber notag big Big bigg Bigg bigl bigr Bigl Bigr "
              "biggl biggr Biggl Biggr bigm Bigm left. right. ! allowbreak relax protect hfill noindent centering".split())
_STYLE = {"mathbf": "b", "textbf": "b", "bf": "b", "boldsymbol": "bi", "bm": "bi", "mathcal": "cal", "mathscr": "cal",
          "mathbb": "bb", "mathit": "it", "textit": "it", "mathsf": "rm", "mathtt": "rm"}
_TEXT = {"text", "textrm", "mbox", "textnormal", "hbox", "textup"}
_UPRIGHT = {"mathrm", "operatorname", "rm", "mathup"}
_MATRIX = {"matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"),
           "Vmatrix": ("‖", "‖"), "array": ("", ""), "smallmatrix": ("", "")}
_ALIGN = {"aligned", "align", "align*", "split", "gathered", "gather", "gather*", "eqnarray", "equation", "equation*", "alignedat"}

_TOK = re.compile(r"\\[A-Za-z]+\*?|\\.|[{}^_&~]|\d+(?:\.\d+)?|\s+|.", re.S)


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- LaTeX → 나무
class _Parser:
    def __init__(self, s):
        self.t = _TOK.findall(s or "")
        self.i = 0

    def peek(self):
        while self.i < len(self.t) and self.t[self.i].isspace():
            self.i += 1
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        items = []
        while self.peek() is not None:
            items.extend(self.row()[1])
            if self.peek() is not None:   # 짝 없는 } & \\ \right \end — 건너뛴다
                self.next()
        return ("row", items)

    def row(self, stop=()):
        items = []
        while True:
            tok = self.peek()
            if tok is None or tok == "}" or tok in stop or (tok in ("&", "\\\\", "\\right", "\\end") and not stop):
                break
            a = self.atom()
            if a is None:
                continue
            items.append(self.scripts(a))
        return ("row", items)

    def raw_arg(self):
        """{ … } 안의 글을 그대로 (공백 유지). 여는 중괄호가 없으면 토큰 하나."""
        if self.peek() != "{":
            return self.next() or ""
        self.next()
        depth, out = 1, []
        while self.i < len(self.t):
            tok = self.t[self.i]
            self.i += 1
            if tok == "{":
                depth += 1
            elif tok == "}":
                depth -= 1
                if depth == 0:
                    break
            out.append(tok)
        return "".join(out)

    def arg(self):
        tok = self.peek()
        if tok is None or tok == "}":
            return ("row", [])
        a = self.atom()
        return a if a is not None else ("row", [])

    def delim(self):
        tok = self.next() or ""
        if tok == ".":
            return ""
        if tok.startswith("\\"):
            name = tok[1:]
            return {"{": "{", "}": "}", "|": "‖", "lbrace": "{", "rbrace": "}", "lbrack": "[", "rbrack": "]"}.get(name) or OPS.get(name) or ""
        return tok

    def scripts(self, base):
        sub = sup = None
        while True:
            tok = self.peek()
            if tok == "_" and sub is None:
                self.next(); sub = self.arg()
            elif tok == "^" and sup is None:
                self.next(); sup = self.arg()
            elif tok == "'" and sup is None:
                n = 0
                while self.peek() == "'":
                    self.next(); n += 1
                sup = ("o", "′" * n)
            else:
                break
        if sub is None and sup is None:
            return base
        return ("ss", base, sub, sup)

    def atom(self):
        tok = self.next()
        if tok is None:
            return None
        if tok == "{":
            r = self.row()
            if self.peek() == "}":
                self.next()
            return r
        if tok in ("^", "_"):
            self.i -= 1
            return self.scripts(("row", []))
        if tok == "~":
            return ("space", 0.33)
        if tok == "&":
            return None
        if tok.startswith("\\"):
            return self.command(tok[1:])
        if tok[0].isdigit():
            return ("n", tok)
        if tok == "-":
            return ("o", "−")
        if tok == "*":
            return ("o", "∗")
        if tok.isalpha():
            if tok.isascii() or "\u0370" <= tok <= "\u03ff":
                return ("i", tok)
            return ("t", tok)   # 한글 등
        return ("o", tok)

    def command(self, name):
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            a = self.arg(); b = self.arg()
            return ("frac", a, b)
        if name == "binom":
            a = self.arg(); b = self.arg()
            return ("d", "(", ")", ("row", [("m", "matrix", [[a], [b]])]))
        if name == "sqrt":
            idx = None
            if self.peek() == "[":
                self.next()
                idx = self.row(stop=("]",))
                if self.peek() == "]":
                    self.next()
            return ("sqrt", self.arg(), idx)
        if name in ACCENT:
            return ("acc", name, self.arg())
        if name in ("overline", "underline"):
            return ("bar", "top" if name == "overline" else "bot", self.arg())
        if name in _TEXT:
            return ("t", self.raw_arg())
        if name in _UPRIGHT:
            raw = self.raw_arg()
            if re.search(r"[\\^_{}]", raw):
                return ("style", "rm", _Parser(raw).parse())
            return ("rm", raw.strip())
        if name in _STYLE:
            return ("style", _STYLE[name], self.arg())
        if name == "left":
            l = self.delim()
            body = self.row(stop=("\\right",))
            r = ""
            if self.peek() == "\\right":
                self.next(); r = self.delim()
            return ("d", l, r, body)
        if name == "right":   # 짝 없는 \right
            self.delim()
            return None
        if name == "begin":
            env = self.raw_arg().strip()
            if env in ("array", "alignedat"):
                self.raw_arg()   # 열 지정
            rows, row = [], []
            while True:
                row.append(self.row(stop=("&", "\\\\", "\\end")))
                tok = self.peek()
                if tok == "&":
                    self.next()
                elif tok == "\\\\":
                    self.next(); rows.append(row); row = []
                else:
                    if tok == "\\end":
                        self.next(); self.raw_arg()
                    break
            if any(c[1] for c in row) or not rows:
                rows.append(row)
            return ("m", env, rows)
        if name == "end":
            self.raw_arg()
            return None
        if name in ("label", "tag", "vspace", "hspace", "phantom", "vphantom", "hphantom", "color"):
            self.raw_arg()
            return None
        if name == "\\":
            return None
        if name in BIG:
            return ("big", BIG[name])
        if name in FUNCS:
            return ("f", name)
        if name in GREEK:
            return ("i", GREEK[name])
        if name in OPS:
            return ("o", OPS[name])
        if name in IDENT:
            return ("i", IDENT[name])
        if name in SPACE:
            return ("space", SPACE[name])
        if name in ("{", "}", "%", "_", "&", "$", "#"):
            return ("o", name)
        if name == "|":
            return ("o", "‖")
        if name in _IGNORE or name in ("!",):
            return None
        if name == "not":
            return ("o", "̸")
        return ("err", "\\" + name)


def parse(tex):
    try:
        return _Parser(tex).parse()
    except Exception:
        return ("row", [("err", tex or "")])


# ---------------------------------------------------------------- 글자 모양 (굵게·필기체·칠판체)
def _alnum(ch, kind):
    """수학용 글자 모양. MathML Core 는 mathvariant 를 거의 안 받으므로 유니코드 수학 글자로 바꾼다."""
    o = ord(ch)
    up, lo, dg = 65 <= o <= 90, 97 <= o <= 122, 48 <= o <= 57
    if kind == "b":
        return chr(0x1D400 + o - 65) if up else chr(0x1D41A + o - 97) if lo else chr(0x1D7CE + o - 48) if dg else ch
    if kind == "bi":
        return chr(0x1D468 + o - 65) if up else chr(0x1D482 + o - 97) if lo else ch
    if kind == "cal":
        holes = {"B": "ℬ", "E": "ℰ", "F": "ℱ", "H": "ℋ", "I": "ℐ", "L": "ℒ", "M": "ℳ", "R": "ℛ", "e": "ℯ", "g": "ℊ", "o": "ℴ"}
        return holes.get(ch) or (chr(0x1D49C + o - 65) if up else chr(0x1D4B6 + o - 97) if lo else ch)
    if kind == "bb":
        holes = {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}
        return holes.get(ch) or (chr(0x1D538 + o - 65) if up else chr(0x1D552 + o - 97) if lo else chr(0x1D7D8 + o - 48) if dg else ch)
    return ch


def _is_upper_greek(ch):
    return len(ch) == 1 and "\u0391" <= ch <= "\u03a9"


# ---------------------------------------------------------------- 나무 → MathML
_NOSTRETCH = set("()[]{}|/") | {"‖", "⟨", "⟩", "⌊", "⌋", "⌈", "⌉"}


def _limits(base):
    return (base[0] == "big" and base[1] in _LIMITS_BIG) or (base[0] == "f" and base[1] in _LIMITS_FUNC)


def _mrow(n, st=None):
    if n[0] == "row" and len(n[1]) == 1:
        return _mml(n[1][0], st)
    s = _mml(n, st)
    return "<mrow>%s</mrow>" % s if n[0] == "row" else s


def _mml_row(items, st):
    out = []
    for k, x in enumerate(items):
        out.append(_mml(x, st))
        fn = x[0] == "f" or (x[0] == "ss" and x[1][0] == "f")
        if fn and k + 1 < len(items):
            nx = items[k + 1]
            if not (nx[0] == "d" or (nx[0] == "o" and nx[1] in "([")):
                out.append('<mspace width="0.167em"/>')
    return "".join(out)


def _mml(n, st=None):
    k = n[0]
    if k == "row":
        return _mml_row(n[1], st)
    if k == "i":
        ch = n[1]
        if st in ("b", "bi", "cal", "bb") and ch.isascii():
            return "<mi>%s</mi>" % _alnum(ch, st)
        if st == "rm" or _is_upper_greek(ch):
            return '<mi mathvariant="normal">%s</mi>' % _esc(ch)
        return "<mi>%s</mi>" % _esc(ch)
    if k == "n":
        return "<mn>%s</mn>" % ("".join(_alnum(c, st) for c in n[1]) if st in ("b", "bb") else n[1])
    if k == "o":
        if n[1] in _NOSTRETCH:   # \uadf8\ub0e5 \uc4f4 \uad04\ud638\ub294 \ub298\uc5b4\ub098\uc9c0 \uc54a\ub294\ub2e4 (\ub298\ub9ac\ub824\uba74 \left \right)
            return '<mo stretchy="false">%s</mo>' % _esc(n[1])
        return "<mo>%s</mo>" % _esc(n[1])
    if k == "t":
        return "<mtext>%s</mtext>" % _esc(n[1]).replace(" ", "\u00a0")
    if k == "rm":
        t = n[1]
        if " " in t:
            return "<mtext>%s</mtext>" % _esc(t).replace(" ", "\u00a0")
        return ('<mi mathvariant="normal">%s</mi>' if len(t) == 1 else "<mi>%s</mi>") % _esc(t)
    if k == "f":
        return "<mi>%s</mi>" % n[1]
    if k == "big":
        return "<mo>%s</mo>" % n[1]
    if k == "frac":
        return "<mfrac>%s%s</mfrac>" % (_mrow(n[1], st), _mrow(n[2], st))
    if k == "sqrt":
        if n[2] is None:
            return "<msqrt>%s</msqrt>" % _mml(n[1], st)
        return "<mroot>%s%s</mroot>" % (_mrow(n[1], st), _mrow(n[2], st))
    if k == "ss":
        base, sub, sup = n[1], n[2], n[3]
        b = _mrow(base, st) if not (base[0] == "row" and not base[1]) else "<mrow></mrow>"
        under = _limits(base)
        if sub is not None and sup is not None:
            return "<%s>%s%s%s</%s>" % (("munderover",) + (b, _mrow(sub, st), _mrow(sup, st)) + ("munderover",)) if under else \
                   "<msubsup>%s%s%s</msubsup>" % (b, _mrow(sub, st), _mrow(sup, st))
        if sub is not None:
            return ("<munder>%s%s</munder>" if under else "<msub>%s%s</msub>") % (b, _mrow(sub, st))
        return ("<mover>%s%s</mover>" if under else "<msup>%s%s</msup>") % (b, _mrow(sup, st))
    if k == "acc":
        wide = n[1].startswith("wide")
        return '<mover accent="true">%s<mo stretchy="%s">%s</mo></mover>' % (_mrow(n[2], st), "true" if wide else "false", ACCENT[n[1]][0])
    if k == "bar":
        return '<mrow class="m%s">%s</mrow>' % ("over" if n[1] == "top" else "under", _mml(n[2], st))
    if k == "d":
        l = '<mo fence="true" stretchy="true">%s</mo>' % _esc(n[1]) if n[1] else ""
        r = '<mo fence="true" stretchy="true">%s</mo>' % _esc(n[2]) if n[2] else ""
        return "<mrow>%s%s%s</mrow>" % (l, _mml(n[3], st), r)
    if k == "m":
        env, rows = n[1], n[2]
        if env in _ALIGN or env == "cases":
            cells = lambda r: "".join('<mtd class="%s">%s</mtd>' % ("mL" if (j % 2 or env == "cases") else "mR", _mml(c, st)) for j, c in enumerate(r))
        else:
            cells = lambda r: "".join("<mtd>%s</mtd>" % _mml(c, st) for c in r)
        tbl = "<mtable>%s</mtable>" % "".join("<mtr>%s</mtr>" % cells(r) for r in rows)
        l, r = ("{", "") if env == "cases" else _MATRIX.get(env, ("", ""))
        if l or r:
            return "<mrow>%s%s%s</mrow>" % ('<mo fence="true" stretchy="true">%s</mo>' % l if l else "", tbl,
                                            '<mo fence="true" stretchy="true">%s</mo>' % r if r else "")
        return tbl
    if k == "space":
        return '<mspace width="%.3fem"/>' % n[1]
    if k == "style":
        return _mml(n[2], n[1])
    if k == "err":
        return '<mtext class="mErr">%s</mtext>' % _esc(n[1])
    return ""


def to_mathml(tex, display=False):
    body = _mml(parse(tex))
    return '<math%s><mrow>%s</mrow></math>' % (' display="block"' if display else "", body)


# ---------------------------------------------------------------- 나무 → OMML
_REL = set("=≈≠≤≥<>≡∼≃≅∝→←↔⇒⇐⇔≪≫,;")


def _r(t, sty=None, nor=False, scr=None):
    pr = ""
    if nor:
        pr = "<m:nor/>"
    else:
        if scr:
            pr += '<m:scr m:val="%s"/>' % scr
        if sty:
            pr += '<m:sty m:val="%s"/>' % sty
    return '<m:r>%s<m:t xml:space="preserve">%s</m:t></m:r>' % (("<m:rPr>%s</m:rPr>" % pr) if pr else "", _esc(t))


def _sty(st):
    """글자 모양 문맥 → (m:sty, m:scr)"""
    return {"b": ("b", None), "bi": ("bi", None), "rm": ("p", None), "it": ("i", None), "cal": (None, "script"), "bb": (None, "double-struck")}.get(st, (None, None))


def _take_arg(items, j):
    """j 번째부터 함수·큰 연산자의 '대상'이 끝나는 자리(끝 바로 뒤 번호)."""
    if j >= len(items):
        return j
    if items[j][0] == "o" and items[j][1] == "(":
        depth = 0
        for q in range(j, len(items)):
            if items[q][0] == "o" and items[q][1] == "(":
                depth += 1
            elif items[q][0] == "o" and items[q][1] == ")":
                depth -= 1
                if depth == 0:
                    return q + 1
        return len(items)
    return j + 1


def _om_row(items, st):
    out, k = [], 0
    while k < len(items):
        x = items[k]
        base = x[1] if x[0] == "ss" else x
        if base[0] == "big":   # 큰 연산자: 뒤따르는 식(관계 기호 앞까지)을 대상으로 묶는다
            end = k + 1
            while end < len(items) and not (items[end][0] == "o" and items[end][1] in _REL):
                end += 1
            sub = x[2] if x[0] == "ss" else None
            sup = x[3] if x[0] == "ss" else None
            out.append('<m:nary><m:naryPr><m:chr m:val="%s"/><m:limLoc m:val="%s"/>%s%s</m:naryPr><m:sub>%s</m:sub><m:sup>%s</m:sup><m:e>%s</m:e></m:nary>' % (
                base[1], "undOvr" if base[1] in _LIMITS_BIG else "subSup",
                "" if sub is not None else '<m:subHide m:val="1"/>', "" if sup is not None else '<m:supHide m:val="1"/>',
                _om(sub, st) if sub is not None else "", _om(sup, st) if sup is not None else "", _om_row(items[k + 1:end], st)))
            k = end
            continue
        if base[0] == "f":
            end = _take_arg(items, k + 1)
            out.append("<m:func><m:fName>%s</m:fName><m:e>%s</m:e></m:func>" % (_om(x, st), _om_row(items[k + 1:end], st)))
            k = end
            continue
        out.append(_om(x, st))
        k += 1
    return "".join(out)


def _om(n, st=None):
    k = n[0]
    sty, scr = _sty(st)
    if k == "row":
        return _om_row(n[1], st)
    if k == "i":
        if sty is None and scr is None and _is_upper_greek(n[1]):
            return _r(n[1], "p")
        return _r(n[1], sty, scr=scr)
    if k == "n":
        return _r(n[1], sty if sty == "b" else None, scr=scr)
    if k == "o":
        return _r(n[1])
    if k == "t":
        return _r(n[1], nor=True)
    if k == "rm":
        return _r(n[1], "p")
    if k == "f":
        return _r(n[1], "p")
    if k == "big":
        return _r(n[1])
    if k == "frac":
        return "<m:f><m:num>%s</m:num><m:den>%s</m:den></m:f>" % (_om(n[1], st), _om(n[2], st))
    if k == "sqrt":
        if n[2] is None:
            return '<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e>%s</m:e></m:rad>' % _om(n[1], st)
        return "<m:rad><m:deg>%s</m:deg><m:e>%s</m:e></m:rad>" % (_om(n[2], st), _om(n[1], st))
    if k == "ss":
        base, sub, sup = n[1], n[2], n[3]
        if base[0] == "f" and base[1] in _LIMITS_FUNC and sub is not None:
            low = "<m:limLow><m:e>%s</m:e><m:lim>%s</m:lim></m:limLow>" % (_om(base, st), _om(sub, st))
            return low if sup is None else "<m:sSup><m:e>%s</m:e><m:sup>%s</m:sup></m:sSup>" % (low, _om(sup, st))
        b = _om(base, st)
        if sub is not None and sup is not None:
            return "<m:sSubSup><m:e>%s</m:e><m:sub>%s</m:sub><m:sup>%s</m:sup></m:sSubSup>" % (b, _om(sub, st), _om(sup, st))
        if sub is not None:
            return "<m:sSub><m:e>%s</m:e><m:sub>%s</m:sub></m:sSub>" % (b, _om(sub, st))
        return "<m:sSup><m:e>%s</m:e><m:sup>%s</m:sup></m:sSup>" % (b, _om(sup, st))
    if k == "acc":
        return '<m:acc><m:accPr><m:chr m:val="%s"/></m:accPr><m:e>%s</m:e></m:acc>' % (ACCENT[n[1]][1], _om(n[2], st))
    if k == "bar":
        return '<m:bar><m:barPr><m:pos m:val="%s"/></m:barPr><m:e>%s</m:e></m:bar>' % (n[1], _om(n[2], st))
    if k == "d":
        return '<m:d><m:dPr><m:begChr m:val="%s"/><m:endChr m:val="%s"/></m:dPr><m:e>%s</m:e></m:d>' % (_esc(n[1]), _esc(n[2]), _om(n[3], st))
    if k == "m":
        env, rows = n[1], n[2]
        if env in _ALIGN or env == "cases":
            arr = "<m:eqArr>%s</m:eqArr>" % "".join(
                "<m:e>%s</m:e>" % "".join((_r("\u2003&" if env == "cases" else "&") if j else "") + _om(c, st) for j, c in enumerate(r)) for r in rows)
            if env == "cases":
                return '<m:d><m:dPr><m:begChr m:val="{"/><m:endChr m:val=""/></m:dPr><m:e>%s</m:e></m:d>' % arr
            return arr
        cols = max([len(r) for r in rows] or [1])
        mat = '<m:m><m:mPr><m:mcs><m:mc><m:mcPr><m:count m:val="%d"/><m:mcJc m:val="center"/></m:mcPr></m:mc></m:mcs></m:mPr>%s</m:m>' % (
            cols, "".join("<m:mr>%s</m:mr>" % "".join("<m:e>%s</m:e>" % _om(c, st) for c in (r + [("row", [])] * (cols - len(r)))) for r in rows))
        l, r = _MATRIX.get(env, ("", ""))
        if l or r:
            return '<m:d><m:dPr><m:begChr m:val="%s"/><m:endChr m:val="%s"/></m:dPr><m:e>%s</m:e></m:d>' % (l, r, mat)
        return mat
    if k == "space":
        w = n[1]
        return _r("\u2009" if w < 0.2 else "\u2005" if w < 0.25 else "\u2004" if w < 0.3 else "\u00a0" if w < 0.4 else "\u2002" if w < 0.9 else "\u2003" * int(round(w)))
    if k == "style":
        return _om(n[2], n[1])
    if k == "err":
        return _r(n[1], nor=True)
    return ""


def to_omml(tex, display=False):
    body = _om(parse(tex)) or _r(" ")
    om = "<m:oMath>%s</m:oMath>" % body
    return "<m:oMathPara>%s</m:oMathPara>" % om if display else om


# ---------------------------------------------------------------- OMML → LaTeX
_REV = {}
for _tbl in (GREEK, OPS, IDENT, BIG):
    for _k, _v in _tbl.items():
        if not _v.isascii():   # | < > : 같은 글자는 명령으로 바꾸지 않는다
            _REV.setdefault(_v, "\\" + _k)
_REV.update({"−": "-", "∗": "*", "µ": "\\mu", "‖": "\\|", "′": "'", "″": "''", "…": "\\ldots", "⋯": "\\cdots", "\u00b7": "\\cdot",
             "\u2009": "\\,", "\u2005": "\\:", "\u2004": "\\;", "\u2002": "\\enspace", "\u2003": "\\quad", "\u00a0": "~",
             "\u200b": "", "\u2061": "", "\u2062": "", "\u2063": "", "\u2064": "", "\u200a": "\\,", "\u2006": "\\,", "\u205f": "\\:",
             "%": "\\%", "#": "\\#", "&": "\\&", "_": "\\_", "$": "\\$", "{": "\\{", "}": "\\}", "\\": "\\backslash", "~": "\\sim", "^": "\\hat{}",
             "′": "'"})
_ACC_REV = {v[1]: "\\" + k for k, v in ACCENT.items() if not k.startswith("wide")}
_ACC_REV.update({"^": "\\hat", "~": "\\tilde", "¯": "\\bar", "‾": "\\overline", "\u0305": "\\overline", "˙": "\\dot", "¨": "\\ddot", "→": "\\vec", "\u20d1": "\\vec"})
_UNSTYLE = {}   # 유니코드 수학 글자(굵게 등) → (보통 글자, 명령)
for _kind, _cmd in (("b", "\\mathbf"), ("bi", "\\boldsymbol"), ("cal", "\\mathcal"), ("bb", "\\mathbb")):
    for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789":
        _m = _alnum(_c, _kind)
        if _m != _c:
            _UNSTYLE.setdefault(_m, (_c, _cmd))
for _o in range(0x1D434, 0x1D468):   # 수학 기울임 글자 → 보통 글자 (워드가 가끔 이 글자로 저장)
    _UNSTYLE.setdefault(chr(_o), ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"[_o - 0x1D434], ""))
_UNSTYLE.setdefault("ℎ", ("h", ""))


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _val(el, name, default=None):
    """<m:xPr><m:name m:val=…/> 의 값"""
    for pr in el:
        if _local(pr.tag).endswith("Pr"):
            for c in pr:
                if _local(c.tag) == name:
                    for a, v in c.attrib.items():
                        if _local(a) == "val":
                            return v
                    return ""   # 있지만 값 없음 (= 켬)
    return default


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _chars(text):
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            out.append("\\text{%s}" % buf); buf = ""
    for ch in text:
        if ch in _UNSTYLE:
            plain, cmd = _UNSTYLE[ch]
            flush(); out.append("%s{%s}" % (cmd, plain) if cmd else plain)
        elif ch in _REV:
            flush(); out.append(_REV[ch] + (" " if _REV[ch][-1:].isalpha() and _REV[ch].startswith("\\") else ""))
        elif ch == " ":
            if buf:
                buf += " "
            else:
                out.append(" ")
        elif ch.isascii() or "\u0370" <= ch <= "\u03ff":
            flush(); out.append(ch)
        elif len(unicodedata.normalize("NFD", ch)) == 2 and unicodedata.normalize("NFD", ch)[1] in _ACC_REV:   # Ī → \bar{I}
            d = unicodedata.normalize("NFD", ch)
            flush(); out.append("%s{%s}" % (_ACC_REV[d[1]], _chars(d[0])))
        elif ch.isalpha() or "\uac00" <= ch <= "\ud7a3":   # 한글 등 글자는 \text 로
            buf += ch
        else:
            flush(); out.append(ch)
    flush()
    return "".join(out)


def _group(s):
    """첨자·분수의 인자로 쓸 때 필요한 중괄호."""
    return "{%s}" % s.strip()


def _base(s):
    s = s.strip()
    if len(s) <= 1 or re.match(r"^\\[A-Za-z]+$", s) or re.match(r"^\\[A-Za-z]+\{[^{}]*\}$", s) or re.match(r"^\d+(\.\d+)?$", s):
        return s
    return "{%s}" % s


def _kids(el, st=None):
    return "".join(_lx(c, st) for c in el)


def _arg(el, name):
    c = _child(el, name)
    return _kids(c).strip() if c is not None else ""


def _lx(el, st=None):
    tag = _local(el.tag)
    if tag in ("oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "lim", "fName", "box", "borderBox", "phant", "groupChr", "smartTag", "ins"):
        return _kids(el)
    if tag == "r":
        text = "".join(t.text or "" for t in el.iter() if _local(t.tag) == "t")
        if not text:
            return ""
        pr = _child(el, "rPr")
        flags = {_local(c.tag): next((v for a, v in c.attrib.items() if _local(a) == "val"), "") for c in pr} if pr is not None else {}
        if "nor" in flags and flags["nor"] not in ("0", "off", "false"):
            if not text.strip():
                return _chars(text)   # 빈칸뿐인 글 런은 간격으로
            return "\\text{%s}" % text.replace("{", "").replace("}", "")
        sty = flags.get("sty"); scr = flags.get("scr")
        if scr == "script":
            return "\\mathcal{%s}" % text
        if scr == "double-struck":
            return "\\mathbb{%s}" % text
        if sty == "p":
            core = text.strip()
            if core in FUNCS:
                return "\\%s " % core
            if re.match(r"^[A-Za-z][A-Za-z0-9/ .\-]*$", core):
                return "\\mathrm{%s}" % core
            return _chars(text)   # 숫자·기호·그리스 대문자는 원래 곧은 글자
        if sty in ("b", "bi"):
            pat = r"[A-Za-z0-9]+" if sty == "b" else r"[A-Za-z]+"
            cmd = "\\mathbf" if sty == "b" else "\\boldsymbol"
            out, pos = [], 0
            for m in re.finditer(pat, text):
                out.append(_chars(text[pos:m.start()])); out.append("%s{%s}" % (cmd, m.group(0))); pos = m.end()
            out.append(_chars(text[pos:]))
            return "".join(out)
        return _chars(text)
    if tag == "f":
        num, den = _arg(el, "num"), _arg(el, "den")
        typ = _val(el, "type")
        if typ == "lin":
            return "%s/%s" % (_base(num), _base(den))
        if typ == "noBar":
            return "\\binom{%s}{%s}" % (num, den)
        return "\\frac{%s}{%s}" % (num, den)
    if tag == "sSub":
        return "%s_%s" % (_base(_arg(el, "e")), _group(_arg(el, "sub")))
    if tag == "sSup":
        sup = _arg(el, "sup")
        if re.match(r"^'+$", sup):
            return _base(_arg(el, "e")) + sup
        return "%s^%s" % (_base(_arg(el, "e")), _group(sup))
    if tag == "sSubSup":
        return "%s_%s^%s" % (_base(_arg(el, "e")), _group(_arg(el, "sub")), _group(_arg(el, "sup")))
    if tag == "sPre":
        return "{}_%s^%s%s" % (_group(_arg(el, "sub")), _group(_arg(el, "sup")), _base(_arg(el, "e")))
    if tag == "rad":
        deg = _arg(el, "deg")
        hide = _val(el, "degHide")
        if deg and hide in (None, "0", "off", "false"):
            return "\\sqrt[%s]{%s}" % (deg, _arg(el, "e"))
        return "\\sqrt{%s}" % _arg(el, "e")
    if tag == "acc":
        chr_ = _val(el, "chr")
        cmd = _ACC_REV.get(chr_, "\\hat") if chr_ is not None else "\\hat"
        return "%s{%s}" % (cmd, _arg(el, "e"))
    if tag == "bar":
        return "%s{%s}" % ("\\overline" if _val(el, "pos") == "top" else "\\underline", _arg(el, "e"))
    if tag == "d":
        beg, end, sep = _val(el, "begChr"), _val(el, "endChr"), _val(el, "sepChr")
        beg = "(" if beg is None else beg
        end = ")" if end is None else end
        sep = "|" if sep is None else sep
        es = [c for c in el if _local(c.tag) == "e"]
        only = [c for c in es[0] if not _local(c.tag).endswith("Pr")] if len(es) == 1 else []
        if len(only) == 1 and _local(only[0].tag) == "eqArr" and beg == "{" and not end:
            rows = [re.sub(r"\\quad\s*&", "&", _kids(c).strip().replace("\\&", "&")) for c in only[0] if _local(c.tag) == "e"]
            return "\\begin{cases} %s \\end{cases}" % " \\\\ ".join(rows)
        if len(only) == 1 and _local(only[0].tag) == "m" and (beg, end) in (("(", ")"), ("[", "]"), ("|", "|"), ("{", "}")):
            env = {"(": "pmatrix", "[": "bmatrix", "|": "vmatrix", "{": "Bmatrix"}[beg]
            return _lx(only[0]).replace("{matrix}", "{%s}" % env)
        body = ("," if sep == "," else " %s " % _chars(sep).strip()).join(_kids(c).strip() for c in el if _local(c.tag) == "e")
        d = lambda c: "." if not c else {"{": "\\{", "}": "\\}", "‖": "\\|", "⟨": "\\langle ", "⟩": "\\rangle ", "⌊": "\\lfloor ", "⌋": "\\rfloor ",
                                         "⌈": "\\lceil ", "⌉": "\\rceil "}.get(c, c)
        return "\\left%s %s \\right%s" % (d(beg), body, d(end))
    if tag == "nary":
        chr_ = _val(el, "chr")
        op = _REV.get(chr_ if chr_ is not None else "∫", "\\int")
        sub = "" if _val(el, "subHide") not in (None, "0", "off", "false") else _arg(el, "sub")
        sup = "" if _val(el, "supHide") not in (None, "0", "off", "false") else _arg(el, "sup")
        return "%s%s%s %s" % (op, "_" + _group(sub) if sub else "", "^" + _group(sup) if sup else "", _arg(el, "e"))
    if tag == "func":
        name = _arg(el, "fName")
        m = re.match(r"^\\mathrm\{([A-Za-z]+)\}(.*)$", name)
        if m:
            name = ("\\" + m.group(1) if m.group(1) in FUNCS else "\\operatorname{%s}" % m.group(1)) + m.group(2)
        return "%s %s" % (name, _arg(el, "e"))
    if tag == "limLow":
        return "%s_%s" % (_base(_arg(el, "e")), _group(_arg(el, "lim")))
    if tag == "limUpp":
        return "%s^%s" % (_base(_arg(el, "e")), _group(_arg(el, "lim")))
    if tag == "m":
        rows = [" & ".join(_kids(c).strip() for c in mr if _local(c.tag) == "e") for mr in el if _local(mr.tag) == "mr"]
        return "\\begin{matrix} %s \\end{matrix}" % " \\\\ ".join(rows)
    if tag == "eqArr":
        rows = [_kids(c).strip().replace("\\&", "&") for c in el if _local(c.tag) == "e"]
        return "\\begin{aligned} %s \\end{aligned}" % " \\\\ ".join(rows)
    if tag.endswith("Pr") or tag in ("del", "bookmarkStart", "bookmarkEnd", "proofErr"):
        return ""
    return _kids(el)


def _tidy(s):
    s = re.sub(r"(\\[A-Za-z]+) +(?![A-Za-z])", r"\1", s)        # 명령 뒤 공백은 글자 앞에서만 필요
    s = re.sub(r"[ \t]+", " ", s).strip()
    s = s.replace("\\text{ }", "\\ ")
    # 높이가 없는 괄호는 \left \right 없이 (분수·근호·합·위첨자 같은 키 큰 것이 없을 때). 안쪽 괄호부터
    tall = re.compile(r"\\(?:frac|sqrt|sum|int|prod|begin|binom|overline|left|right)|\^")

    def flat(m):
        return m.group(0) if tall.search(m.group(2)) else m.group(1) + m.group(2).strip() + m.group(3)
    for _ in range(6):
        t = re.sub(r"\\left([(\[])((?:(?!\\left|\\right).)*?)\\right([)\]])", flat, s)
        if t == s:
            break
        s = t
    return s


def omml_to_latex(xml):
    """<m:oMath>…</m:oMath> (또는 oMathPara) 조각 → LaTeX. 실패하면 빈 문자열."""
    try:
        pre = set(re.findall(r"</?([A-Za-z_][\w.-]*):", xml)) | set(re.findall(r"\s([A-Za-z_][\w.-]*):[\w.-]+=", xml))
        pre.discard("xmlns"); pre.discard("xml")
        xml = re.sub(r'\sxmlns:[\w.-]+="[^"]*"', "", xml)
        root = ET.fromstring("<root %s>%s</root>" % (" ".join('xmlns:%s="urn:x-%s"' % (p, p) for p in sorted(pre)), xml))
        return _tidy(_kids(root))
    except Exception:
        return ""


def omml_flat(xml):
    """수식 속 글자만 이어 붙인 것 (구조 없음) — 예전 가져오기가 ⟦…⟧ 안에 넣던 글."""
    return "".join(re.findall(r"<m:t[^>]*>([^<]*)</m:t>", xml)).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


# ---------------------------------------------------------------- 글 속의 수식 찾기
MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(?!\s)([^$\n]+?)(?<![\s\\])\$", re.S)


def split_math(text):
    """글을 [(kind, 내용)] 으로: kind = 'text' | 'inline' | 'display'."""
    out, pos = [], 0
    for m in MATH_RE.finditer(text or ""):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        out.append(("display", m.group(1).strip()) if m.group(1) is not None else ("inline", m.group(2).strip()))
        pos = m.end()
    if pos < len(text or ""):
        out.append(("text", text[pos:]))
    return out
