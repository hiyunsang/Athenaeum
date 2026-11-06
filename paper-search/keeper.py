# -*- coding: utf-8 -*-
"""
Athenaeum 서버 지킴이 — 서버(기본 포트 8770)가 꺼져 있으면 다시 켠다.
윈도우 작업 스케줄러의 작업 'AthenaeumKeeper' 가 5분마다 부른다(pythonw 라 창이 뜨지 않는다).
켜고 끄기는 홈 ⚙ 설정 → 기기 → 「서버 지킴이」.

    pythonw keeper.py            꺼져 있으면 켠다
    python  keeper.py --dry      살펴보기만 하고 무엇을 할지 찍는다

- 포트가 닫힌 채로 24초가 이어질 때만 켠다 — 재시작 도구가 서버를 껐다 켜는 1~2초 사이에 끼어들지 않게.
- 포트는 열려 있는데 답이 없으면(멈춘 서버) 건드리지 않고 기록만 한다. 돌고 있는 작업을 죽일 수 있어서다.
- 켤 때는 탐색기(셸)에게 Athenaeum_시작.vbs 를 열게 한다. 직접 띄우면 서버가 작업 스케줄러의 작업 묶음(Job)에 딸려
  들어가 그 작업이 끝날 때 같이 닫힐 수 있다. 15초 안에 안 뜨면 그때 직접 띄운다.
- 수집\\지킴이_쉼.txt 가 있고 30분이 안 지났으면 쉰다 (설치 도우미가 업데이트하는 동안 서버를 다시 켜지 않게).
- 켰을 때와 이상할 때만 수집\\지킴이기록.txt 에 한 줄 남긴다.

2026-11-06: Claude 데스크톱 앱이 새벽에 자동 업데이트되면서 서버가 같이 닫혔고 9시간 동안 아무도 몰랐다.
다른 기기에서 여는 기능(Tailscale)을 쓰면 서버가 꺼졌을 때 집에서는 다시 켤 방법이 없다 — 그래서 둔다.
"""
import io
import os
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))          # paper-search
ROOT = os.path.dirname(HERE)
PORT = int(os.environ.get("ATHENAEUM_PORT", "8770"))
DATA = os.path.join(ROOT, "수집")
LOG = os.path.join(DATA, "지킴이기록.txt")
PAUSE = os.path.join(DATA, "지킴이_쉼.txt")
DRY = "--dry" in sys.argv
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200 | NO_WINDOW
BREAKAWAY = 0x01000000


def say(msg):
    if DRY and sys.stdout:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8", "replace"))
        sys.stdout.flush()


def log(msg):
    say(msg)
    if DRY:
        return
    try:
        os.makedirs(DATA, exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def port_open():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    except OSError:
        return False
    finally:
        s.close()


def answers(timeout=8):
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/api/version" % PORT, timeout=timeout).read()
        return True
    except Exception:
        return False


def paused():
    try:
        return time.time() - os.path.getmtime(PAUSE) < 1800
    except OSError:
        return False


def pythonw():
    bundled = os.path.join(ROOT, "python", "pythonw.exe")
    if os.path.exists(bundled):
        return bundled
    here = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return here if os.path.exists(here) else "pythonw"


def start_shell():
    """탐색기에게 자동 시작 스크립트를 열게 한다 (기본 포트일 때만 — 스크립트에는 포트를 넘길 수 없다)."""
    vbs = os.path.join(HERE, "Athenaeum_시작.vbs")
    explorer = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "explorer.exe")
    if PORT != 8770 or not os.path.isfile(vbs) or not os.path.isfile(explorer):
        return False
    try:
        subprocess.Popen([explorer, vbs], close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def start_direct():
    args = [pythonw(), os.path.join(HERE, "server.py")]
    kw = dict(cwd=HERE, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:      # 작업 묶음에서 벗어나 띄워 본다 — 묶음이 허락하지 않으면 PermissionError
        subprocess.Popen(args, creationflags=DETACHED | BREAKAWAY, **kw)
    except OSError:
        subprocess.Popen(args, creationflags=DETACHED, **kw)


def wait_port(sec):
    for _ in range(sec):
        if port_open():
            return True
        time.sleep(1)
    return False


def main():
    if not os.path.isfile(os.path.join(HERE, "server.py")):
        return 0
    if paused():
        say("쉬는 중 (수집\\지킴이_쉼.txt — 설치 도우미가 업데이트하고 있습니다)")
        return 0
    for i in range(3):
        if port_open():
            if answers():
                say("서버가 켜져 있습니다 (포트 %d)" % PORT)
            elif answers(20):
                say("서버가 켜져 있습니다 (답이 느립니다)")
            else:
                log("포트 %d 는 열려 있는데 서버가 답하지 않습니다 — 건드리지 않았습니다 (작업 관리자에서 확인)" % PORT)
            return 0
        if DRY:
            say("포트 %d 가 닫혀 있습니다 — 지킴이가 돌면 24초 뒤에도 닫혀 있을 때 서버를 켭니다" % PORT)
            return 0
        if i < 2:
            time.sleep(12)
    how = "탐색기를 거쳐"
    if not start_shell() or not wait_port(15):
        if port_open():
            pass
        else:
            how = "직접"
            start_direct()
    t0 = time.time()
    if wait_port(40) and answers(30):
        log("서버가 꺼져 있어 다시 켰습니다 (%s, %d초 만에 응답)" % (how, int(time.time() - t0) + 1))
    else:
        log("서버가 꺼져 있어 켜려 했지만 40초 안에 뜨지 않았습니다 (%s) — 콘솔에서 python paper-search\\server.py 로 오류를 볼 것" % how)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:      # 지킴이가 죽어 작업 스케줄러에 오류로 남지 않게
        try:
            log("지킴이 오류: %s: %s" % (type(e).__name__, str(e)[:200]))
        except Exception:
            pass
        sys.exit(0)
