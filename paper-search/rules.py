# -*- coding: utf-8 -*-
"""제목 키워드 -> 라벨 제안 규칙. (제안일 뿐, 확정은 사용자가 검색 화면에서 함)"""
import re

# (정규식, [라벨...]) - 파일명의 제목 부분(소문자)에 적용
TITLE_RULES = [
    (r"silicon(?!e)", ["Silicon"]),
    (r"alumini?um|al ?6061|a356|almg", ["Aluminum"]),
    (r"al ?6061", ["Al6061"]),
    (r"titanium|ti-?6al-?4v", ["Titanium"]),
    (r"ti-?6al-?4v", ["Ti6Al4V"]),
    (r"\bsteel|aisi ?\d|s45c", ["Steel"]),
    (r"stainless|sus ?304", ["Stainless steel"]),
    (r"sus ?304", ["SUS304"]),
    (r"aisi ?1045", ["AISI 1045"]),
    (r"inconel|nickel-? ?based|superalloy", ["Nickel-based alloy"]),
    (r"inconel ?718", ["Inconel 718"]),
    (r"\bcopper\b", ["Copper"]),
    (r"tantalum", ["Tantalum"]),
    (r"znse|zinc selenide", ["ZnSe", "Soft-brittle"]),
    (r"\bkdp\b|kh2po4|potassium dihydrogen phosphate", ["KDP", "Soft-brittle"]),
    (r"soft-?brittle|soft brittle", ["Soft-brittle"]),
    (r"germanium", ["Germanium"]),
    (r"caf2|calcium fluoride|fluorite", ["CaF2", "Soft-brittle"]),
    (r"(?<!metallic )\bglass\b(?! form)", ["Glass"]),
    (r"sapphire", ["Sapphire"]),
    (r"ceramic|mgal2o4|zirconia|nanoceramic", ["Ceramic"]),
    (r"composite|cfrp|\bmmcs?\b", ["Composite"]),
    (r"single-? ?crystal|monocrystal", ["Single crystal"]),
    (r"polycrystal", ["Polycrystal"]),
    (r"anisotrop", ["Anisotropic"]),
    (r"additive|3d print", ["Additive manufactured"]),
    (r"orthogonal", ["Orthogonal cutting"]),
    (r"turning", ["Turning"]),
    (r"milling", ["Milling"]),
    (r"drilling", ["Drilling"]),
    (r"grinding", ["Grinding"]),
    (r"scratch", ["Scratching"]),
    (r"sliding", ["Sliding"]),
    (r"micro-? ?(cutting|machining|milling|burr)|micrometric", ["Micro-machining"]),
    (r"nano-? ?(metric|cutting|scale cutting)|nanocutting", ["Nano-cutting"]),
    (r"ultra-? ?precision|diamond (cutting|turning)", ["Ultra-precision machining"]),
    (r"cryogenic", ["Cryogenic"]),
    (r"textur", ["Textured tool"]),
    (r"built-? ?up-? ?edge|\bbue\b", ["BUE"]),
    (r"built-? ?up-? ?layer|\bbul\b", ["Built-up layer"]),
    (r"dead metal zone|\bdmz\b", ["Dead metal zone"]),
    (r"stagnat", ["Stagnation zone"]),
    (r"chip (formation|morpholog|geometr|breaking|curl|speed)", ["Chip formation"]),
    (r"shear (localization|band)|adiabatic shear", ["Shear localization"]),
    (r"ductile.{1,3}brittle|brittle transition|ductile mode|ductilit", ["Ductile-brittle transition"]),
    (r"sinuous", ["Sinuous flow"]),
    (r"surface (defect|deterioration|integrity|finish|roughness|generation)", ["Surface defect"]),
    (r"\bburrs?\b", ["Burr"]),
    (r"(tool|flank|crater) wear|wear (behavior|resistance)", ["Tool wear"]),
    (r"adhesion|adhesive|transfer layer", ["Adhesion"]),
    (r"friction|tribolog|tool-? ?chip (contact|interface)", ["Friction"]),
    (r"subsurface", ["Subsurface damage"]),
    (r"in-? ?situ|in-? ?sem", ["In-situ"]),
    (r"high-? ?speed (imaging|camera|photograph|film)", ["High-speed imaging"]),
    (r"\bsem\b|scanning electron|in-? ?sem", ["SEM"]),
    (r"\bdic\b|digital image correlation", ["DIC"]),
    (r"\bpiv\b|particle image", ["PIV"]),
    (r"ebsd", ["EBSD"]),
    (r"\beds\b", ["EDS"]),
    (r"quick-? ?stop", ["Quick-stop"]),
    (r"finite element|\bfem\b|\bfea\b|numerical (model|simulation|study)", ["FEM"]),
    (r"molecular dynamics", ["Molecular dynamics"]),
    (r"analytical (model|approach|tool)|slip-? ?line|machining theory", ["Analytical model"]),
    (r"machine learning|neural network|artificial intelligence|\bdeep learning", ["Machine learning"]),
    (r"strain hardening|work hardening", ["Strain hardening"]),
    (r"thermal soften", ["Thermal softening"]),
    (r"thermal|temperature|heat transfer", ["Thermal effect"]),
    (r"plastic (flow|strain|deformation)|flow dynamics", ["Plastic flow"]),
    (r"fracture|crack", ["Fracture"]),
    (r"damage", ["Damage"]),
    (r"phase transformation", ["Phase transformation"]),
    (r"material separation", ["Material separation"]),
    (r"recrystalliz", ["Recrystallization"]),
]


# 본문에는 너무 흔하게 등장해서 제목에서만 판단하는 라벨
# (예: temperature는 거의 모든 절삭 논문에 나옴 - 본문 기준으로 붙이면 노이즈)
TEXT_TOO_GENERIC = {
    "Thermal effect", "Chip formation", "Friction", "Damage", "Fracture",
    "Plastic flow", "Tool wear", "Surface defect", "Adhesion", "Steel",
    "Analytical model", "FEM", "Anisotropic", "Strain hardening",
    "Material separation", "Turning", "Milling",
    "Silicon",  # 다른 재료 논문에서 비교 재료로 자주 언급되어 오탐이 잦음
}


def suggest_labels(title):
    title = title.lower()
    out = []
    for pattern, labels in TITLE_RULES:
        if re.search(pattern, title):
            for lb in labels:
                if lb not in out:
                    out.append(lb)
    return out


def keyword_section(fulltext):
    """논문의 저자 키워드(Keywords:) 부분을 뽑는다. 서론이 섞여 들어가지 않게 자른다."""
    m = re.search(r"(?:key ?words?|index terms)[:\-\s]+(.{5,300})", fulltext)
    if not m:
        return ""
    kw = m.group(1)
    cut = re.search(r"\b(?:1\.?\s*)?introduction\b", kw)
    return kw[:cut.start()] if cut else kw


def classify_labels(title, fulltext):
    """(자동확정 라벨, 제안 라벨) 반환.

    자동확정: 제목 일치, 저자 키워드 일치, 또는 본문 6회 이상 + 앞부분 등장.
    제안(?): 본문 3~5회 + 앞부분 등장 (흔한 단어 제외).
    """
    title_l = title.lower()
    text = fulltext or ""
    front = text[:3000]
    kw = keyword_section(text)
    auto, sugg = [], []
    for pattern, labels in TITLE_RULES:
        strong = bool(re.search(pattern, title_l)) or bool(kw and re.search(pattern, kw))
        count = len(re.findall(pattern, text)) if text else 0
        in_front = bool(text and re.search(pattern, front))
        for lb in labels:
            if lb in auto:
                continue
            if strong:
                auto.append(lb)
            elif lb in TEXT_TOO_GENERIC:
                continue
            elif count >= 6 and in_front:
                auto.append(lb)
            elif count >= 3 and in_front and lb not in sugg:
                sugg.append(lb)
    return auto, [s for s in sugg if s not in auto]


def suggest_from_text(title, fulltext):
    """제목 규칙 + 본문 빈도 규칙. 본문은 '자주(5회 이상) + 앞부분(초록/서론)에도 등장'일 때만.

    스치듯 언급한 단어가 라벨이 되는 것을 막는다. 결과는 어차피 '제안'이라
    최종 확정은 사용자가 한다.
    """
    out = suggest_labels(title)
    if not fulltext:
        return out
    front = fulltext[:3000]
    for pattern, labels in TITLE_RULES:
        if all(lb in out or lb in TEXT_TOO_GENERIC for lb in labels):
            continue
        try:
            count = len(re.findall(pattern, fulltext))
        except re.error:
            continue
        if count >= 5 and re.search(pattern, front):
            for lb in labels:
                if lb not in out and lb not in TEXT_TOO_GENERIC:
                    out.append(lb)
    return out
