# -*- coding: utf-8 -*-
"""
Athenaeum 포터블 배포판 만들기 (파이썬이 없는 컴퓨터용)

python tools\\make_release.py            → dist\\Athenaeum-portable-YYYYMMDD.zip
  1) python.org 의 embeddable 파이썬(설치 불필요)을 받아 dist\\Athenaeum\\python\\ 에 풀고
  2) get-pip 으로 pip 을 넣은 뒤 paper-search\\requirements.txt 를 그 파이썬에 설치
  3) 프로그램 파일(paper-search, README, 사용법)을 복사 — 개인 데이터(tags.json 라벨 데이터·본문 색인)는 비움
  4) 맨 위에 'Athenaeum 실행.bat' 을 두고 ZIP 으로 묶는다
받는 사람: Claude Code 설치·로그인 → ZIP 풀기 → 'Athenaeum 실행.bat' 더블클릭. 파이썬·pip·PATH 필요 없음.
"""
import io, os, sys, json, shutil, zipfile, subprocess, time, urllib.request

PY_VER = "3.11.9"
EMBED_URL = "https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip" % (PY_VER, PY_VER)
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "paper-search")
DIST = os.path.join(ROOT, "dist"); CACHE = os.path.join(ROOT, "tools", "_cache")
OUT = os.path.join(DIST, "Athenaeum")
EXCLUDE_DIRS = {"__pycache__", "_cache"}
EXCLUDE_FILES = {"text_index.json", "맵생성기록.txt", "proto_turning_zones.svg", "proto_turning_zones.png"}


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def fetch(url, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        log("cached " + os.path.basename(dst)); return dst
    log("download " + url)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r, open(dst + ".part", "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(dst + ".part", dst)
    log("  %.1f MB" % (os.path.getsize(dst) / 1e6)); return dst


def run(cmd, **kw):
    log("run " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(r.stdout[-3000:]); print(r.stderr[-3000:]); raise SystemExit("failed: " + cmd[0])
    return r


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT); os.makedirs(CACHE, exist_ok=True)

    # 1) embeddable python
    pyzip = fetch(EMBED_URL, os.path.join(CACHE, os.path.basename(EMBED_URL)))
    pydir = os.path.join(OUT, "python")
    with zipfile.ZipFile(pyzip) as z:
        z.extractall(pydir)
    pth = [f for f in os.listdir(pydir) if f.endswith("._pth")][0]
    p = os.path.join(pydir, pth); s = io.open(p, encoding="utf-8").read().replace("#import site", "import site")
    if "Lib\\site-packages" not in s:
        s = s.replace("import site", "Lib\\site-packages\nimport site")
    io.open(p, "w", encoding="utf-8").write(s)
    pyexe = os.path.join(pydir, "python.exe")

    # 2) pip + packages
    getpip = fetch(GETPIP_URL, os.path.join(CACHE, "get-pip.py"))
    run([pyexe, getpip, "--no-warn-script-location", "-q"])
    run([pyexe, "-m", "pip", "install", "-q", "--no-warn-script-location", "-r", os.path.join(SRC, "requirements.txt")])
    # 덩치 줄이기: numpy·scipy 의 tests 폴더 (동작에 필요 없음)
    sp = os.path.join(pydir, "Lib", "site-packages")
    for pkg in ("numpy", "scipy"):
        for dirpath, dirs, files in os.walk(os.path.join(sp, pkg)):
            for d in list(dirs):
                if d == "tests":
                    shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True); dirs.remove(d)
    r = run([pyexe, "-c", "import pymupdf, pypdf, requests, win32com.client, numpy, scipy, fontTools; print(pymupdf.__doc__.split()[1], pypdf.__version__, numpy.__version__, scipy.__version__)"])
    log("packages ok: " + r.stdout.strip())

    # 3) 프로그램 파일
    dst_src = os.path.join(OUT, "paper-search")
    def ignore(d, names):
        return [n for n in names if n in EXCLUDE_DIRS or n in EXCLUDE_FILES or n.endswith(".pyc")]
    shutil.copytree(SRC, dst_src, ignore=ignore)
    io.open(os.path.join(dst_src, "tags.json"), "w", encoding="utf-8").write("{}\n")          # 라벨 데이터는 받는 사람의 것으로 새로
    lp = os.path.join(dst_src, "labels.json"); g = json.load(io.open(lp, encoding="utf-8"))
    if isinstance(g.get("_체계"), dict):
        g["_체계"]["새라벨"] = {}
    io.open(lp, "w", encoding="utf-8").write(json.dumps(g, ensure_ascii=False, indent=1))
    cfgp = os.path.join(dst_src, "수집설정.json")
    if os.path.exists(cfgp):   # 내 컴퓨터 경로 대신 기본값 (감시 폴더는 첫 실행 때 그 사용자의 다운로드 폴더로 잡힘)
        c = json.load(io.open(cfgp, encoding="utf-8"))
        c["감시폴더"] = ""; c["논문폴더"] = ""; c["주제폴더"] = ""
        io.open(cfgp, "w", encoding="utf-8").write(json.dumps(c, ensure_ascii=False, indent=2))
    for f in ("README.md", "Athenaeum 사용법.md", "LICENSE", "LICENSE.md", "LICENSE.txt"):
        if os.path.exists(os.path.join(ROOT, f)):
            shutil.copy2(os.path.join(ROOT, f), os.path.join(OUT, f))
    # 4) 맨 위 실행 파일. 배치 파일은 시스템 코드페이지로 읽히므로 내용은 ASCII 만 (한글 파일명을 안에 적으면 다른 컴퓨터에서 못 찾음).
    #    안쪽 bat 을 ASCII 이름 run.bat 으로도 복사해 두고 그것을 부른다.
    shutil.copy2(os.path.join(dst_src, "Athenaeum_실행.bat"), os.path.join(dst_src, "run.bat"))
    with open(os.path.join(OUT, "Athenaeum 실행.bat"), "wb") as f:
        f.write(b'@echo off\r\nrem Athenaeum launcher (portable). Calls paper-search\\run.bat (ASCII name so any code page works).\r\ncall "%~dp0paper-search\\run.bat"\r\n')
    io.open(os.path.join(OUT, "처음 읽어 주세요.txt"), "w", encoding="utf-8-sig").write(
        "Athenaeum 포터블판\n\n"
        "1. Claude Code 를 설치하고 터미널에서  claude  →  /login  으로 한 번 로그인하세요 (요약·번역·검토가 이 로그인을 씁니다).\n"
        "   https://claude.com/claude-code\n"
        "2. 'Athenaeum 실행.bat' 을 더블클릭하세요. 브라우저에 화면이 뜹니다. 파이썬은 이 폴더 안에 들어 있어 따로 설치할 것이 없습니다.\n"
        "3. 홈 위쪽 '수집' 패널에서 감시 폴더(기본: 내 다운로드 폴더)를 확인하고, '라벨 체계 설정 ↗' 에서 내 분야 체계를 Claude 에게 제안받아 적용하세요.\n"
        "4. 논문 PDF 는 이 폴더 안 '논문모음' 에 모이고, 요약·번역·메모·원고도 이 폴더 안에 저장됩니다. 폴더를 통째로 옮겨도 됩니다.\n\n"
        "부팅 때 자동 시작: paper-search\\Athenaeum_시작.vbs 의 바로가기를 시작프로그램 폴더(Win+R → shell:startup)에 넣으세요.\n"
        "자세한 사용법: Athenaeum 사용법.md\n")

    # 5) ZIP
    os.makedirs(DIST, exist_ok=True)
    zpath = os.path.join(DIST, "Athenaeum-portable-%s.zip" % time.strftime("%Y%m%d"))
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirs, files in os.walk(OUT):
            for f in files:
                full = os.path.join(dirpath, f)
                z.write(full, os.path.relpath(full, DIST))
    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(OUT) for f in fs)
    log("done: %s  (folder %.0f MB, zip %.0f MB)" % (zpath, total / 1e6, os.path.getsize(zpath) / 1e6))


if __name__ == "__main__":
    main()
