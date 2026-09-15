# -*- coding: utf-8 -*-
"""
Athenaeum 설치 도우미 (창에서 묻고 답하는 식).

  포터블판:  맨 위 'Athenaeum 설치.bat'  →  paper-search\\setup.bat  →  동봉 python 으로 이 파일 실행
  저장소판:  python paper-search\\setup.py

하는 일
  1) 기능 안내
  2) 파이썬 패키지 확인
  3) Claude Code 설치·로그인 확인 (없으면 설치 창을 열어 주고, 끝날 때까지 기다림)
  4) 바탕화면에 'Athenaeum' 바로가기 (앱처럼 아이콘 포함)
  5) (선택) 부팅 때 자동 시작 — 시작프로그램 폴더에 Athenaeum_시작.vbs 바로가기
  6) (선택) 바로 실행

시험용:  setup.py --test <폴더>   → 바로가기를 바탕화면·시작프로그램 대신 그 폴더에 만든다 (내 PC 설정을 건드리지 않음)
파일을 지우는 일은 하지 않는다. 지우려면 uninstall.bat.
"""
import io, os, sys, shutil, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))          # paper-search
ROOT = os.path.dirname(HERE)                               # 포터블판이면 Athenaeum\, 저장소면 저장소 루트
ICON = os.path.join(HERE, "logo", "athenaeum.ico")
NO_WINDOW = 0x08000000
NEW_CONSOLE = 0x00000010

if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TEST_DIR = None
if "--test" in sys.argv:
    TEST_DIR = os.path.abspath(sys.argv[sys.argv.index("--test") + 1])
    os.makedirs(TEST_DIR, exist_ok=True)


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


def wait_enter(msg):
    try:
        input(msg)
    except EOFError:
        pass


# ---------- 1) 기능 안내 ----------
def intro():
    say("=" * 64)
    say("  Athenaeum 설치 도우미")
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
    say("요약·번역·검토는 Claude Code 로 합니다 (내 Claude 구독 사용량을 씀, API 키 없음).")
    say("이 도우미는 파일을 지우지 않습니다. 지울 때는 'Athenaeum 삭제.bat' 을 쓰세요.")
    say()


# ---------- 2) 파이썬 패키지 ----------
def check_python():
    say("[1/5] 파이썬 %s.%s — %s" % (sys.version_info[0], sys.version_info[1], sys.executable))
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


# ---------- 3) Claude Code ----------
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
    say("[2/5] Claude Code")
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


# ---------- 4) 5) 바로가기 ----------
def special_folder(name):
    if TEST_DIR:
        return TEST_DIR
    import win32com.client
    return win32com.client.Dispatch("WScript.Shell").SpecialFolders(name)


def make_shortcut(lnk_path, target, workdir, icon=None, args=""):
    import win32com.client
    sh = win32com.client.Dispatch("WScript.Shell")
    s = sh.CreateShortcut(lnk_path)
    s.TargetPath = target
    s.Arguments = args
    s.WorkingDirectory = workdir
    if icon and os.path.isfile(icon):
        s.IconLocation = icon + ",0"
    s.WindowStyle = 1
    s.Save()


def launcher_bat():
    top = os.path.join(ROOT, "Athenaeum 실행.bat")            # 포터블판
    return top if os.path.isfile(top) else os.path.join(HERE, "Athenaeum_실행.bat")


def desktop_shortcut():
    say("[3/5] 바탕화면 바로가기")
    lnk = os.path.join(special_folder("Desktop"), "Athenaeum.lnk")
    bat = launcher_bat()
    make_shortcut(lnk, bat, os.path.dirname(bat), ICON)
    say("      만듦: " + lnk)
    say("      (더블클릭하면 서버가 켜지고 앱 창이 뜹니다. 이미 켜져 있으면 창만 엽니다)")


def autostart_shortcut():
    say("[4/5] 부팅 때 자동 시작")
    vbs = os.path.join(HERE, "Athenaeum_시작.vbs")
    lnk = os.path.join(special_folder("Startup"), "Athenaeum 자동시작.lnk")
    if ask("      컴퓨터를 켤 때 서버를 조용히 미리 켜 둘까요? (수집이 바로 돌고, 바로가기를 누르면 즉시 뜸)"):
        make_shortcut(lnk, vbs, HERE, ICON)
        say("      만듦: " + lnk)
    else:
        if os.path.isfile(lnk):
            os.remove(lnk)
            say("      기존 자동 시작 바로가기를 뺐습니다")
        say("      건너뜀 (나중에 다시 이 도우미를 실행하면 됩니다)")


# ---------- 6) 실행 ----------
def launch():
    say("[5/5] 실행")
    if TEST_DIR:
        say("      (시험 모드라 실행하지 않음)")
        return
    if ask("      지금 Athenaeum 을 열까요?"):
        os.startfile(launcher_bat())
        say("      잠시 뒤 브라우저에 화면이 뜹니다. 홈 위쪽 '수집' 패널에서 감시 폴더를, '라벨 체계 설정 ↗' 에서 내 분야 체계를 정하세요.")


def main():
    intro()
    ok_py = check_python()
    ok_claude = check_claude()
    desktop_shortcut()
    autostart_shortcut()
    say()
    say("정리")
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
