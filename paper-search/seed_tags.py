# -*- coding: utf-8 -*-
"""초기 라벨 생성: 주제 트리(확정 라벨) + 제목 키워드(제안 라벨) + 본문 텍스트 인덱스."""
import json
import os
import sys

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules
import win32com.client
from pypdf import PdfReader

TREE = r"C:\Users\PC1\Desktop\MAENG\논문주제"
ARCHIVE = r"C:\Users\PC1\Documents\MAENG_paper"
BASE = os.path.dirname(os.path.abspath(__file__))

# 트리 폴더명 -> 확정 라벨 (경로의 모든 폴더가 누적 적용됨)
FOLDER_LABELS = [
    ("cryogenic", ["Cryogenic"]),
    ("d-t-b", ["Ductile-brittle transition"]),
    ("model", ["Analytical model"]),
    ("orthogonal cutting", ["Orthogonal cutting"]),
    ("fem_fea", ["FEM"]),
    ("in situ", ["In-situ"]),
    ("in-sem", ["In-situ", "SEM"]),
    ("dic_piv", ["DIC", "PIV"]),
    ("thermal effect", ["Thermal effect"]),
    ("bue", ["BUE"]),
    ("bul", ["Built-up layer"]),
    ("textured tool", ["Textured tool"]),
    ("dmz", ["Dead metal zone"]),
    ("tribology", ["Friction"]),
    ("고전이론", ["Classical theory"]),
    ("난삭재", ["Difficult-to-cut"]),
    ("single crystal", ["Single crystal"]),
    ("실리콘", ["Silicon"]),
    ("티타늄", ["Titanium"]),
]


def labels_for_path(rel_dir):
    labels = []
    for part in rel_dir.split(os.sep):
        p = part.lower().strip()
        for key, lbs in FOLDER_LABELS:
            if key in p:
                for lb in lbs:
                    if lb not in labels:
                        labels.append(lb)
    return labels


def title_of(filename):
    stem = os.path.splitext(filename)[0]
    parts = stem.split("_", 3)
    return parts[3] if len(parts) == 4 else stem


def main():
    shell = win32com.client.Dispatch("WScript.Shell")
    confirmed = {}  # 파일명 -> [확정 라벨]
    for dirpath, dirs, files in os.walk(TREE):
        rel = os.path.relpath(dirpath, TREE)
        labels = labels_for_path(rel) if rel != "." else []
        for f in files:
            if not f.lower().endswith(".lnk"):
                continue
            try:
                target = shell.CreateShortCut(os.path.join(dirpath, f)).TargetPath
            except Exception:
                continue
            if not target:
                continue
            name = os.path.basename(target)
            cur = confirmed.setdefault(name, [])
            for lb in labels:
                if lb not in cur:
                    cur.append(lb)

    tags = {}
    for f in sorted(os.listdir(ARCHIVE)):
        if not f.lower().endswith(".pdf"):
            continue
        conf = confirmed.get(f, [])
        sugg = [s for s in rules.suggest_labels(title_of(f)) if s not in conf]
        tags[f] = {"labels": conf, "suggested": sugg}
    with open(os.path.join(BASE, "tags.json"), "w", encoding="utf-8") as fp:
        json.dump(tags, fp, ensure_ascii=False, indent=1)
    n_conf = sum(1 for v in tags.values() if v["labels"])
    print("라벨 시드 완료: 논문 {}편 / 트리에서 확정 라벨 받은 논문 {}편".format(len(tags), n_conf))

    # 본문 텍스트 인덱스 (본문 검색용)
    texts = {}
    for i, f in enumerate(sorted(tags)):
        path = os.path.join(ARCHIVE, f)
        try:
            reader = PdfReader(path)
            if reader.is_encrypted:
                reader.decrypt("")
            text = " ".join((p.extract_text() or "") for p in reader.pages)
            texts[f] = " ".join(text.lower().split())
        except Exception as e:
            texts[f] = ""
            print("본문 추출 실패:", f[:50], "-", e)
    with open(os.path.join(BASE, "text_index.json"), "w", encoding="utf-8") as fp:
        json.dump(texts, fp, ensure_ascii=False)
    print("본문 인덱스 완료: {}편".format(len(texts)))


if __name__ == "__main__":
    main()
