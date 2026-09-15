# -*- coding: utf-8 -*-
"""
Athenaeum 설치 도우미 (창에서 묻고 답하는 식).

  포터블판:  맨 위 'Athenaeum 설치.bat'  →  paper-search\\setup.bat  →  동봉 python 으로 이 파일 실행
  저장소판:  python paper-search\\setup.py

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

시험용:  setup.py --test <폴더>   → 바로가기·설치 기록을 바탕화면·시작프로그램 대신 그 폴더에 두고, 기존 설치도 그 폴더의 바로가기에서만 찾는다.
사용자 데이터(논문·번역·메모·원고·단어장·라벨·수집설정)는 어떤 경우에도 지우거나 옮기지 않는다. 지우려면 uninstall.bat.
"""
import io, os, sys, json, shutil, subprocess, time

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


def read_version(root):
    try:
        return io.open(os.path.join(root, "paper-search", "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        return ""


VERSION = read_version(ROOT) or "?"


def say(s=""):
    print(s, flush=True)


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
    say("  원고   근거 카드 → 개요 → 초안 → 검토 → 영문 → Word 파일. 교수님 주석 논의, 도식 만들기")
    say()
    say("라벨 체계는 비어 있는 채로 시작합니다. 논문이 10편쯤 모이면 홈 화면이 'Claude 에게 체계 설계 맡기기' 를 권하고,")
    say("그 뒤로는 새 논문이 자동 분류되며, 논문이 많이 늘면 다시 분류하라고 알려 줍니다.")
    say("요약·번역·검토는 Claude Code 로 합니다 (내 Claude 구독 사용량을 씀, API 키 없음).")
    say("이 도우미는 파일을 지우지 않습니다. 지울 때는 'Athenaeum 삭제.bat' 을 쓰세요.")
    say()


# ---------- 2) 파이썬 패키지 ----------
def check_python():
    say("[1/6] 파이썬 %s.%s — %s" % (sys.version_info[0], sys.version_info[1], sys.executable))
    missing = []
    for mod in ("pymupdf", "pypdf", "requests", "win32com.client", "numpy", "scipy"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
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


def update_in_place(old):
    """기존 폴더의 프로그램 파일만 새것으로. 데이터(논문모음·번역·메모·원고·단어장·라벨·설정)는 그대로."""
    say("      기존 폴더를 %s → %s 로 업데이트합니다: %s" % (read_version(old) or "0.9.1 이하", VERSION, old))
    stop_server_at(old)
    n = copy_tree_program(HERE, os.path.join(old, "paper-search"))
    say("      paper-search 프로그램 파일 %d개 교체 (tags·labels·수집설정 등 데이터 파일은 그대로)" % n)
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
        shutil.copytree(newpy, oldpy)
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.isdir(tmp):
            say("      옛 python 폴더(%s)를 다 지우지 못했습니다. 나중에 직접 지워도 됩니다." % tmp)
    for f in os.listdir(ROOT):   # 맨 위의 bat·txt·md
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(old, f))
    say("      업데이트 끝. 이제부터 바로가기와 실행은 기존 폴더를 씁니다. 새로 푼 이 폴더(%s)는 지워도 됩니다." % ROOT)
    return True


def handle_existing():
    say("[2/6] 기존 설치 확인")
    olds = find_existing_installs()
    if not olds:
        say("      다른 설치 없음")
        return
    for old in olds:
        say("      발견: %s  (버전 %s, 논문 %d편)" % (old, read_version(old) or "0.9.1 이하", paper_count(old)))
        c = choose("      어떻게 할까요?", [
            (1, "업데이트 — 기존 폴더의 프로그램만 새것으로 바꾸고 논문·번역·원고·라벨은 그대로 (권장)"),
            (2, "정리 — 기존 서버를 끄고 그쪽 바로가기를 지움. 폴더·데이터는 남기니 직접 지우세요. 여기에 새로 설치"),
            (3, "그대로 두고 여기에 따로 설치 (바탕화면 바로가기는 이쪽으로 바뀜)")], 1)
        if c == 1:
            if update_in_place(old):
                remove_shortcuts_pointing(old)   # 새 바로가기를 다시 만든다
                target["root"] = old
        elif c == 2:
            stop_server_at(old)
            remove_shortcuts_pointing(old)
            say("      기존 폴더는 직접 지우세요 (논문·번역·원고가 들어 있으니 필요하면 먼저 옮기세요): " + old)
        else:
            say("      그대로 둠")


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


def check_claude():
    say("[3/6] Claude Code")
    exe = find_claude()
    while not exe:
        say("      설치되어 있지 않습니다. 요약·번역·라벨 분류에 꼭 필요합니다.")
        say("      설치 명령 (PowerShell):   irm https://claude.ai/install.ps1 | iex")
        say("      안내 페이지:              https://claude.com/claude-code")
        if TEST_DIR:
            say("      (시험 모드라 설치 창은 열지 않음)")
            return False
        if ask("      지금 PowerShell 창을 열어 설치를 시작할까요?"):
            subprocess.Popen(["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command",
                              "irm https://claude.ai/install.ps1 | iex"], creationflags=NEW_CONSOLE)
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
        subprocess.Popen(["cmd", "/k", "echo 로그인: 아래에서 /login 을 입력하고 브라우저에서 승인한 뒤 이 창을 닫으세요 && \"%s\"" % exe],
                         creationflags=NEW_CONSOLE)
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


def autostart_shortcut():
    say("[5/6] 부팅 때 자동 시작")
    vbs = os.path.join(target["root"], "paper-search", "Athenaeum_시작.vbs")
    lnk = os.path.join(special_folder("Startup"), "Athenaeum 자동시작.lnk")
    if ask("      컴퓨터를 켤 때 서버를 조용히 미리 켜 둘까요? (수집이 바로 돌고, 바로가기를 누르면 즉시 뜸)"):
        make_shortcut(lnk, vbs, os.path.dirname(vbs), icon_path())
        say("      만듦: " + lnk)
    else:
        if os.path.isfile(lnk):
            os.remove(lnk)
            say("      기존 자동 시작 바로가기를 뺐습니다")
        say("      건너뜀 (나중에 다시 이 도우미를 실행하면 됩니다)")


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


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say("오류: %s" % e)
        wait_enter("Enter 를 누르면 닫힙니다. ")
        sys.exit(1)
