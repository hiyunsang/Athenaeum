# -*- coding: utf-8 -*-
"""논문 라벨 검색 로컬 서버. 논문검색_실행.bat 으로 실행."""
import html as html_mod
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
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


def gen_path(name, kind):
    stem = os.path.splitext(name)[0]
    return os.path.join(GEN_DIR, "{}.{}.md".format(stem, "요약" if kind == "summary" else "번역"))


def find_claude():
    for c in ("claude", "claude.cmd", "claude.exe"):
        p = shutil.which(c)
        if p:
            return p
    return None


def run_generation(name, kind):
    key = (name, kind)
    try:
        with _gen_sem:
            _run_generation(name, kind)
        _jobs.pop(key, None)
        return
    except subprocess.TimeoutExpired:
        _jobs[key] = {"status": "error", "error": "시간 초과 (40분) - 논문이 너무 긴 듯"}
    except Exception as e:
        _jobs[key] = {"status": "error", "error": str(e)[:300]}


def _run_generation(name, kind):
    from pypdf import PdfReader
    reader = PdfReader(os.path.join(ARCHIVE, name))
    if reader.is_encrypted:
        reader.decrypt("")
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    if len(text.strip()) < 500:
        raise RuntimeError("이 PDF는 글자를 추출할 수 없습니다 (스캔본인 듯)")
    exe = find_claude()
    if not exe:
        raise RuntimeError("claude 명령을 찾을 수 없습니다 (Claude Code 설치 확인)")
    prompt = build_prompt(kind, paper_header(name)) + text
    r = subprocess.run([exe, "-p", "--output-format", "text"],
                       input=prompt.encode("utf-8"),
                       capture_output=True, timeout=2400)
    out = r.stdout.decode("utf-8", "replace").strip()
    errtxt = r.stderr.decode("utf-8", "replace").strip()
    if r.returncode != 0 or not out:
        msg = (errtxt or out)[:300]
        low = msg.lower()
        if "authenticate" in low or "login" in low or "oauth" in low:
            msg = "명령줄 로그인이 필요합니다: 터미널에서 claude 입력 → /login 입력 → 브라우저 로그인"
        raise RuntimeError(msg or "생성 실패 (원인 불명)")
    os.makedirs(GEN_DIR, exist_ok=True)
    with open(gen_path(name, kind), "w", encoding="utf-8") as f:
        f.write(out)


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
        with _lock:
            tags = load_json(TAGS_PATH, {})
            for f in new_files:
                if f not in tags:
                    title = new_titles.get(f) or parse_name(f)["title"]
                    auto, sugg = rules.classify_labels(title, texts.get(f, ""))
                    tags[f] = {"labels": auto, "suggested": sugg, "rejected": []}
                    if f in new_titles:
                        tags[f]["title"] = new_titles[f]
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
            if any(f not in tags for f in archive_pdfs()):
                threading.Thread(target=refresh_new_papers, daemon=True).start()
            papers = []
            for f in archive_pdfs():
                meta = parse_name(f)
                t = tags.get(f, {})
                if t.get("title"):  # 파일명은 80자로 잘리므로 전체 제목이 있으면 그걸 사용
                    meta["title"] = t["title"]
                meta.update({"file": f, "labels": t.get("labels", []),
                             "suggested": t.get("suggested", []),
                             "rejected": t.get("rejected", []),
                             "has_summary": os.path.exists(gen_path(f, "summary")),
                             "has_translation": os.path.exists(gen_path(f, "translation"))})
                papers.append(meta)
            self._send(200, {"papers": papers, "groups": load_json(LABELS_PATH, {})})
        elif url.path == "/view":
            q = parse_qs(url.query)
            name = os.path.basename(q.get("file", [""])[0])
            kind = q.get("kind", ["summary"])[0]
            from urllib.parse import quote
            page = (VIEW_PAGE
                    .replace("__TITLE__", html_mod.escape(os.path.splitext(name)[0][:60]))
                    .replace("__KIND_LABEL__", "요약" if kind == "summary" else "전문번역")
                    .replace("__FILE_Q__", quote(name))
                    .replace("__KIND__", kind))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif url.path == "/pdf":
            q = parse_qs(url.query)
            name = os.path.basename(q.get("file", [""])[0])
            path = os.path.join(ARCHIVE, name)
            if os.path.isfile(path) and name.lower().endswith(".pdf"):
                with open(path, "rb") as f:
                    self._send(200, f.read(), "application/pdf")
            else:
                self._send(404, {"error": "not found"})
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
                    self._send(200, job if job["status"] == "error" else {"status": "running"})
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
        if self.path == "/api/generate":
            name = os.path.basename(body.get("file", ""))
            kind = body.get("kind", "summary")
            if kind not in ("summary", "translation") or not os.path.isfile(os.path.join(ARCHIVE, name)):
                self._send(400, {"error": "bad request"})
                return
            if os.path.isfile(gen_path(name, kind)):
                self._send(200, {"status": "done"})
                return
            key = (name, kind)
            job = _jobs.get(key)
            if job and job["status"] == "running":
                self._send(200, {"status": "running"})
                return
            _jobs[key] = {"status": "running"}
            threading.Thread(target=run_generation, args=(name, kind), daemon=True).start()
            self._send(200, {"status": "running"})
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
