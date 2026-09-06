# -*- coding: utf-8 -*-
"""
Athenaeum 논문 수집 모듈  (예전 이름: paper-organizer, 논문 자동 정리 프로그램 — 2026-09-06 에 하나로 합침)

다운로드 폴더를 지켜보다가 새 PDF가 생기면:
  1. PDF 앞쪽 페이지에서 DOI를 찾고
  2. Crossref(무료 논문 정보 사이트)로 제목·저자·연도·저널을 조회한 뒤
  3. "연도_저널약어_저자_제목.pdf" 로 이름을 바꿔 논문모음(보관소)으로 옮긴다.
주제 폴더(바탕화면 논문주제 트리)에 진짜 PDF가 들어오면 보관소로 옮기고 그 자리에 바로가기(.lnk)를 남긴다.
DOI를 못 찾은 PDF(견적서·강의자료 등 논문이 아닌 파일)는 절대 건드리지 않는다. 파일을 지우는 일도 없다.

server.py 가 init() → start() 로 서버 안 스레드에서 돌리고,
홈 화면의 '수집' 패널이 view() / control() 로 상태를 보고 설정을 바꾼다.
  설정: paper-search\\수집설정.json   (수집 패널에서 편집. 파일을 직접 고쳐 저장해도 자동 반영)
  기록: MAENG_paper\\수집\\수집기록.txt (모든 처리 내역), 건너뜀기록.json (논문 아님으로 판정한 파일 - 재시작 때 다시 안 훑게)
혼자 돌려 보기(시험용):  python intake.py --once
"""

import collections
import hashlib
import html
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import unicodedata

import requests
from pypdf import PdfReader

try:
    import win32com.client
except ImportError:
    win32com = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))        # paper-search
ROOT = os.path.dirname(BASE_DIR)                              # MAENG_paper
CONFIG_PATH = os.path.join(BASE_DIR, "수집설정.json")
DATA_DIR = os.path.join(ROOT, "수집")
LOG_PATH = os.path.join(DATA_DIR, "수집기록.txt")
SKIP_PATH = os.path.join(DATA_DIR, "건너뜀기록.json")
DEFAULT_ARCHIVE = os.path.join(ROOT, "논문모음")
LEGACY_DIR = os.path.join(ROOT, "paper-organizer")           # 합치기 전 폴더 (남아 있으면 설정·기록을 옮겨 온다)

# 중복 실행 방지용 포트 (예전 독립 프로그램과 같은 번호 - 둘이 동시에 파일을 옮기는 사고를 막는다)
SINGLE_INSTANCE_PORT = 47653

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
# 줄바꿈으로 끊긴 DOI도 이어붙여서 찾는 버전
DOI_JOIN_RE = re.compile(r"10\.\d{4,9}/(?:[-._;()/:A-Za-z0-9]|\n(?=[-._;()/:A-Za-z0-9]))+")

# 저널명 약어 자동 생성용 (이니셜 방식: 단어 첫 글자, 예: JMSE)
STOPWORDS = {"of", "the", "and", "in", "on", "for", "a", "an", "de", "la", "&", "amp"}

_listeners = []   # log() 가 부르는 함수들 (서버가 화면용 이벤트를 받아 간다)
_forced_archive = None   # 서버 안에서 돌 때는 보관소를 서버의 ARCHIVE 로 고정 (설정 파일의 논문폴더보다 우선)
_wake = threading.Event()   # '지금 검사'·일시정지·설정 저장 때 감시 스레드를 바로 깨운다


def _int_or(v, default, lo, hi):
    """설정 파일을 손으로 고치다 잘못 넣은 값("3초", null 등)에도 스레드가 죽지 않게 정수로 안전 변환."""
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, v))


def log(msg):
    line = "[{}] {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if sys.stdout:  # pythonw(백그라운드)로 실행하면 화면 출력이 없음
        try:
            print(line, flush=True)
        except OSError:
            pass
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    for fn in list(_listeners):
        try:
            fn(msg)
        except Exception:
            pass


def migrate_legacy():
    """합치기 전 paper-organizer 폴더가 남아 있으면 설정·기록·건너뜀 목록을 새 자리로 가져온다 (한 번만)."""
    if not os.path.isdir(LEGACY_DIR):
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    old_cfg = os.path.join(LEGACY_DIR, "config.json")
    if os.path.isfile(old_cfg) and not os.path.isfile(CONFIG_PATH):
        shutil.copy2(old_cfg, CONFIG_PATH)
    old_log = os.path.join(LEGACY_DIR, "정리기록.txt")
    if os.path.isfile(old_log):
        try:
            with open(old_log, encoding="utf-8", errors="ignore") as src, open(LOG_PATH, "a", encoding="utf-8") as dst:
                shutil.copyfileobj(src, dst)
            os.remove(old_log)
        except OSError:
            pass
    old_skip = os.path.join(LEGACY_DIR, "건너뜀기록.json")
    if os.path.isfile(old_skip) and not os.path.isfile(SKIP_PATH):
        try:
            shutil.move(old_skip, SKIP_PATH)
        except OSError:
            pass


class Config:
    # 화면에서 고칠 수 있는 항목과 검사 규칙
    EDITABLE = ("감시폴더", "주제폴더", "검사주기_초", "제목최대길이", "저널약어")

    def __init__(self):
        self.mtime = 0
        self.data = {}
        self.reload_if_changed()

    def reload_if_changed(self):
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            return
        if mtime == self.mtime:
            return
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                self.data = json.load(f)
            if self.mtime != 0:
                log("설정 파일이 바뀌어 다시 읽었습니다.")
            self.mtime = mtime
        except (OSError, ValueError) as e:
            log("설정 파일을 읽지 못했습니다 (내용 확인 필요): {}".format(e))

    @property
    def watch_dir(self):
        # 설정 파일이 다른 컴퓨터에서 왔거나 폴더가 없으면 이 사용자의 다운로드 폴더로 (배포 대비)
        p = self.data.get("감시폴더") or ""
        return p if p and os.path.isdir(p) else os.path.expanduser("~/Downloads")

    @property
    def target_dir(self):
        return _forced_archive or (self.data.get("논문폴더") or DEFAULT_ARCHIVE)   # 빈 값(배포판 기본)이면 저장소의 논문모음

    @property
    def interval(self):
        return _int_or(self.data.get("검사주기_초", 3), 3, 1, 300)

    @property
    def title_max(self):
        return _int_or(self.data.get("제목최대길이", 140), 140, 20, 200)

    @property
    def journal_map(self):
        return {
            normalize_journal(k): v
            for k, v in self.data.get("저널약어", {}).items()
        }

    @property
    def tree_dir(self):
        p = self.data.get("주제폴더", "") or ""
        return p if os.path.isdir(p) else ""   # 없는 폴더(다른 컴퓨터의 경로)는 무시

    def public(self):
        """화면에 보여 줄 설정 값 (없는 항목은 기본값으로 채워서)."""
        return {"감시폴더": self.watch_dir, "논문폴더": self.target_dir, "주제폴더": self.tree_dir,
                "검사주기_초": self.interval, "제목최대길이": self.title_max,
                "저널약어": dict(self.data.get("저널약어", {}))}

    def update(self, changes):
        """홈 화면에서 바꾼 설정을 검사해 파일에 쓴다. 문제가 있으면 오류 문장 목록을 돌려주고 아무것도 바꾸지 않는다."""
        errs, data = [], dict(self.data)
        if "감시폴더" in changes:
            raw = str(changes["감시폴더"]).strip()
            p = os.path.normpath(raw) if raw else ""
            if not raw or not os.path.isabs(p):
                errs.append("감시 폴더는 전체 경로로 적어 주세요 (예: %s)" % os.path.expanduser("~/Downloads"))
            elif not os.path.isdir(p):
                errs.append("감시 폴더가 없습니다: %s" % p)
            else:
                data["감시폴더"] = p
        if "주제폴더" in changes:
            raw = str(changes["주제폴더"]).strip()
            p = os.path.normpath(raw) if raw else ""
            if p and not os.path.isabs(p):
                errs.append("주제 폴더는 전체 경로로 적어 주세요")
            elif p and not os.path.isdir(p):
                errs.append("주제 폴더가 없습니다: %s" % p)
            else:
                data["주제폴더"] = p
        if "검사주기_초" in changes:
            try:
                v = int(changes["검사주기_초"])
                if not 1 <= v <= 300:
                    raise ValueError
                data["검사주기_초"] = v
            except (TypeError, ValueError):
                errs.append("검사 주기는 1~300 초 사이의 정수여야 합니다")
        if "제목최대길이" in changes:
            try:
                v = int(changes["제목최대길이"])
                if not 40 <= v <= 200:
                    raise ValueError
                data["제목최대길이"] = v
            except (TypeError, ValueError):
                errs.append("제목 최대 길이는 40~200 사이여야 합니다")
        if "저널약어" in changes:
            m = changes["저널약어"]
            if not isinstance(m, dict):
                errs.append("저널 약어 형식이 잘못되었습니다")
            else:
                clean = {}
                for k, v in m.items():
                    k, v = str(k).strip(), str(v).strip()
                    if k and v:
                        if re.search(r'[\\/:*?"<>|]', v):
                            errs.append("약어에 파일명에 못 쓰는 문자가 있습니다: %s" % v)
                        clean[k] = v
                data["저널약어"] = clean
        if errs:
            return errs
        # 폴더 관계 검사: 보관소를 감시하거나(무한 루프), 주제 폴더가 보관소를 품으면(보관소 PDF 를 전부 바로가기로 바꿔 버림) 안 된다
        archive = os.path.normcase(os.path.abspath(self.target_dir))
        watch = os.path.normcase(os.path.abspath(data.get("감시폴더", self.watch_dir)))
        tree = data.get("주제폴더", self.tree_dir)
        tree = os.path.normcase(os.path.abspath(tree)) if tree else ""

        def inside(child, parent):
            return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)
        if inside(watch, archive) or inside(archive, watch):
            return ["감시 폴더와 보관 폴더(논문모음)는 서로 안에 있으면 안 됩니다"]
        if tree and (inside(archive, tree) or inside(tree, archive)):
            return ["주제 폴더가 보관 폴더(논문모음)를 품거나 그 안에 있으면 안 됩니다. 보관소의 논문이 전부 바로가기로 바뀌어 버립니다"]
        if tree and inside(tree, watch):
            return ["주제 폴더가 감시 폴더 안에 있으면 안 됩니다"]
        tmp = CONFIG_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except OSError as e:
            return ["설정 파일을 쓰지 못했습니다 (다른 프로그램이 열고 있나요?): %s" % e]
        self.data = data
        try:
            self.mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            pass
        log("설정 저장: 감시 {} · 주기 {}초 · 저널약어 {}개".format(self.watch_dir, self.interval, len(data.get("저널약어", {}))))
        _wake.set()
        return []


def normalize_journal(name):
    name = name.lower().strip()
    if name.startswith("the "):
        name = name[4:]
    return re.sub(r"\s+", " ", name)


def auto_abbrev(journal):
    """이니셜 약어 자동 생성 (예: Journal of Manufacturing Science and Engineering -> JMSE).

    저널 한 단어짜리는 이니셜이 의미가 없으므로 단어 전체를 대문자로 (Wear -> WEAR).
    CIRP처럼 이미 약어인 단어(대문자 2개 이상)는 통째로 유지 (CIRP Annals -> CIRPA).
    """
    words = [w for w in re.split(r"[\s\-:,.&/]+", journal) if w]
    significant = [w for w in words if w.lower() not in STOPWORDS]
    if not significant:
        return "ETC"
    if len(significant) == 1:
        return significant[0].upper()
    parts = []
    for w in significant:
        if sum(1 for c in w if c.isupper()) >= 2:
            parts.append(w)
        else:
            parts.append(w[0].upper())
    return "".join(parts)


def journal_abbrev(journal, cfg):
    if not journal:
        return "ETC"
    mapped = cfg.journal_map.get(normalize_journal(journal))
    return mapped if mapped else auto_abbrev(journal)


def extract_doi_candidates(pdf_path):
    """앞쪽 페이지에서 DOI 후보들(나온 순서대로)과 대조용 본문 텍스트를 뽑는다."""
    reader = PdfReader(pdf_path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            return [], ""
    candidates, texts = [], []
    for page in reader.pages[:3]:
        text = page.extract_text() or ""
        texts.append(text)
        for m in DOI_RE.finditer(text):
            doi = m.group(0).rstrip(".,;)")
            if doi not in candidates:
                candidates.append(doi)
    # 줄바꿈으로 끊긴 DOI 복원 시도 (추출 텍스트에서 DOI가 중간에 잘리는 경우)
    for m in DOI_JOIN_RE.finditer("\n".join(texts)):
        doi = m.group(0).replace("\n", "").rstrip(".,;)")
        if doi not in candidates:
            candidates.append(doi)
    return candidates[:4], "\n".join(texts[:2])


def doi_variants(doi):
    """PDF 텍스트 추출 과정에서 DOI 뒤에 붙는 찌꺼기를 제거한 후보들."""
    out = []

    def add(v):
        v = v.rstrip(".,;()-")
        if v and "/" in v and v not in out:
            out.append(v)

    add(doi)
    add(re.sub(r"\(0123456789.*$", "", doi))    # Springer 첫 페이지 세로 글씨
    add(re.sub(r"/[A-Z]\d{3,}$", "", doi))      # 폰트 글리프 코드 (예: /H20852)
    add(re.sub(r"(?<=\d)[A-Za-z]+$", "", doi))  # 뒤에 바로 붙은 단어
    return out[:3]


def title_matches(title, page_text):
    """Crossref 제목의 단어들이 실제 PDF 앞부분에 있는지 확인.

    본문에 인용된 다른 논문의 DOI를 잘못 집는 것을 막는다
    (내 논문 제목은 반드시 첫 페이지에 있지만, 인용 논문 제목은 없음).
    """
    words = [w for w in re.findall(r"[a-z]{4,}", title.lower())]
    if not words:
        return False
    text = page_text.lower()
    hit = sum(1 for w in words if w in text)
    return hit / len(words) >= 0.5


def fetch_metadata(doi):
    r = requests.get(
        "https://api.crossref.org/works/" + doi,
        headers={"User-Agent": "athenaeum-intake/1.0"},
        timeout=15,
    )
    r.raise_for_status()
    return parse_message(r.json()["message"])


def parse_message(msg):
    title = msg.get("title") or [""]
    title = re.sub(r"<[^>]+>", "", html.unescape(title[0])).strip()

    authors = msg.get("author") or []
    family = authors[0].get("family", "") if authors else ""

    year = ""
    for key in ("published-print", "published-online", "issued"):
        parts = msg.get(key, {}).get("date-parts", [[None]])
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0])
            break

    journal = html.unescape((msg.get("container-title") or [""])[0])
    return {"title": title, "author": family, "year": year, "journal": journal}


def search_by_filename(pdf_path, page_text):
    """최후 수단: 파일명 단어로 Crossref 검색 후 제목 대조로 확인."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)  # camelCase 분리
    words = re.findall(r"[A-Za-z]{3,}", stem)
    if len(words) < 3:
        return None
    r = requests.get(
        "https://api.crossref.org/works",
        params={"query.bibliographic": " ".join(words), "rows": "3"},
        headers={"User-Agent": "athenaeum-intake/1.0"},
        timeout=20,
    )
    r.raise_for_status()
    for item in r.json()["message"].get("items", []):
        meta = parse_message(item)
        if meta["title"] and title_matches(meta["title"], page_text):
            return meta
    return None


# 파일명에 문제를 일으키는 특수문자 → 일반 문자 (바로가기 생성/타 프로그램 호환)
CHAR_FOLD = {
    "–": "-", "—": "-", "‒": "-", "‑": "-", "−": "-",      # 여러 종류의 대시 → 하이픈
    "‘": "'", "’": "'", "‚": "'", "ʼ": "'", "′": "'",      # 굽은 작은따옴표·프라임 → '
    "“": "'", "”": "'", "„": "'", "…": "...", "·": "-",      # 굽은 큰따옴표 → ' (큰따옴표는 파일명에 못 씀), 말줄임표, 가운뎃점
    "ı": "i", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
    "đ": "d", "Đ": "D", "ß": "ss", "æ": "ae", "Æ": "Ae",
    "œ": "oe", "Œ": "Oe", "ð": "d", "þ": "th",
}


def fold_chars(text):
    """악센트 제거(é→e)와 특수문자 치환. 한글과 ASCII는 그대로 통과."""
    text = unicodedata.normalize("NFC", text)  # 한글을 완성형으로 유지
    out = []
    for c in text:
        if c in CHAR_FOLD:
            out.append(CHAR_FOLD[c])
            continue
        try:
            c.encode("cp949")  # 한글과 ASCII는 통과
            out.append(c)
            continue
        except UnicodeEncodeError:
            pass
        folded = unicodedata.normalize("NFKD", c)
        folded = "".join(x for x in folded if not unicodedata.combining(x))
        try:
            folded.encode("cp949")
            out.append(folded)
        except UnicodeEncodeError:
            out.append("-")
    return "".join(out)


def sanitize(text):
    text = fold_chars(text)
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_filename(meta, cfg):
    title = sanitize(meta["title"])
    if len(title) > cfg.title_max:
        title = title[: cfg.title_max].rsplit(" ", 1)[0]
    title = title.rstrip(" ,;.-:")
    parts = [
        meta["year"] or "0000",
        journal_abbrev(meta["journal"], cfg),
        sanitize(meta["author"]) or "Unknown",
        title or "Untitled",
    ]
    return "_".join(parts) + ".pdf"


def unique_path(directory, filename):
    path = os.path.join(directory, filename)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(filename)
    n = 2
    while True:
        path = os.path.join(directory, "{} ({}){}".format(stem, n, ext))
        if not os.path.exists(path):
            return path
        n += 1


PII_RE = re.compile(r"(S\d{16})")   # Elsevier 파일명 1-s2.0-S1526612502701382-main.pdf 의 PII


def pii_dois(pdf_path):
    """DOI 가 본문에 인쇄되지 않은 옛 Elsevier 논문: 파일명의 PII 로 DOI 를 추정하고 Crossref alternative-id 로도 찾는다."""
    m = PII_RE.search(os.path.basename(pdf_path))
    if not m:
        return []
    pii = m.group(1)
    out = ["10.1016/%s-%s(%s)%s-%s" % (pii[:5], pii[5:9], pii[9:11], pii[11:16], pii[16])]   # S1526-6125(02)70138-2 꼴
    try:
        r = requests.get("https://api.crossref.org/works", params={"filter": "alternative-id:" + pii, "rows": "2"},
                         headers={"User-Agent": "athenaeum-intake/1.0"}, timeout=20)
        if r.status_code == 200:
            for it in r.json()["message"].get("items", []):
                if it.get("DOI") and it["DOI"] not in out:
                    out.append(it["DOI"])
    except requests.RequestException:
        pass
    return out


def search_by_text(page_text):
    """(사용 안 함 - 오탐 사고) 첫 쪽 본문 문장으로 Crossref 검색. 제목 단어 50% 대조만으로는 카메라 견적서가 CMOS 센서 논문으로 통과함."""
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", page_text[:3000])
    if len(words) < 25:
        return None
    # 앞쪽 40 낱말 (초록 첫 두 문장 정도) 로 검색
    q = " ".join(words[:40])
    try:
        r = requests.get("https://api.crossref.org/works", params={"query.bibliographic": q, "rows": "3"},
                         headers={"User-Agent": "athenaeum-intake/1.0"}, timeout=25)
        r.raise_for_status()
    except requests.RequestException:
        return None
    for item in r.json()["message"].get("items", []):
        meta = parse_message(item)
        if meta["title"] and title_matches(meta["title"], page_text):
            return meta
    return None


def resolve_meta(pdf_path):
    """PDF에서 DOI를 찾아 서지정보를 얻는다. 실패하면 None. (서버의 라벨링·관련맵도 이 함수를 같이 쓴다)
    순서: 본문 DOI → 파일명 PII(Elsevier). DOI 실마리가 없으면 None (파일명 낱말 검색은 DOI 후보가 있었을 때의 보조 수단으로만).
    (첫 쪽 문장으로 Crossref 검색하는 방식은 견적서·공지 PDF 를 엉뚱한 논문으로 판정해 파일을 옮기는 사고를 내서 쓰지 않는다.)"""
    candidates, page_text = extract_doi_candidates(pdf_path)
    if not candidates:
        candidates = pii_dois(pdf_path)   # 파일명의 Elsevier PII → DOI (결정적 근거)
    if not candidates:
        # DOI 실마리가 전혀 없으면 논문으로 보지 않는다. 파일명 낱말 검색을 여기서 쓰면 'Program Notice', '견적서 Memrecam' 같은
        # 파일이 엉뚱한 논문으로 판정된다 (2026-09-06 두 번 사고). 파일명 검색은 DOI 후보가 있었으나 조회가 다 실패했을 때만.
        return None
    meta = None
    attempts = 0
    for doi in candidates:
        for variant in doi_variants(doi):
            if attempts >= 8 or meta:
                break
            attempts += 1
            try:
                m = fetch_metadata(variant)
            except requests.HTTPError:
                continue  # 등록 안 된 DOI (추출 찌꺼기 등) - 다음 후보 시도
            if m["title"] and title_matches(m["title"], page_text):
                meta = m
        if meta:
            break
    if meta is None:
        # DOI 경로가 다 실패했을 때만 파일명·본문 검색 시도 (DOI가 있는 문서 = 논문일 확률 높음)
        meta = search_by_filename(pdf_path, page_text)
    return meta


def process_pdf(pdf_path, cfg):
    """정리하면 옮긴 경로를, 논문이 아니라 건너뛰면 None을 반환. 재시도 필요하면 예외."""
    name = os.path.basename(pdf_path)
    meta = resolve_meta(pdf_path)
    if meta is None:
        log("건너뜀 (논문으로 확인 안 됨): {}".format(name))
        return None

    os.makedirs(cfg.target_dir, exist_ok=True)
    canonical = os.path.join(cfg.target_dir, build_filename(meta, cfg))
    if os.path.exists(canonical):
        # 같은 논문이 이미 보관소에 있음 = 재다운로드. (2)를 만들지 않고 중복사본으로.
        dup_dir = os.path.join(cfg.target_dir, "_중복사본")
        os.makedirs(dup_dir, exist_ok=True)
        dup_path = unique_path(dup_dir, os.path.basename(canonical))
        shutil.move(pdf_path, dup_path)
        log("재다운로드 (이미 보관 중) -> 중복사본: {}".format(os.path.basename(canonical)))
        return canonical
    shutil.move(pdf_path, canonical)
    log("정리 완료: {}  ->  {}".format(name, os.path.basename(canonical)))
    return canonical


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_lnk(folder, stem, target):
    """folder 안에 stem 이름의 바로가기(.lnk)를 만든다."""
    shell = win32com.client.Dispatch("WScript.Shell")
    stem = sanitize(stem)  # 특수문자가 있으면 바로가기 저장이 실패함
    lnk_path = os.path.join(folder, stem + ".lnk")
    n = 2
    while os.path.exists(lnk_path):
        lnk_path = os.path.join(folder, "{} ({}).lnk".format(stem, n))
        n += 1
    lnk = shell.CreateShortCut(lnk_path)
    lnk.TargetPath = target
    lnk.Save()


def build_hash_index(archive_dir):
    index = {}
    try:
        for f in os.listdir(archive_dir):
            p = os.path.join(archive_dir, f)
            if f.lower().endswith(".pdf") and os.path.isfile(p):
                index[sha1_of(p)] = p
    except OSError:
        pass
    return index


def scan_tree_once(cfg, size_history, hash_index, cooldown):
    """주제 폴더에 들어온 진짜 PDF를 보관소로 옮기고 그 자리에 바로가기를 남긴다."""
    tree = cfg.tree_dir
    if not tree or not os.path.isdir(tree) or win32com is None:
        return
    for dirpath, dirs, files in os.walk(tree):
        for name in files:
            if not name.lower().endswith(".pdf"):
                continue
            path = os.path.join(dirpath, name)
            if cooldown.get(path, 0) > time.time():
                continue
            if not is_stable(path, size_history):
                continue
            stem = os.path.splitext(name)[0]
            try:
                h = sha1_of(path)
                if h in hash_index:
                    # 보관소에 이미 있는 논문 - 복사본은 치우고 바로가기로 교체
                    dup_dir = os.path.join(cfg.target_dir, "_중복사본")
                    os.makedirs(dup_dir, exist_ok=True)
                    shutil.move(path, unique_path(dup_dir, name))
                    make_lnk(dirpath, stem, hash_index[h])
                    log("주제폴더: 바로가기로 교체 - {}".format(name))
                    continue
                meta = resolve_meta(path)
                os.makedirs(cfg.target_dir, exist_ok=True)
                if meta:
                    new_path = unique_path(cfg.target_dir, build_filename(meta, cfg))
                else:
                    # 주제 폴더에 넣었다는 건 논문이라는 뜻이므로 이름 그대로라도 보관
                    new_path = unique_path(cfg.target_dir, name)
                shutil.move(path, new_path)
                hash_index[h] = new_path
                make_lnk(dirpath, stem, new_path)
                log("주제폴더: 보관소로 이동 + 바로가기 - {}  ->  {}".format(
                    name, os.path.basename(new_path)))
            except Exception as e:
                if is_file_locked_error(e):
                    cooldown[path] = time.time() + 300
                    log("주제폴더: 파일이 열려 있어 나중에 다시 시도 - {}".format(name))
                else:
                    cooldown[path] = time.time() + 600
                    log("주제폴더 처리 실패 (10분 뒤 재시도): {} - {}".format(name, e))


def is_stable(path, size_history):
    """다운로드가 끝났는지 확인: 파일 크기가 직전 검사 때와 같으면 완료로 판단."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    prev = size_history.get(path)
    size_history[path] = size
    return prev is not None and prev == size


def is_file_locked_error(e):
    """다른 프로그램(PDF 뷰어 등)이 파일을 열고 있어서 생긴 오류인지."""
    return isinstance(e, OSError) and getattr(e, "winerror", None) == 32


def scan_once(cfg, skipped, retries, size_history, hash_index, cooldown):
    watch = cfg.watch_dir
    try:
        names = os.listdir(watch)
    except OSError as e:
        log("감시폴더를 열 수 없습니다: {} ({})".format(watch, e))
        return
    for name in names:
        if not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(watch, name)
        if path in skipped or not os.path.isfile(path):
            continue
        if cooldown.get(path, 0) > time.time():
            continue
        if os.path.exists(path + ".crdownload") or not is_stable(path, size_history):
            continue
        try:
            new_path = process_pdf(path, cfg)
            if new_path is None:
                skipped.add(path)
            else:
                hash_index[sha1_of(new_path)] = new_path
            retries.pop(path, None)
            cooldown.pop(path, None)
        except Exception as e:
            if is_file_locked_error(e):
                # 사용자가 읽고 있는 중 - 포기하지 말고 5분마다 다시 시도
                cooldown[path] = time.time() + 300
                log("파일이 열려 있어 나중에 다시 시도: {}".format(name))
                continue
            count = retries.get(path, 0) + 1
            retries[path] = count
            if count >= 3:
                log("포기 (3회 실패): {} - {}".format(name, e))
                skipped.add(path)
                retries.pop(path, None)
            else:
                log("잠시 후 재시도 ({}회째 실패): {} - {}".format(count, name, e))


# ====================== 서버 안에서 돌리기 (Athenaeum 홈 화면 '수집' 패널) ======================

_state = {"enabled": True, "alive": False, "waiting_lock": False, "error": "", "last_scan": 0, "started": 0,
          "events": collections.deque(maxlen=400), "skipped_n": 0}
_watch = {"skipped": set(), "skip_cache": {}, "retries": {}, "size_history": {}, "cooldown": {}, "hash_index": {}, "reset_skips": False}
_cfg = None
_lock_sock = None


def _kind(msg):
    for k, v in (("정리 완료", "done"), ("재다운로드", "dup"), ("건너뜀", "skip"), ("포기", "fail"), ("열려 있어", "locked"),
                 ("재시도", "retry"), ("감시 시작", "start"), ("바로가기", "link"), ("설정", "config")):
        if k in msg:
            return v
    return "info"


_today = {"day": "", "done": 0, "dup": 0, "skip": 0, "fail": 0}


def _count_today(kind, t):
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    if _today["day"] != day:
        _today.update(day=day, done=0, dup=0, skip=0, fail=0)
    if kind in _today:
        _today[kind] += 1


def _push(msg, t=None):
    t = t or time.time()
    kind = _kind(msg)
    _state["events"].appendleft({"t": t, "msg": msg, "kind": kind})
    _count_today(kind, t)


_LOG_LINE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)$")


def _backfill_events(n=120):
    """서버를 다시 켜도 패널이 비지 않게, 기록 파일의 마지막 n 줄을 이벤트로 되살린다.
    '오늘' 통계는 화면에 보이는 n 줄이 아니라 오늘 날짜의 모든 줄에서 센다 (다운로드 폴더를 처음 훑을 때 수백 줄이 나오므로)."""
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 1500000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return
    today = time.strftime("%Y-%m-%d")
    parsed = []
    for line in lines:
        m = _LOG_LINE.match(line)
        if not m:
            continue
        try:
            t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        parsed.append((t, m.group(2)))
    for t, msg in parsed[:-n]:
        if msg and time.strftime("%Y-%m-%d", time.localtime(t)) == today:
            _count_today(_kind(msg), t)
    for t, msg in parsed[-n:]:
        _push(msg, t)


def init(load_json=None, save_json=None, archive=None):
    """server.py 가 부른다. 보관소 경로를 서버와 같은 값으로 맞춘다."""
    global DEFAULT_ARCHIVE, _cfg, _forced_archive
    if archive:
        DEFAULT_ARCHIVE = archive
        _forced_archive = archive   # 서버 안에서는 보관소가 곧 검색·읽기 화면의 논문모음이므로 설정 파일로 바꿀 수 없게
    migrate_legacy()
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.isfile(CONFIG_PATH):
        # 설정 파일이 없으면 기본값으로 만들어 둔다 (화면에서 고칠 수 있게)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"감시폴더": os.path.expanduser("~/Downloads"), "논문폴더": DEFAULT_ARCHIVE, "주제폴더": "",
                       "검사주기_초": 3, "제목최대길이": 140, "저널약어": {}}, f, ensure_ascii=False, indent=2)
    _cfg = Config()
    _listeners.append(_push)
    _backfill_events()


def start():
    """감시 스레드 시작 (init 다음에)."""
    threading.Thread(target=_run, name="intake", daemon=True).start()


def _load_skips():
    """건너뛴 파일(논문 아님·포기) 목록을 파일에서 되살린다. 크기·수정시각이 그대로인 것만."""
    try:
        with open(SKIP_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    skipped = set()
    for pth, sig in list(cache.items()):
        try:
            st = os.stat(pth)
            if [int(st.st_size), int(st.st_mtime)] == sig:
                skipped.add(pth)
            else:
                del cache[pth]
        except OSError:
            del cache[pth]
    return skipped, cache


def _save_skips(skipped, cache):
    changed = False
    for pth in list(skipped):
        if pth not in cache:
            try:
                st = os.stat(pth)
                cache[pth] = [int(st.st_size), int(st.st_mtime)]
                changed = True
            except OSError:
                pass
    if changed:
        try:
            tmp = SKIP_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
            os.replace(tmp, SKIP_PATH)
        except OSError:
            pass


def _run():
    global _lock_sock
    cfg = _cfg or Config()
    # 예전 독립 실행 프로그램(또는 서버 두 개)이 같은 폴더를 건드리지 않게 포트 하나를 잠금으로 쥔다
    _lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    while True:
        try:
            _lock_sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
            break
        except OSError:
            _state["waiting_lock"] = True
            time.sleep(15)
    _state["waiting_lock"] = False
    skipped, skip_cache = _load_skips()
    state = _watch
    state["skipped"], state["skip_cache"] = skipped, skip_cache
    try:
        state["hash_index"] = build_hash_index(cfg.target_dir)
    except Exception:
        state["hash_index"] = {}
    _state.update(alive=True, started=time.time())
    log("감시 시작 (Athenaeum 안에서): {}  ->  {}".format(cfg.watch_dir, cfg.target_dir))
    while True:
        try:
            cfg.reload_if_changed()   # 정지 중에도 파일 편집은 바로 반영 (화면의 설정 폼이 옛 값을 보이지 않게)
            if state["reset_skips"]:
                # '건너뛴 파일 다시 검사': 논문 아님으로 판정했던 파일들을 잊고 다음 훑기에서 다시 본다 (새 규칙이 생겼을 때)
                state["reset_skips"] = False
                state["skipped"].clear(); state["skip_cache"].clear(); state["retries"].clear(); state["cooldown"].clear()
                try:
                    if os.path.exists(SKIP_PATH):
                        os.remove(SKIP_PATH)
                except OSError:
                    pass
                log("건너뛴 파일 목록을 지우고 다운로드 폴더를 다시 검사합니다")
            if _state["enabled"]:
                scan_once(cfg, state["skipped"], state["retries"], state["size_history"], state["hash_index"], state["cooldown"])
                scan_tree_once(cfg, state["size_history"], state["hash_index"], state["cooldown"])
                _save_skips(state["skipped"], state["skip_cache"])
                _state["last_scan"] = time.time()
                _state["error"] = ""
                _state["skipped_n"] = len(state["skipped"])
        except Exception as e:
            _state["error"] = str(e)[:200]
        try:
            wait = cfg.interval if _state["enabled"] else 5
        except Exception:
            wait = 3
        _wake.wait(wait)   # '지금 검사'·재개·설정 저장이 set() 하면 바로 깬다
        _wake.clear()


def view():
    """홈 화면 '수집' 패널이 10초마다 가져가는 상태."""
    cfg = _cfg or Config()
    try:
        cfg.reload_if_changed()   # 파일을 메모장으로 고친 직후에도 화면이 새 값을 보이게
    except Exception:
        pass
    ev = list(_state["events"])
    if _today["day"] != time.strftime("%Y-%m-%d"):
        _today.update(day=time.strftime("%Y-%m-%d"), done=0, dup=0, skip=0, fail=0)
    return {"enabled": _state["enabled"], "alive": _state["alive"], "waiting_lock": _state["waiting_lock"],
            "error": _state["error"], "last_scan": _state["last_scan"],
            "watch": cfg.watch_dir, "target": cfg.target_dir, "interval": cfg.interval,
            "config": cfg.public(), "config_path": CONFIG_PATH, "log_path": LOG_PATH,
            "today": {k: _today[k] for k in ("done", "dup", "skip", "fail")},
            "skipped_n": _state.get("skipped_n", 0), "events": ev[:150]}


def control(body):
    """패널의 버튼: pause / resume / scan / open_log / open_watch / open_config / config(설정 저장)."""
    op = body.get("op")
    out_errs = []
    if op == "pause":
        _state["enabled"] = False
        _wake.set()
    elif op == "resume":
        _state["enabled"] = True
        _wake.set()
    elif op == "scan":
        _state["enabled"] = True
        _wake.set()   # 감시 스레드를 바로 깨워 즉시 한 번 돈다
    elif op == "retry_skipped":
        _watch["reset_skips"] = True
        _state["enabled"] = True
        _wake.set()
    elif op == "open_log":
        try:
            os.startfile(LOG_PATH)
        except OSError:
            pass
    elif op == "open_watch":
        try:
            os.startfile((_cfg or Config()).watch_dir)
        except OSError:
            pass
    elif op == "open_config":
        try:
            os.startfile(CONFIG_PATH)
        except OSError:
            pass
    elif op == "config":
        cfg = _cfg or Config()
        changes = body.get("config") or {}
        try:
            out_errs = cfg.update({k: v for k, v in changes.items() if k in Config.EDITABLE})
        except Exception as e:
            out_errs = ["설정을 저장하지 못했습니다: %s" % e]
    v = view()
    v["errors"] = out_errs
    return v


# ====================== 혼자 돌리기 (시험용) ======================

def main():
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        if sys.stdout:
            print("이미 실행 중입니다 (Athenaeum 서버가 감시하고 있음). 이 창은 닫아도 됩니다.")
        return
    init()
    cfg = _cfg
    once = "--once" in sys.argv
    skipped, _cache = _load_skips()
    retries, size_history, cooldown = {}, {}, {}
    hash_index = build_hash_index(cfg.target_dir)
    log("감시 시작 (단독 실행): {}  ->  {}".format(cfg.watch_dir, cfg.target_dir))
    if once:
        scan_once(cfg, skipped, retries, size_history, hash_index, cooldown)  # 크기 기록
        scan_tree_once(cfg, size_history, hash_index, cooldown)
        time.sleep(1)
        scan_once(cfg, skipped, retries, size_history, hash_index, cooldown)  # 안정 확인 후 처리
        scan_tree_once(cfg, size_history, hash_index, cooldown)
        _save_skips(skipped, _cache)
        log("1회 정리 완료.")
        return
    while True:
        cfg.reload_if_changed()
        scan_once(cfg, skipped, retries, size_history, hash_index, cooldown)
        scan_tree_once(cfg, size_history, hash_index, cooldown)
        _save_skips(skipped, _cache)
        time.sleep(cfg.interval)


if __name__ == "__main__":
    main()
