# Athenaeum

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
- Python 패키지: `pymupdf`, `pypdf`, `requests`, `pywin32` (바로가기·PPT 처리)
- [Claude Code CLI](https://claude.com/claude-code) 설치 후 `claude` 로그인 (요약·번역·검토·도식은 Claude 구독 사용량을 씁니다. 별도 API 키 없음)
- Microsoft Edge (도식 PNG 렌더링에 headless 로 사용)

## 실행

```
paper-search\Athenaeum_실행.bat
```
서버가 `http://localhost:8770` 에 뜨고 브라우저가 앱 창으로 열립니다. 시작프로그램에 `Athenaeum_시작.vbs` 를 넣으면 부팅 때 자동 실행됩니다.

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
