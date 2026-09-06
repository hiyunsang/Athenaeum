# -*- coding: utf-8 -*-
"""전체 논문 라벨을 Claude로 재판정.

- Claude가 고른 라벨 = 확정
- 기존에 있었는데 Claude가 안 고른 라벨 = 제안(?)으로 강등 (지워지지 않음)
- 사용자가 거부(rejected)한 라벨은 절대 다시 안 붙임
서버(localhost:8770)가 켜져 있어야 함. 결과는 API로 저장.
"""
import json, os
import sys
import threading
import urllib.request

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, r"C:\Users\PC1\Documents\MAENG_paper\paper-search")
import server as sv
import rules

BASE = "http://localhost:8770"
groups = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "labels.json"), encoding="utf-8"))
texts = json.load(open(r"C:\Users\PC1\Documents\MAENG_paper\paper-search\text_index.json", encoding="utf-8"))
papers = json.load(urllib.request.urlopen(BASE + "/api/data"))["papers"]

lock = threading.Lock()
done = [0]
changed = [0]
failed = []


def work(chunk):
    for p in chunk:
        text = texts.get(p["file"], "")
        picked = sv.classify_with_claude(
            p["title"], rules.keyword_section(text), text[:6000], groups)
        with lock:
            done[0] += 1
            if done[0] % 10 == 0:
                print("진행 {}/{} (변경 {})".format(done[0], len(papers), changed[0]), flush=True)
        if picked is None:
            with lock:
                failed.append(p["file"][:60])
            continue
        rejected = p["rejected"]
        labels = [l for l in picked if l not in rejected]
        demoted = [l for l in p["labels"] if l not in labels and l not in rejected]
        if set(labels) == set(p["labels"]) and not demoted:
            continue
        body = json.dumps({"file": p["file"], "labels": labels,
                           "suggested": demoted, "rejected": rejected}).encode()
        urllib.request.urlopen(urllib.request.Request(
            BASE + "/api/tags", data=body, method="POST"))
        with lock:
            changed[0] += 1


threads = []
n = 3  # 동시 3개
for i in range(n):
    t = threading.Thread(target=work, args=(papers[i::n],))
    t.start()
    threads.append(t)
for t in threads:
    t.join()

print("\n재판정 완료: 전체 {} / 라벨 바뀐 논문 {} / 실패(기존 유지) {}".format(
    len(papers), changed[0], len(failed)))
for f in failed[:10]:
    print("  실패:", f)
