# -*- coding: utf-8 -*-
"""
논문 자동 정리 프로그램 (MAENG Lab)

다운로드 폴더를 감시하다가 새 PDF가 생기면:
  1. PDF 첫 페이지들에서 DOI를 찾고
  2. Crossref API로 제목/저자/연도/저널을 조회한 뒤
  3. "연도_저널약어_저자_제목.pdf" 로 이름을 바꿔 논문폴더로 옮긴다.

DOI를 못 찾은 PDF(논문이 아닌 파일 등)는 건드리지 않는다.
설정은 같은 폴더의 config.json 에서 바꿀 수 있다 (저장하면 자동 반영).
"""

import hashlib
import html
import json
import os
import re
import shutil
import socket
import sys
import time
import unicodedata

import requests
from pypdf import PdfReader

try:
    import win32com.client
except ImportError:
    win32com = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "정리기록.txt")

# 중복 실행 방지용 포트 (이미 실행 중이면 두 번째 실행은 조용히 종료)
SINGLE_INSTANCE_PORT = 47653

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
# 줄바꿈으로 끊긴 DOI도 이어붙여서 찾는 버전
DOI_JOIN_RE = re.compile(r"10\.\d{4,9}/(?:[-._;()/:A-Za-z0-9]|\n(?=[-._;()/:A-Za-z0-9]))+")

# 저널명 약어 자동 생성용 (이니셜 방식: 단어 첫 글자, 예: JMSE)
STOPWORDS = {"of", "the", "and", "in", "on", "for", "a", "an", "de", "la", "&", "amp"}


def log(msg):
    line = "[{}] {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if sys.stdout:  # pythonw(백그라운드)로 실행하면 화면 출력이 없음
        try:
            print(line, flush=True)
        except OSError:
            pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


class Config:
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
        return self.data.get("감시폴더", os.path.expanduser("~/Downloads"))

    @property
    def target_dir(self):
        return self.data.get("논문폴더", os.path.join(BASE_DIR, "논문"))

    @property
    def interval(self):
        return max(1, int(self.data.get("검사주기_초", 3)))

    @property
    def title_max(self):
        return max(20, int(self.data.get("제목최대길이", 80)))

    @property
    def journal_map(self):
        return {
            normalize_journal(k): v
            for k, v in self.data.get("저널약어", {}).items()
        }

    @property
    def tree_dir(self):
        return self.data.get("주제폴더", "")


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
        headers={"User-Agent": "paper-organizer/1.0"},
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
        headers={"User-Agent": "paper-organizer/1.0"},
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
    "–": "-", "—": "-", "‒": "-", "‑": "-", "−": "-",
    "'": "'", "'": "'", "‚": "'", "ʼ": "'", "′": "'",
    """: "'", """: "'", "„": "'", "…": "...", "·": "-",
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


def resolve_meta(pdf_path):
    """PDF에서 DOI를 찾아 서지정보를 얻는다. 실패하면 None."""
    candidates, page_text = extract_doi_candidates(pdf_path)
    if not candidates:
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
        # DOI 경로가 다 실패했을 때만 파일명 검색 시도 (DOI가 있는 문서 = 논문일 확률 높음)
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
    new_path = unique_path(cfg.target_dir, build_filename(meta, cfg))
    shutil.move(pdf_path, new_path)
    log("정리 완료: {}  ->  {}".format(name, os.path.basename(new_path)))
    return new_path


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


def main():
    # 이미 실행 중인지 확인 (중복 실행이면 파일을 서로 옮기려다 꼬이므로 종료)
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        if sys.stdout:
            print("이미 실행 중입니다. 이 창은 닫아도 됩니다.")
        return

    cfg = Config()
    once = "--once" in sys.argv
    skipped = set()      # DOI 없음 등으로 이번 실행에서 건너뛴 파일
    retries = {}         # 일시적 오류(인터넷 등) 재시도 횟수
    size_history = {}    # 다운로드 완료 판정용 파일 크기 기록
    hash_index = build_hash_index(cfg.target_dir)  # 보관소 내용 지문 (중복 판별용)
    cooldown = {}        # 열려 있는 파일 등 - 다음 시도 시각

    log("감시 시작: {}  ->  {}".format(cfg.watch_dir, cfg.target_dir))
    if once:
        scan_once(cfg, skipped, retries, size_history, hash_index, cooldown)  # 크기 기록
        scan_tree_once(cfg, size_history, hash_index, cooldown)
        time.sleep(1)
        scan_once(cfg, skipped, retries, size_history, hash_index, cooldown)  # 안정 확인 후 처리
        scan_tree_once(cfg, size_history, hash_index, cooldown)
        log("1회 정리 완료.")
        return

    while True:
        cfg.reload_if_changed()
        scan_once(cfg, skipped, retries, size_history, hash_index, cooldown)
        scan_tree_once(cfg, size_history, hash_index, cooldown)
        time.sleep(cfg.interval)


if __name__ == "__main__":
    main()
