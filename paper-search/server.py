# -*- coding: utf-8 -*-
"""논문 라벨 검색 로컬 서버. 논문검색_실행.bat 으로 실행."""
import html as html_mod
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import rules

ARCHIVE = r"C:\Users\PC1\Documents\MAENG_paper\논문모음"
PORT = 8770
TAGS_PATH = os.path.join(BASE, "tags.json")
LABELS_PATH = os.path.join(BASE, "labels.json")
TEXTS_PATH = os.path.join(BASE, "text_index.json")
_lock = threading.Lock()
_texts_cache = None
_refreshing = threading.Event()

# 요약/전문번역 (claude 명령줄 = 구독 토큰 사용, API 키 불필요)
GEN_DIR = os.path.join(os.path.dirname(ARCHIVE), "번역")
_jobs = {}  # (파일명, 종류) -> {"status": "running"|"error", "error": str}
_gen_sem = threading.BoundedSemaphore(2)  # 동시 생성 2개까지, 나머지는 자동 대기
_map_sem = threading.BoundedSemaphore(1)  # 맵은 한 번에 하나 (OpenAlex 속도 제한 보호)

def paper_header(name):
    """'[저널약어] (연도), 제목' - 요약/번역 첫 줄용."""
    with _lock:
        tags = load_json(TAGS_PATH, {})
    meta = parse_name(name)
    title = tags.get(name, {}).get("title") or meta["title"]
    return "[{}] ({}), {}".format(meta["journal"] or "?", meta["year"] or "?", title)


def build_prompt(kind, header):
    if kind == "summary":
        return (
            "당신은 기계가공(Machining)·재료(Material) 분야 학술 논문을 한국어로 상세하게 "
            "분석·요약하는 '전문 논문 분석가'다. 아래 논문 전문을 읽고 마크다운 요약을 작성하라.\n\n"
            "첫 줄은 반드시 다음 제목으로 시작하라:\n"
            "# (요약) " + header + "\n\n"
            "이후 섹션 구성:\n"
            "## 한줄 요약 - 무엇을 했고 무엇을 발견했는지 2~3문장\n"
            "## 연구 배경과 목적 - 배경, 필요성, 기존 연구와의 차별점을 상세하게 해설\n"
            "## 실험/해석 설정 - 장비, 재료, 공정 조건, 측정 방법을 구체적 수치까지 누락 없이\n"
            "## 결과 및 고찰 - 실험 데이터와 그래프가 의미하는 바, 주요 발견 사항을 길고 "
            "심도 있게 서술 (구체적 수치 포함)\n"
            "## 결론 - 저자들의 결론\n"
            "## 한계 및 시사점 - 한계와 후속 연구 관점\n"
            "## 그림·표 설명 - 논문의 모든 그림(Fig.)과 표(Table)의 캡션을 순서대로 번역해 나열 "
            "(형식: **Fig. 1** - 번역된 캡션). 원문 PDF를 옆에 두고 대조하는 용도이므로 하나도 빠뜨리지 마라.\n\n"
            "규칙:\n"
            "- 답변 길이에 제한을 두지 말고 최대한 상세하고 풍부하게 작성하라.\n"
            "- 표준 한국어 기술 용어를 사용하고(예: feed rate → 이송 속도, microstructure → 미세 조직), "
            "중요 용어는 첫 등장 시 영어를 병기하라 (예: 구성인선(built-up edge, BUE)).\n"
            "- 단순 직역이 아니라 논문의 논리를 파악한 자연스럽고 격식 있는 학술 문체로 쓰라.\n"
            "- 요약 외 다른 말(인사말, 마무리 질문)은 절대 하지 마라.\n\n=== 논문 전문 ===\n"
        )
    return (
        "당신은 기계가공(Machining)·재료(Material) 분야 학술 논문을 정확하고 전문적으로 "
        "번역하는 '전문 논문 번역가'다. 아래 논문 전문을 한국어로 번역하라.\n\n"
        "첫 줄은 반드시 다음 제목으로 시작하라:\n"
        "# (전문번역) " + header + "\n\n"
        "규칙:\n"
        "- 초록(Abstract)부터 결론(Conclusion)까지 단 한 문장도 누락하거나 요약하지 말고 "
        "전체를 순서대로 번역하라. 이것이 최우선 과제다.\n"
        "- 섹션 제목은 마크다운 제목(##)으로, 수식과 그림/표 번호는 원문 그대로 유지하라.\n"
        "- 표준 한국어 기술 용어를 사용하고(예: feed rate → 이송 속도, microstructure → 미세 조직), "
        "전문용어는 첫 등장 시 영어를 병기하라 (예: 구성인선(built-up edge, BUE)).\n"
        "- 단순 직역이 아니라 문장 간 논리적 흐름이 자연스러운 격식 있는 학술 문체로 쓰라.\n"
        "- 그림/표 캡션도 빠짐없이 번역하라. 추출된 본문 중간에 섞여 있는 캡션은 "
        "'**Fig. N** - 번역문' 형태의 별도 문단으로 정리하고, 문서 맨 끝에 '## 그림·표 설명' "
        "섹션으로 모든 캡션 번역을 한 번 더 모아 정리하라.\n"
        "- 참고문헌 목록은 번역하지 말고 '(참고문헌 생략)'으로 대체하라.\n"
        "- 번역 외 다른 말은 절대 하지 마라.\n\n=== 논문 전문 ===\n"
    )


MAPS_DIR = os.path.join(os.path.dirname(ARCHIVE), "관련맵")


def gen_path(name, kind):
    stem = os.path.splitext(name)[0]
    if kind == "map":
        return os.path.join(MAPS_DIR, stem + ".map.json")
    return os.path.join(GEN_DIR, "{}.{}.md".format(stem, "요약" if kind == "summary" else "번역"))


def find_claude():
    for c in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(c)
        if p:
            return p
    return None


# 오해하기 쉬운 라벨의 정의·금지 조건 (분류 정확도의 핵심)
LABEL_NOTES = {
    "Review": "기존 연구를 종합·정리하는 리뷰/총설 논문일 때만. 일반 연구 논문에는 절대 붙이지 않음",
    "Ductile-brittle transition": "취성 재료(Si, 세라믹, ZnSe 등)가 연성 모드로 가공되는 전이를 실제로 다룰 때만. 연성 금속의 유동 모드 전이(laminar→sinuous 등)는 절대 아님",
    "Sinuous flow": "연성 금속 절삭에서 sinuous/folding 유동을 다룰 때",
    "Difficult-to-cut": "난삭성 자체가 논문의 주제일 때만",
    "In-situ": "가공 중 실시간 관찰을 실제로 수행했을 때만",
    "Thermal effect": "온도·열이 핵심 변수/측정 대상일 때만, 단순 언급 제외",
    "Silicon": "실리콘을 실제 가공/해석했을 때만, 비교 언급 제외",
    "FEM": "유한요소해석을 실제 수행했을 때만",
    "Molecular dynamics": "MD 시뮬레이션을 실제 수행했을 때만",
    "Analytical model": "수식 기반 해석 모델을 실제 제시/사용했을 때만",
    "Single crystal": "실험/해석 시편이 단결정일 때",
    "Polycrystal": "다결정 시편의 결정립 효과를 다룰 때",
}


def ask_claude_json(prompt, timeout=240):
    """claude -p 로 질문하고 JSON 객체 하나를 파싱해 돌려준다. 실패하면 None."""
    exe = find_claude()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-p", "--model", "opus", "--output-format", "text"],
                           input=prompt.encode("utf-8"), capture_output=True, timeout=timeout)
        m = re.search(r"\{.*\}", r.stdout.decode("utf-8", "replace"), re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def openalex_search(query, year="", per_page=30):
    """제목+초록 AND 매칭 검색. 결과 노드 목록 반환."""
    import mapper
    q_clean = query.replace(",", " ")
    q_filter = q_clean if re.search(r"\b(AND|OR|NOT)\b|\"", q_clean) else " AND ".join(q_clean.split())
    filters = ["title_and_abstract.search:" + q_filter]
    if str(year).isdigit():
        filters.append("publication_year:>" + str(int(year) - 1))
    d = mapper._get(mapper.API + "/works", {
        "search": query, "filter": ",".join(filters), "per-page": str(per_page),
        "select": mapper.SELECT + ",abstract_inverted_index,relevance_score"})
    out = []
    for it in d.get("results", []):
        n = mapper._node(it, set(), 0, None)
        n.pop("_refs", None)
        inv = it.get("abstract_inverted_index") or {}
        ws = sorted((p, w) for w, ps in inv.items() for p in ps)
        n["abstract"] = " ".join(w for _, w in ws)[:500]
        n["rel"] = it.get("relevance_score") or 0
        out.append(n)
    return out, d.get("meta", {}).get("count", 0)


def _run_smart(q, year, key):
    """스마트 탐색: Claude 검색어 확장 -> 다중 검색 -> Claude 선별·소주제 분류."""
    import mapper

    def stage(s):
        job = _jobs.get(key)
        if job and job.get("status") == "running":
            job["stage"] = s

    stage("1/3 검색어 확장 중")
    plan = ask_claude_json(
        "당신은 기계가공·재료 분야 문헌 조사 전문가다. 사용자가 조사하려는 주제를 보고, "
        "학술 DB(OpenAlex, 영어) 검색어를 6~8개 만들어라.\n"
        "- 동의어·인접 용어·하위 주제·관련 현상까지 폭넓게 (예: 재료명+공정명 조합, 약어, 관련 현상)\n"
        "- 각 검색어는 2~4개 영어 단어. 제목·초록에 모두 들어가야 하는 단어 조합이다.\n"
        "- 한국어 입력이면 영어로 바꿔라.\n"
        "- 이 주제에서 제외해야 할 맥락도 적어라 (예: 임플란트, 커패시터).\n"
        "- JSON 한 줄만: {\"intent\": \"사용자 의도 한 문장(한국어)\", \"queries\": [\"...\"], "
        "\"exclude\": \"제외 맥락(한국어)\"}\n\n[사용자 입력] " + q, timeout=180)
    if not plan or not plan.get("queries"):
        plan = {"intent": q, "queries": [q], "exclude": ""}
    queries = [str(x) for x in plan["queries"]][:8]
    if q not in queries and re.search(r"[A-Za-z]", q):
        queries.insert(0, q)

    stage("2/3 검색 중 (0/{})".format(len(queries)))
    merged, counts = {}, {}
    for i, qq in enumerate(queries):
        try:
            res, total = openalex_search(qq, year, per_page=30)
            counts[qq] = total
            for n in res:
                if n["id"] not in merged:
                    n["from"] = qq
                    merged[n["id"]] = n
        except Exception as e:
            counts[qq] = "실패"
        stage("2/3 검색 중 ({}/{})".format(i + 1, len(queries)))
    with _lock:
        tags = load_json(TAGS_PATH, {})
    owned_idx = archive_title_index(tags)
    items = list(merged.values())
    for n in items:
        n["owned"] = owned_idx.get(mapper.norm_title(n["title"])) or ""
    items.sort(key=lambda n: n["rel"], reverse=True)
    items = items[:120]

    stage("3/3 Claude가 선별·분류 중 ({}편)".format(len(items)))
    listing = "\n".join("[{}] ({}) {} :: {}".format(i, n["year"], n["title"][:120], (n["abstract"] or "")[:220])
                        for i, n in enumerate(items))
    verdict = ask_claude_json(
        "당신은 기계가공·재료 분야 문헌 조사 전문가다. 사용자의 조사 의도에 맞는 논문만 골라 "
        "소주제(연구 동네)별로 묶어라.\n"
        "[조사 의도] " + plan.get("intent", q) + "\n"
        "[제외할 맥락] " + (plan.get("exclude") or "없음") + "\n"
        "규칙:\n- 의도와 무관한 논문(제외 맥락 포함)은 excluded에 번호로 넣어라.\n"
        "- 관련 논문은 3~7개 소주제로 묶고, 소주제마다 한국어 이름과 한 줄 설명을 붙여라. "
        "각 소주제 안에서는 중요도 순으로 번호를 나열하라.\n"
        "- JSON 한 줄만: {\"groups\": [{\"name\": \"...\", \"why\": \"...\", \"items\": [번호...]}], "
        "\"excluded\": [번호...]}\n\n[논문 목록: 번호 (연도) 제목 :: 초록]\n" + listing, timeout=300)
    groups = []
    if verdict and verdict.get("groups"):
        used = set()
        for g in verdict["groups"]:
            idxs = [i for i in g.get("items", []) if isinstance(i, int) and 0 <= i < len(items) and i not in used]
            used.update(idxs)
            if idxs:
                groups.append({"name": g.get("name", ""), "why": g.get("why", ""),
                               "items": [items[i] for i in idxs]})
        excluded = len([i for i in verdict.get("excluded", []) if isinstance(i, int)])
        leftover = [items[i] for i in range(len(items))
                    if i not in used and i not in set(verdict.get("excluded", []))]
        if leftover:
            groups.append({"name": "기타 관련", "why": "소주제로 묶이지 않은 관련 논문", "items": leftover})
    else:
        groups = [{"name": "검색 결과 (분류 실패)", "why": "Claude 분류에 실패해 관련도순으로 표시", "items": items}]
        excluded = 0
    _jobs[key] = {"status": "done", "result": {
        "intent": plan.get("intent", q), "exclude": plan.get("exclude", ""),
        "queries": [{"q": qq, "total": counts.get(qq, 0)} for qq in queries],
        "groups": groups, "excluded": excluded, "candidates": len(items)}}


NOTES_DIR = os.path.join(os.path.dirname(ARCHIVE), "메모")


def notes_path(name):
    return os.path.join(NOTES_DIR, os.path.splitext(name)[0] + ".json")


def load_notes(name):
    return load_json(notes_path(name), {"notes": [], "qa": []})


def save_notes(name, data):
    os.makedirs(NOTES_DIR, exist_ok=True)
    save_json(notes_path(name), data)


def pdf_pages_text(name, per_page=3500):
    """페이지별 원문 텍스트 (위치 찾기·질문 답변용)."""
    from pypdf import PdfReader
    reader = PdfReader(os.path.join(ARCHIVE, name))
    if reader.is_encrypted:
        reader.decrypt("")
    pages = []
    for i, p in enumerate(reader.pages):
        t = " ".join((p.extract_text() or "").split())
        pages.append(t[:per_page])
    return pages


def claude_text(prompt, timeout=240):
    exe = find_claude()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-p", "--model", "opus", "--output-format", "text"],
                           input=prompt.encode("utf-8"), capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", "replace").strip() or None
    except Exception:
        return None


def locate_in_pdf(name, passage):
    """한국어 요약 구절 -> 근거가 되는 원문 영어 문장과 페이지."""
    pages = pdf_pages_text(name)
    listing = "\n".join("[p{}] {}".format(i + 1, t) for i, t in enumerate(pages))[:60000]
    res = ask_claude_json(
        "아래는 논문의 페이지별 원문이다. 주어진 한국어 요약 구절이 근거하는 원문 문장을 찾아라.\n"
        "- 원문 문장은 해당 페이지 텍스트에서 '글자 그대로' 복사해라 (검색에 쓰이므로 바꾸면 안 됨). 200자 이내, 한 문장 또는 연속 두 문장.\n"
        "- 정확히 대응하는 문장이 없으면 가장 가까운 문장을 택하고 confidence를 낮게.\n"
        "- JSON 한 줄만: {\"page\": 페이지번호, \"quote\": \"원문 문장\", \"confidence\": \"high|low\"}\n\n"
        "[요약 구절] " + passage[:600] + "\n\n[페이지별 원문]\n" + listing, timeout=180)
    return res


def answer_question(name, quote, question):
    """선택 구절 + 논문 원문 맥락으로 Claude에게 질문. 짧고 쉬운 답."""
    title = paper_header(name)
    pages = pdf_pages_text(name, per_page=2500)
    context = " ".join(pages)[:28000]
    return claude_text(
        "당신은 기계가공 분야 논문을 함께 읽어주는 연구 조교다. 아래 논문 원문을 근거로 질문에 한국어로 답하라.\n"
        "답변 스타일 (중요):\n"
        "- 3~6문장, 쉬운 말로, 핵심만. 현학적 표현·불필요한 배경 설명 금지. 필요하면 bullet 최대 3개.\n"
        "- 전문용어는 영어 병기. 논문에 근거가 있으면 '(p.N)' 표시, 논문에 없는 일반 지식이면 그렇다고 한 마디.\n"
        "- 마지막에 '더 자세히: …' 한 줄로 파고들 수 있는 방향 하나만 제시.\n\n"
        "[논문] " + title + "\n"
        + ("[선택한 구절] " + quote[:800] + "\n" if quote else "")
        + "[질문] " + question + "\n\n[논문 원문(일부)]\n" + context, timeout=240)


def pdf_pages_text_raw(name):
    """줄바꿈을 유지한 페이지별 원문 (캡션 추출용)."""
    from pypdf import PdfReader
    reader = PdfReader(os.path.join(ARCHIVE, name))
    if reader.is_encrypted:
        reader.decrypt("")
    return [(p.extract_text() or "") for p in reader.pages]


CAP_START = re.compile(r"^\s*(Fig\.?|Figure|Table)\s*(\d+)\s*[.:|]?\s*(.*)$", re.I)


def extract_captions(name):
    """줄 첫머리가 Fig. N / Table N 인 줄을 캡션으로 보고, 이어지는 줄을 붙인다."""
    caps = {}
    for pi, page in enumerate(pdf_pages_text_raw(name)):
        lines = page.splitlines()
        i = 0
        while i < len(lines):
            m = CAP_START.match(lines[i])
            if m and len(m.group(3)) > 8:
                kind = "table" if m.group(1).lower().startswith("t") else "fig"
                n = int(m.group(2))
                text = m.group(3).strip()
                j = i + 1
                while j < len(lines) and len(text) < 450 and lines[j].strip() and not CAP_START.match(lines[j]):
                    text += " " + lines[j].strip()
                    j += 1
                key = (kind, n)
                if key not in caps or len(text) > len(caps[key]["en"]):
                    caps[key] = {"kind": kind, "n": n, "page": pi + 1, "en": text[:500]}
                i = j
            else:
                i += 1
    return [caps[k] for k in sorted(caps)]


def captions_path(name):
    return os.path.join(GEN_DIR, os.path.splitext(name)[0] + ".캡션.json")


def get_captions(name):
    """캡션 한국어 번역 (캐시). 없으면 Claude로 한 번에 번역."""
    p = captions_path(name)
    cached = load_json(p, None)
    if cached is not None:
        return cached
    caps = extract_captions(name)
    if not caps:
        data = {"captions": []}
    else:
        listing = "\n".join("[{}] {} {}: {}".format(i, "Table" if c["kind"] == "table" else "Fig.", c["n"], c["en"])
                            for i, c in enumerate(caps))
        res = ask_claude_json(
            "아래 논문 그림/표 캡션들을 한국어로 번역하라. " + GLOSSARY_RULE +
            " 각 항목을 번호 그대로 매핑해 JSON 한 줄만: {\"ko\": {\"0\": \"...\", \"1\": \"...\"}}\n\n" + listing, timeout=240)
        ko = (res or {}).get("ko", {}) if isinstance(res, dict) else {}
        for i, c in enumerate(caps):
            c["ko"] = ko.get(str(i), "")
        data = {"captions": caps}
    os.makedirs(GEN_DIR, exist_ok=True)
    save_json(p, data)
    return data


def job_view(job):
    """화면에 보낼 작업 상태: 상태·단계·경과 시간(초)·오류."""
    v = {"status": job.get("status", "running"), "stage": job.get("stage", "")}
    if job.get("error"):
        v["error"] = job["error"]
    t = job.get("t_start") or job.get("t0")
    if t and v["status"] == "running":
        v["elapsed"] = int(time.time() - t)
        v["waiting"] = "t_start" not in job
    # 진행률: 구간 완료 수(done/total) 또는 단계 문구의 'k/n'. 남은 시간(eta)은 지금까지의 속도로 추정 (화면 진행 막대용)
    done, total = job.get("done"), job.get("total")
    if not total:
        m = re.search(r"(\d+)\s*/\s*(\d+)", v["stage"] or "")
        if m:
            done, total = int(m.group(1)), int(m.group(2))
    if total:
        v["done"], v["total"] = int(done or 0), int(total)
        if v.get("elapsed") and done:
            v["eta"] = int(v["elapsed"] / done * (total - done)) + int(job.get("eta_extra", 0))
    if job.get("phase"):
        v["phase"] = job["phase"]
    return v


def _run_smart_safe(q, year, key):
    try:
        _run_smart(q, year, key)
    except Exception as e:
        _jobs[key] = {"status": "error", "error": str(e)[:200]}


def classify_with_claude(title, kw, front, groups):
    """Claude에게 논문의 '실제 연구 주제' 라벨만 고르게 한다. 실패하면 None."""
    exe = find_claude()
    if not exe:
        return None
    lines = []
    for g, ls in groups.items():
        anno = [l + (" (주의: " + LABEL_NOTES[l] + ")" if l in LABEL_NOTES else "") for l in ls]
        lines.append("[" + g + "] " + ", ".join(anno))
    catalog = "\n".join(lines)
    prompt = (
        "당신은 기계가공(machining) 분야 논문 분류 전문가다. 아래 논문에 붙일 라벨을 "
        "카탈로그에서 고르라.\n\n규칙:\n"
        "- 카탈로그에 있는 라벨만, 논문이 '실제로 연구·사용한 것'만 고른다.\n"
        "- 각 라벨마다 본문에서 근거가 되는 구절을 짧게 인용해야 한다. "
        "근거 구절을 본문에서 직접 찾을 수 없으면 그 라벨은 붙이지 마라.\n"
        "- 서론에서 언급만 한 주제, 비교 대상으로 스친 재료·방법은 절대 넣지 않는다.\n"
        "- 재료 라벨은 논문이 실제로 가공/해석한 재료만. 여러 재료를 모두 실험했다면 모두 포함.\n"
        "- 각 라벨의 (주의: ...) 조건을 반드시 지켜라.\n"
        "- 답은 JSON 한 줄만: {\"labels\": [{\"label\": \"...\", \"evidence\": \"본문 근거 구절\"}]}\n\n"
        "[라벨 카탈로그]\n" + catalog + "\n\n"
        "[논문 제목] " + title + "\n"
        "[저자 키워드] " + (kw or "(없음)") + "\n"
        "[본문 앞부분]\n" + front
    )
    try:
        r = subprocess.run([exe, "-p", "--model", "opus", "--output-format", "text"],
                           input=prompt.encode("utf-8"),
                           capture_output=True, timeout=240)
        m = re.search(r"\{.*\}", r.stdout.decode("utf-8", "replace"), re.S)
        items = json.loads(m.group(0))["labels"]
        valid = set(sum(groups.values(), []))
        picked = []
        for it in items:
            name = it.get("label") if isinstance(it, dict) else it
            if name in valid and name not in picked:
                picked.append(name)
        return picked if picked else None
    except Exception:
        return None


def _set_stage(key, stage, started=False):
    job = _jobs.get(key)
    if job and job.get("status") == "running":
        job["stage"] = stage
        if started:
            job["t_start"] = time.time()


def run_generation(name, kind, ext=None):
    key = (name, kind)
    _jobs.setdefault(key, {"status": "running"})
    _jobs[key]["t0"] = time.time()
    try:
        if kind == "map":
            _set_stage(key, "대기 중 (맵은 한 번에 하나)")
            with _map_sem:
                _set_stage(key, "시작", started=True)
                _run_map(name, ext)
        else:
            _set_stage(key, "대기 중 (요약·번역 동시 2개)")
            with _gen_sem:
                _set_stage(key, "본문 추출 중", started=True)
                _run_generation(name, kind)
        _jobs.pop(key, None)
        return
    except subprocess.TimeoutExpired:
        _jobs[key] = {"status": "error", "error": "시간 초과 (40분) - 논문이 너무 긴 듯"}
    except Exception as e:
        _jobs[key] = {"status": "error", "error": str(e)[:300]}


def archive_title_index(tags):
    import mapper
    out = {}
    for f, t in tags.items():
        out[mapper.norm_title(t.get("title") or parse_name(f)["title"])] = f
    return out


def _run_map(name, ext=None):
    """ext가 있으면 보관소에 없는 외부 논문({id,title,doi})을 시드로 맵 생성."""
    import mapper
    key = (name, "map")

    def prog(stage):
        job = _jobs.get(key)
        if job and job.get("status") == "running":
            job["stage"] = stage

    with _lock:
        tags = load_json(TAGS_PATH, {})
    archive_titles = archive_title_index(tags)
    dois = []
    if ext:
        title = ext.get("title", "")
        if ext.get("doi"):
            dois = [ext["doi"].replace("https://doi.org/", "")]
    else:
        title = tags.get(name, {}).get("title") or parse_name(name)["title"]
        # PDF에서 DOI를 뽑아 검색 대신 직접 조회 (검색 API 속도 제한 회피)
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(BASE), "paper-organizer"))
            import paper_organizer as po
            cands, _ = po.extract_doi_candidates(os.path.join(ARCHIVE, name))
            for c in cands[:2]:
                for v in po.doi_variants(c):
                    if v not in dois:
                        dois.append(v)
        except Exception:
            pass
    data = mapper.build_map(title, archive_titles, progress=prog, dois=dois[:5])
    os.makedirs(MAPS_DIR, exist_ok=True)
    save_json(gen_path(name, "map"), data)


def _run_generation(name, kind):
    from pypdf import PdfReader
    reader = PdfReader(os.path.join(ARCHIVE, name))
    if reader.is_encrypted:
        reader.decrypt("")
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    if len(text.strip()) < 500:
        raise RuntimeError("이 PDF는 글자를 추출할 수 없습니다 (스캔본인 듯)")
    if not find_claude():
        raise RuntimeError("claude 명령을 찾을 수 없습니다 (Claude Code 설치 확인)")
    out = generate_document(name, kind, text)
    os.makedirs(GEN_DIR, exist_ok=True)
    with open(gen_path(name, kind), "w", encoding="utf-8") as f:
        f.write(out)


def _claude(prompt, timeout=900):
    """claude -p 실행. 실패 시 원인이 담긴 RuntimeError."""
    exe = find_claude()
    r = subprocess.run([exe, "-p", "--model", "opus", "--output-format", "text"],
                       input=prompt.encode("utf-8"), capture_output=True, timeout=timeout)
    out = r.stdout.decode("utf-8", "replace").strip()
    errtxt = r.stderr.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not out:
        msg = (errtxt or out)[:300]
        low = msg.lower()
        if "authenticate" in low or "login" in low or "oauth" in low:
            msg = "명령줄 로그인이 필요합니다: 터미널에서 claude 입력 → /login 입력 → 브라우저 로그인"
        elif "limit" in low or "usage" in low:
            msg = "Claude 구독 사용량 한도에 걸린 듯합니다 - 잠시 후 다시 시도"
        raise RuntimeError(msg or "Claude 응답 없음")
    return out


def split_chunks(text, size):
    """문단/문장 경계에서 size자 안팎으로 나눈다."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z0-9][^\n]{0,60}\n)", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(p) > size:  # 아주 긴 문단은 문장 단위로
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", p):
                if len(cur) + len(s) > size and cur:
                    chunks.append(cur); cur = ""
                cur += (" " if cur else "") + s
            continue
        if len(cur) + len(p) > size and cur:
            chunks.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + p
    if cur:
        chunks.append(cur)
    return chunks


# 목차 항목 줄: '2.1. Introduction . . . . 134' / 'References . . . . 160'
_TOC_LINE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Za-z(][^\n]*?(?:\s*\.){3,}\s*\d{1,4}\s*$")
# 머리부에서 버릴 저널 잡정보 줄
_FRONT_NOISE = [re.compile(p, re.I) for p in (
    r"Contents lists available at", r"journal homepage", r"^\s*https?://", r"^\s*www\.", r"^\s*\d{4}-\d{3}[\dX]/",
    r"E-mail address", r"Corresponding author", r"Crown Copyright", r"^\s*©", r"All rights reserved", r"^\s*Copyright ©",
    r"^\s*Contents\s*$")]  # 원문의 'Contents' 제목 줄은 버리고 우리가 모은 목차 블록의 제목만 남김
_NOMEN = re.compile(r"^\s*(Abbreviations?|Nomenclature|List of symbols|Notations?|Symbols)\b", re.I)


def remove_running_heads(text):
    """페이지마다 반복되는 머리말/꼬리말 줄(저널명·저자 러닝헤드)을 지운다."""
    lines = text.split("\n")
    cnt = {}
    for l in lines:
        k = " ".join(l.split())
        if len(k) >= 20:
            cnt[k] = cnt.get(k, 0) + 1
    rep = {k for k, c in cnt.items() if c >= 3}
    return "\n".join(l for l in lines if " ".join(l.split()) not in rep)


def _toc_title(line):
    t = re.sub(r"(?:\s*\.){3,}\s*\d{1,4}\s*$", "", line).strip()
    t = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", t)
    return " ".join(t.split()).lower()


def prepare_chunks(text, size):
    """머리부(제목·저자·초록·목차·약어)를 한 구간으로 묶고, 본문은 size 단위로 나눈다.
    목차·약어표가 구간 경계에 걸려 '목차 (계속)'처럼 쪼개지는 것을 막는다.
    PDF에서 목차가 두 페이지에 걸쳐 약어 각주와 섞여 나와도 목차 줄만 모아 한 블록으로 정리한다.
    반환: (chunks, has_front)"""
    text = remove_running_heads(text)
    lines = text.split("\n")
    head = lines[:400]  # 머리부는 앞 400줄 안에 있음
    toc_idx = [i for i, l in enumerate(head) if _TOC_LINE.match(l)]
    nomen_idx = [i for i, l in enumerate(head) if _NOMEN.match(l)]
    start = (toc_idx[-1] + 1) if toc_idx else 0
    body_start = None
    if toc_idx:  # 목차 첫 항목의 제목이 본문 소제목으로 다시 나오는 줄 = 본문 시작
        key = " ".join(_toc_title(lines[toc_idx[0]]).split()[:3])
        for i in range(start, min(len(lines), start + 250)):
            l = " ".join(lines[i].split()).lower()
            if key and re.match(r"^1\.?\s", l) and key in l and not _TOC_LINE.match(lines[i]):
                body_start = i
                break
    if body_start is None:  # '1. Introduction' / 'Introduction' 줄
        for i in range(start, min(len(lines), start + 250)):
            l = lines[i].strip()
            if len(l) < 100 and not _TOC_LINE.match(l) and re.match(r"^(1\.?\s+[A-Z]|Introduction\b)", l):
                body_start = i
                break
    has_nomen = any(i < (body_start or 0) for i in nomen_idx)
    if body_start is None or body_start < 5 or (len(toc_idx) < 3 and not has_nomen):
        return split_chunks(text, size), False
    tocset = set(toc_idx)
    toc = [re.sub(r"(?:\s*\.){3,}\s*(\d{1,4})\s*$", r" … \1", " ".join(lines[i].split())) for i in toc_idx]
    out, inserted = [], False
    for i in range(body_start):
        if i in tocset:
            if not inserted:
                out.append("\nContents\n" + "\n".join(toc) + "\n")
                inserted = True
            continue
        if any(p.search(lines[i]) for p in _FRONT_NOISE):
            continue
        out.append(lines[i])
    front = "\n".join(out).strip()
    body = "\n".join(lines[body_start:])
    front_chunks = split_chunks(front, size) if len(front) > size * 1.6 else [front]
    return front_chunks + split_chunks(body, size), True


GLOSSARY_RULE =("표준 한국어 기술 용어를 쓰고(feed rate → 이송 속도, microstructure → 미세 조직, built-up edge → 구성인선), "
                 "중요 용어는 첫 등장 시 영어 병기. 직역투가 아닌 자연스러운 학술 문체.")


_chunk_sem = threading.BoundedSemaphore(4)  # 전체 동시 Claude 호출 4개 (구간 병렬 처리)


# 머리부 구간(제목·저자·초록·키워드·목차·약어) 전용 규칙
FRONT_RULE = ("- 이 구간은 논문 머리부다: 제목·저자(소속)·초록·키워드·목차·약어. 저널 정보·DOI·저작권·이메일 같은 서지 잡정보는 생략.\n"
              "- 논문 제목은 '**제목**: 한국어 번역 (원제)' 한 줄로만 쓰고 # 제목 줄은 만들지 마라 (문서 머리말은 따로 붙는다).\n"
              "- 목차는 '## 목차 (Contents)' 아래에 원문 순서대로 **전부 한 목록**으로 (원문 번호 유지, 페이지 번호 생략). "
              "약어·기호표는 '## 약어 (Abbreviations)' 아래 '- 항목 : 설명' 으로 빠짐없이.\n")
# 모든 구간 공통: 잘린 자리에 표시를 남기지 말 것
CUT_RULE = "- 구간 경계에서 잘린 문장·목록은 있는 부분만 자연스럽게 옮기고, '(계속)', '(이하 구간 이어짐)' 같은 표시는 절대 쓰지 마라.\n"


def chunk_prompt(header, kind, i, n, ch, prev_src, front=False):
    is_sum = kind == "summary"
    if is_sum:
        return (
            "당신은 기계가공·재료 분야 논문을 한국어로 '압축 번역'하는 전문가다. 아래는 논문 [" + header + "]의 "
            "{}/{} 구간이다.\n".format(i + 1, n) +
            "규칙:\n- 원문의 소제목 구조를 그대로 따라라 (소제목은 ## 또는 ### 로, 원문 번호 유지). "
            "고정된 틀(배경/방법/결과)로 재편하지 마라 - 리뷰 논문이면 각 소단원을 그 순서대로 상세히.\n"
            "- 모든 문장을 옮기지는 말되, 핵심 주장·방법·수치·논리 전개·저자의 결론은 빠짐없이 담아라. "
            "길이 제한 없음. 원문 분량에 비례해 상세하게.\n"
            "- 그림/표 캡션은 요약에 넣지 마라 (PDF 옆에 따로 표시됨). 본문이 그림을 참조하면 'Fig. N' 표기만 유지.\n"
            + (FRONT_RULE if front else
               "- 목차(Contents)·약어(Abbreviations)·기호표(Nomenclature) 구간은 항목마다 한 줄씩 '- 항목 : 설명' 목록으로.\n")
            + CUT_RULE +
            "- 참고문헌 목록은 '(참고문헌 생략)'으로만 표시.\n- " + GLOSSARY_RULE + "\n"
            "- 요약 외 다른 말(인사, 안내)은 절대 쓰지 마라. 제목 줄도 쓰지 마라.\n"
            + ("\n[직전 구간의 원문 끝부분 - 문맥 파악용, 요약하지 말 것]\n" + prev_src + "\n" if prev_src else "")
            + "\n[원문 구간]\n" + ch)
    return (
        "당신은 기계가공·재료 분야 논문 전문 번역가다. 아래는 논문 [" + header + "]의 "
        "{}/{} 구간이다. 한국어로 번역하라.\n".format(i + 1, n) +
        "규칙:\n- 단 한 문장도 누락·요약하지 말고 전부 순서대로 번역. 소제목은 ## 또는 ### (원문 번호 유지).\n"
        "- 수식·그림/표 번호는 원문 그대로, 그림/표 캡션도 번역 ('**Fig. N** - 번역').\n"
        + (FRONT_RULE if front else "") + CUT_RULE +
        "- 이 구간이 참고문헌 목록이면 '(참고문헌 생략)'만 출력.\n- " + GLOSSARY_RULE + "\n"
        "- 번역 외 다른 말은 절대 쓰지 마라. 제목 줄도 쓰지 마라.\n"
        + "\n[원문 구간]\n" + ch)


def generate_document(name, kind, text):
    """구간 단위로 Claude를 불러 요약(압축 번역) 또는 전문번역을 만든다. 구간은 4개씩 병렬."""
    header = paper_header(name)
    key = (name, kind)
    is_sum = kind == "summary"
    chunks, has_front = prepare_chunks(text, 9000 if is_sum else 6000)  # 머리부(목차·약어)는 통째로 1구간
    n = len(chunks)
    results, errors, done = [None] * n, [], [0]
    label = "요약" if is_sum else "번역"
    job = _jobs.get(key)
    if job:  # 완성된 구간을 읽기 화면에서 미리 볼 수 있게 공유
        job["partial"] = results
        job["total"] = n
    _set_stage(key, "Claude {} 중 (0/{} 구간, 4개 동시)".format(label, n))
    if job:
        job["done"] = 0; job["phase"] = "chunks"; job["eta_extra"] = 25 if is_sum else 0  # 요약은 마지막 한줄요약 단계 ~25초

    def work(i):
        ch = chunks[i]
        prev_src = chunks[i - 1][-500:] if i and not (has_front and i == 1) else ""  # 본문 1구간엔 목차 꼬리를 문맥으로 주지 않음
        prompt = chunk_prompt(header, kind, i, n, ch, prev_src, front=(has_front and i == 0))
        try:
            with _chunk_sem:
                out = _claude(prompt)
                too_short = len(out) < len(ch) * (0.06 if is_sum else 0.35) and "참고문헌 생략" not in out
                if too_short:  # 뒤쪽을 건너뛴 듯하면 한 번 더
                    out2 = _claude(prompt)
                    if len(out2) > len(out):
                        out = out2
            results[i] = out
        except Exception as e:
            errors.append(e)
        done[0] += 1
        _set_stage(key, "Claude {} 중 ({}/{} 구간 완료, 4개 동시)".format(label, done[0], n))
        if job:
            job["done"] = done[0]

    threads = [threading.Thread(target=work, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    # 구간 결과 정리: 구간이 만든 # 제목은 ##로 내림(문서 제목은 하나만), 참고문헌 구간마다 반복된 '(참고문헌 생략)'은 하나로
    results = [re.sub(r"^#\s+(?!#)", "## ", r, flags=re.M) for r in results]
    body = "\n\n".join(results)
    body = re.sub(r"(?:\(참고문헌 생략\)\s*){2,}", "(참고문헌 생략)\n\n", body)
    if is_sum:
        _set_stage(key, "한줄 요약·핵심 정리 작성 중")
        if job:
            job["done"] = n; job["phase"] = "wrap"
        wrap = ask_claude_json(
            "아래는 논문 [" + header + "]의 구간별 압축 요약 전체다. 이 논문의 (1) 한줄 요약 2~3문장, "
            "(2) 핵심 정리: 주요 발견·주장 5~8개 bullet과 한계·시사점을 한국어로 써라. " + GLOSSARY_RULE +
            "\nJSON 한 줄만: {\"overview\": \"...\", \"closing\": \"- ...\\n- ...\"}\n\n" + body[:40000], timeout=400)
        head = "# (요약) " + header + "\n\n"
        if wrap and wrap.get("overview"):
            head += "## 한줄 요약\n" + wrap["overview"].strip() + "\n\n"
        tail = ("\n\n## 핵심 정리 및 시사점\n" + wrap["closing"].strip()) if wrap and wrap.get("closing") else ""
        return head + body + tail
    return "# (전문번역) " + header + "\n\n" + body


VIEW_PAGE = """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>__TITLE__</title><style>
body { font-family: 'Malgun Gothic', sans-serif; margin: 0; color: #222; }
#bar { position: sticky; top: 0; background: #fff; border-bottom: 1px solid #ddd;
       padding: 8px 16px; font-size: 13px; color: #777; display: flex; gap: 16px; }
#bar a { color: #3b5bd6; text-decoration: none; }
#wrap { display: flex; height: calc(100vh - 38px); }
#doc { flex: 1; overflow-y: auto; padding: 10px 34px 60px; line-height: 1.75; }
#doc h2 { border-bottom: 2px solid #3b5bd6; padding-bottom: 4px; margin-top: 34px; }
#pdf { flex: 1; display: none; border-left: 1px solid #ccc; }
#pdf iframe { width: 100%; height: 100%; border: 0; }
.on #pdf { display: block; }
</style></head><body>
<div id="bar">__KIND_LABEL__ · <a href="javascript:history.back()">← 목록으로</a>
<a href="#" id="tg">📄 원문 PDF 나란히 보기</a></div>
<div id="wrap"><div id="doc">불러오는 중...</div><div id="pdf"></div></div>
<script>
const tg = document.getElementById('tg'), wrap = document.getElementById('wrap');
tg.onclick = (e) => {
  e.preventDefault();
  if (!wrap.classList.contains('on')) {
    if (!document.querySelector('#pdf iframe')) {
      const f = document.createElement('iframe');
      f.src = '/pdf?file=__FILE_Q__';
      document.getElementById('pdf').appendChild(f);
    }
    wrap.classList.add('on'); tg.textContent = 'PDF 닫기';
  } else { wrap.classList.remove('on'); tg.textContent = '📄 원문 PDF 나란히 보기'; }
};
fetch('/api/gentext?file=__FILE_Q__&kind=__KIND__').then(r => r.text()).then(md => {
  let h = md.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  h = h.replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>')
       .replace(/^# (.*)$/gm,'<h2>$1</h2>')
       .replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>')
       .replace(/^- (.*)$/gm,'<li>$1</li>').replace(/\\n\\n/g,'</p><p>');
  document.getElementById('doc').innerHTML = '<p>'+h+'</p>';
});
</script></body></html>"""


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def archive_pdfs():
    try:
        return sorted(f for f in os.listdir(ARCHIVE)
                      if f.lower().endswith(".pdf")
                      and os.path.isfile(os.path.join(ARCHIVE, f)))
    except OSError:
        return []


def parse_name(filename):
    stem = os.path.splitext(filename)[0]
    parts = stem.split("_", 3)
    if len(parts) == 4 and parts[0].isdigit():
        return {"year": parts[0], "journal": parts[1], "author": parts[2], "title": parts[3]}
    return {"year": "", "journal": "", "author": "", "title": stem}


def refresh_new_papers():
    """보관소에 새로 들어온 논문에 제안 라벨을 붙이고 본문 인덱스도 채운다."""
    global _texts_cache
    if _refreshing.is_set():
        return
    _refreshing.set()
    try:
        _refresh_new_papers()
    finally:
        _refreshing.clear()


def _refresh_new_papers():
    global _texts_cache
    with _lock:
        tags = load_json(TAGS_PATH, {})
    new_files = [f for f in archive_pdfs() if f not in tags]
    texts = load_json(TEXTS_PATH, {})
    missing = [f for f in archive_pdfs() if f not in texts]
    if missing:
        try:
            from pypdf import PdfReader
            for f in missing:
                try:
                    reader = PdfReader(os.path.join(ARCHIVE, f))
                    if reader.is_encrypted:
                        reader.decrypt("")
                    t = " ".join((p.extract_text() or "") for p in reader.pages)
                    texts[f] = " ".join(t.lower().split())
                except Exception:
                    texts[f] = ""
            with _lock:
                save_json(TEXTS_PATH, texts)
        except ImportError:
            pass
    if new_files:
        new_titles = {}
        try:
            # paper-organizer는 이 폴더와 같은 부모 폴더 안에 있음
            sys.path.insert(0, os.path.join(os.path.dirname(BASE), "paper-organizer"))
            import paper_organizer as po
            for f in new_files:
                try:
                    meta = po.resolve_meta(os.path.join(ARCHIVE, f))
                    if meta and meta.get("title"):
                        new_titles[f] = meta["title"]
                except Exception:
                    pass
        except ImportError:
            pass
        groups = load_json(LABELS_PATH, {})
        for f in new_files:
            title = new_titles.get(f) or parse_name(f)["title"]
            text = texts.get(f, "")
            # 1순위: Claude가 실제 주제를 판정 / 실패 시 규칙 기반으로 폴백
            picked = classify_with_claude(title, rules.keyword_section(text), text[:6000], groups)
            if picked is not None:
                entry = {"labels": picked, "suggested": [], "rejected": []}
            else:  # Claude 실패(한도·시간초과 등): 규칙으로 임시 분류하고, 다음 /api/data 때 재시도하도록 표시
                auto, sugg = rules.classify_labels(title, text)
                entry = {"labels": auto, "suggested": sugg, "rejected": [], "claude_tries": 1}
            if f in new_titles:
                entry["title"] = new_titles[f]
            with _lock:
                tags = load_json(TAGS_PATH, {})
                if f not in tags:
                    tags[f] = entry
                    save_json(TAGS_PATH, tags)
    # Claude 분류가 실패했던 논문 재시도 (한 번에 3편, 논문당 최대 3회). 사용자가 손댄 항목(rejected 있음)은 건드리지 않음
    with _lock:
        tags = load_json(TAGS_PATH, {})
    have = set(archive_pdfs())
    retry = [f for f, e in tags.items() if 0 < e.get("claude_tries", 0) < 3 and not e.get("rejected") and f in have][:3]
    if retry:
        groups = load_json(LABELS_PATH, {})
        for f in retry:
            e = tags[f]
            title = e.get("title") or parse_name(f)["title"]
            text = texts.get(f, "")
            picked = classify_with_claude(title, rules.keyword_section(text), text[:6000], groups)
            with _lock:
                tags = load_json(TAGS_PATH, {})
                cur = tags.get(f)
                if not cur:
                    continue
                if picked is not None:
                    merged = list(dict.fromkeys(cur.get("labels", []) + picked))
                    cur["labels"] = merged
                    cur["suggested"] = [s for s in cur.get("suggested", []) if s not in merged]
                    cur.pop("claude_tries", None)
                else:
                    cur["claude_tries"] = cur.get("claude_tries", 0) + 1
                save_json(TAGS_PATH, tags)
    with _lock:
        _texts_cache = texts


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif url.path == "/api/data":
            with _lock:
                tags = load_json(TAGS_PATH, {})
            # 새 파일이 있거나, Claude 분류에 실패해 재시도가 남은 논문이 있으면 백그라운드로 분류
            classifying = _refreshing.is_set()
            classifying_n = sum(1 for f in archive_pdfs() if f not in tags) +                 sum(1 for e in tags.values() if 0 < e.get("claude_tries", 0) < 3 and not e.get("rejected"))
            if classifying_n:
                threading.Thread(target=refresh_new_papers, daemon=True).start()
                classifying = True
            papers = []
            for f in archive_pdfs():
                meta = parse_name(f)
                try:
                    meta["added"] = os.path.getmtime(os.path.join(ARCHIVE, f))
                except OSError:
                    meta["added"] = 0
                t = tags.get(f, {})
                if t.get("title"):  # 파일명은 80자로 잘리므로 전체 제목이 있으면 그걸 사용
                    meta["title"] = t["title"]
                meta.update({"file": f, "labels": t.get("labels", []),
                             "suggested": t.get("suggested", []),
                             "rejected": t.get("rejected", []),
                             "has_summary": os.path.exists(gen_path(f, "summary")),
                             "has_translation": os.path.exists(gen_path(f, "translation")),
                             "has_map": os.path.exists(gen_path(f, "map")),
                             "has_notes": os.path.exists(notes_path(f))})
                jobs = {}
                for kind in ("summary", "translation", "map"):
                    j = _jobs.get((f, kind))
                    if j:
                        jobs[kind] = job_view(j)
                if jobs:
                    meta["jobs"] = jobs
                papers.append(meta)
            # classifying: 백그라운드 Claude 분류 진행 중 → 화면이 잠시 뒤 다시 받아 라벨을 채움
            self._send(200, {"papers": papers, "groups": load_json(LABELS_PATH, {}), "classifying": classifying, "classifying_n": classifying_n})
        elif url.path == "/view":
            with open(os.path.join(BASE, "reader.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif url.path == "/api/notes":
            name = os.path.basename(parse_qs(url.query).get("file", [""])[0])
            self._send(200, load_notes(name))
        elif url.path == "/api/captions":
            name = os.path.basename(parse_qs(url.query).get("file", [""])[0])
            if not os.path.isfile(os.path.join(ARCHIVE, name)):
                self._send(404, {"error": "no file"})
            else:
                try:
                    self._send(200, get_captions(name))
                except Exception as e:
                    self._send(200, {"captions": [], "error": str(e)[:200]})
        elif url.path == "/api/paperinfo":
            name = os.path.basename(parse_qs(url.query).get("file", [""])[0])
            with _lock:
                tags = load_json(TAGS_PATH, {})
            meta = parse_name(name)
            meta["title"] = tags.get(name, {}).get("title") or meta["title"]
            meta["file"] = name
            meta["has_summary"] = os.path.exists(gen_path(name, "summary"))
            meta["has_translation"] = os.path.exists(gen_path(name, "translation"))
            self._send(200, meta)
        elif url.path == "/pdf":
            q = parse_qs(url.query)
            name = os.path.basename(q.get("file", [""])[0])
            path = os.path.join(ARCHIVE, name)
            if os.path.isfile(path) and name.lower().endswith(".pdf"):
                with open(path, "rb") as f:
                    self._send(200, f.read(), "application/pdf")
            else:
                self._send(404, {"error": "not found"})
        elif url.path == "/mapview":
            with open(os.path.join(BASE, "mapview.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif url.path == "/api/mapdata":
            q = parse_qs(url.query)
            name = os.path.basename(q.get("file", [""])[0])
            p = gen_path(name, "map")
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    self._send(200, f.read(), "application/json; charset=utf-8")
            else:
                self._send(404, {"error": "not generated"})
        elif url.path == "/api/gentext":
            q = parse_qs(url.query)
            name = os.path.basename(q.get("file", [""])[0])
            kind = q.get("kind", ["summary"])[0]
            p = gen_path(name, kind)
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    self._send(200, f.read().encode("utf-8"), "text/plain; charset=utf-8")
            else:
                self._send(404, {"error": "not generated"})
        elif url.path == "/api/genstatus":
            q = parse_qs(url.query)
            name = os.path.basename(q.get("file", [""])[0])
            kind = q.get("kind", ["summary"])[0]
            if os.path.isfile(gen_path(name, kind)):
                self._send(200, {"status": "done"})
            else:
                job = _jobs.get((name, kind))
                if job is None:
                    self._send(200, {"status": "none"})
                else:
                    self._send(200, job_view(job))
        elif url.path == "/api/genpartial":
            # 생성 중인 요약/번역의 완성된 구간들 (읽기 화면 실시간 표시용)
            q = parse_qs(url.query)
            name = os.path.basename(q.get("file", [""])[0])
            kind = q.get("kind", ["summary"])[0]
            job = _jobs.get((name, kind))
            if os.path.isfile(gen_path(name, kind)):
                self._send(200, {"status": "done"})
            elif not job:
                self._send(200, {"status": "none"})
            else:
                v = job_view(job)
                v["total"] = job.get("total", 0)
                v["parts"] = {str(i): t for i, t in enumerate(job.get("partial") or []) if t}
                self._send(200, v)
        elif url.path == "/api/jobs":
            # 진행 중/실패한 모든 작업 (재시작 전 확인용)
            out = [{"file": k[0], "kind": k[1], **job_view(j)} for k, j in list(_jobs.items())]
            self._send(200, {"jobs": out, "running": sum(1 for j in out if j["status"] == "running")})
        elif url.path == "/explore":
            with open(os.path.join(BASE, "explore.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif url.path == "/ui.css":
            with open(os.path.join(BASE, "ui.css"), "rb") as f:
                self._send(200, f.read(), "text/css; charset=utf-8")
        elif url.path == "/ui.js":
            with open(os.path.join(BASE, "ui.js"), "rb") as f:
                self._send(200, f.read(), "application/javascript; charset=utf-8")
        elif url.path.startswith("/fonts/"):
            # 로컬 글꼴 파일 (Pretendard 등) — 인터넷 없이도 뜨게 paper-search\fonts\ 에서 제공
            fname = os.path.basename(url.path)
            fpath = os.path.join(BASE, "fonts", fname)
            if fname.lower().endswith((".woff2", ".woff", ".ttf")) and os.path.isfile(fpath):
                ctype = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf"}[fname.rsplit(".", 1)[1].lower()]
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=31536000")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404); self.end_headers()
        elif url.path == "/api/explore":
            # 주제 키워드로 OpenAlex 전체 검색 (탐색)
            import mapper
            q = parse_qs(url.query)
            query = q.get("q", [""])[0].strip()
            year = q.get("year", [""])[0]
            sort = q.get("sort", ["relevance"])[0]
            if not query:
                self._send(400, {"error": "검색어가 없습니다"})
                return
            # 제목+초록에 모든 단어가 들어간 논문만 (본문 어딘가 스치는 논문 제외).
            # 사용자가 AND/OR/NOT/따옴표를 쓰면 그대로, 아니면 단어들을 AND로 묶음
            q_clean = query.replace(",", " ")
            if re.search(r"\b(AND|OR|NOT)\b|\"", q_clean):
                q_filter = q_clean
            else:
                q_filter = " AND ".join(q_clean.split())
            filters = ["title_and_abstract.search:" + q_filter]
            if year.isdigit():
                filters.append("publication_year:>" + str(int(year) - 1))
            params = {"search": query, "filter": ",".join(filters), "per-page": "50",
                      "select": mapper.SELECT + ",abstract_inverted_index,relevance_score"}
            if sort == "cited":
                params["sort"] = "cited_by_count:desc"
            elif sort == "recent":
                params["sort"] = "publication_year:desc"
            try:
                d = mapper._get(mapper.API + "/works", params)
            except Exception as e:
                self._send(200, {"error": str(e)[:200], "results": []})
                return
            with _lock:
                tags = load_json(TAGS_PATH, {})
            owned_idx = archive_title_index(tags)
            # OpenAlex는 어간 처리 때문에 machining≈machine 으로 매칭함.
            # 입력한 단어가 그 형태 그대로(복수/과거형 정도만 허용) 제목·초록에 있는 결과를 위로 올린다.
            words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", q_filter)
                     if w.upper() not in ("AND", "OR", "NOT")]
            def strict_hits(text):
                hits = 0
                for w in words:
                    forms = {w, w + "s", w + "es"}
                    if w.endswith("ing"):
                        forms |= {w[:-3] + "ed", w[:-3] + "e"}  # machining -> machined, (machine 제외는 아래서)
                        forms.discard(w[:-3] + "e")
                    if any(re.search(r"\b" + re.escape(f) + r"\b", text) for f in forms):
                        hits += 1
                return hits
            results = []
            for it in d.get("results", []):
                n = mapper._node(it, set(), 0,
                                 owned_idx.get(mapper.norm_title(it.get("display_name"))))
                n.pop("_refs", None)
                inv = it.get("abstract_inverted_index") or {}
                ws = sorted((p, w) for w, ps in inv.items() for p in ps)
                abstract = " ".join(w for _, w in ws)
                n["abstract"] = abstract[:500]
                n["strict"] = strict_hits((n["title"] + " " + abstract).lower())
                n["rel"] = it.get("relevance_score") or 0
                results.append(n)
            # 정확 일치 단어 수 우선, 그 안에서 선택한 정렬 기준
            keyf = {"cited": lambda x: (x["strict"], x["cit"]),
                    "recent": lambda x: (x["strict"], x["year"]),
                    }.get(sort, lambda x: (x["strict"], x["rel"]))
            results.sort(key=keyf, reverse=True)
            self._send(200, {"results": results, "total": d.get("meta", {}).get("count", 0),
                             "words": words})
        elif url.path == "/api/smart_status":
            k = parse_qs(url.query).get("key", [""])[0]
            job = _jobs.get((k, "smart"))
            if not job:
                self._send(200, {"status": "none"})
            elif job["status"] == "done":
                self._send(200, {"status": "done", "result": job["result"]})
            else:
                v = job_view(job); v["error"] = job.get("error", "")
                self._send(200, v)
        elif url.path == "/api/openalex_status":
            # 맵 서비스(OpenAlex) 상태 확인: 재시도 없이 가볍게 한 번씩만
            import requests as rq
            result = {}
            probes = {
                "lookup": ("https://api.openalex.org/works/W2741809807", {}),
                "search": ("https://api.openalex.org/works", {"search": "tantalum cutting", "per-page": "1"}),
            }
            for name, (u, p) in probes.items():
                try:
                    p = dict(p, mailto="maenglaboratory@gmail.com")
                    r = rq.get(u, params=p, timeout=12)
                    result[name] = r.status_code
                except Exception:
                    result[name] = 0
            result["ok"] = result.get("lookup") == 200 and result.get("search") == 200
            self._send(200, result)
        elif url.path == "/api/fulltext":
            q = parse_qs(url.query).get("q", [""])[0].lower().strip()
            texts = _texts_cache or load_json(TEXTS_PATH, {})
            hits = [f for f, t in texts.items() if q and q in t]
            self._send(200, {"files": hits, "indexed": len(texts)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except ValueError:
            self._send(400, {"error": "bad json"})
            return
        if self.path == "/api/notes":
            name = os.path.basename(body.get("file", ""))
            data = load_notes(name)
            op = body.get("op")
            if op == "add_note":
                data["notes"].insert(0, {"t": time.strftime("%Y-%m-%d %H:%M"), "quote": body.get("quote", "")[:1000],
                                         "text": body.get("text", "")[:5000], "kind": body.get("kind", "")})
            elif op == "del_note":
                idx = body.get("idx")
                if isinstance(idx, int) and 0 <= idx < len(data["notes"]):
                    data["notes"].pop(idx)
            elif op == "del_qa":
                idx = body.get("idx")
                if isinstance(idx, int) and 0 <= idx < len(data["qa"]):
                    data["qa"].pop(idx)
            save_notes(name, data)
            self._send(200, data)
        elif self.path == "/api/ask":
            name = os.path.basename(body.get("file", ""))
            question = (body.get("question") or "").strip()
            quote_txt = (body.get("quote") or "").strip()
            if not question or not os.path.isfile(os.path.join(ARCHIVE, name)):
                self._send(400, {"error": "질문이 없습니다"})
                return
            answer = answer_question(name, quote_txt, question)
            if not answer:
                self._send(200, {"error": "Claude 응답 실패 (로그인/사용량 확인)"})
                return
            data = load_notes(name)
            data["qa"].insert(0, {"t": time.strftime("%Y-%m-%d %H:%M"), "quote": quote_txt[:1000],
                                  "q": question[:2000], "a": answer[:20000], "kind": body.get("kind", "")})
            save_notes(name, data)
            self._send(200, {"answer": answer, "qa": data["qa"]})
        elif self.path == "/api/locate":
            name = os.path.basename(body.get("file", ""))
            passage = (body.get("passage") or "").strip()
            if not passage or not os.path.isfile(os.path.join(ARCHIVE, name)):
                self._send(400, {"error": "구절이 없습니다"})
                return
            res = locate_in_pdf(name, passage)
            self._send(200, res or {"error": "원문 위치를 찾지 못했습니다"})
        elif self.path == "/api/smart":
            q = (body.get("q") or "").strip()
            year = str(body.get("year") or "")
            if not q:
                self._send(400, {"error": "검색어가 없습니다"})
                return
            key = ("smart:" + q + "|" + year, "smart")
            job = _jobs.get(key)
            if not job or job.get("status") == "error":
                _jobs[key] = {"status": "running", "stage": "시작", "t0": time.time()}
                threading.Thread(target=_run_smart_safe, args=(q, year, key), daemon=True).start()
            self._send(200, {"key": key[0]})
        elif self.path == "/api/generate":
            name = os.path.basename(body.get("file", ""))
            kind = body.get("kind", "summary")
            ext = body.get("ext")  # 보관소에 없는 외부 논문 시드 {id, title, doi}
            if ext and kind == "map" and ext.get("id"):
                name = "ext_" + re.sub(r"[^A-Za-z0-9]", "", ext["id"])
            elif kind not in ("summary", "translation", "map") or not os.path.isfile(os.path.join(ARCHIVE, name)):
                self._send(400, {"error": "bad request"})
                return
            if body.get("force") and os.path.isfile(gen_path(name, kind)):
                try:
                    os.remove(gen_path(name, kind))  # 다시 생성: 기존 결과 삭제 후 새로
                except OSError:
                    pass
            if os.path.isfile(gen_path(name, kind)):
                self._send(200, {"status": "done"})
                return
            key = (name, kind)
            job = _jobs.get(key)
            if job and job["status"] == "running":
                self._send(200, {"status": "running"})
                return
            _jobs[key] = {"status": "running"}
            threading.Thread(target=run_generation, args=(name, kind, ext), daemon=True).start()
            self._send(200, {"status": "running", "name": name})
        elif self.path == "/api/open":
            name = os.path.basename(body.get("file", ""))
            path = os.path.join(ARCHIVE, name)
            if os.path.isfile(path):
                os.startfile(path)
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "file not found"})
        elif self.path == "/api/tags":
            name = os.path.basename(body.get("file", ""))
            with _lock:
                tags = load_json(TAGS_PATH, {})
                entry = tags.get(name, {})  # title 등 기존 정보 유지
                entry["labels"] = list(body.get("labels", []))
                entry["suggested"] = list(body.get("suggested", []))
                entry["rejected"] = list(body.get("rejected", []))
                tags[name] = entry
                save_json(TAGS_PATH, tags)
            self._send(200, {"ok": True})
        elif self.path == "/api/labels":
            group = body.get("group", "")
            label = (body.get("label") or "").strip()
            with _lock:
                groups = load_json(LABELS_PATH, {})
                if group in groups and label and label not in groups[group]:
                    groups[group].append(label)
                    save_json(LABELS_PATH, groups)
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})


def main():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
        probe.close()
        return  # 이미 실행 중
    probe.close()
    threading.Thread(target=refresh_new_papers, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
