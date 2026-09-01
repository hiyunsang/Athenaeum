# -*- coding: utf-8 -*-
"""관련 논문 맵 생성 — Connected Papers 방식의 경량판.

무료 OpenAlex API에서 시드 논문의 참고문헌·피인용 논문을 모으고,
'공통 참고문헌 수(bibliographic coupling)'로 유사도를 계산해 그래프를 만든다.
"""
import re
import time

import requests

API = "https://api.openalex.org"
HEADERS = {"User-Agent": "maeng-paper-map/1.0"}
SELECT = ("id,doi,display_name,publication_year,cited_by_count,"
          "referenced_works,authorships,primary_location,keywords")
MAX_NODES = 45


def _get(url, params=None):
    for i in range(4):
        r = requests.get(url, params=params, headers=HEADERS, timeout=40)
        if r.status_code == 429:
            time.sleep(2 + i * 3)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("OpenAlex가 요청을 계속 거절합니다 (잠시 후 다시 시도)")


def norm_title(t):
    return re.sub(r"[^a-z0-9가-힣]", "", (t or "").lower())


def wid(url_id):
    return url_id.rsplit("/", 1)[-1] if url_id else ""


def find_seed(title):
    d = _get(API + "/works", {"search": title, "per-page": "5"})
    want = norm_title(title)
    for it in d.get("results", []):
        got = norm_title(it.get("display_name"))
        if got[:50] == want[:50] or want in got or got in want:
            return it
    raise RuntimeError("OpenAlex에서 이 논문을 찾지 못했습니다 (인용 DB 미등록일 수 있음)")


def fetch_many(ids):
    """여러 논문 정보를 한꺼번에 가져온다 (배치 필터, 실패분은 개별 조회)."""
    out = {}
    todo = [wid(i) for i in ids]
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        try:
            d = _get(API + "/works", {"filter": "openalex:" + "|".join(chunk),
                                      "per-page": str(len(chunk)), "select": SELECT})
            for it in d.get("results", []):
                out[wid(it["id"])] = it
        except Exception:
            pass
        time.sleep(0.15)
    missing = [t for t in todo if t not in out]
    for t in missing[:60]:
        try:
            out[t] = _get(API + "/works/" + t)
        except Exception:
            pass
        time.sleep(0.12)
    return out


def _node(it, refs, inter, owned_file):
    auth = ""
    try:
        auth = it["authorships"][0]["author"]["display_name"].split()[-1]
    except (KeyError, IndexError, TypeError):
        pass
    venue = ""
    try:
        venue = (it.get("primary_location") or {}).get("source", {}).get("display_name", "") or ""
    except AttributeError:
        pass
    kw = []
    try:
        kw = [k["display_name"] for k in (it.get("keywords") or [])[:5]]
    except (KeyError, TypeError):
        pass
    return {
        "id": wid(it["id"]),
        "doi": it.get("doi") or "",
        "title": it.get("display_name") or "",
        "year": it.get("publication_year") or 0,
        "cit": it.get("cited_by_count") or 0,
        "author": auth,
        "venue": venue[:60],
        "kw": kw,
        "inter": inter,
        "owned": owned_file or "",
        "_refs": refs,
    }


def build_map(seed_title, archive_titles):
    """archive_titles: {정규화된 제목: 파일명} - 보유 논문 표시용."""
    seed = find_seed(seed_title)
    seed_id = wid(seed["id"])
    seed_refs = set(wid(x) for x in seed.get("referenced_works", []))

    # 후보군: 참고문헌 + 이 논문을 인용한 논문들
    pool = {}
    d = _get(API + "/works", {"filter": "cites:" + seed_id, "per-page": "100",
                              "sort": "cited_by_count:desc", "select": SELECT})
    citer_ids = set()
    for it in d.get("results", []):
        k = wid(it["id"])
        citer_ids.add(k)
        pool[k] = it
    pool.update(fetch_many(list(seed_refs)))
    pool.pop(seed_id, None)

    # 시드와의 유사도 = 공통 참고문헌 수 (+ 많이 인용된 논문 약간 우대)
    scored = []
    for k, it in pool.items():
        refs = set(wid(x) for x in it.get("referenced_works", []))
        inter = len(refs & seed_refs)
        owned = archive_titles.get(norm_title(it.get("display_name")))
        scored.append((inter, it.get("cited_by_count") or 0, k, it, refs, owned))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chosen = scored[:MAX_NODES]

    nodes = [_node(it, refs, inter, owned)
             for inter, cit, k, it, refs, owned in chosen]

    # 노드끼리의 간선: 공통 참고문헌 수 + 직접 인용 보너스, 노드당 상위 4개
    edges = {}
    for i in range(len(nodes)):
        cand = []
        for j in range(len(nodes)):
            if i == j:
                continue
            a, b = nodes[i], nodes[j]
            w = len(a["_refs"] & b["_refs"])
            if b["id"] in a["_refs"] or a["id"] in b["_refs"]:
                w += 3
            if w >= 2:
                cand.append((w, j))
        cand.sort(reverse=True)
        for w, j in cand[:4]:
            key = (min(i, j), max(i, j))
            edges[key] = max(edges.get(key, 0), w)

    # 시드 간선: 유사도 상위 8개와 연결
    seed_node = _node(seed, set(), 0, "")
    seed_node.pop("_refs", None)
    seed_node["seed"] = True
    for n in nodes:
        n.pop("_refs", None)
    nodes.insert(0, seed_node)
    edge_list = [[a + 1, b + 1, w] for (a, b), w in edges.items()]
    for idx in range(1, min(9, len(nodes))):
        edge_list.append([0, idx, 5])

    return {"nodes": nodes, "edges": edge_list,
            "refs": len(seed_refs), "citers": len(citer_ids)}
