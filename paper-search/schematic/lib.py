# -*- coding: utf-8 -*-
"""schematic.lib - small SVG drawing kit for journal-style 2D schematics (machining, materials).

- Units px, origin top-left, y grows DOWN. Angles in degrees; a *direction* angle (angle_mark, force_vectors,
  shear_plane phi, chip up_angle) uses 0 = +x (right), 90 = UP on screen.
- Every drawing function returns an SVG fragment string; draw it with Canvas.add(...) or Panel.add(...).
- Canvas(w, h) is the page. c.panel(x, y, w, h, label="(a)") returns a Panel whose (0,0) is its own top-left
  and which clips to its bounds; c.svg() returns the finished <svg> text.
- Fills that need <defs> take the canvas (or a panel): hatch(c, "h1"), linear_gradient(c, "g1", stops) return
  "url(#...)" strings for fill=...; glow(c, "f1") returns a filter attribute for group(fragment, attrs).
- Colors are CSS strings; dash is an SVG dasharray like "6 4"; stroke/sw/fill(text)=None = current style default
  (style("journal"|"simple"|"color")).
- Machining parts (tool, workpiece, chip, shear_plane, force_vectors, velocity_arrow) return (svg, info): info is a
  dict of key points, e.g. info["tip"], info["rake_end"]; Canvas/Panel.add() also accepts the tuple directly.
- cmap(t, name) maps 0..1 to a color (jet | hot | viridis | gray | coolwarm); colorbar/field_layer use the same maps.
"""
import math
import random
import inspect
import numpy as np
from scipy.spatial import Voronoi

__all__ = [
    "Canvas", "Panel", "style", "STYLES", "group",
    "rect", "circle", "ellipse", "polygon", "polyline", "line", "path", "pathd", "curve",
    "arrow", "darrow", "dimension", "angle_mark",
    "text", "lines", "label_box", "sub",
    "hatch", "linear_gradient", "radial_gradient", "glow", "cmap", "colorbar", "axis_box", "zoom_link", "scale_bar",
    "jitter_grid", "voronoi_grains", "displace", "clip_poly", "field_layer", "shade_polygons", "grain_field",
    "tool", "workpiece", "chip", "chip_thickness", "shear_plane", "force_vectors", "velocity_arrow",
    "api_doc",
]

# ----------------------------------------------------------------------------- styles
STYLES = {
    "journal": {"font": "Georgia, 'Times New Roman', serif", "sw": 2, "text": "#111", "accent": "#1f4fd1",
                "palette": ["#1f4fd1", "#e53935", "#2e9e44", "#f5a623", "#7b4fb0", "#00838f"], "label_size": 20},
    "simple": {"font": "Arial, Helvetica, sans-serif", "sw": 1.5, "text": "#000", "accent": "#000",
               "palette": ["#000000", "#444444", "#777777", "#999999", "#bbbbbb", "#dddddd"], "label_size": 18},
    "color": {"font": "Arial, Helvetica, sans-serif", "sw": 2.5, "text": "#222", "accent": "#0072b2",
              "palette": ["#0072b2", "#d55e00", "#009e73", "#e69f00", "#cc79a7", "#56b4e9"], "label_size": 20},
}
_STYLE = {}


def _set_style(name):
    base = STYLES.get(name) or STYLES["journal"]
    _STYLE.clear()
    _STYLE.update(base)
    _STYLE["name"] = name if name in STYLES else "journal"
    return _STYLE


def style(name="journal"):
    """Set the default look: "journal" (serif, 2px ink), "simple" (thin black line art) or "color" (sans, bold colors)."""
    return dict(_set_style(name))


_set_style("journal")


# ----------------------------------------------------------------------------- helpers
def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pts(points):
    return " ".join("%s,%s" % (_f(p[0]), _f(p[1])) for p in points)


def _ink(v):
    return _STYLE["text"] if v is None else v


def _sw(v):
    return _STYLE["sw"] if v is None else v


def _font(v):
    return _STYLE["font"] if v is None else v


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _lerp(a, b, t):
    return a + (b - a) * t


def _lerp2(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _hexc(rgb):
    return "#%02x%02x%02x" % tuple(int(round(_clamp(v, 0, 255))) for v in rgb)


def _mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return _hexc([_lerp(a[i], b[i], t) for i in range(3)])


def _text_w(s, size):
    # rough width: ASCII 0.55 em, CJK 1.0 em
    return sum((1.0 if ord(ch) > 0x2E7F else 0.55) * size for ch in str(s))


def _num(v):
    v = float(v)
    if abs(v - round(v)) < 1e-6:
        return "%d" % round(v)
    return "%.3g" % v


def _frag(x):
    """Accept str, (svg, info) tuple, or list of fragments."""
    if isinstance(x, str):
        return x
    if isinstance(x, tuple) and len(x) == 2 and isinstance(x[0], str):
        return x[0]
    if isinstance(x, (list, tuple)):
        return "".join(_frag(i) for i in x)
    if x is None:
        return ""
    return str(x)


def _paint(fill="none", stroke=None, sw=None, opacity=1, dash=None, cap=None):
    if fill is None:
        fill = "none"
    stroke = _ink(stroke)
    a = ' fill="%s"' % fill
    if stroke and stroke != "none":
        a += ' stroke="%s" stroke-width="%s" stroke-linejoin="round"' % (stroke, _f(_sw(sw)))
        if cap:
            a += ' stroke-linecap="%s"' % cap
        if dash:
            a += ' stroke-dasharray="%s"' % dash
    if opacity is not None and float(opacity) < 1:
        a += ' opacity="%s"' % _f(opacity)
    return a


def _canvas_of(obj):
    return obj.canvas if isinstance(obj, Panel) else obj


# ----------------------------------------------------------------------------- canvas / panel
class Canvas(object):
    """Page of w x h px: add(frag) draws, defs(frag) registers <defs>, panel(...) makes a sub-figure, svg() returns the SVG text."""

    def __init__(self, w, h, bg="#fff", style="journal"):
        self.w = float(w)
        self.h = float(h)
        self.bg = bg
        _set_style(style)
        self._defs = []
        self._body = []
        self._n = 0

    def add(self, fragment):
        """Append an SVG fragment (string, list of strings, or a (svg, info) tuple from a machining part) to the page."""
        self._body.append(_frag(fragment))
        return self

    def defs(self, fragment):
        """Append a gradient/pattern/filter/clipPath fragment to <defs>; identical fragments are stored once."""
        fragment = _frag(fragment)
        if fragment and fragment not in self._defs:
            self._defs.append(fragment)
        return self

    def uid(self, prefix="id"):
        """Return a new unique id string such as "id3" (for your own defs entries)."""
        self._n += 1
        return "%s%d" % (prefix, self._n)

    def panel(self, x, y, w, h, label=None, border=True, bg=None, clip=True):
        """Create a Panel (sub-figure) at (x,y) of size w x h with its own origin; label like "(a)" is drawn centered below it."""
        p = Panel(self, x, y, w, h, label, border, bg, clip)
        self._body.append(p)
        return p

    def svg(self):
        """Return the complete <svg> document string (xmlns, width/height/viewBox, defs, background, body)."""
        out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s">'
               % (_f(self.w), _f(self.h), _f(self.w), _f(self.h))]
        if self._defs:
            out.append("<defs>" + "\n".join(self._defs) + "</defs>")
        if self.bg and self.bg != "none":
            out.append('<rect x="0" y="0" width="%s" height="%s" fill="%s"/>' % (_f(self.w), _f(self.h), self.bg))
        for item in self._body:
            out.append(item.render() if isinstance(item, Panel) else item)
        out.append("</svg>")
        return "\n".join(out)


class Panel(object):
    """Sub-area of a Canvas; coordinates given to add() are relative to its top-left; .w/.h are its size; defs() goes to the canvas."""

    def __init__(self, canvas, x, y, w, h, label=None, border=True, bg=None, clip=True):
        self.canvas = canvas
        self.x, self.y, self.w, self.h = float(x), float(y), float(w), float(h)
        self.label, self.border, self.bg = label, border, bg
        self._body = []
        self.clip_id = canvas.uid("clip") if clip else None
        if clip:
            canvas.defs('<clipPath id="%s"><rect x="0" y="0" width="%s" height="%s"/></clipPath>'
                        % (self.clip_id, _f(self.w), _f(self.h)))

    def add(self, fragment):
        """Append an SVG fragment (string, list, or (svg, info) tuple) drawn in panel coordinates."""
        self._body.append(_frag(fragment))
        return self

    def defs(self, fragment):
        """Register a <defs> fragment on the parent canvas."""
        return self.canvas.defs(fragment)

    def uid(self, prefix="id"):
        """Return a new unique id from the parent canvas."""
        return self.canvas.uid(prefix)

    def to_canvas(self, x, y):
        """Convert panel coordinates to canvas coordinates: (x + panel.x, y + panel.y)."""
        return (x + self.x, y + self.y)

    def render(self):
        """Return the panel as a translated, clipped <g> fragment (called by Canvas.svg())."""
        inner = "".join(self._body)
        if self.clip_id:
            inner = '<g clip-path="url(#%s)">%s</g>' % (self.clip_id, inner)
        out = ['<g transform="translate(%s,%s)">' % (_f(self.x), _f(self.y))]
        if self.bg and self.bg != "none":
            out.append(rect(0, 0, self.w, self.h, self.bg, "none"))
        out.append(inner)
        if self.border:
            out.append(rect(0, 0, self.w, self.h, "none", None, None))
        if self.label:
            ls = _STYLE.get("label_size", 20)
            out.append(text(self.w / 2, self.h + ls * 1.5, self.label, ls, halo=None))
        out.append("</g>")
        return "\n".join(out)


def group(fragment, attrs="", opacity=1, transform=None):
    """Wrap fragment(s) in <g>; attrs is a raw attribute string (e.g. from glow()), transform like "rotate(30 100 100)"."""
    a = (" " + attrs.strip()) if attrs else ""
    if opacity is not None and float(opacity) < 1:
        a += ' opacity="%s"' % _f(opacity)
    if transform:
        a += ' transform="%s"' % transform
    return "<g%s>%s</g>" % (a, _frag(fragment))


# ----------------------------------------------------------------------------- primitives
def rect(x, y, w, h, fill="none", stroke=None, sw=None, rx=0, opacity=1, dash=None):
    """Rectangle from top-left (x,y), size w x h; rx rounds corners; stroke=None uses the style ink, "none" removes it."""
    return '<rect x="%s" y="%s" width="%s" height="%s"%s%s/>' % (
        _f(x), _f(y), _f(w), _f(h), (' rx="%s"' % _f(rx)) if rx else "", _paint(fill, stroke, sw, opacity, dash))


def circle(cx, cy, r, fill="none", stroke=None, sw=None, opacity=1, dash=None):
    """Circle centered at (cx,cy) with radius r."""
    return '<circle cx="%s" cy="%s" r="%s"%s/>' % (_f(cx), _f(cy), _f(r), _paint(fill, stroke, sw, opacity, dash))


def ellipse(cx, cy, rx, ry, fill="none", stroke=None, sw=None, opacity=1, dash=None):
    """Ellipse centered at (cx,cy) with radii rx (horizontal) and ry (vertical)."""
    return '<ellipse cx="%s" cy="%s" rx="%s" ry="%s"%s/>' % (_f(cx), _f(cy), _f(rx), _f(ry),
                                                              _paint(fill, stroke, sw, opacity, dash))


def polygon(points, fill="none", stroke=None, sw=None, opacity=1, dash=None):
    """Closed polygon through points [(x,y), ...]."""
    return '<polygon points="%s"%s/>' % (_pts(points), _paint(fill, stroke, sw, opacity, dash))


def polyline(points, stroke=None, sw=None, dash=None, opacity=1):
    """Open line through points [(x,y), ...] (no fill)."""
    return '<polyline points="%s"%s/>' % (_pts(points), _paint("none", stroke, sw, opacity, dash, cap="round"))


def line(x1, y1, x2, y2, stroke=None, sw=None, dash=None, opacity=1):
    """Straight line from (x1,y1) to (x2,y2)."""
    return '<line x1="%s" y1="%s" x2="%s" y2="%s"%s/>' % (_f(x1), _f(y1), _f(x2), _f(y2),
                                                          _paint("none", stroke, sw, opacity, dash, cap="round"))


def pathd(points, closed=True):
    """Path data string "M x y L x y ... Z" through points [(x,y), ...] (for path())."""
    d = "M " + " L ".join("%s %s" % (_f(p[0]), _f(p[1])) for p in points)
    return d + (" Z" if closed else "")


def path(d, fill="none", stroke=None, sw=None, opacity=1, dash=None):
    """Raw SVG path; d is path data ("M 0 0 L 10 10 ...") or a list of points (closed automatically)."""
    if not isinstance(d, str):
        d = pathd(d, True)
    return '<path d="%s"%s/>' % (d, _paint(fill, stroke, sw, opacity, dash, cap="round"))


def curve(points, stroke=None, sw=None, fill="none", closed=False, tension=0.5, dash=None, opacity=1):
    """Smooth Catmull-Rom spline through points [(x,y),...]; closed loops back; tension 0 = straight, 0.5 = natural, 1 = loose."""
    P = [(float(p[0]), float(p[1])) for p in points]
    n = len(P)
    if n < 2:
        return ""
    if n == 2:
        return path("M %s %s L %s %s" % (_f(P[0][0]), _f(P[0][1]), _f(P[1][0]), _f(P[1][1])), fill, stroke, sw, opacity, dash)
    k = tension / 3.0
    segs = range(n) if closed else range(n - 1)
    d = ["M %s %s" % (_f(P[0][0]), _f(P[0][1]))]
    for i in segs:
        if closed:
            p0, p1, p2, p3 = P[i - 1], P[i], P[(i + 1) % n], P[(i + 2) % n]
        else:
            p0, p1, p2, p3 = P[max(i - 1, 0)], P[i], P[i + 1], P[min(i + 2, n - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) * k, p1[1] + (p2[1] - p0[1]) * k)
        c2 = (p2[0] - (p3[0] - p1[0]) * k, p2[1] - (p3[1] - p1[1]) * k)
        d.append("C %s %s %s %s %s %s" % (_f(c1[0]), _f(c1[1]), _f(c2[0]), _f(c2[1]), _f(p2[0]), _f(p2[1])))
    if closed:
        d.append("Z")
    return path(" ".join(d), fill, stroke, sw, opacity, dash)


def _quad_sub(p0, c, p2, t0, t1):
    """Sub-curve [t0, t1] of the quadratic Bezier (p0, c, p2)."""
    a = _lerp2(p0, c, t1)
    b = _lerp2(c, p2, t1)
    m = _lerp2(a, b, t1)
    p0, c, p2 = p0, a, m
    if t1 <= 1e-9:
        return p0, p0, p0
    s = t0 / t1
    a = _lerp2(p0, c, s)
    b = _lerp2(c, p2, s)
    m = _lerp2(a, b, s)
    return m, b, p2


def _head(x, y, ang, head, color, shape=0.42):
    hx, hy = x - head * math.cos(ang), y - head * math.sin(ang)
    px, py = math.sin(ang) * head * shape, -math.cos(ang) * head * shape
    return '<polygon points="%s" fill="%s" stroke="none"/>' % (_pts([(x, y), (hx + px, hy + py), (hx - px, hy - py)]), color)


def _arrow(x1, y1, x2, y2, color, w, head, bend, dash, both):
    color = _ink(color)
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 1e-6:
        return ""
    head = min(head, L / 2.2) if both else min(head, L / 1.3)
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    c = ((x1 + x2) / 2.0 + uy * bend, (y1 + y2) / 2.0 - ux * bend)
    Lest = (L + math.hypot(c[0] - x1, c[1] - y1) + math.hypot(x2 - c[0], y2 - c[1])) / 2.0
    k = head * 0.7 / Lest
    q0, qc, q2 = _quad_sub((x1, y1), c, (x2, y2), k if both else 0.0, 1.0 - k)
    d = "M %s %s Q %s %s %s %s" % (_f(q0[0]), _f(q0[1]), _f(qc[0]), _f(qc[1]), _f(q2[0]), _f(q2[1]))
    out = '<path d="%s" fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round"%s/>' % (
        d, color, _f(w), (' stroke-dasharray="%s"' % dash) if dash else "")
    out += _head(x2, y2, math.atan2(y2 - c[1], x2 - c[0]), head, color)
    if both:
        out += _head(x1, y1, math.atan2(y1 - c[1], x1 - c[0]), head, color)
    return out


def arrow(x1, y1, x2, y2, color=None, w=3, head=12, bend=0, dash=None):
    """Arrow from (x1,y1) with the head at (x2,y2); bend = sideways bulge in px (+ = left of travel, i.e. up for a rightward arrow)."""
    return _arrow(x1, y1, x2, y2, color, w, head, bend, dash, False)


def darrow(x1, y1, x2, y2, color=None, w=2, head=10, bend=0, dash=None):
    """Double-headed arrow between (x1,y1) and (x2,y2) (for dimensions); bend as in arrow()."""
    return _arrow(x1, y1, x2, y2, color, w, head, bend, dash, True)


def dimension(x1, y1, x2, y2, label, offset=24, color=None, size=14, sw=1.2, ext=True):
    """Dimension between two points: extension ticks, double arrow shifted by offset (+ = above a left-to-right line), centered label."""
    color = _ink(color)
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return ""
    nx, ny = dy / L, -dx / L
    sgn = 1 if offset >= 0 else -1
    ox, oy = nx * offset, ny * offset
    ax1, ay1, ax2, ay2 = x1 + ox, y1 + oy, x2 + ox, y2 + oy
    out = []
    if ext:
        e = offset + sgn * 6
        for px, py in ((x1, y1), (x2, y2)):
            out.append(line(px + nx * sgn * 2, py + ny * sgn * 2, px + nx * e, py + ny * e, color, sw * 0.8))
    out.append(darrow(ax1, ay1, ax2, ay2, color, sw, max(7, size * 0.6)))
    gap = 5
    lx, ly = (ax1 + ax2) / 2.0 + nx * sgn * gap, (ay1 + ay2) / 2.0 + ny * sgn * gap
    if abs(nx) < 0.35:
        base = ly - size * 0.1 if ny * sgn < 0 else ly + size * 0.85
        out.append(text(lx, base, label, size, color, anchor="middle"))
    else:
        anchor = "start" if nx * sgn > 0 else "end"
        out.append(text(lx + (2 if anchor == "start" else -2), ly + size * 0.35, label, size, color, anchor=anchor))
    return "".join(out)


def angle_mark(cx, cy, r, a0_deg, a1_deg, label=None, color=None, size=14, sw=1.5, dash=None, short=True):
    """Arc of radius r around (cx,cy) between angles a0 and a1 (deg, 0 = +x, 90 = up); short=True draws the smaller arc; label outside its middle."""
    color = _ink(color)
    da = (a1_deg - a0_deg) % 360.0
    if da == 0:
        da = 360.0
    if short and da > 180.0:
        a0_deg, da = a1_deg, 360.0 - da
    a0, a1 = math.radians(a0_deg), math.radians(a0_deg + da)
    p0 = (cx + r * math.cos(a0), cy - r * math.sin(a0))
    p1 = (cx + r * math.cos(a1), cy - r * math.sin(a1))
    if da >= 359.9:
        out = circle(cx, cy, r, "none", color, sw, dash=dash)
    else:
        d = "M %s %s A %s %s 0 %d 0 %s %s" % (_f(p0[0]), _f(p0[1]), _f(r), _f(r), 1 if da > 180 else 0, _f(p1[0]), _f(p1[1]))
        out = path(d, "none", color, sw, dash=dash)
    if label:
        am = math.radians(a0_deg + da / 2.0)
        lr = r + size * 0.9
        lx, ly = cx + lr * math.cos(am), cy - lr * math.sin(am)
        anchor = "start" if math.cos(am) > 0.4 else ("end" if math.cos(am) < -0.4 else "middle")
        out += text(lx, ly + size * 0.35, label, size, color, anchor=anchor)
    return out


# ----------------------------------------------------------------------------- text
def text(x, y, s, size=16, fill=None, weight="normal", italic=False, anchor="middle", halo="#fff", halo_w=None,
         rotate=0, family=None, opacity=1):
    """Text with its baseline at (x,y); anchor start|middle|end; halo=color paints a stroke under the glyphs (None = off); rotate deg CW."""
    st = "font-family:%s;font-size:%spx;font-weight:%s;%s" % (_font(family), _f(size), weight,
                                                                "font-style:italic;" if italic else "")
    fill = _ink(fill)
    hw = halo_w if halo_w is not None else max(2.0, size * 0.18)
    tr = (' transform="rotate(%s %s %s)"' % (_f(rotate), _f(x), _f(y))) if rotate else ""
    op = (' opacity="%s"' % _f(opacity)) if (opacity is not None and float(opacity) < 1) else ""
    s = _esc(s)
    base = '<text x="%s" y="%s" text-anchor="%s" style="%s"%s%s' % (_f(x), _f(y), anchor, st, tr, op)
    out = ""
    if halo and halo != "none":
        out += base + ' fill="%s" stroke="%s" stroke-width="%s" stroke-linejoin="round">%s</text>' % (halo, halo, _f(hw), s)
    out += base + ' fill="%s">%s</text>' % (fill, s)
    return out


def lines(x, y, words, size=16, dy=None, **text_kwargs):
    """Several lines of text starting at baseline (x,y); words = list of strings; dy = line spacing (default 1.3*size)."""
    if isinstance(words, str):
        words = words.split("\n")
    dy = size * 1.3 if dy is None else dy
    return "".join(text(x, y + dy * i, w, size, **text_kwargs) for i, w in enumerate(words))


def label_box(x, y, s, size=14, fill=None, bg="#fff", stroke=None, pad=6, rx=3, sw=1.2, weight="normal"):
    """Text s inside a rounded box centered at (x,y); fill = text color, bg = box color, stroke = box outline ("none" to drop)."""
    w = _text_w(s, size) + 2 * pad
    h = size * 1.15 + 2 * pad
    return rect(x - w / 2.0, y - h / 2.0, w, h, bg, stroke, sw, rx) + text(x, y + size * 0.35, s, size, fill, weight, halo=None)


def sub(x, y, base, subscript, size=16, fill=None, italic=True, anchor="middle", halo="#fff", weight="normal"):
    """Symbol with subscript, e.g. sub(x, y, "V", "c") -> italic V with small c; baseline at (x,y), anchor as in text()."""
    wb = _text_w(base, size) + (size * 0.08 if italic else 0)
    ws = _text_w(subscript, size * 0.7)
    total = wb + ws
    x0 = x - total / 2.0 if anchor == "middle" else (x - total if anchor == "end" else x)
    out = text(x0, y, base, size, fill, weight, italic, "start", halo)
    out += text(x0 + wb, y + size * 0.28, subscript, size * 0.7, fill, weight, False, "start", halo)
    return out


# ----------------------------------------------------------------------------- defs, colors, decorations
def hatch(canvas, hid, color="#555", spacing=8, angle=45, sw=1):
    """Register a diagonal hatch pattern in <defs> and return the fill string "url(#hid)"; angle in deg, spacing in px."""
    cv = _canvas_of(canvas)
    cv.defs('<pattern id="%s" patternUnits="userSpaceOnUse" width="%s" height="%s" patternTransform="rotate(%s)">'
            '<line x1="0" y1="0" x2="0" y2="%s" stroke="%s" stroke-width="%s"/></pattern>'
            % (hid, _f(spacing), _f(spacing), _f(angle), _f(spacing), color, _f(sw)))
    return "url(#%s)" % hid


def _stops(stops):
    out = []
    for s in stops:
        off, col = s[0], s[1]
        op = s[2] if len(s) > 2 else None
        out.append('<stop offset="%s" stop-color="%s"%s/>' % (_f(off), col, (' stop-opacity="%s"' % _f(op)) if op is not None else ""))
    return "".join(out)


def linear_gradient(canvas, gid, stops, x1=0, y1=0, x2=1, y2=0):
    """Register a linear gradient (x1,y1)->(x2,y2) in 0..1 box units; stops = [(offset, color[, opacity]), ...]; returns "url(#gid)"."""
    cv = _canvas_of(canvas)
    cv.defs('<linearGradient id="%s" x1="%s" y1="%s" x2="%s" y2="%s">%s</linearGradient>'
            % (gid, _f(x1), _f(y1), _f(x2), _f(y2), _stops(stops)))
    return "url(#%s)" % gid


def radial_gradient(canvas, gid, stops, cx=0.5, cy=0.5, r=0.5):
    """Register a radial gradient centered at (cx,cy) radius r in 0..1 box units; stops as in linear_gradient; returns "url(#gid)"."""
    cv = _canvas_of(canvas)
    cv.defs('<radialGradient id="%s" cx="%s" cy="%s" r="%s">%s</radialGradient>' % (gid, _f(cx), _f(cy), _f(r), _stops(stops)))
    return "url(#%s)" % gid


def glow(canvas, fid, std=6):
    """Register a Gaussian-blur filter and return the attribute string 'filter="url(#fid)"' for group(frag, glow(...))."""
    cv = _canvas_of(canvas)
    cv.defs('<filter id="' + fid + '" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="'
            + _f(std) + '"/></filter>')
    return 'filter="url(#%s)"' % fid


CMAPS = {
    "jet": ["#0b1f6e", "#1f6fd6", "#6fc3d8", "#8fd06a", "#f1e23a", "#f5a623", "#e8231a", "#9a0a0a"],
    "hot": ["#fbe9df", "#f8cbb4", "#f3a483", "#eb7250", "#e0402a", "#c81a12", "#8e0909"],
    "viridis": ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
    "gray": ["#000000", "#ffffff"],
    "coolwarm": ["#3b4cc0", "#8db0fe", "#dddddd", "#f49a7b", "#b40426"],
}


def cmap(t, name="jet"):
    """Map t in 0..1 to a hex color; name = "jet" | "hot" | "viridis" | "gray" | "coolwarm" (t is clamped)."""
    cm = CMAPS.get(name, CMAPS["jet"])
    t = _clamp(float(t)) * (len(cm) - 1)
    i = min(int(t), len(cm) - 2)
    return _mix(cm[i], cm[i + 1], t - i)


def colorbar(canvas, x, y, w, h, name="jet", vmin=0, vmax=1, label="", ticks=5, vertical=True, size=13, color=None, fmt=None):
    """Continuous colorbar box (x,y,w,h) for cmap `name` with tick labels from vmin to vmax and a title; needs the canvas for the gradient."""
    color = _ink(color)
    stops = [(i / 11.0, cmap(i / 11.0, name)) for i in range(12)]
    out = []
    cv = _canvas_of(canvas) if canvas is not None else None
    if cv is not None:
        gid = cv.uid("cbar")
        fill = linear_gradient(cv, gid, stops, 0, 1, 0, 0) if vertical else linear_gradient(cv, gid, stops, 0, 0, 1, 0)
        out.append(rect(x, y, w, h, fill, color, 1))
    else:
        n = 24
        for i in range(n):
            t = (i + 0.5) / n
            if vertical:
                out.append(rect(x, y + h * (1 - (i + 1.0) / n), w, h / n + 0.5, cmap(t, name), "none"))
            else:
                out.append(rect(x + w * i / n, y, w / n + 0.5, h, cmap(t, name), "none"))
        out.append(rect(x, y, w, h, "none", color, 1))
    n = max(2, int(ticks))
    for i in range(n):
        t = i / (n - 1.0)
        v = vmin + (vmax - vmin) * t
        s = (fmt % v) if fmt else _num(v)
        if vertical:
            yy = y + h * (1 - t)
            out.append(line(x + w, yy, x + w + 5, yy, color, 1))
            out.append(text(x + w + 8, yy + size * 0.35, s, size, color, anchor="start", halo=None))
        else:
            xx = x + w * t
            out.append(line(xx, y + h, xx, y + h + 5, color, 1))
            out.append(text(xx, y + h + 8 + size * 0.8, s, size, color, halo=None))
    if label:
        if vertical:
            out.append(text(x + w / 2.0, y - size * 0.7, label, size, color, halo=None))
        else:
            out.append(text(x + w / 2.0, y + h + 8 + size * 0.8 + size * 1.3, label, size, color, halo=None))
    return "".join(out)


def axis_box(x, y, size=110, labels=("X", "Y"), color=None, sw=None):
    """Small boxed coordinate frame at top-left (x,y): origin O, X arrow to the right, Y arrow downward (y-down convention)."""
    color = _ink(color)
    k = size / 130.0
    out = [rect(x, y, size, size * 0.885, "#fff", color, _sw(sw) * 1.1),
           text(x + 22 * k, y + 40 * k, "O", 30 * k, color, halo=None),
           arrow(x + 38 * k, y + 50 * k, x + 110 * k, y + 50 * k, color, max(1.5, 3 * k), 12 * k),
           text(x + 118 * k, y + 44 * k, labels[0], 28 * k, color, halo=None),
           arrow(x + 38 * k, y + 50 * k, x + 38 * k, y + 103 * k, color, max(1.5, 3 * k), 12 * k),
           text(x + 60 * k, y + 108 * k, labels[1], 28 * k, color, halo=None)]
    return "".join(out)


def zoom_link(x, y, w, h, tx, ty, tw, th, color=None, sw=1.5, target_box=True):
    """Dashed source box (x,y,w,h) linked by dotted lines to a target box (tx,ty,tw,th) drawn on the facing side."""
    color = _ink(color)
    out = [rect(x, y, w, h, "none", color, sw, dash="6 4")]
    if target_box:
        out.append(rect(tx, ty, tw, th, "none", color, sw))
    ddx, ddy = (tx + tw / 2.0) - (x + w / 2.0), (ty + th / 2.0) - (y + h / 2.0)
    if abs(ddx) >= abs(ddy):
        if ddx > 0:
            pairs = [((x + w, y), (tx, ty)), ((x + w, y + h), (tx, ty + th))]
        else:
            pairs = [((x, y), (tx + tw, ty)), ((x, y + h), (tx + tw, ty + th))]
    else:
        if ddy > 0:
            pairs = [((x, y + h), (tx, ty)), ((x + w, y + h), (tx + tw, ty))]
        else:
            pairs = [((x, y), (tx, ty + th)), ((x + w, y), (tx + tw, ty + th))]
    for a, b in pairs:
        out.append(line(a[0], a[1], b[0], b[1], color, sw * 0.8, dash="2 4"))
    return "".join(out)


def scale_bar(x, y, length_px, label, color=None, sw=3, size=14, tick=6):
    """Horizontal scale bar starting at (x,y) of length_px with end ticks and the label centered above it."""
    color = _ink(color)
    out = [line(x, y, x + length_px, y, color, sw),
           line(x, y - tick, x, y + tick, color, sw * 0.6),
           line(x + length_px, y - tick, x + length_px, y + tick, color, sw * 0.6),
           text(x + length_px / 2.0, y - tick - 4, label, size, color)]
    return "".join(out)


# ----------------------------------------------------------------------------- grains / fields
def jitter_grid(x0, y0, x1, y1, spacing, jitter=0.35, seed=0):
    """Points of a square grid (step = spacing) covering the box, each shifted randomly by up to jitter*spacing; seed fixes them."""
    rnd = random.Random(seed)
    j = jitter * spacing
    pts = []
    y = float(y0)
    while y <= y1 + 1e-9:
        x = float(x0)
        while x <= x1 + 1e-9:
            pts.append((x + rnd.uniform(-j, j), y + rnd.uniform(-j, j)))
            x += spacing
        y += spacing
    return pts


def clip_poly(poly, box):
    """Clip polygon [(x,y),...] to the axis-aligned box (x0,y0,x1,y1); returns the clipped polygon (may be empty)."""
    x0, y0, x1, y1 = box

    def clip(pts, inside, inter):
        out = []
        for i in range(len(pts)):
            P, Q = pts[i - 1], pts[i]
            if inside(Q):
                if not inside(P):
                    out.append(inter(P, Q))
                out.append(Q)
            elif inside(P):
                out.append(inter(P, Q))
        return out

    def ix(P, Q, x):
        t = (x - P[0]) / (Q[0] - P[0])
        return (x, P[1] + t * (Q[1] - P[1]))

    def iy(P, Q, y):
        t = (y - P[1]) / (Q[1] - P[1])
        return (P[0] + t * (Q[0] - P[0]), y)

    pts = [(float(p[0]), float(p[1])) for p in poly]
    for f in [(lambda p: p[0] >= x0, lambda P, Q: ix(P, Q, x0)), (lambda p: p[0] <= x1, lambda P, Q: ix(P, Q, x1)),
              (lambda p: p[1] >= y0, lambda P, Q: iy(P, Q, y0)), (lambda p: p[1] <= y1, lambda P, Q: iy(P, Q, y1))]:
        if not pts:
            break
        pts = clip(pts, *f)
    return pts


def voronoi_grains(seeds, box, pad=80):
    """Voronoi cells of seed points [(x,y),...] clipped to box (x0,y0,x1,y1); returns a list of polygons (infinite cells dropped)."""
    x0, y0, x1, y1 = box
    step = max(10.0, pad / 2.0)
    ring = []
    for x in np.arange(x0 - pad, x1 + pad + 1, step):
        ring += [(x, y0 - pad), (x, y1 + pad)]
    for y in np.arange(y0 - pad, y1 + pad + 1, step):
        ring += [(x0 - pad, y), (x1 + pad, y)]
    seeds = [(float(p[0]), float(p[1])) for p in seeds]
    vor = Voronoi(np.array(seeds + ring, dtype=float))
    cells = []
    for i in range(len(seeds)):
        reg = vor.regions[vor.point_region[i]]
        if not reg or -1 in reg:
            continue
        poly = clip_poly([tuple(vor.vertices[j]) for j in reg], box)
        if len(poly) >= 3:
            cells.append(poly)
    return cells


def displace(points, fn):
    """Apply fn(x, y) -> (x, y) to every vertex; accepts one polygon [(x,y),...] or a list of polygons and keeps the shape."""
    points = list(points)
    if not points:
        return []
    first = points[0]
    if len(first) and isinstance(first[0], (list, tuple, np.ndarray)):
        return [displace(p, fn) for p in points]
    return [tuple(fn(float(p[0]), float(p[1]))) for p in points]


def _eval_field(fn, X, Y):
    try:
        T = np.asarray(fn(X, Y), dtype=float)
        if T.shape == X.shape:
            return T
    except Exception:
        pass
    T = np.zeros(X.shape)
    for j in range(X.shape[0]):
        for i in range(X.shape[1]):
            T[j, i] = float(fn(float(X[j, i]), float(Y[j, i])))
    return T


def field_layer(x0, y0, x1, y1, fn, nx=40, ny=None, name="jet", opacity=1, blur=None, canvas=None):
    """Fill the box with small rects colored by cmap(fn(x, y)) on an nx x ny grid (fn returns 0..1); blur=px softens (needs canvas)."""
    nx = max(1, int(nx))
    cw = (x1 - x0) / float(nx)
    if ny is None:
        ny = max(1, int(round((y1 - y0) / cw)))
    ch = (y1 - y0) / float(ny)
    pad = 1 if blur else 0
    xs = x0 + (np.arange(-pad, nx + pad) + 0.5) * cw
    ys = y0 + (np.arange(-pad, ny + pad) + 0.5) * ch
    X, Y = np.meshgrid(xs, ys)
    T = _eval_field(fn, X, Y)
    out = []
    for j in range(X.shape[0]):
        for i in range(X.shape[1]):
            out.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>'
                       % (_f(X[j, i] - cw / 2 - 0.4), _f(Y[j, i] - ch / 2 - 0.4), _f(cw + 0.8), _f(ch + 0.8), cmap(T[j, i], name)))
    g = "".join(out)
    if blur and canvas is not None:
        cv = _canvas_of(canvas)
        fid, cid = cv.uid("blur"), cv.uid("clip")
        cv.defs('<filter id="' + fid + '" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="'
                + _f(blur) + '"/></filter>')
        cv.defs('<clipPath id="%s"><rect x="%s" y="%s" width="%s" height="%s"/></clipPath>' % (cid, _f(x0), _f(y0), _f(x1 - x0), _f(y1 - y0)))
        g = '<g clip-path="url(#%s)"><g filter="url(#%s)">%s</g></g>' % (cid, fid, g)
    if opacity is not None and float(opacity) < 1:
        g = '<g opacity="%s">%s</g>' % (_f(opacity), g)
    return g


def shade_polygons(polys, fn, name="jet", stroke=None, sw=1.2, opacity=1):
    """Draw polygons each filled with cmap(fn(cx, cy)) evaluated at the polygon centroid (fn returns 0..1)."""
    out = []
    for poly in polys:
        if len(poly) < 3:
            continue
        cx = sum(p[0] for p in poly) / float(len(poly))
        cy = sum(p[1] for p in poly) / float(len(poly))
        out.append(polygon(poly, cmap(fn(cx, cy), name), stroke, sw, opacity))
    return "".join(out)


def grain_field(box, spacing=60, seed=0, deform=None, jitter=0.35):
    """Random grain polygons filling box (x0,y0,x1,y1): jittered grid + Voronoi, optionally warped by deform(x, y) -> (x, y)."""
    x0, y0, x1, y1 = box
    seeds = jitter_grid(x0 - spacing, y0 - spacing, x1 + spacing, y1 + spacing, spacing, jitter, seed)
    polys = voronoi_grains(seeds, box, pad=max(80, 2 * spacing))
    if deform is not None:
        polys = [clip_poly(displace(p, deform), box) for p in polys]
        polys = [p for p in polys if len(p) >= 3]
    return polys


# ----------------------------------------------------------------------------- machining parts (return (svg, info))
def tool(x_tip, y_tip, rake=0, clearance=6, nose_r=6, length=260, height=180, fill="#f6cd4e", stroke=None, sw=None,
         direction="left"):
    """Tool wedge with its cutting edge at (x_tip,y_tip); body extends to `direction`; rake>0 tilts the rake face back over the body."""
    s = -1.0 if direction == "left" else 1.0
    a, g = math.radians(rake), math.radians(clearance)
    rd = (s * math.sin(a), -math.cos(a))          # unit vector up the rake face from the tip
    fd = (s * math.cos(g), -math.sin(g))          # unit vector along the flank from the tip toward the body
    R = (x_tip + s * height * math.tan(a), y_tip - height)
    B1 = (x_tip + s * length, y_tip - height)
    F = (x_tip + s * length, y_tip - length * math.tan(g))
    Lr = height / max(math.cos(a), 1e-6)
    r = max(0.0, min(nose_r, Lr * 0.4, length * 0.4))
    Tr = (x_tip + rd[0] * r, y_tip + rd[1] * r)
    Tf = (x_tip + fd[0] * r, y_tip + fd[1] * r)
    d = "M %s %s L %s %s L %s %s L %s %s L %s %s Q %s %s %s %s Z" % (
        _f(Tr[0]), _f(Tr[1]), _f(R[0]), _f(R[1]), _f(B1[0]), _f(B1[1]), _f(F[0]), _f(F[1]), _f(Tf[0]), _f(Tf[1]),
        _f(x_tip), _f(y_tip), _f(Tr[0]), _f(Tr[1]))
    svg = path(d, fill, stroke, sw if sw is not None else _sw(None) * 1.5)
    info = {
        "tip": (x_tip, y_tip), "rake_end": R, "flank_end": F, "back_top": B1,
        "rake_mid": ((x_tip + R[0]) / 2.0, (y_tip + R[1]) / 2.0), "flank_mid": ((x_tip + F[0]) / 2.0, (y_tip + F[1]) / 2.0),
        "center": (x_tip + s * length * 0.55, y_tip - height * 0.55),
        "rake_dir": rd, "flank_dir": fd, "rake_normal": (-s * math.cos(a), -math.sin(a)),
        "chip_angle": math.degrees(math.atan2(-rd[1], rd[0])), "side": s, "rake": rake, "clearance": clearance,
    }
    return svg, info


def workpiece(x, y, w, h, fill="#dcdcdc", stroke=None, sw=None, uncut=None, cut_depth=0, side="right", uncut_fill=None):
    """Block (x,y,w,h); cut_depth>0 lowers the top except an uncut layer on `side` starting at x=uncut; tool tip = (uncut, y+cut_depth)."""
    sw = sw if sw is not None else _sw(None) * 1.5
    x, y, w, h = float(x), float(y), float(w), float(h)
    if cut_depth <= 0:
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        tip = (x, y) if side == "right" else (x + w, y)
        layer = []
        xs, ym = tip[0], y
    else:
        xs = uncut if uncut is not None else (x + 0.6 * w if side == "right" else x + 0.4 * w)
        xs = min(max(float(xs), x), x + w)
        ym = y + cut_depth
        if side == "right":
            pts = [(x, ym), (xs, ym), (xs, y), (x + w, y), (x + w, y + h), (x, y + h)]
            layer = [(xs, y), (x + w, y), (x + w, ym), (xs, ym)]
        else:
            pts = [(x, y), (xs, y), (xs, ym), (x + w, ym), (x + w, y + h), (x, y + h)]
            layer = [(x, y), (xs, y), (xs, ym), (x, ym)]
        tip = (xs, ym)
    svg = polygon(pts, fill, "none")
    if uncut_fill and layer:
        svg += polygon(layer, uncut_fill, "none")
    svg += polygon(pts, "none", stroke, sw)
    info = {"tip": tip, "step_x": xs, "machined_y": ym, "top_y": y, "bottom_y": y + h, "left": x, "right": x + w,
            "center": (x + w / 2.0, (ym + y + h) / 2.0), "layer": layer, "outline": pts, "side": side,
            "machined": ((x, ym), (xs, ym)) if side == "right" else ((xs, ym), (x + w, ym)),
            "uncut_top": ((xs, y), (x + w, y)) if side == "right" else ((x, y), (xs, y))}
    return svg, info


def chip_thickness(t_uncut, phi, rake=0):
    """Chip thickness from uncut thickness t_uncut, shear angle phi and rake angle (deg): t_uncut*cos(phi-rake)/sin(phi)."""
    return t_uncut * math.cos(math.radians(phi - rake)) / max(math.sin(math.radians(phi)), 1e-6)


def chip(x_tip, y_tip, thickness=40, length=260, curl=0.8, up_angle=70, fill="#c8c8c8", stroke=None, sw=None, serrated=False,
         teeth=10, phi=None, direction="left"):
    """Chip from (x_tip,y_tip) along up_angle (deg, 90 = up) on the side opposite the tool body (direction as in tool()); curl 0..1 (<0 flips)."""
    sw = sw if sw is not None else _sw(None) * 1.5
    a = math.radians(up_angle)
    d = (math.cos(a), -math.sin(a))
    side = 1.0 if direction == "left" else -1.0
    n = (side * math.sin(a), side * math.cos(a))    # unit normal from the rake-side face into the chip
    bend = 1.0 if curl >= 0 else -1.0
    curl = _clamp(abs(curl))
    t = float(thickness)
    # root: where the free face starts (slanted along the shear plane if phi given)
    s_free0 = 0.0
    root_end = (x_tip + n[0] * t, y_tip + n[1] * t)
    if phi is not None:
        sd = (side * math.cos(math.radians(phi)), -math.sin(math.radians(phi)))
        dot = sd[0] * n[0] + sd[1] * n[1]
        if dot > 0.2:
            rho = t / dot
            root_end = (x_tip + sd[0] * rho, y_tip + sd[1] * rho)
            s_free0 = (root_end[0] - x_tip) * d[0] + (root_end[1] - y_tip) * d[1]
    Ls = length * 0.3 if curl > 0 else float(length)
    theta = curl * math.radians(170)
    Rc = (length - Ls) / theta if theta > 1e-6 else float("inf")
    Rc = max(Rc, t * 0.7)
    theta = (length - Ls) / Rc if Rc != float("inf") else 0.0
    p0 = (x_tip + n[0] * t / 2.0, y_tip + n[1] * t / 2.0)
    p1 = (p0[0] + d[0] * Ls, p0[1] + d[1] * Ls)
    C = (p1[0] + bend * n[0] * Rc, p1[1] + bend * n[1] * Rc) if theta > 0 else None

    def center(s):
        if C is None or s <= Ls:
            return (p0[0] + d[0] * s, p0[1] + d[1] * s), n
        ph = (s - Ls) / Rc
        c = (C[0] - bend * n[0] * Rc * math.cos(ph) + d[0] * Rc * math.sin(ph),
             C[1] - bend * n[1] * Rc * math.cos(ph) + d[1] * Rc * math.sin(ph))
        nrm = (bend * (C[0] - c[0]) / Rc, bend * (C[1] - c[1]) / Rc)
        return c, nrm

    N = max(24, int(length / 5))
    rake_edge, free_edge = [], []
    for i in range(N + 1):
        s = length * i / float(N)
        c, nrm = center(s)
        rake_edge.append((c[0] - nrm[0] * t / 2.0, c[1] - nrm[1] * t / 2.0))
        if s >= s_free0:
            free_edge.append((c[0] + nrm[0] * t / 2.0, c[1] + nrm[1] * t / 2.0, nrm[0], nrm[1], s))
    if not free_edge or free_edge[0][4] > s_free0 + 1e-6:
        c, nrm = center(s_free0)
        free_edge.insert(0, (c[0] + nrm[0] * t / 2.0, c[1] + nrm[1] * t / 2.0, nrm[0], nrm[1], s_free0))
    end_c, end_n = center(length)
    end_dir = (rake_edge[-1][0] - rake_edge[-2][0], rake_edge[-1][1] - rake_edge[-2][1])
    el = math.hypot(*end_dir) or 1.0
    end_dir = (end_dir[0] / el, end_dir[1] / el)
    # outline
    parts = ["M %s %s" % (_f(x_tip), _f(y_tip))]
    parts += ["L %s %s" % (_f(p[0]), _f(p[1])) for p in rake_edge[1:]]
    cap = (end_c[0] + end_dir[0] * t * 0.35, end_c[1] + end_dir[1] * t * 0.35)
    fe_last = free_edge[-1]
    parts.append("Q %s %s %s %s" % (_f(cap[0]), _f(cap[1]), _f(fe_last[0]), _f(fe_last[1])))
    if serrated and len(free_edge) > 3:
        m = max(2, int(teeth))
        s_a, s_b = free_edge[0][4], length * 0.94
        pts = []
        for k in range(m + 1):
            s = s_a + (s_b - s_a) * k / float(m)
            c, nrm = center(s)
            pts.append((c[0] + nrm[0] * t / 2.0, c[1] + nrm[1] * t / 2.0, nrm[0], nrm[1]))
        depth = t * 0.22
        out_pts = [free_edge[-1][:2]]
        for k in range(m, 0, -1):
            B, A = pts[k], pts[k - 1]
            peak = (_lerp(B[0], A[0], 0.45) + (A[2] + B[2]) / 2.0 * depth, _lerp(B[1], A[1], 0.45) + (A[3] + B[3]) / 2.0 * depth)
            out_pts += [B[:2], peak]
        out_pts.append(pts[0][:2])
        parts += ["L %s %s" % (_f(p[0]), _f(p[1])) for p in out_pts[1:]]
    else:
        parts += ["L %s %s" % (_f(p[0]), _f(p[1])) for p in reversed(free_edge[:-1])]
    parts.append("Z")
    svg = path(" ".join(parts), fill, stroke, sw)
    xs = [p[0] for p in rake_edge] + [p[0] for p in free_edge]
    ys = [p[1] for p in rake_edge] + [p[1] for p in free_edge]
    fm = free_edge[len(free_edge) // 2]
    info = {"tip": (x_tip, y_tip), "root_end": root_end, "end": end_c, "free_mid": (fm[0], fm[1]),
            "rake_mid": rake_edge[len(rake_edge) // 2], "top": (xs[ys.index(min(ys))], min(ys)),
            "bbox": (min(xs), min(ys), max(xs), max(ys)), "dir": d, "side": side, "thickness": t, "curl_center": C}
    return svg, info


def shear_plane(x_tip, y_tip, phi=25, length=120, color="#e53935", sw=2, dash="6 5", label=None, direction="left", size=14):
    """Shear-plane line from the tip at angle phi (deg from the horizontal cutting direction) going up-forward; direction as in tool()."""
    s = 1.0 if direction == "left" else -1.0
    a = math.radians(phi)
    ex, ey = x_tip + s * length * math.cos(a), y_tip - length * math.sin(a)
    svg = line(x_tip, y_tip, ex, ey, color, sw, dash)
    if label:
        lx, ly = ex + s * 8 * math.cos(a) + s * 4, ey - 8 * math.sin(a)
        svg += text(lx, ly + size * 0.35, label, size, color, anchor="start" if s > 0 else "end")
    info = {"tip": (x_tip, y_tip), "end": (ex, ey), "mid": ((x_tip + ex) / 2.0, (y_tip + ey) / 2.0), "angle": phi,
            "dir": (s * math.cos(a), -math.sin(a))}
    return svg, info


def _auto_label(x, y, s, size, fill, anchor="middle", halo="#fff"):
    s = str(s)
    if len(s) >= 2 and s[0].isalpha() and s[1:].isalnum() and s[1:].islower() or (len(s) >= 2 and s[0].isalpha() and s[1:].isdigit()):
        return sub(x, y, s[0], s[1:], size, fill, True, anchor, halo)
    return text(x, y, s, size, fill, italic=True, anchor=anchor, halo=halo)


def force_vectors(x, y, forces, scale=1.0, color=None, size=14, w=3, head=12):
    """Force arrows from (x,y): forces = [(label, angle_deg, magnitude_px[, color]), ...], angle 0 = +x, 90 = up; labels past the heads."""
    color = _ink(color)
    out, ends = [], {}
    for f in forces:
        label, ang, mag = f[0], float(f[1]), float(f[2]) * scale
        col = f[3] if len(f) > 3 else color
        a = math.radians(ang)
        ex, ey = x + mag * math.cos(a), y - mag * math.sin(a)
        out.append(arrow(x, y, ex, ey, col, w, head))
        lx, ly = ex + size * 0.9 * math.cos(a), ey - size * 0.9 * math.sin(a)
        anchor = "start" if math.cos(a) > 0.3 else ("end" if math.cos(a) < -0.3 else "middle")
        out.append(_auto_label(lx, ly + size * 0.35, label, size, col, anchor))
        ends[label] = (ex, ey)
    out.append(circle(x, y, max(2.0, w * 0.9), color, "none"))
    return "".join(out), {"origin": (x, y), "ends": ends}


def velocity_arrow(x, y, dx, dy, label="Vc", color=None, w=4, head=14, size=16):
    """Velocity arrow from (x,y) to (x+dx, y+dy) with an italic label (e.g. Vc rendered as V with subscript c) beside its middle."""
    color = _ink(color)
    L = math.hypot(dx, dy) or 1.0
    nx, ny = dy / L, -dx / L
    if ny > 0.01 or (abs(ny) <= 0.01 and nx < 0):
        nx, ny = -nx, -ny
    mx, my = x + dx / 2.0 + nx * (size * 0.9 + w), y + dy / 2.0 + ny * (size * 0.9 + w)
    svg = arrow(x, y, x + dx, y + dy, color, w, head)
    if label:
        anchor = "start" if nx > 0.3 else ("end" if nx < -0.3 else "middle")
        svg += _auto_label(mx, my + size * 0.35, label, size, color, anchor)
    return svg, {"start": (x, y), "end": (x + dx, y + dy), "mid": (x + dx / 2.0, y + dy / 2.0), "label_pos": (mx, my)}


# ----------------------------------------------------------------------------- API reference
_API = [
    ("Canvas and panels", ["Canvas", "Panel", "style", "group"]),
    ("Primitives (return SVG strings)", ["rect", "circle", "ellipse", "polygon", "polyline", "line", "path", "pathd", "curve",
                                         "arrow", "darrow", "dimension", "angle_mark"]),
    ("Text", ["text", "lines", "label_box", "sub"]),
    ("Defs, colors, decorations", ["hatch", "linear_gradient", "radial_gradient", "glow", "cmap", "colorbar", "axis_box",
                                   "zoom_link", "scale_bar"]),
    ("Grains and fields", ["jitter_grid", "voronoi_grains", "displace", "clip_poly", "field_layer", "shade_polygons", "grain_field"]),
    ("Machining parts (each returns (svg, info))", ["tool", "workpiece", "chip", "chip_thickness", "shear_plane", "force_vectors",
                                                    "velocity_arrow"]),
]
_METHODS = {"Canvas": ["add", "defs", "panel", "svg", "uid"], "Panel": ["add", "defs", "uid", "to_canvas"]}


def _doc1(obj):
    doc = inspect.getdoc(obj) or ""
    for ln in doc.splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def _sig(obj, drop_self=False):
    try:
        s = str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(...)"
    if drop_self:
        s = s.replace("(self, ", "(").replace("(self)", "()")
    return s


def api_doc():
    """Return a compact text reference of this module's public API: one line per function/class (signature + summary)."""
    g = globals()
    out = ["schematic.lib - px units, origin top-left, y DOWN; direction angles: 0 = +x, 90 = up. All drawing functions return",
           "SVG strings; stroke/sw/fill=None means the current style default. Usage: c = Canvas(w, h); p = c.panel(...);",
           "p.add(rect(...)); svg = c.svg().  Machining parts return (svg, info) - add() accepts the tuple directly."]
    for title, names in _API:
        out.append("")
        out.append("## " + title)
        for n in names:
            obj = g.get(n)
            if obj is None:
                continue
            if inspect.isclass(obj):
                out.append("class %s%s: %s" % (n, _sig(obj), _doc1(obj)))
                for mn in _METHODS.get(n, []):
                    m = getattr(obj, mn, None)
                    if m is not None:
                        out.append("  .%s%s: %s" % (mn, _sig(m, True), _doc1(m)))
                if n == "Panel":
                    out.append("  .w, .h, .x, .y: panel size and position on the canvas")
            else:
                out.append("%s%s: %s" % (n, _sig(obj), _doc1(obj)))
    return "\n".join(out)
