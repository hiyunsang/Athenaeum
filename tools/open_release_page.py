# -*- coding: utf-8 -*-
"""릴리스 페이지 열기 — GitHub Releases 에 새 판을 올리는 마지막 손질을 돕는다.

    python tools\\open_release_page.py            판은 paper-search\\VERSION 에서 읽는다
    python tools\\open_release_page.py 1.1.0      판을 직접 적는다
    python tools\\open_release_page.py --dry      아무것도 열지 않고 무엇을 할지만 적는다

하는 일
  ① dist\\릴리스노트_v<판>.md 를 릴리스 본문 꼴로 다듬어 클립보드에 복사한다
     (GitHub 의 새 릴리스 페이지는 tag·title·body 를 URL 로 받지만, 본문 2만 자 넘는 URL 은 414 로 거절한다 — 그래서 클립보드)
  ② 탐색기에서 그 판의 포터블 ZIP 을 골라 둔다 (dist\\Athenaeum-portable-*-v<판>.zip 가운데 가장 새것)
  ③ 기본 브라우저로 GitHub 의 새 릴리스 페이지를 연다 — 태그와 제목은 채워져 있다
그 뒤는 사람이 한다: 본문 칸에 붙이고(Ctrl+V), 탐색기의 ZIP 을 끌어다 놓고, Publish release.

태그를 옮기거나 force push 하는 일은 여기서 하지 않는다 — 태그가 어디를 가리키는지만 보여 주고,
원격 태그가 로컬과 다르면 알려 준다.
"""
import glob
import io
import os
import subprocess
import sys
import tempfile
import urllib.parse
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "hiyunsang/Athenaeum"
NO_WINDOW = 0x08000000


def version_of(argv):
    for a in argv:
        if not a.startswith("--"):
            return a.lstrip("vV")
    p = os.path.join(ROOT, "paper-search", "VERSION")
    return io.open(p, encoding="utf-8").read().strip()


def release_body(ver):
    """릴리스 노트 파일 → 본문. 첫 줄이 '1.1.0 — 요약' 꼴이면 요약을 굵게 맨 위에 두고, 나머지는 그대로."""
    p = os.path.join(ROOT, "dist", "릴리스노트_v%s.md" % ver)
    if not os.path.exists(p):
        return p, ""
    text = io.open(p, encoding="utf-8").read()
    first, _, rest = text.partition("\n")
    if "—" in first:
        summary = first.split("—", 1)[1].strip()
        return p, "**%s**\n\n%s" % (summary, rest.lstrip("\n"))
    return p, text


def newest_zip(ver):
    cands = glob.glob(os.path.join(ROOT, "dist", "Athenaeum-portable-*-v%s.zip" % ver))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def git(*args):
    try:
        r = subprocess.run(["git"] + list(args), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        return r.stdout.decode("utf-8", "replace").strip()
    except Exception as e:
        return "? (%s)" % e


def tag_state(ver):
    tag = "v" + ver
    local = git("rev-parse", "--short", tag + "^{commit}") or "없음"
    head = git("rev-parse", "--short", "HEAD")
    remote = "없음"
    for line in git("ls-remote", "--tags", "origin").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] in ("refs/tags/%s^{}" % tag, "refs/tags/" + tag):
            remote = git("rev-parse", "--short", parts[0]) if parts[1].endswith("^{}") else remote
            if parts[1] == "refs/tags/" + tag and remote == "없음":
                remote = git("rev-parse", "--short", parts[0] + "^{commit}") or parts[0][:7]
    return tag, local, remote, head


def to_clipboard(text):
    fd, tmp = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    io.open(tmp, "w", encoding="utf-8", newline="\n").write(text)
    try:
        cmd = "Get-Content -Raw -Encoding UTF8 -LiteralPath '%s' | Set-Clipboard" % tmp.replace("'", "''")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=30, creationflags=NO_WINDOW)
        return r.returncode == 0, r.stderr.decode("utf-8", "replace").strip()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    dry = "--dry" in sys.argv
    ver = version_of(sys.argv[1:])
    tag, local, remote, head = tag_state(ver)
    notes_path, body = release_body(ver)
    zip_path = newest_zip(ver)
    url = "https://github.com/%s/releases/new?%s" % (REPO, urllib.parse.urlencode(
        {"tag": tag, "title": "Athenaeum v" + ver}, quote_via=urllib.parse.quote))

    print("판 %s · 태그 %s: 로컬 %s · 원격 %s · HEAD %s" % (ver, tag, local, remote, head))
    if local != remote:
        print("  ! 원격 태그가 로컬과 다르다 — 올리기 전에: git push --force origin refs/tags/%s" % tag)
    if local != head:
        print("  ! 로컬 태그가 HEAD 가 아니다 — HEAD 로 옮기려면: git tag -f %s && git push --force origin refs/tags/%s" % (tag, tag))
    print("릴리스 노트: %s%s" % (notes_path, "" if body else " (없음 — 본문은 비어 간다)"))
    print("ZIP: %s" % (zip_path or "없음 — dist 에 이 판의 포터블 ZIP 이 없다 (python tools\\make_release.py)"))
    if zip_path:
        print("  크기 %.1f MB" % (os.path.getsize(zip_path) / 1e6))
    print("릴리스 페이지: %s" % url)
    if dry:
        print("(--dry: 클립보드·탐색기·브라우저는 건드리지 않았다)")
        return 0

    if body:
        ok, err = to_clipboard(body)
        print("본문 %s자를 클립보드에 %s" % (len(body), "복사했다" if ok else "복사하지 못했다: " + err))
    if zip_path:
        subprocess.Popen(["explorer", "/select," + os.path.normpath(zip_path)])
    webbrowser.open(url)
    print()
    print("브라우저의 릴리스 페이지에서: 본문 칸에 Ctrl+V → 탐색기에서 골라 둔 ZIP 을 'Attach binaries' 자리에 끌어다 놓기 → Publish release")
    return 0


if __name__ == "__main__":
    sys.exit(main())
