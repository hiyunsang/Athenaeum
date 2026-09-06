# -*- coding: utf-8 -*-
"""말로 설명한 도식을 Claude 가 그리기 코드로 써서 SVG 로 만드는 생성 엔진.

흐름: 설명·설정·참고 그림(아이패드 스케치·PPT 슬라이드) → Claude(claude -p, 그림은 Read 도구로 읽음) →
schematic.lib 를 쓰는 파이썬 스크립트 → 실행 → SVG → Edge headless PNG.
수정 요청은 이전 스크립트 + 요청문을 다시 Claude 에 주어 고친다. 스크립트가 그림의 '원본'이라 재현·재수정이 된다.
"""
import os, io, re, sys, json, time, subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # paper-search
cfg = {"claude_exe": None, "no_window": lambda: {}}

DOMAINS = {
    "cutting":    "절삭·공구 (직교/경사 절삭, 선삭, 칩, 전단면, 힘·속도 벡터, 공구 각도). lib.tool/chip/workpiece/shear_plane/force_vectors 를 우선 사용.",
    "grinding":   "연삭·밀링 운동학 (휠/커터 원, 절입, 이송, 접촉 호). circle/arc(path)/arrow/dimension 으로.",
    "setup":      "실험 장치 구성도 (기계·센서·카메라·냉각 노즐 등 블록과 연결선). label_box/rect/arrow 로 블록 다이어그램, 신호선은 dash.",
    "flow":       "흐름도·개념도 (단계 상자와 화살표, 분기). label_box/arrow, 정렬된 격자 배치.",
    "micro":      "미세조직·재료 (결정립, 변형층, 상 분포). grain_field/voronoi_grains/displace/shade_polygons.",
    "mechanics":  "힘·역학 벡터 (자유물체도, Merchant 원, 응력 상태). force_vectors/angle_mark/dimension/circle.",
    "general":    "일반 도식. 필요한 부품을 자유롭게 조합.",
}
COMPLEXITY = {
    "simple":   "간단하게: 검은 선과 라벨만, 채움 없음 또는 옅은 회색, 요소 수 최소. 인쇄용 선화.",
    "normal":   "보통: 구역별 단색 채움, 굵은 외곽선, 라벨 후광, 화살표. 저널 그림 기본형.",
    "detailed": "자세히: 그라데이션·컬러맵·결정립·질감, 다중 패널, 확대 연결선, 범례. 표지 그림 수준.",
}
STYLES = {
    "color":  "논문 컬러 (세리프 글꼴, 진한 외곽선, 구역별 채도 있는 색).",
    "line":   "흑백 선화 (채움 없음, 해칭으로 재료 구분, 검은 화살표).",
    "thermal": "온도장·컬러맵 중심 (field_layer/shade_polygons/colorbar, 뜨거운 곳 빨강).",
}
SIZES = {"single": (900, 700), "double": (1800, 1000), "square": (1000, 1000), "tall": (900, 1300)}


def _lib_api():
    try:
        sys.path.insert(0, BASE)
        from schematic import lib
        return lib.api_doc()
    except Exception as e:
        return "(lib.api_doc 사용 불가: %s)" % e


def build_prompt(spec, ref_images=(), prev_script=None, feedback=None, error=None):
    w, h = SIZES.get(spec.get("size", "single"), SIZES["single"])
    lang = spec.get("lang", "ko")
    api = _lib_api()
    head = ("당신은 기계가공 분야 논문 그림(도식)을 그리는 일러스트레이터이자 파이썬 프로그래머다. "
            "아래 라이브러리 `schematic.lib` 만 사용해 그림을 그리는 **완전한 파이썬 스크립트 하나**를 써라.\n"
            "출력 규칙: 설명 없이 ```python 코드 블록 하나만. 스크립트는 `import sys; sys.path.insert(0, r'%s')` 뒤 `from schematic.lib import *` 로 시작하고, "
            "마지막에 `print(c.svg())` 로 SVG 를 표준 출력에 찍어야 한다. 캔버스 크기는 정확히 Canvas(%d, %d).\n" % (BASE, w, h))
    rules = ("그림 규칙:\n- 라벨 언어: %s. 전문용어는 %s.\n- 분야: %s\n- 복잡도: %s\n- 스타일: %s\n"
             "- 글자는 text(..., halo=...) 로 배경 위에서 읽히게, 크기는 캔버스 폭의 1.5~3%%. 라벨과 도형이 겹치지 않게 여백을 둔다.\n"
             "- 화살표 머리는 가리키는 쪽에. 치수는 dimension(), 각도는 angle_mark(). 좌표축 상자가 필요하면 axis_box().\n"
             "- 물리적으로 맞게: 절삭이면 공구 경사각·여유각 방향, 칩은 경사면을 타고 올라가며 말림, 전단면은 날끝에서 위-앞으로.\n"
             "- 색은 뜻이 있게: 구역·부품마다 일관된 색, 온도는 컬러맵, 강조 1색.\n"
             "- 코드는 계산으로 좌표를 잡되(변수·비례), 150줄 이내. 존재하지 않는 함수를 부르지 마라. 아래 API 만 사용.\n" % (
                 "한국어(필요하면 영어 병기)" if lang == "ko" else "영어", "영어 원어 유지" if lang == "ko" else "표준 영어 용어",
                 DOMAINS.get(spec.get("domain", "general"), DOMAINS["general"]),
                 COMPLEXITY.get(spec.get("complexity", "normal"), COMPLEXITY["normal"]),
                 STYLES.get(spec.get("style", "color"), STYLES["color"])))
    refs = ""
    if ref_images:
        refs = ("참고 그림 %d장이 있다. **먼저 Read 도구로 각 파일을 열어 보고** 구성·배치·요소·라벨을 그대로 따르되 깔끔한 벡터 도식으로 다시 그려라 "
                "(손그림이면 의도를 해석해 정돈, PPT 슬라이드면 배치를 유지):\n%s\n" % (len(ref_images), "\n".join("- " + p for p in ref_images)))
    body = "[그림 설명]\n%s\n\n" % (spec.get("description") or "(설명 없음 - 참고 그림을 따라 그려라)")
    if prev_script:
        body += "[이전 스크립트]\n```python\n%s\n```\n\n" % prev_script
    if feedback:
        body += "[수정 요청] 이전 스크립트를 바탕으로 다음을 반영해 전체 스크립트를 다시 써라 (요청하지 않은 부분은 유지):\n%s\n\n" % feedback
    if error:
        body += "[실행 오류] 이전 스크립트를 실행하니 아래 오류가 났다. 원인을 고쳐 전체 스크립트를 다시 써라:\n%s\n\n" % error[-1500:]
    return head + rules + refs + body + "[schematic.lib API]\n" + api


def _claude(prompt, ref_dirs=(), timeout=600):
    exe = cfg.get("claude_exe")
    if not exe:
        raise RuntimeError("claude 명령을 찾을 수 없습니다")
    args = [exe, "-p", "--model", "opus", "--output-format", "text"]
    if ref_dirs:
        args += ["--allowedTools", "Read"]
        for d in sorted(set(ref_dirs)):
            args += ["--add-dir", d]
    r = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, timeout=timeout, **cfg["no_window"]())
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode != 0 and not out.strip():
        err = r.stderr.decode("utf-8", "replace")[:300]
        raise RuntimeError("Claude 실패: " + (err or "응답 없음"))
    return out


def extract_script(text):
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    code = (m.group(1) if m else text).strip()
    if "print(c.svg())" not in code and "print(" not in code:
        code += "\nprint(c.svg())\n"
    return code


def run_script(code, timeout=90):
    """스크립트를 별도 프로세스로 실행해 SVG 를 얻는다. (성공 여부, svg 또는 오류)"""
    env = dict(os.environ); env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=timeout, cwd=BASE, env=env, **cfg["no_window"]())
    out = r.stdout.decode("utf-8", "replace")
    err = r.stderr.decode("utf-8", "replace")
    if r.returncode != 0 or "<svg" not in out:
        return False, (err or out)[-3000:]
    return True, out[out.index("<svg"):]


def svg_size(svg, default=(900, 700)):
    m = re.search(r'<svg[^>]*\swidth="(\d+)"[^>]*\sheight="(\d+)"', svg)
    return (int(m.group(1)), int(m.group(2))) if m else default


TEMPLATE_DIR = os.path.join(BASE, "schematic", "templates")


def find_seed(spec, prior_scripts=()):
    """첫 생성을 백지에서 하지 않게: 같은 분야의 최근 그림 스크립트 → 없으면 분야 틀(templates/<domain>.py).
    (백지 설계 7분 vs 기존 코드 고치기 30초 — 시간 차이의 대부분이 여기서 난다)"""
    for sc in prior_scripts:
        if sc and "from schematic.lib import" in sc:
            return sc, "같은 분야의 최근 그림"
    p = os.path.join(TEMPLATE_DIR, "%s.py" % spec.get("domain", "general"))
    if os.path.isfile(p):
        return io.open(p, encoding="utf-8").read(), "분야 틀"
    p = os.path.join(TEMPLATE_DIR, "general.py")
    if os.path.isfile(p):
        return io.open(p, encoding="utf-8").read(), "일반 틀"
    return None, None


def save_template(spec, code):
    """분야 틀이 아직 없으면 성공한 스크립트를 틀로 저장 (다음부터 그 분야는 이 코드에서 시작)"""
    try:
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        p = os.path.join(TEMPLATE_DIR, "%s.py" % spec.get("domain", "general"))
        if not os.path.isfile(p):
            io.open(p, "w", encoding="utf-8").write(code)
            return True
    except Exception:
        pass
    return False


def generate(spec, ref_images=(), prev_script=None, feedback=None, log=None, prior_scripts=()):
    """설명(+참고 그림, +이전 스크립트/수정 요청) → (script, svg, 시도 기록). 실행 오류는 최대 2번 Claude 에 되먹여 고친다."""
    log = log if log is not None else []
    ref_dirs = [os.path.dirname(p) for p in ref_images]
    if not prev_script and not feedback:
        seed, why = find_seed(spec, prior_scripts)
        if seed:
            prev_script = seed
            feedback = ("아래 [이전 스크립트]는 출발점(틀)이다. 구조와 함수 사용법은 그대로 살리되, [그림 설명]에 맞게 형상·부품·라벨·수치를 바꿔 "
                        "새 그림을 완성하라. 설명에 없는 요소는 빼고 필요한 요소는 더하라.")
            log.append("출발점: " + why)
    prompt = build_prompt(spec, ref_images, prev_script, feedback)
    t0 = time.time()
    raw = _claude(prompt, ref_dirs)
    code = extract_script(raw)
    log.append("Claude 응답 %.0f초, 코드 %d줄" % (time.time() - t0, code.count("\n") + 1))
    for attempt in range(3):
        ok, res = run_script(code)
        if ok:
            log.append("실행 성공 (시도 %d)" % (attempt + 1))
            if save_template(spec, code):
                log.append("이 분야의 시작 틀로 저장")
            return code, res, log
        log.append("실행 오류 (시도 %d): %s" % (attempt + 1, res.strip().splitlines()[-1][:160] if res.strip() else "?"))
        if attempt == 2:
            break
        t0 = time.time()
        raw = _claude(build_prompt(spec, ref_images, code, feedback, error=res), ref_dirs)
        code = extract_script(raw)
        log.append("고친 코드 받음 %.0f초" % (time.time() - t0))
    raise RuntimeError("스크립트 실행 실패: " + res.strip().splitlines()[-1][:200] if res.strip() else "알 수 없는 오류")


# ---------- 참고 그림 입력 (아이패드 스케치 이미지, PPT) ----------
def pptx_to_images(pptx_path, out_dir, base):
    """PPT 슬라이드를 PNG 로. PowerPoint 가 있으면 COM 으로 정확히 내보내고, 없으면 파일 안의 그림들을 꺼낸다."""
    os.makedirs(out_dir, exist_ok=True)
    outs = []
    try:
        import win32com.client, pythoncom
        pythoncom.CoInitialize()
        app = win32com.client.Dispatch("PowerPoint.Application")
        pres = app.Presentations.Open(os.path.abspath(pptx_path), True, False, False)   # ReadOnly, Untitled, WithWindow=False
        try:
            for i in range(1, pres.Slides.Count + 1):
                p = os.path.join(out_dir, "%s_slide%d.png" % (base, i))
                pres.Slides(i).Export(p, "PNG", 1600, 900)
                outs.append(p)
        finally:
            pres.Close()
        if outs:
            return outs, "PowerPoint 로 %d장 내보냄" % len(outs)
    except Exception as e:
        note = "PowerPoint 내보내기 실패(%s) → 파일 속 그림 추출" % str(e)[:80]
    else:
        note = "슬라이드 없음"
    import zipfile
    try:
        z = zipfile.ZipFile(pptx_path)
        k = 0
        for n in z.namelist():
            if n.startswith("ppt/media/") and n.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                k += 1
                p = os.path.join(out_dir, "%s_media%d%s" % (base, k, os.path.splitext(n)[1].lower()))
                open(p, "wb").write(z.read(n)); outs.append(p)
    except Exception as e:
        note += "; 추출 실패 %s" % str(e)[:60]
    return outs, note


def save_ref_image(data, name, out_dir, base):
    """올라온 참고 그림(png/jpg/heic 불가/pptx) 저장 → 이미지 경로 목록"""
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(name)[1].lower()
    if ext in (".pptx", ".ppt"):
        p = os.path.join(out_dir, base + ext)
        open(p, "wb").write(data)
        return pptx_to_images(p, out_dir, base)
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        raise RuntimeError("지원하지 않는 형식: " + ext + " (PNG/JPG/PPTX)")
    p = os.path.join(out_dir, base + ext)
    open(p, "wb").write(data)
    # 너무 큰 사진(아이패드 스캔)은 2000px 로 줄여 Claude 가 읽기 쉽게
    try:
        from PIL import Image
        im = Image.open(p)
        if max(im.size) > 2000:
            im.thumbnail((2000, 2000)); im.save(p)
    except Exception:
        pass
    return [p], "저장"
