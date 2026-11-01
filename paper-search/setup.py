# -*- coding: utf-8 -*-
"""
Athenaeum 설치 도우미.

  포터블판:  맨 위 'Athenaeum 설치.bat'  →  paper-search\\setup.bat  →  동봉 python 으로 이 파일 실행
  저장소판:  python paper-search\\setup.py

기본은 **브라우저 설치 창**(2026-11-01): 이 파일이 127.0.0.1 의 빈 포트에 작은 서버를 띄워 install.html(Athenaeum 과 같은 디자인)을 앱 창으로 열고,
안내 → 점검(기존 설치 업데이트/정리/따로) → Claude Code(설치 창·로그인 창을 열어 주고, 끝나면 저절로 알아챔) → 바로가기·자동 시작 → 설치(진행 막대) → 끝(바로 열기)
차례로 간다. 창을 닫으면 도우미도 끝난다. 글자 창(옛 방식)으로 하려면  --console.

하는 일
  1) 기능 안내
  2) 파이썬 패키지 확인
  3) 기존 설치 찾기 (바탕화면·시작프로그램 바로가기, 돌고 있는 서버, 설치 기록) → 업데이트 / 정리 / 그대로
     업데이트 = 기존 폴더 안의 프로그램 파일(paper-search·python·맨 위 bat)만 새것으로 바꾸고 데이터는 그대로 둔다.
       새로 푼 이 폴더는 그 뒤 지워도 된다. 기존 폴더가 git 저장소(.git)면 개발용으로 보고 건드리지 않는다.
  4) Claude Code 설치·로그인 확인 (없으면 설치 창을 열어 주고, 끝날 때까지 기다림)
  5) 바탕화면에 'Athenaeum' 바로가기 (앱처럼 아이콘 포함)
  6) (선택) 부팅 때 자동 시작 — 시작프로그램 폴더에 Athenaeum_시작.vbs 바로가기
  7) (선택) 바로 실행

옵션
  --console        글자 창에서 묻고 답하는 옛 방식
  --test <폴더>    시험: 바로가기·설치 기록을 바탕화면·시작프로그램 대신 그 폴더에 두고, 기존 설치도 그 폴더의 바로가기에서만 찾고,
                   창(설치·로그인·실행)은 열지 않는다. --web 이 없으면 글자 창으로 돈다
  --web            --test 와 함께: 브라우저 창 방식으로 시험
  --no-browser     서버만 띄우고 브라우저는 열지 않는다 (주소를 찍어 준다)
  --port N         서버 포트 (기본은 빈 포트)
사용자 데이터(논문·번역·메모·원고·단어장·라벨·수집설정)는 어떤 경우에도 지우거나 옮기지 않는다. 지우려면 uninstall.bat.
"""
import io, os, sys, json, shutil, subprocess, threading, time, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))          # (새로 푼) paper-search
ROOT = os.path.dirname(HERE)                               # 포터블판이면 Athenaeum\, 저장소면 저장소 루트
NO_WINDOW = 0x08000000
NEW_CONSOLE = 0x00000010
# 기존 설치를 업데이트할 때 건드리지 않는 파일 (사용자 데이터·설정)
DATA_FILES = {"tags.json", "labels.json", "수집설정.json", "text_index.json", "맵생성기록.txt"}

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TEST_DIR = None
if "--test" in sys.argv:
    TEST_DIR = os.path.abspath(sys.argv[sys.argv.index("--test") + 1])
    os.makedirs(TEST_DIR, exist_ok=True)

# 설치(바로가기·실행) 대상 폴더. 업데이트를 고르면 기존 폴더로 바뀐다.
target = {"root": ROOT}
WEB_LOG = []   # 브라우저 창의 '설치' 단계에 보이는 진행 글 — say() 가 쌓는다


def read_version(root):
    try:
        return io.open(os.path.join(root, "paper-search", "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        return ""


VERSION = read_version(ROOT) or "?"


def say(s=""):
    print(s, flush=True)
    WEB_LOG.append(str(s))


def ask(q, default="y"):
    """y/n 질문. Enter 면 default. 입력이 끊긴(파일로 넘긴) 경우도 default."""
    hint = "[Y/n]" if default == "y" else "[y/N]"
    try:
        a = input("%s %s " % (q, hint)).strip().lower()
    except EOFError:
        a = ""
    if not a:
        return default == "y"
    return a[0] == "y"


def choose(q, options, default=1):
    """번호 선택. options: [(번호, 설명)]. Enter 면 default."""
    for n, desc in options:
        say("      %d) %s" % (n, desc))
    try:
        a = input("%s [%d] " % (q, default)).strip()
    except EOFError:
        a = ""
    try:
        v = int(a) if a else default
    except ValueError:
        v = default
    return v if v in [n for n, _ in options] else default


def wait_enter(msg):
    try:
        input(msg)
    except EOFError:
        pass


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ---------- 1) 기능 안내 ----------
def intro():
    say("=" * 64)
    say("  Athenaeum 설치 도우미  (이 판: %s)" % VERSION)
    say("=" * 64)
    say("연구실 논문 작업대입니다. 이 컴퓨터 안에서만 돌고, 논문·요약·번역·원고는 전부 이 폴더 안에 저장됩니다.")
    say("  폴더: " + ROOT)
    say()
    say("무엇을 하나")
    say("  수집   다운로드 폴더를 지켜보다 새 논문 PDF 를 '연도_저널_저자_제목.pdf' 로 정리해 보관")
    say("  검색   라벨(재료·공정·현상·방법…) 교집합 검색, 본문 검색, 라벨 자동 부여")
    say("  탐색   OpenAlex 로 세상의 논문 찾기, 관련 논문 맵, 결과를 놓고 Claude 와 대화")
    say("  읽기   요약 · 문장 단위로 원문과 맞춘 전문 번역 · 드래그하면 PDF 원문 표시 · 메모·질문 · 단어장")
    say("  공부   주제를 적으면 내 서재의 논문만으로 교과서 한 장 — 문장마다 원문 근거, 그림·수식, 드래그해 묻기, 마인드맵, 발표 자료(.pptx)")
    say("  원고   근거 카드 → 개요 → 초안 → 검토 → 영문 → Word 파일. 심사 의견 처리, 글쓰기 지능 Stylus(문단·결론·초록), 논증 모형·절 계획, 교수님 주석 논의, 도식 만들기")
    say("  연구 노트  연구 주제를 놓고 Claude 와 토의 — 서재 전체를 알고 공저자처럼. 설계서·반론·서론 구조·연구의 틀·큰 물음")
    say("  학습   서재의 논문을 읽어 '이 저널의 이 절은 이렇게 쓴다' 바탕을 세움 — 원고·연구 노트의 글에 쓰임 (설정에서 켜고 끔)")
    say()
    say("라벨 체계는 비어 있는 채로 시작합니다. 논문이 10편쯤 모이면 홈 화면이 'Claude 에게 체계 설계 맡기기' 를 권하고,")
    say("그 뒤로는 새 논문이 자동 분류되며, 논문이 많이 늘면 다시 분류하라고 알려 줍니다.")
    say("요약·번역·공부·원고의 Claude 호출은 Claude Code 로 합니다 (내 Claude 구독 사용량을 씀, API 키 없음).")
    say("이 도우미는 파일을 지우지 않습니다. 지울 때는 'Athenaeum 삭제.bat' 을 쓰세요.")
    say()


# ---------- 2) 파이썬 패키지 ----------
def python_missing():
    missing = []
    for mod in ("pymupdf", "pypdf", "requests", "win32com.client", "numpy", "scipy"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    return missing


def check_python():
    say("[1/6] 파이썬 %s.%s — %s" % (sys.version_info[0], sys.version_info[1], sys.executable))
    missing = python_missing()
    if missing:
        say("      빠진 패키지: " + ", ".join(missing))
        say("      pip install -r paper-search\\requirements.txt  로 설치하세요 (포터블판이면 python 폴더가 손상된 것)")
        return False
    say("      패키지 모두 있음")
    return True


# ---------- 3) 기존 설치 ----------
def special_folder(name):
    if TEST_DIR:
        return TEST_DIR
    import win32com.client
    return win32com.client.Dispatch("WScript.Shell").SpecialFolders(name)


def marker_path():
    if TEST_DIR:
        return os.path.join(TEST_DIR, "install.json")
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Athenaeum", "install.json")


def root_of(path):
    """경로(bat·vbs·server.py 등)에서 Athenaeum 폴더(paper-search\\server.py 가 있는 곳)를 찾는다."""
    if not path:
        return None
    d = path if os.path.isdir(path) else os.path.dirname(path)
    for _ in range(4):
        if os.path.isfile(os.path.join(d, "paper-search", "server.py")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return None


def shortcut_links():
    """바탕화면·시작프로그램의 .lnk 들: [(경로, 대상+인수)]"""
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    out = []
    dirs = [TEST_DIR] if TEST_DIR else [sh.SpecialFolders("Desktop"), sh.SpecialFolders("Startup")]
    for d in dirs:
        try:
            names = os.listdir(d)
        except Exception:
            continue
        for f in names:
            if f.lower().endswith(".lnk"):
                try:
                    s = sh.CreateShortcut(os.path.join(d, f))
                    out.append((os.path.join(d, f), (s.TargetPath or "") + " " + (s.Arguments or "")))
                except Exception:
                    pass
    return out


def running_server_paths():
    """돌고 있는 Athenaeum 서버(server.py)의 경로들 — 프로세스 명령줄에서."""
    ps = "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains('server.py') } | ForEach-Object { $_.CommandLine }"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60, creationflags=NO_WINDOW)
        lines = r.stdout.decode("utf-8", "replace").splitlines()
    except Exception:
        return []
    out = []
    for line in lines:
        i = line.lower().find("server.py")
        if i < 0:
            continue
        seg = line[:i + len("server.py")]
        seg = seg[seg.rfind('"') + 1:] if '"' in seg else seg.split()[-1]
        out.append(seg.strip())
    return out


def find_existing_installs():
    """현재 폴더가 아닌 다른 Athenaeum 설치 폴더들 (중복 제거). git 저장소(개발용)는 제외."""
    cands = []
    for _, tgt in shortcut_links():
        # 대상 경로에 공백이 있을 수 있어(Athenaeum 실행.bat) 전체 문자열과 첫 토큰 둘 다로 찾아본다
        r = root_of(tgt.strip().strip('"')) or root_of(tgt.split(" ")[0].strip('"'))
        if r:
            cands.append(r)
    for p in running_server_paths():
        r = root_of(p)
        if r:
            cands.append(r)
    try:
        m = json.load(io.open(marker_path(), encoding="utf-8"))
        r = root_of(os.path.join(m.get("root", ""), "paper-search", "server.py"))
        if r:
            cands.append(r)
    except Exception:
        pass
    out = []
    for r in cands:
        if same_path(r, ROOT) or any(same_path(r, x) for x in out):
            continue
        if os.path.isdir(os.path.join(r, ".git")):
            continue
        out.append(r)
    return out


def paper_count(root):
    try:
        return sum(1 for f in os.listdir(os.path.join(root, "논문모음")) if f.lower().endswith(".pdf"))
    except Exception:
        return 0


def stop_server_at(root):
    """그 폴더의 server.py 를 돌리는 프로세스를 끈다."""
    key = os.path.join(root, "paper-search", "server.py").lower().replace("'", "''")
    ps = ("$k='%s'; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($k) } "
          "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'stopped ' + $_.ProcessId }") % key
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60, creationflags=NO_WINDOW)
        n = r.stdout.decode("utf-8", "replace").count("stopped")
    except Exception:
        n = 0
    if n:
        say("      서버 프로세스 %d개 종료" % n)
        time.sleep(2)
    return n


def remove_shortcuts_pointing(root):
    key = os.path.normcase(os.path.abspath(root))
    n = 0
    for lnk, tgt in shortcut_links():
        if key in os.path.normcase(tgt):
            try:
                os.remove(lnk); n += 1
                say("      바로가기 제거: " + lnk)
            except Exception as e:
                say("      바로가기 제거 실패: %s (%s)" % (lnk, e))
    return n


def copy_tree_program(src, dst):
    """프로그램 파일만 복사 (사용자 데이터 파일·캐시 제외). 덮어쓴 파일 수."""
    n = 0
    for dp, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = os.path.relpath(dp, src)
        od = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(od, exist_ok=True)
        for f in files:
            if f.endswith(".pyc") or (rel == "." and f in DATA_FILES):
                continue
            shutil.copy2(os.path.join(dp, f), os.path.join(od, f)); n += 1
    return n


def copy_tree_progress(src, dst, prog=None, lo=0, hi=100):
    """폴더를 통째로 복사하며 진행(lo~hi %)을 prog(pct) 로 알린다 — python 폴더(약 300 MB, 파일 수천 개)의 진행 막대용."""
    files = [(dp, f) for dp, _, fs in os.walk(src) for f in fs]
    n = len(files) or 1
    for i, (dp, f) in enumerate(files):
        rel = os.path.relpath(dp, src)
        od = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(od, exist_ok=True)
        shutil.copy2(os.path.join(dp, f), os.path.join(od, f))
        if prog and i % 40 == 0:
            prog(lo + (hi - lo) * i / n)
    if prog:
        prog(hi)
    return n


def update_in_place(old, prog=None):
    """기존 폴더의 프로그램 파일만 새것으로. 데이터(논문모음·번역·메모·원고·단어장·라벨·설정)는 그대로. prog(pct) 가 있으면 진행을 알린다."""
    prog = prog or (lambda pct: None)
    say("      기존 폴더를 %s → %s 로 업데이트합니다: %s" % (read_version(old) or "0.9.1 이하", VERSION, old))
    stop_server_at(old)
    try:   # 다른 경로·방법으로 켜진 옛 서버가 포트를 잡고 있으면 그것도 끈다 (안 그러면 새 판을 켜도 옛 서버에 붙어 '변한 게 없어' 보인다)
        sys.path.insert(0, HERE)
        import launch_check
        if launch_check.port_open(8770):
            launch_check.stop_port(8770, say)
    except Exception as e:
        say("      (포트 점검 건너뜀: %s)" % e)
    prog(8)
    n = copy_tree_program(HERE, os.path.join(old, "paper-search"))
    say("      paper-search 프로그램 파일 %d개 교체 (tags·labels·수집설정 등 데이터 파일은 그대로)" % n)
    prog(16)
    newpy = os.path.join(ROOT, "python")
    if os.path.isdir(newpy) and not same_path(newpy, os.path.join(old, "python")):
        oldpy = os.path.join(old, "python"); tmp = oldpy + ".old"
        if os.path.isdir(oldpy):
            if os.path.isdir(tmp):
                shutil.rmtree(tmp, ignore_errors=True)
            try:
                os.rename(oldpy, tmp)
            except OSError as e:
                say("      python 폴더가 사용 중이라 바꾸지 못했습니다 (%s). 열린 창·서버를 닫고 다시 실행하세요." % e)
                return False
        say("      python 폴더 복사 중 (약 300 MB)…")
        copy_tree_progress(newpy, oldpy, prog, 16, 88)
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.isdir(tmp):
            say("      옛 python 폴더(%s)를 다 지우지 못했습니다. 나중에 직접 지워도 됩니다." % tmp)
    for f in os.listdir(ROOT):   # 맨 위의 bat·txt·md
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(old, f))
    prog(90)
    say("      업데이트 끝. 이제부터 바로가기와 실행은 기존 폴더를 씁니다. 새로 푼 이 폴더(%s)는 지워도 됩니다." % ROOT)
    say("      Athenaeum 앱 창이 열려 있으면 F5 로 새로 고치세요. 어느 판인지는 홈의 ⚙ 환경설정 머리에 적혀 있습니다.")
    return True


EXISTING_OPTIONS = [
    (1, "업데이트 — 기존 폴더의 프로그램만 새것으로 바꾸고 논문·번역·원고·라벨은 그대로 (권장)"),
    (2, "정리 — 기존 서버를 끄고 그쪽 바로가기를 지움. 폴더·데이터는 남기니 직접 지우세요. 여기에 새로 설치"),
    (3, "그대로 두고 여기에 따로 설치 (바탕화면 바로가기는 이쪽으로 바뀜)")]


def apply_existing_choice(old, c, prog=None):
    """기존 설치 하나에 대한 선택(1 업데이트 · 2 정리 · 3 그대로)을 실행한다. 글자 창·브라우저 창이 같이 쓴다. 업데이트가 안 됐으면 False."""
    if c == 1:
        if not update_in_place(old, prog):
            return False
        remove_shortcuts_pointing(old)   # 새 바로가기를 다시 만든다
        target["root"] = old
    elif c == 2:
        stop_server_at(old)
        remove_shortcuts_pointing(old)
        say("      기존 폴더는 직접 지우세요 (논문·번역·원고가 들어 있으니 필요하면 먼저 옮기세요): " + old)
    else:
        say("      그대로 둠")
    return True


def handle_existing():
    say("[2/6] 기존 설치 확인")
    olds = find_existing_installs()
    if not olds:
        say("      다른 설치 없음")
        return
    for old in olds:
        say("      발견: %s  (버전 %s, 논문 %d편)" % (old, read_version(old) or "0.9.1 이하", paper_count(old)))
        c = choose("      어떻게 할까요?", EXISTING_OPTIONS, 1)
        apply_existing_choice(old, c)


# ---------- 4) Claude Code ----------
def find_claude():
    names = ("claude", "claude.cmd", "claude.exe")
    for c in names:
        p = shutil.which(c)
        if p:
            return p
    home = os.path.expanduser("~")
    known = [os.path.join(home, ".local", "bin"),
             os.path.join(os.environ.get("APPDATA", ""), "npm"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "claude")]
    path = os.pathsep.join(d for d in known if d and os.path.isdir(d))
    # 레지스트리의 최신 PATH (이 창을 연 뒤 설치한 경우)
    try:
        import winreg
        for root, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    path += os.pathsep + os.path.expandvars(winreg.QueryValueEx(k, "Path")[0])
            except OSError:
                pass
    except Exception:
        pass
    for c in names:
        p = shutil.which(c, path=path)
        if p:
            return p
    return None


def claude_version(exe):
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, timeout=30, creationflags=NO_WINDOW)
        return (r.stdout or r.stderr).decode("utf-8", "replace").strip().splitlines()[0][:60]
    except Exception:
        return ""


def logged_in():
    """로그인 흔적 (Claude Code 가 저장하는 자격 파일). 내용은 읽지 않는다."""
    home = os.path.expanduser("~")
    return os.path.isfile(os.path.join(home, ".claude", ".credentials.json"))


CLAUDE_INSTALL_CMD = "irm https://claude.ai/install.ps1 | iex"


def open_claude_install():
    """Claude Code 설치 명령을 PowerShell 창에 연다 (글자 창·브라우저 창·서버의 「처음 설정」 이 같은 한 줄)."""
    subprocess.Popen(["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", CLAUDE_INSTALL_CMD], creationflags=NEW_CONSOLE)


def open_claude_login(exe):
    """claude 를 보이는 창에 연다 — 사용자가 /login (내용은 ASCII: cmd 창은 시스템 코드페이지)."""
    subprocess.Popen(["cmd", "/k", "echo Login: type /login below, approve in the browser, then type /exit and close this window. && \"%s\"" % exe],
                     creationflags=NEW_CONSOLE)


def check_claude():
    say("[3/6] Claude Code")
    exe = find_claude()
    while not exe:
        say("      설치되어 있지 않습니다. 요약·번역·라벨 분류에 꼭 필요합니다.")
        say("      설치 명령 (PowerShell):   " + CLAUDE_INSTALL_CMD)
        say("      안내 페이지:              https://claude.com/claude-code")
        if TEST_DIR:
            say("      (시험 모드라 설치 창은 열지 않음)")
            return False
        if ask("      지금 PowerShell 창을 열어 설치를 시작할까요?"):
            open_claude_install()
            wait_enter("      설치 창이 '완료' 라고 하면 여기로 돌아와 Enter 를 누르세요… ")
            exe = find_claude()
            if not exe:
                say("      아직 못 찾았습니다. 설치 창의 메시지를 확인하세요.")
                if not ask("      다시 확인할까요?"):
                    return False
        else:
            say("      나중에 설치해도 됩니다. 홈 화면에 안내가 뜨고, 설치가 끝나면 저절로 사라집니다.")
            return False
    say("      찾음: %s  %s" % (exe, claude_version(exe)))
    if logged_in():
        say("      로그인 흔적 있음 (~\\.claude\\.credentials.json)")
        return True
    say("      로그인 흔적이 없습니다. 터미널에서  claude  를 실행하고  /login  으로 브라우저 로그인을 한 번 하면 됩니다.")
    if TEST_DIR:
        return False
    if ask("      지금 터미널 창을 열어 드릴까요?"):
        open_claude_login(exe)
        wait_enter("      로그인이 끝나면 여기로 돌아와 Enter 를 누르세요… ")
        if logged_in():
            say("      로그인 확인")
            return True
        say("      로그인 흔적을 아직 못 찾았습니다. 요약을 처음 만들 때 로그인 안내가 뜨면 그때 하셔도 됩니다.")
    return False


# ---------- 5) 6) 바로가기 ----------
def make_shortcut(lnk_path, tgt, workdir, icon=None, args=""):
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    s = sh.CreateShortcut(lnk_path)
    s.TargetPath = tgt
    s.Arguments = args
    s.WorkingDirectory = workdir
    if icon and os.path.isfile(icon):
        s.IconLocation = icon + ",0"
    s.WindowStyle = 1
    s.Save()


def icon_path():
    return os.path.join(target["root"], "paper-search", "logo", "athenaeum.ico")


def launcher_bat():
    top = os.path.join(target["root"], "Athenaeum 실행.bat")            # 포터블판
    return top if os.path.isfile(top) else os.path.join(target["root"], "paper-search", "Athenaeum_실행.bat")


def desktop_shortcut():
    say("[4/6] 바탕화면 바로가기")
    lnk = os.path.join(special_folder("Desktop"), "Athenaeum.lnk")
    bat = launcher_bat()
    make_shortcut(lnk, bat, os.path.dirname(bat), icon_path())
    say("      만듦: %s  →  %s" % (lnk, bat))
    say("      (더블클릭하면 서버가 켜지고 앱 창이 뜹니다. 이미 켜져 있으면 창만 엽니다)")


def autostart_set(on):
    """부팅 때 자동 시작 바로가기를 만들거나(on) 뺀다."""
    vbs = os.path.join(target["root"], "paper-search", "Athenaeum_시작.vbs")
    lnk = os.path.join(special_folder("Startup"), "Athenaeum 자동시작.lnk")
    if on:
        make_shortcut(lnk, vbs, os.path.dirname(vbs), icon_path())
        say("      만듦: " + lnk)
    else:
        if os.path.isfile(lnk):
            os.remove(lnk)
            say("      기존 자동 시작 바로가기를 뺐습니다")
        say("      자동 시작 건너뜀 (나중에 다시 이 도우미를 실행하면 됩니다)")


def autostart_shortcut():
    say("[5/6] 부팅 때 자동 시작")
    autostart_set(ask("      컴퓨터를 켤 때 서버를 조용히 미리 켜 둘까요? (수집이 바로 돌고, 바로가기를 누르면 즉시 뜸)"))


def write_marker():
    try:
        p = marker_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8").write(json.dumps(
            {"root": target["root"], "version": read_version(target["root"]) or VERSION, "time": time.strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False))
    except Exception:
        pass


# ---------- 7) 실행 ----------
def launch():
    say("[6/6] 실행")
    if TEST_DIR:
        say("      (시험 모드라 실행하지 않음)")
        return
    if ask("      지금 Athenaeum 을 열까요?"):
        os.startfile(launcher_bat())
        say("      잠시 뒤 브라우저에 화면이 뜹니다. 홈 위쪽 '수집' 패널에서 감시 폴더를 확인하세요.")


def main():
    """글자 창 방식 (--console, 또는 --test 시험)."""
    intro()
    ok_py = check_python()
    handle_existing()
    ok_claude = check_claude()
    desktop_shortcut()
    autostart_shortcut()
    write_marker()
    say()
    say("정리")
    say("  설치 폴더      " + target["root"] + ("" if same_path(target["root"], ROOT) else "   (업데이트됨 — 새로 푼 폴더는 지워도 됨)"))
    say("  파이썬 패키지  " + ("OK" if ok_py else "확인 필요"))
    say("  Claude Code    " + ("OK (설치·로그인)" if ok_claude else "설치 또는 로그인 필요 — 홈 화면 안내를 따르세요"))
    say("  바로가기       바탕화면 Athenaeum")
    say("  지우기         'Athenaeum 삭제.bat' 실행 후 폴더 삭제 (논문·번역·원고는 그대로 남음)")
    say()
    launch()
    if not TEST_DIR:
        wait_enter("Enter 를 누르면 닫힙니다. ")


# ---------- 브라우저 설치 창 (기본) ----------
# install.html 이 127.0.0.1 의 이 서버에서 안내 → 점검 → Claude Code → 바로가기 → 설치 → 끝 으로 간다. 글자 창의 함수들을 그대로 부르고(say 가 진행 글을 WEB_LOG 에 쌓는다),
# 묻는 대신 화면이 고른 것(기존 설치마다 1·2·3, 자동 시작)을 /api/install 로 받아 스레드에서 돌린다. 창은 1~10초마다 /api/state 를 묻고, 닫히면 /api/bye 를 보내 도우미가 끝난다.
WEB = {"existing": None, "prog": {"running": False, "done": False, "ok": None, "error": "", "pct": 0, "target": ""},
       "last": time.time(), "seen": False, "stop": False, "bye": 0.0}
_CLAUDE_VER = {}


def _com_init():
    """스레드에서 win32com(바로가기 만들기·읽기)을 쓰기 전에 — pythoncom 은 처음 import 한 스레드만 COM 을 초기화하므로 다른 스레드는 'CoInitialize가 호출되지 않았습니다' 로 죽는다."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass


def web_scan_existing():
    _com_init()
    try:
        WEB["existing"] = [{"root": r, "version": read_version(r) or "0.9.1 이하", "papers": paper_count(r)} for r in find_existing_installs()]
    except Exception as e:
        WEB["existing"] = []
        say("      기존 설치 찾기 실패: %s" % e)


def web_state():
    exe = find_claude()
    if exe and exe not in _CLAUDE_VER:
        _CLAUDE_VER[exe] = claude_version(exe)
    missing = python_missing()
    return {"version": VERSION, "root": ROOT, "test": bool(TEST_DIR), "test_dir": TEST_DIR or "",
            "python": {"ok": not missing, "missing": missing, "exe": sys.executable, "ver": "파이썬 %s.%s" % (sys.version_info[0], sys.version_info[1])},
            "existing": WEB["existing"],
            "claude": {"found": bool(exe), "path": exe or "", "version": _CLAUDE_VER.get(exe, "") if exe else "", "logged_in": logged_in()},
            "progress": WEB["prog"], "log": WEB_LOG[-300:]}


def web_install(opts):
    """화면이 고른 대로 설치 (스레드). opts = {existing: {폴더: 1|2|3}, autostart: bool}"""
    _com_init()
    p = WEB["prog"]
    p.update(running=True, done=False, ok=None, error="", pct=1, target="")

    def prog(pct):
        p["pct"] = max(p["pct"], min(99, int(pct)))
    try:
        ch = opts.get("existing") or {}
        names = {1: "업데이트", 2: "정리", 3: "그대로"}
        for x in WEB["existing"] or []:
            try:
                c = int(ch.get(x["root"], 1) or 1)
            except (TypeError, ValueError):
                c = 1
            say("[2/6] 기존 설치 %s → %s" % (x["root"], names.get(c, "그대로")))
            if not apply_existing_choice(x["root"], c, prog):
                raise RuntimeError("기존 폴더를 업데이트하지 못했습니다 — 열린 Athenaeum 창과 서버를 닫고 다시 실행하세요")
        prog(90)
        desktop_shortcut()
        say("[5/6] 부팅 때 자동 시작")
        autostart_set(bool(opts.get("autostart", True)))
        write_marker()
        prog(99)
        p["ok"] = True
        say("설치 끝.")
    except Exception as e:
        p["ok"] = False
        p["error"] = str(e)
        say("오류: %s" % e)
    finally:
        p.update(running=False, done=True, pct=100, target=target["root"])


def browser_exe():
    """크롬·엣지 실행 파일 (App Paths 레지스트리 → PATH) — 설치 창을 앱 창(--app)으로 열려고. 없으면 None."""
    try:
        import winreg
        for name in ("chrome.exe", "msedge.exe"):
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % name) as k:
                        p = winreg.QueryValueEx(k, "")[0]
                        if p and os.path.isfile(p):
                            return p
                except OSError:
                    pass
    except Exception:
        pass
    for name in ("chrome", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def open_app_window(url):
    exe = browser_exe()
    if exe:
        try:
            subprocess.Popen([exe, "--app=" + url, "--window-size=1000,780"], creationflags=NO_WINDOW)
            return True
        except Exception:
            pass
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def web_main(port=0, open_browser=True):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class WizHandler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _touch(self):
            WEB["last"] = time.time(); WEB["seen"] = True; WEB["bye"] = 0.0

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path, ctype):
            if not os.path.isfile(path):
                return self._send(404, {"error": "not found"})
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)

        def do_GET(self):
            self._touch()
            p = self.path.split("?")[0]
            if p == "/":
                return self._file(os.path.join(HERE, "install.html"), "text/html; charset=utf-8")
            if p == "/ui.css":
                return self._file(os.path.join(HERE, "ui.css"), "text/css; charset=utf-8")
            if p.startswith("/fonts/"):
                name = os.path.basename(p)
                return self._file(os.path.join(HERE, "fonts", name), "font/woff2" if name.lower().endswith(".woff2") else "font/ttf")
            if p.startswith("/logo/"):
                name = os.path.basename(p)
                ext = name.rsplit(".", 1)[-1].lower()
                return self._file(os.path.join(HERE, "logo", name), {"svg": "image/svg+xml", "png": "image/png", "ico": "image/x-icon"}.get(ext, "application/octet-stream"))
            if p == "/api/state":
                if "rescan=1" in self.path:
                    web_scan_existing()
                return self._send(200, web_state())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            self._touch()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            except Exception:
                body = {}
            p = self.path.split("?")[0]
            if p == "/api/bye":   # 창이 닫히며 보낸다 — 몇 초 안에 다른 요청이 없으면 도우미를 끝낸다 (새로 고침이면 바로 다음 요청이 온다)
                WEB["bye"] = time.time()
                return self._send(200, {"ok": True})
            if p == "/api/claude_install":
                if TEST_DIR:
                    return self._send(200, {"ok": False, "test": True})
                open_claude_install()
                return self._send(200, {"ok": True})
            if p == "/api/claude_login":
                exe = find_claude()
                if TEST_DIR or not exe:
                    return self._send(200, {"ok": False, "test": bool(TEST_DIR)})
                open_claude_login(exe)
                return self._send(200, {"ok": True})
            if p == "/api/install":
                if WEB["prog"]["running"]:
                    return self._send(200, {"ok": False, "error": "이미 진행 중"})
                WEB["prog"].update(running=True, done=False, ok=None, error="", pct=0)   # 스레드가 뜨기 전에 화면이 옛 '끝' 상태를 보지 않게
                threading.Thread(target=web_install, args=(body,), daemon=True).start()
                return self._send(200, {"ok": True})
            if p == "/api/launch":
                if TEST_DIR:
                    return self._send(200, {"ok": False, "test": True})
                try:
                    os.startfile(launcher_bat())
                except Exception as e:
                    return self._send(200, {"ok": False, "error": str(e)})
                return self._send(200, {"ok": True})
            if p == "/api/quit":
                self._send(200, {"ok": True})
                WEB["stop"] = True
                return
            self._send(404, {"error": "not found"})

    threading.Thread(target=web_scan_existing, daemon=True).start()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), WizHandler)
    except OSError as e:
        print("설치 창 서버를 띄우지 못했습니다 (%s) — 글자 창으로 갑니다." % e, flush=True)
        return main()
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("Athenaeum 설치 도우미 %s: %s" % (VERSION, url), flush=True)
    if open_browser:
        if open_app_window(url):
            print("브라우저에 설치 창을 열었습니다. 이 글자 창은 설치 창을 닫으면 저절로 닫힙니다.", flush=True)
        else:
            print("브라우저를 열지 못했습니다. 위 주소를 브라우저에 직접 넣어 주세요.", flush=True)
    t0 = time.time(); fell = False
    try:
        while not WEB["stop"]:
            time.sleep(0.5)
            now = time.time()
            if WEB["prog"]["running"]:
                continue
            if WEB["seen"]:
                if WEB["bye"] and now - WEB["bye"] > 4:
                    print("설치 창이 닫혔습니다.", flush=True)
                    break
                if now - WEB["last"] > 90:
                    print("설치 창에서 응답이 없어 끝냅니다.", flush=True)
                    break
            else:
                if open_browser and not fell and now - t0 > 15:
                    fell = True
                    print("앱 창이 안 떴으면 기본 브라우저로 엽니다…", flush=True)
                    webbrowser.open(url)
                if now - t0 > 300:
                    print("아무도 열지 않아 끝냅니다.", flush=True)
                    break
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
    time.sleep(0.3)


def _opt(name, default=None):
    a = sys.argv[1:]
    if name in a:
        i = a.index(name)
        return a[i + 1] if i + 1 < len(a) else default
    return default


if __name__ == "__main__":
    console = "--console" in sys.argv or (TEST_DIR is not None and "--web" not in sys.argv)
    if not os.path.isfile(os.path.join(HERE, "install.html")):
        console = True
    try:
        if console:
            main()
        else:
            web_main(int(_opt("--port", 0) or 0), "--no-browser" not in sys.argv)
    except Exception as e:
        say("오류: %s" % e)
        if console:
            wait_enter("Enter 를 누르면 닫힙니다. ")
        sys.exit(1)
