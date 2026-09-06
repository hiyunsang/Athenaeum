# -*- coding: utf-8 -*-
"""야간 일괄 생성기: 대기열 파일(JSON)에 적힌 논문들의 요약/관련맵을 서버 API로 차례로 만든다.

사용: pythonw batch_generate.py <대기열.json>
대기열 형식: {"summary": [...], "translation": [...], "map": [...], "force": ["summary"]}  (force 에 든 종류는 있어도 다시 생성)

- 요약 1개씩, 관련맵 1개씩 두 줄기로 동시에 진행 (요약은 서버 안에서 구간 4개 병렬)
- Claude 사용 한도에 걸리면 30분 쉬고 다시, OpenAlex가 거절하면 15분 쉬고 다시
- 서버가 재시작돼 작업이 사라지면 한 번 다시 요청
- 기록: MAENG_paper\번역\야간작업_기록.txt (아침에 이 파일을 보면 무엇이 됐는지 알 수 있음)
"""
import json, sys, time, threading, urllib.request, urllib.parse, os, io

BASE_URL = "http://localhost:8770"
LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "번역", "야간작업_기록.txt")
_lock = threading.Lock()


def log(msg):
    line = time.strftime("%m-%d %H:%M:%S") + "  " + msg
    with _lock:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def get(path):
    return json.load(urllib.request.urlopen(BASE_URL + path, timeout=60))


def post(path, body):
    req = urllib.request.Request(BASE_URL + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def is_limit(err):
    e = (err or "").lower()
    return any(k in e for k in ("한도", "limit", "usage", "rate", "거절", "429", "too many"))


def run_one(name, kind, label, force=False):
    """한 논문의 kind(summary/translation/map)를 끝까지. force=True 면 있어도 다시 생성. 성공 True / 포기 False"""
    q = urllib.parse.quote(name)
    retries = 0
    while True:
        try:
            r = post("/api/generate", {"file": name, "kind": kind, "force": bool(force)})
            force = False  # 재시도 때는 이어서(다시 지우지 않음)
        except Exception as e:
            log("[%s] 요청 실패 (%s) - 서버 확인 중, 2분 후 재시도: %s" % (label, e, name[:60]))
            time.sleep(120); retries += 1
            if retries > 10:
                log("[%s] 포기 (서버 응답 없음): %s" % (label, name[:60])); return False
            continue
        if r.get("status") == "done" or r.get("exists"):
            log("[%s] 이미 있음: %s" % (label, name[:70])); return True
        log("[%s] 시작: %s" % (label, name[:70]))
        t0 = time.time()
        while True:
            time.sleep(20)
            try:
                s = get("/api/genstatus?file=" + q + "&kind=" + kind)
            except Exception as e:
                log("[%s] 상태 조회 실패 (%s) - 계속 대기" % (label, e)); continue
            st = s.get("status")
            if st == "done":
                log("[%s] 완료 (%d분): %s" % (label, (time.time() - t0) / 60, name[:70])); return True
            if st == "none":
                log("[%s] 작업이 사라짐(서버 재시작?) - 다시 요청: %s" % (label, name[:60])); break
            if st == "error":
                err = s.get("error", "")
                if is_limit(err) and retries < 8:
                    wait = 30 if kind == "summary" else 15
                    retries += 1
                    log("[%s] 한도/거절: %s → %d분 후 재시도(%d/8): %s" % (label, err[:80], wait, retries, name[:60]))
                    time.sleep(wait * 60); break
                log("[%s] 실패, 건너뜀: %s | %s" % (label, err[:120], name[:70])); return False
            if time.time() - t0 > 3 * 3600:
                log("[%s] 3시간 초과, 건너뜀: %s" % (label, name[:60])); return False


def worker(kind, names, label, force=False, workers=5):
    """논문 여러 편을 동시에 처리(기본 3편). 구간 병렬은 서버가 8개로 묶어 두므로 총 동시 호출은 8개를 넘지 않는다."""
    lock = threading.Lock()
    state = {"i": 0, "ok": 0}

    def run():
        while True:
            with lock:
                if state["i"] >= len(names):
                    return
                n = names[state["i"]]
                state["i"] += 1
            if run_one(n, kind, label, force):
                with lock:
                    state["ok"] += 1
            time.sleep(3)

    ts = [threading.Thread(target=run, daemon=True) for _ in range(max(1, min(workers, len(names))))]
    for t in ts:
        t.start()
        time.sleep(2)
    for t in ts:
        t.join()
    log("[%s] 줄기 종료: %d/%d 성공" % (label, state["ok"], len(names)))


def claude_worker(queue):
    """Claude 작업은 한 줄기로: 번역 전부 → 요약 전부 (동시에 돌리면 사용 한도를 더 빨리 소진).
    번역을 먼저 하는 이유: 원문 추출 품질이 그대로 드러나는 쪽이라 결과를 빨리 확인할 수 있다."""
    force = set(queue.get("force", []))
    worker("translation", queue.get("translation", []), "번역", "translation" in force)
    worker("summary", queue.get("summary", []), "요약", "summary" in force)


def main():
    queue = json.load(io.open(sys.argv[1], encoding="utf-8"))
    log("===== 야간 일괄 생성 시작: 요약 %d편, 번역 %d편, 관련맵 %d편%s =====" % (
        len(queue.get("summary", [])), len(queue.get("translation", [])), len(queue.get("map", [])),
        (" (다시 생성: " + ",".join(queue.get("force", [])) + ")") if queue.get("force") else ""))
    ts = [threading.Thread(target=claude_worker, args=(queue,)),
          threading.Thread(target=worker, args=("map", queue.get("map", []), "관련맵"))]
    for t in ts:
        t.start(); time.sleep(5)
    for t in ts:
        t.join()
    log("===== 야간 일괄 생성 끝 =====")


if __name__ == "__main__":
    main()
