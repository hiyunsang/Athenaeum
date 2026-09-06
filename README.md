# Athenaeum

[github.com/hiyunsang/Athenaeum](https://github.com/hiyunsang/Athenaeum)

연구실 논문 작업대 — 논문을 **모으고(수집) → 찾고(검색·탐색) → 읽고(요약·전문 번역·질문) → 쓰는(원고)** 일을 한 화면에서 합니다.
로컬 컴퓨터에서 혼자 돌아가는 프로그램이고, 논문 PDF·요약·번역·원고는 전부 내 컴퓨터 폴더에만 저장됩니다.

*A local paper workstation for a research lab: collect PDFs automatically, search by labels, read with Claude-made summaries and sentence-aligned translations, and draft manuscripts on evidence cards. Everything stays on your machine.*

## 무엇을 하나

| 단계 | 기능 |
|---|---|
| 수집 | 다운로드 폴더를 지켜보다 새 논문 PDF 를 `연도_저널약어_저자_제목.pdf` 로 이름 바꿔 보관 (DOI → Crossref). 논문이 아닌 PDF 는 건드리지 않음 |
| 검색 | 라벨 교집합 검색(저널·유형·재료·공정·현상·방법…), 본문 검색, 라벨 자동 부여(Claude) |
| 탐색 | OpenAlex 전체 학술 DB 주제 검색, 스마트 탐색(검색어 확장·선별·소주제 분류), 관련 논문 맵, 결과를 놓고 Claude 와 대화 |
| 읽기 | 요약(리뷰/연구 논문 구분, 초록 포함), 문장 단위로 원문과 맞춰진 전문 번역(수식·첨자 유지), 문장 드래그 → PDF 원문 표시, 메모·질문, 캡션 번역 |
| 원고 | 근거 카드(출처 자동) → 개요 → 문단 모드(주장·카드·초안·검토·영문) / 원고 모드(이어 쓰기) → 규격 점검 → .docx. 워드 원고 가져오기, 교수님 주석 논의, 도식 만들기 |

자세한 사용법은 [Athenaeum 사용법.md](Athenaeum%20사용법.md).

## 필요한 것

- Windows 10/11, Python 3.8 이상
- Python 패키지: `pymupdf`, `pypdf`, `requests`, `pywin32`, `numpy`, `scipy` — `pip install -r paper-search\requirements.txt`
- [Claude Code CLI](https://claude.com/claude-code) 설치 후 `claude` 로그인 (요약·번역·검토·도식은 Claude 구독 사용량을 씁니다. 별도 API 키 없음)
- Microsoft Edge (도식 PNG 렌더링에 headless 로 사용)

## 가장 쉬운 설치: 포터블판 (파이썬 없이)

[Releases](https://github.com/hiyunsang/Athenaeum/releases) 에서 `Athenaeum-portable-*.zip`(약 100MB) 을 받아 원하는 곳에 풀고 **`Athenaeum 실행.bat`** 을 더블클릭하면 끝입니다. 파이썬과 필요한 패키지가 `python\` 폴더에 들어 있어 따로 설치할 것이 없습니다. 단 하나, 요약·번역·검토에 쓰는 [Claude Code](https://claude.com/claude-code) 는 설치하고 `claude` → `/login` 으로 한 번 로그인해야 합니다.
논문·요약·번역·원고는 모두 그 폴더 안(`논문모음`, `번역`, `원고` …)에 저장되므로 폴더를 통째로 옮기거나 백업하면 됩니다. 포터블판은 `python tools\make_release.py` 로 만듭니다.

## 직접 설치 (파이썬이 있는 경우)

1. 이 저장소를 내려받습니다 (Code → Download ZIP, 또는 `git clone`). 폴더 이름은 자유입니다. 그 폴더 안에 `논문모음`(PDF 보관소) 등 데이터 폴더가 첫 실행 때 만들어집니다.
2. Python 3.8 이상을 설치할 때 **Add to PATH** 를 켭니다. 그다음 터미널에서
   ```
   pip install -r paper-search\requirements.txt
   ```
3. [Claude Code](https://claude.com/claude-code) 를 설치하고 터미널에서 `claude` → `/login` 으로 한 번 로그인합니다. (요약·번역·검토는 이 로그인의 Claude 구독 사용량을 씁니다. 별도 API 키 없음)
4. `paper-search\Athenaeum_실행.bat` 을 더블클릭하면 브라우저에 화면이 뜹니다. 홈 위쪽 **수집** 패널에서 감시 폴더(기본: 내 다운로드 폴더)를 확인하고, **라벨 체계 설정 ↗** 에서 내 분야에 맞는 체계를 Claude 에게 제안받아 적용합니다.
5. 다운로드 폴더에 논문 PDF 를 받으면 자동으로 이름이 정리되어 `논문모음` 으로 들어오고, 라벨이 자동으로 붙습니다.

알아둘 것: Windows 전용입니다(바로가기·PPT·Edge 사용). `labels.json` 은 기계가공 분야 예시 체계이고, `tags.json` 에는 원작자의 라벨 데이터가 들어 있지만 내 `논문모음` 에 없는 논문은 무시되므로 지워도 되고 두어도 됩니다.

## 실행

```
paper-search\Athenaeum_실행.bat
```
서버가 `http://localhost:8770` 에 뜨고 브라우저가 앱 창으로 열립니다.
부팅 때 자동으로 서버를 켜려면 `paper-search\Athenaeum_시작.vbs` 의 **바로가기**를 시작프로그램 폴더(`Win+R` → `shell:startup`)에 넣습니다. 창 없이 조용히 돕니다.

## 폴더

```
MAENG_paper\                ← 저장소 루트 (이름은 자유)
  paper-search\             ← 프로그램 (이 저장소에 있는 것)
    server.py               서버 (표준 라이브러리 HTTP)
    intake.py               수집 (다운로드 감시 → Crossref → 보관)
    manuscript.py, manuscript.html   원고
    reader.html, index.html, explore.html, mapview.html
    schematic\              도식 그리기 라이브러리
    logo\                   로고·아이콘 (make_logo.py 로 재생성)
    수집설정.json           수집 설정 (감시 폴더·저널 약어)
    labels.json, tags.json  라벨 체계·라벨 데이터
  논문모음\                 ← PDF 보관소 (저장소에 안 올림)
  번역\  메모\  원고\  관련맵\  수집\   ← 생성물·개인 자료 (저장소에 안 올림)
```

## 데이터와 개인정보

논문 PDF, 요약·번역, 메모, 원고, 다운로드 기록은 `.gitignore` 로 저장소에서 제외됩니다. 저장소에는 코드와 라벨 체계만 있습니다.

## 라이선스

MIT License. 자유롭게 쓰고 고치고 나눌 수 있습니다. 저작권 표시만 남겨 주세요.
함께 담긴 것들은 각자의 라이선스를 따릅니다: Pretendard 글꼴(SIL OFL 1.1, `paper-search/fonts/Pretendard-LICENSE.txt`), 포터블판의 Python 과 `requirements.txt` 패키지들.
