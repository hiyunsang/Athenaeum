# -*- coding: utf-8 -*-
"""
Athenaeum 서버 재시작 (창 없이).

    python tools\\restart_server.py            진행 중인 요약·번역 작업이 있으면 멈추지 않고 알려만 준다
    python tools\\restart_server.py --force    작업이 있어도 재시작 (그 작업은 끊긴다)

1) /api/jobs 로 진행 중 작업 확인  2) 포트를 잡은 프로세스 종료  3) pythonw 로 server.py 를 창 없이 다시 띄움
4) 응답이 올 때까지 기다린다. 코드를 고친 뒤 서버에 반영하려면 이것을 실행한다.

주의: 일괄 생성(batch_generate.py)이 도는 중에 재시작하면 서버 안의 작업이 사라진다.
      배치는 '작업이 사라짐'을 보고 다시 요청하지만 그 논문은 처음부터 다시 만든다.
"""
import io, json, os, subprocess, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PS = os.path.join(ROOT, "paper-search")
PORT = int(os.environ.get("ATHENAEUM_PORT", "8770"))
URL = "http://localhost:%d" % PORT
NO_WINDOW = 0x08000000
DETACHED = 0x00000008 | 0x00000200 | NO_WINDOW


def out(msg):
    sys.stdout.buffer.write((msg + "\n").encode("utf-8", "replace")); sys.stdout.flush()


def pythonw():
    """동봉 파이썬(포터블판) → 지금 파이썬 옆의 pythonw → PATH 의 pythonw."""
    bundled = os.path.join(ROOT, "python", "pythonw.exe")
    if os.path.exists(bundled):
        return bundled
    here = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return here if os.path.exists(here) else "pythonw"


def running_jobs():
    n, names = 0, []
    try:
        d = json.load(urllib.request.urlopen(URL + "/api/jobs", timeout=5))
        n, names = d.get("running", 0), [j.get("file", "")[:50] + " (" + j.get("kind", "") + ")"
                                         for j in d.get("jobs", []) if j.get("status") == "running"]
    except Exception:
        pass
    try:   # 쓰는 중인 공부(15~30분짜리) — 끊기면 「이어서」 로 남은 단계부터 다시 쓸 수 있지만 하던 호출은 버려진다
        d = json.load(urllib.request.urlopen(URL + "/api/study?busy=1", timeout=15))
        live = [x for x in d.get("items", []) if x.get("status") == "running"]
        n += len(live)
        names += [(x.get("title") or x.get("topic") or "")[:50] + " (공부)" for x in live]
        busy = d.get("busy") or []      # 발표 자료·바탕 이론·묻기 같은 작은 배경 작업 — 끊기면 표시만 남아 화면이 영원히 기다린다(2026-10-11)
        n += len(busy)
        names += [(x.get("title") or "")[:50] + " (공부 — " + " · ".join(x.get("jobs") or []) + ")" for x in busy]
    except Exception:
        pass
    return n, names


def server_pids():
    """명령줄에 paper-search 의 server.py 가 든 파이썬 프로세스 — netstat 이 포트 주인을 못 알려 줄 때의 폴백.
    (다른 포트의 시험 서버도 같이 잡힌다 — 폴백은 netstat 이 빈손일 때만 쓴다.)"""
    cmd = ('Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*paper-search*server.py*" } '
           '| Select-Object -ExpandProperty ProcessId')
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, creationflags=NO_WINDOW, timeout=60)
        return {p.strip() for p in r.stdout.decode("utf-8", "replace").split()
                if p.strip().isdigit() and int(p) not in (0, os.getpid())}
    except Exception:
        return set()


def pids_on_port():
    r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, creationflags=NO_WINDOW)
    pids = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(":%d" % PORT) and parts[3] == "LISTENING" and parts[4] != "0":
            pids.add(parts[4])
    if not pids:
        # 2026-10-29: Claude 의 샌드박스 안에서는 netstat 이 주인 pid 를 0 으로 보여 '서버 종료 (pid 0)' 만 찍고
        # 옛 서버가 그대로 남았다(옛 코드가 계속 돌아 다시 쓰기가 옛 옵션으로 시작됐다). 그때는 명령줄로 찾는다.
        pids = server_pids()
    return pids


def main():
    force = "--force" in sys.argv
    n, names = running_jobs()
    if n and not force:
        out("진행 중인 작업 %d개가 있어 재시작하지 않았습니다:" % n)
        for x in names:
            out("  - " + x)
        out("끝난 뒤 다시 실행하거나, 끊어도 되면 --force 를 붙이세요.")
        sys.exit(1)
    for pid in pids_on_port():
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, creationflags=NO_WINDOW)
        out("서버 종료 (pid %s)" % pid)
    time.sleep(1.5)
    subprocess.Popen([pythonw(), os.path.join(PS, "server.py")], cwd=PS, creationflags=DETACHED, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(URL + "/", timeout=3)
            try:
                d = json.load(urllib.request.urlopen(URL + "/api/intake", timeout=5))
                out("서버 켜짐 (%d초) · 수집 감시 %s" % (i + 1, "켜짐" if d.get("alive") else "시작 중"))
            except Exception:
                out("서버 켜짐 (%d초)" % (i + 1))
            return
        except Exception:
            pass
    out("30초 안에 서버가 응답하지 않았습니다. 콘솔에서  python paper-search\\server.py  로 오류를 확인하세요.")
    sys.exit(2)


if __name__ == "__main__":
    main()
