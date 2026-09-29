# -*- coding: utf-8 -*-
"""실행 전 점검 — Athenaeum_실행.bat 이 서버를 켜기 전에 부른다.
같은 포트에 **다른 판**의 Athenaeum 서버가 이미 돌고 있으면(업데이트 뒤 옛 서버가 남은 경우) 그것을 끄고 새 서버가 켜지게 한다.
옛 판(0.9.2 이하)은 /api/version 이 없어 '?' 로 보이며 그것도 다른 판으로 친다. 같은 판이면 그대로 둔다(bat 이 창만 연다).
포트를 잡은 프로세스는 Get-NetTCPConnection 으로 정확히 찾고, 명령줄에 server.py 가 있는 것만 끈다 — 다른 프로그램은 건드리지 않는다.
설치 도우미(setup.py)도 업데이트 뒤 stop_port() 를 쓴다."""
import io, os, sys, json, socket, subprocess, time, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = 0x08000000


def local_version():
    try:
        return io.open(os.path.join(BASE, "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        return "?"


def port_open(port):
    s = socket.socket(); s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def running_version(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/version" % port, timeout=4) as r:
            return str(json.load(r).get("version") or "?")
    except Exception:
        return "?"


def _ps(cmd, timeout=60):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.decode("utf-8", "replace")


def listening_pids(port):
    out = _ps("Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }" % port)
    return sorted({int(x) for x in out.split() if x.strip().isdigit()})


def cmdline(pid):
    return _ps("(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % pid).strip()


def stop_port(port, say=print):
    """포트를 잡은 Athenaeum 서버(server.py)를 끈다. 끈 수를 돌려준다. server.py 가 아니면 안 끄고 알린다."""
    n = 0
    for pid in listening_pids(port):
        cl = cmdline(pid)
        if "server.py" in cl.lower():
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)
            say("      옛 서버 종료 (pid %d)" % pid); n += 1
        else:
            say("      포트 %d 를 다른 프로그램이 쓰고 있어 그대로 둡니다: %s" % (port, cl[:80]))
    for _ in range(20):
        if not port_open(port):
            break
        time.sleep(0.5)
    return n


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    port = int(os.environ.get("ATHENAEUM_PORT", "8770"))
    if not port_open(port):
        return 0
    mine, theirs = local_version(), running_version(port)
    if theirs == mine:
        print("      이미 같은 판(%s)이 켜져 있습니다 - 창만 엽니다." % mine)
        return 0
    print("      다른 판의 서버(%s)가 켜져 있어 끄고 이 판(%s)을 켭니다." % (theirs, mine))
    stop_port(port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
