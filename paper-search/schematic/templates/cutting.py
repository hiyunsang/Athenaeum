import sys; sys.path.insert(0, r'C:\Users\PC1\Documents\MAENG_paper\paper-search')
from schematic.lib import *
import math

style("journal")
c = Canvas(900, 700)

INK, GRAY = "#1b1b1b", "#7a8a99"
C_WORK, C_LAYER, C_TOOL, C_CHIP = "#cfd8e3", "#9fbcd8", "#f4c95d", "#e0a256"
ACC, C_F, C_V = "#c62828", "#a00000", "#2e7d32"

rake, clear_a, phi = -10.0, 6.0, 25.0          # rake (negative), clearance, shear angle
tipx, tipy = 470.0, 426.0                      # cutting edge
h = 46.0                                       # uncut chip thickness
wx, wtop, ww, wh = 70.0, tipy - h, 670.0, 150.0
t_chip = chip_thickness(h, phi, rake)          # deformed chip thickness
sl = h / math.sin(math.radians(phi))           # shear-plane length (tip -> free surface)

# ---------------------------------------------------------------- workpiece
c.add(workpiece(wx, wtop, ww, wh, fill=C_WORK, uncut=tipx, cut_depth=h,
                side="left", uncut_fill=C_LAYER))
c.add(line(wx + 30, tipy, tipx - 135, tipy, GRAY, 1.2, dash="6 4"))

# ---------------------------------------------------------------- chip . tool
tsvg, ti = tool(tipx, tipy, rake=rake, clearance=clear_a, nose_r=5,
                length=250, height=170, fill=C_TOOL, direction="right")
c.add(chip(tipx, tipy, thickness=t_chip, length=370, curl=0.78,
           up_angle=ti["chip_angle"], fill=C_CHIP, serrated=True, teeth=11,
           phi=phi, direction="right"))
c.add(tsvg)

# ---------------------------------------------------------------- shear plane . angles
c.add(shear_plane(tipx, tipy, phi=phi, length=sl, color=ACC, sw=2.4,
                  dash="7 5", direction="right"))
c.add(angle_mark(tipx, tipy, 60, 180 - phi, 180, "φ = 25°", ACC, 14, 1.6))
c.add(line(tipx, tipy, tipx, tipy - 182, GRAY, 1.2, dash="8 4 2 4"))
c.add(angle_mark(tipx, tipy, 150, 90, 90 - rake, "γ", INK, 15, 1.4))
c.add(angle_mark(tipx, tipy, 150, 0, clear_a, "α", INK, 14, 1.4))

# ---------------------------------------------------------------- forces . velocity
c.add(force_vectors(tipx, tipy, [("Fc", 180, 130, C_F), ("Ft", 270, 72, C_F)],
                    color=C_F, size=16, w=5.5, head=17))
c.add(velocity_arrow(105, 470, 155, 0, label="Vc", color=C_V, w=4.5, head=15, size=17))
c.add(text(182, 497, "cutting speed", 14, C_V))

# ---------------------------------------------------------------- dimension
c.add(dimension(150, wtop, 150, tipy, "h", offset=-34, color=INK, size=15))
c.add(lines(165, 330, ["uncut chip", "thickness h"], 14, 18, fill=INK))

# ---------------------------------------------------------------- labels
c.add(text(250, 178, "serrated chip", 16, INK, weight="bold"))
c.add(line(318, 377, 412, 398, ACC, 1.4))
c.add(text(248, 372, "shear plane", 14, ACC))
c.add(lines(615, 300, ["tool", "rake angle γ = −10°", "clearance α = 6°"], 15, 23,
            fill=INK, weight="bold"))
c.add(text(355, 450, "cutting force", 14, C_F, weight="bold"))
c.add(text(520, 512, "thrust force", 14, C_F, weight="bold", anchor="start"))
c.add(text(640, 445, "machined surface", 14, INK))
c.add(text(660, 480, "workpiece", 16, INK, weight="bold"))

c.add(text(420, 46, "orthogonal cutting model", 21, INK, weight="bold"))
c.add(axis_box(755, 40, 110, ("X", "Y"), INK))

print(c.svg())