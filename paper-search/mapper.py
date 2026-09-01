# -*- coding: utf-8 -*-
"""관련 논문 맵 생성 — Connected Papers 방식의 경량판.

무료 OpenAlex API에서 시드 논문의 참고문헌·피인용 논문을 모으고,
'공통 참고문헌 수(bibliographic coupling)'로 유사도를 계산해 그래프를 만든다.
"""
import math
import re
import time

import requests

API = "https://api.openalex.org"
HEADERS = {"User-Agent": "maeng-paper-map/1.0"}
# OpenAlex polite pool: 연락용 이메일을 보내면 요청 우선순위가 높아짐 (사용자 허락받음, 2026-09-02)
MAILTO = "maenglaboratory@gmail.com"
SELECT = ("id,doi,display_name,publication_year,cited_by_count,"
          "referenced_works,authorships,primary_location,keywords")
MAX_NODES = 45


def _get(url, params=None):
    params = dict(params or {})
    params["mailto"] = MAILTO
    for i in range(6):
        r = requests.get(url, params=params, headers=HEADERS, timeout=40)
        if r.status_code == 429 or r.status_code >= 500:
            try:
                wait = float(r.headers.get("Retry-After", 0))
            except (TypeError, ValueError):
                wait = 0
            time.sleep(min(30, max(wait, 3 * (2 ** i))))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("OpenAlex가 계속 바쁩니다 - 몇 분 뒤 다시 시도해 주세요")


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
        time.sleep(0.3)
    missing = [t for t in todo if t not in out]
    for t in missing[:60]:
        try:
            out[t] = _get(API + "/works/" + t)
        except Exception:
            pass
        time.sleep(0.25)
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
    """Connected Papers 방식: 2단계 후보 확장 -> coupling+co-citation 유사도
    -> 상위 선택 -> 유사도 행렬 -> Prior/Derivative works.
    archive_titles: {정규화된 제목: 파일명} - 보유 논문 표시용."""
    from collections import Counter

    seed = find_seed(seed_title)
    seed_id = wid(seed["id"])
    seed_refs = set(wid(x) for x in seed.get("referenced_works", []))

    # [1단계 이웃] 참고문헌 + 이 논문을 인용한 논문들
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

    # [2단계 후보] 1단계 논문들의 참고문헌에 자주 등장하는 논문을 후보로 확장
    freq = Counter()
    for it in pool.values():
        for rid in it.get("referenced_works", []):
            freq[wid(rid)] += 1
    for k in [k for k in freq if k in pool or k == seed_id]:
        del freq[k]
    second_hop = [k for k, c in freq.most_common(120) if c >= 2]
    pool.update(fetch_many(second_hop))
    pool.pop(seed_id, None)

    # 후보들의 참고문헌 집합 + '모집단 내 피인용' 벡터 (co-citation 계산용)
    refs_of = {seed_id: seed_refs}
    for k, it in pool.items():
        refs_of[k] = set(wid(x) for x in it.get("referenced_works", []))
    cited_by = {}
    for pid, refs in refs_of.items():
        for r in refs:
            cited_by.setdefault(r, set()).add(pid)

    def similarity(a, b):
        ra, rb = refs_of.get(a, set()), refs_of.get(b, set())
        coup = len(ra & rb) / math.sqrt(max(1, len(ra)) * max(1, len(rb)))
        ca, cb = cited_by.get(a, set()), cited_by.get(b, set())
        cc_inter = len(ca & cb)
        cocite = cc_inter / math.sqrt(max(1, len(ca)) * max(1, len(cb))) if cc_inter else 0.0
        s = 0.6 * coup + 0.4 * cocite
        if b in ra or a in rb:
            s += 0.12
        return min(1.0, s)

    # [선택] 시드와의 결합 유사도 상위 MAX_NODES편
    scored = []
    for k, it in pool.items():
        s = similarity(seed_id, k)
        if s <= 0:
            continue
        owned = archive_titles.get(norm_title(it.get("display_name")))
        inter = len(refs_of[k] & seed_refs)
        scored.append((s, it.get("cited_by_count") or 0, k, it, owned, inter))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    chosen = scored[:MAX_NODES]

    nodes = []
    for s, cit, k, it, owned, inter in chosen:
        n = _node(it, refs_of[k], inter, owned)
        n.pop("_refs", None)
        n["sim"] = round(s, 3)
        nodes.append(n)
    seed_node = _node(seed, set(), 0, "")
    seed_node.pop("_refs", None)
    seed_node["seed"] = True
    nodes.insert(0, seed_node)

    # [유사도 행렬] 배치용 - coupling + co-citation
    ids = [seed_id] + [x[2] for x in chosen]
    sims = [[0.0] * len(ids) for _ in ids]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            sims[i][j] = sims[j][i] = round(similarity(ids[i], ids[j]), 3)

    # [간선] 노드당 유사도 상위 4개만 (표시용)
    edges = {}
    for i in range(len(ids)):
        cand = sorted(((sims[i][j], j) for j in range(len(ids)) if j != i), reverse=True)
        for s, j in cand[:4]:
            if s < 0.05:
                break
            key = (min(i, j), max(i, j))
            edges[key] = max(edges.get(key, 0), int(round(s * 10)))
    edge_list = [[a, b, w] for (a, b), w in edges.items()]

    # [Prior works] 이 그래프의 논문들이 공통으로 인용하는 조상 논문
    graph_ids = set(ids)
    prior_cnt = Counter()
    for k in ids[1:]:
        for r in refs_of.get(k, set()):
            if r not in graph_ids:
                prior_cnt[r] += 1
    prior_ids = [k for k, c in prior_cnt.most_common(10) if c >= max(3, len(ids) // 6)][:8]
    prior_meta = fetch_many(prior_ids)
    prior = []
    for k in prior_ids:
        if k in prior_meta:
            n = _node(prior_meta[k], set(), prior_cnt[k],
                      archive_titles.get(norm_title(prior_meta[k].get("display_name"))))
            n.pop("_refs", None)
            prior.append(n)

    # [Derivative works] 이 그래프의 논문 여러 편을 인용하는 후속 논문 (시드 인용자들 중)
    derived = []
    for k in citer_ids:
        if k in graph_ids or k not in pool:
            continue
        hit = len(refs_of.get(k, set()) & graph_ids)
        if hit >= 3:
            derived.append((hit, k))
    derived.sort(reverse=True)
    derived_nodes = []
    for hit, k in derived[:8]:
        n = _node(pool[k], set(), hit, archive_titles.get(norm_title(pool[k].get("display_name"))))
        n.pop("_refs", None)
        derived_nodes.append(n)

    return {"nodes": nodes, "edges": edge_list, "sims": sims,
            "prior": prior, "derived": derived_nodes,
            "refs": len(seed_refs), "citers": len(citer_ids),
            "pool": len(pool)}
