# Athenaeum — 개발 안내 (Claude Code 가 자동으로 읽는 파일)

연구실용 **로컬 논문 작업대**. 논문을 모으고(수집) → 찾고(검색·탐색) → 읽고(요약·전문 번역·질문) → 쓰는(원고) 일과 단어장을 한 프로그램에서 한다.
Windows 전용, 로컬 서버(`http://localhost:8770`) + 브라우저 화면. 데이터는 전부 로컬 폴더에만 있다.

> 이 저장소를 이어받는 Claude 에게: 저장소 루트에 **`인수인계.md`**(비공개, git 제외)가 있으면 반드시 먼저 읽어라.
> 사용자 성향·진행 중인 일·과거 사고가 거기 있다. 이 파일은 코드 구조와 개발 규칙만 담는다.

사용자와의 대화·UI 문구·커밋 메시지·코드 주석은 **한국어**로 쓴다.

---

## 1. 구조

```
MAENG_paper\                  저장소 루트 (github.com/hiyunsang/Athenaeum, 공개)
  paper-search\               프로그램
    server.py        (2.5k줄) 표준 라이브러리 ThreadingHTTPServer. 라우트·PDF 추출·요약/번역 생성·라벨링·탐색·읽기 기록
    intake.py        (1.0k줄) 수집: Downloads 감시 → DOI → Crossref → '연도_저널약어_저자_제목.pdf' 로 보관
    manuscript.py    (1.3k줄) 원고: 근거 카드·개요·초안·검토·영문화·.docx 입출력·교수님 주석 논의·도식
    vocab.py         (360줄)  단어장: 논문에서 담기·목록 담기·Anki 내보내기
    mapper.py                  관련 논문 맵 (OpenAlex)
    rules.py                   규칙 기반 라벨 분류 (Claude 실패 시 폴백)
    batch_generate.py          일괄 요약·번역 (대기열 JSON)
    schematic\                 도식 그리기 (lib.py 그리기 함수, generate.py 가 Claude 에게 스크립트를 쓰게 함)
    index.html  reader.html  explore.html  mapview.html  manuscript.html  labels.html  vocab.html
    ui.css  ui.js              공통 디자인 토큰·환경설정·진행 막대
    labels.json                라벨 체계 (묶음·하위 분류 트리·자동 추가 기록 = 예약 키 "_체계")
    tags.json                  논문별 라벨 {labels, suggested, rejected, title}
    수집설정.json              수집 설정 (감시폴더·저널약어 등)
    logo\make_logo.py          로고·아이콘 생성
    Athenaeum_실행.bat         실행 (서버 + 앱 창)
    Athenaeum_시작.vbs         부팅 자동 시작
  tools\
    restart_server.py          서버 재시작 (진행 중 작업 보호)
    make_release.py            포터블 배포판 ZIP (파이썬 동봉)
  논문모음\ 번역\ 메모\ 원고\ 단어장\ 관련맵\ 수집\ 백업\ dist\    데이터 — 전부 .gitignore
```

모듈 연결 방식: `server.py` 가 `manuscript`·`vocab`·`intake` 를 import 하고 `init(**cfg)` 로 경로·`load_json`·`save_json`·`claude` 함수를 **주입**한다(순환 import 회피). 각 모듈은 `handle_get(h, url)` / `handle_post(h, body)` 를 제공하고 서버가 경로 접두사로 넘긴다.

### 데이터 파일
| 파일 | 내용 |
|---|---|
| `번역\<stem>.요약.md` | 요약 |
| `번역\<stem>.번역.md` | 전문 번역. 문장마다 `[sN]` 표식 |
| `번역\<stem>.번역.정렬.json` | `N → {t: 원문 문장, p: 쪽(0부터), para: 원문 문단 번호}` |
| `번역\<stem>.캡션.json` | 그림·표 캡션 번역 |
| `번역\수식\<stem>\pN_K.png` | 수식 크롭 이미지. 번역에 `[[EQ:pN_K.png]]` |
| `메모\<stem>.json` | 메모·질문답변 |
| `원고\<id>.json` | 원고 |
| `단어장\단어.json` | 단어장 |
| `읽기기록.json` | 읽은 시각·시간·위치 |

### 주요 라우트
GET: `/` `/view`(읽기) `/explore` `/mapview` `/ms`(원고) `/labels`(라벨 체계) `/vocab`(단어장) `/pdf` `/eq` · `/api/data` `/api/jobs` `/api/genstatus` `/api/align` `/api/captions` `/api/intake` `/api/labels` `/api/explore` `/api/readlog` 등
POST: `/api/generate`(요약·번역) `/api/ask`(질문) `/api/tags` `/api/labels`(체계 편집·제안·재분류) `/api/intake` `/api/smart` `/api/explore_chat` `/api/read_ping` · 접두사 `/api/ms*` `/api/vocab*`

---

## 2. 실행·재시작·시험

- 실행: `paper-search\Athenaeum_실행.bat` (포터블판은 동봉 `python\` 을, 아니면 PATH 의 pythonw 를 씀)
- **코드를 고친 뒤 반영**: `python tools\restart_server.py` — 진행 중인 요약·번역이 있으면 멈추지 않고 알려 준다. 끊어도 되면 `--force`
- 다른 포트로 시험: 환경변수 `ATHENAEUM_PORT=8790`, 브라우저를 안 띄우려면 `ATHENAEUM_NOBROWSER=1`
- Python: 개발 PC 는 3.8.6, 포터블판은 3.11.9. 패키지는 `paper-search\requirements.txt` (pymupdf·pypdf·requests·pywin32·numpy·scipy·fonttools)
- 요약·번역·검토는 `claude -p --model opus` 를 subprocess 로 부른다(사용자 Claude 구독 사용량, API 키 없음). 반드시 `_no_window()` 를 넘길 것 — 안 그러면 pythonw 가 콘솔 창을 띄워 사용자 타이핑을 끊는다
- 포터블판 만들기: `python tools\make_release.py` → `dist\Athenaeum-portable-YYYYMMDD.zip`

### 시험할 때 반드시
- 읽기 화면을 시험으로 열 때 URL 에 **`&nolog=1`** — 안 붙이면 사용자 읽기 기록이 오염된다
- **일괄 생성이 도는 중에는 서버를 재시작하지 않는다** (작업이 끊겨 처음부터 다시 만든다). `restart_server.py` 가 막아 준다
- 브라우저로 페이지를 검증하는 도중에 서버를 재시작하지 않는다 (빈 화면을 검사하게 됨)
- 파일명은 `ls` 로 확인한 뒤 URL 에 넣는다 (80자 잘림·비슷한 제목 착오)

---

## 3. 이 PC 개발 환경의 함정

- **PowerShell 도구는 단순 명령에도 행**이 걸린다 → Bash(Git Bash) 사용
- **한글 경로를 Git Bash 인자로 넘기면 깨진다** → 파이썬 스크립트나 Read/Glob 도구로
- Bash heredoc 은 백슬래시를 뭉갠다(`\b` → 백스페이스, `\v` → 세로탭). **백슬래시·따옴표 많은 패치는 Write 도구로 .py 파일을 만들어 실행.** 파이썬 heredoc 첫 줄에 `# -*- coding: utf-8 -*-`
- Git Bash 의 `tasklist /FI` 는 `/FI` 가 경로로 바뀌어 깨짐. 프로세스 조회는 파이썬에서 `powershell -NoProfile -Command "Get-CimInstance Win32_Process ..."`
- Git Bash 에서 cmd 를 부르면 GNU `timeout` 이 Windows `timeout.exe` 를 가린다 → 대기는 `python -c "import time;time.sleep(1)"`
- 상시 프로세스는 `subprocess.Popen(creationflags=0x8|0x200|0x08000000)` 로 띄워야 세션이 끝나도 산다
- 샌드박스에서 `%LOCALAPPDATA%` 가 가상화돼 사용자 프로세스와 다른 파일을 본다 → 데이터는 Documents 아래에
- Claude 내장 브라우저는 requestAnimationFrame 이 안 돈다(스크롤 이벤트·smooth scroll 안 옴). 로직은 `dispatchEvent` 로 검증
- 내장 브라우저 탭이 숨겨진 채 렌더되면 폭이 0 → 자동 높이(autogrow) 계산이 수만 px 로 폭발. 코드에서 `clientWidth<80` 이면 건너뛸 것

### 배치 파일(.bat) 규칙
1. **내용은 ASCII 만.** 시스템 코드페이지로 읽혀 UTF-8 한글이 깨진다(한글 이름 bat 을 부르려면 ASCII 이름 사본을 만들어 호출 — 포터블판의 `run.bat`)
2. `if ( ... )` 블록 안의 echo 문장에 **괄호 금지** — 블록이 닫혀 "예상되지 않았습니다" 로 죽는다
3. 시험할 때 `start` 로 띄운 서버가 부모 파이프를 물면 `subprocess.run(capture_output=True)` 가 영원히 기다린다 → 파일로 리다이렉트
4. 다른 컴퓨터용 변경은 **ZIP 을 풀어 맨 위 bat 을 cmd 로 실제 실행**해 본 뒤 배포

---

## 4. 코드의 핵심과 과거에 깨졌던 곳

### PDF 추출 (`server.py`: `pdf_blocks` → `pdf_body_and_asides` → `paper_text`)
PyMuPDF `get_text("dict")` 의 블록·줄·span 과 `get_drawings()` 로 그림 영역을 얻어 본문과 곁텍스트(캡션·표·그림 속 글자·수식 부스러기·기호표)를 가른다. 곁텍스트는 본문 뒤 `Figures and Tables` 로 모인다. 2단 편집은 **문서 전체 기준**으로 판정.
- **문단 나누기**: 블록 안에서 "앞 줄이 문장부호로 끝나고 이 줄이 9pt 이상 들여쓰기" 면 새 문단. **일부 PDF(옛 Elsevier 등)는 낱말마다 `line` 을 준다** → 같은 baseline 조각을 먼저 한 줄로 합치고, 들여쓰기 기준은 최솟값이 아니라 가장 흔한 왼쪽 끝. 이것을 빼면 문장마다 문단이 끊긴다
- 규칙을 하나 고칠 때마다 여러 논문으로 회귀 확인. 저널 PDF 는 그림·표·캡션·각주·러닝헤드·수식·기호표가 본문 흐름에 섞여 들어온다
- 정규식 함정: `Nomenclature` 만 잡으면 `Nomenclatures` 를 놓친다 → `Nomenclatures?`
- PyMuPDF 는 SVG 그라데이션을 못 그린다(검게 나옴) → PNG 는 Edge headless (`msedge --headless=new --screenshot`)

### 번역 생성 (`generate_document`)
원문을 구간으로 나눔(`prepare_chunks`·`split_chunks`) → `number_chunks` 가 문장마다 `[sN]` 과 원문 문단 번호를 매겨 정렬표 저장 → 구간 병렬로 Claude 호출(`_chunk_sem`) → 표식 검증·재시도 → `rebuild_paragraphs` 가 번역문을 **원문 문단 번호대로 다시 묶는다**. 읽기 화면은 `[sN]` 을 숨긴 `span.sent[data-s]` 로 바꿔 드래그 즉시 PDF 원문 문장을 강조한다.
- 번역 품질을 잴 때 **"한 문장짜리 문단 비율"은 쓰지 말 것** — 뒤에 붙는 Figures and Tables 의 캡션·표 줄 때문에 멀쩡한 번역도 나빠 보인다. 쓸 지표는 **"2문장 이상 문단에 속한 문장의 비율"** (서재 평균 약 70%, 수식·표가 많은 논문은 50% 아래가 정상)

### 요약
라벨 `Review` 나 제목(review/survey/advances in…)으로 리뷰/연구를 판정해 프롬프트가 다르다. 리뷰 = 이 리뷰에 무엇이 어디 있나 / 연구 = 무엇을 해서 무엇을 봤나. 맨 위 한줄 요약·노벨티와 기여·핵심 결과·읽을 가치·한계, 초록은 반드시 포함.

### 수집 (`intake.py`) — **사용자 파일을 옮기는 코드. 가장 조심할 곳**
- 논문으로 판정하는 근거는 **결정적인 것만**: PDF 본문의 DOI, 또는 파일명의 Elsevier PII(`1-s2.0-S…` → DOI). **DOI 실마리가 없으면 절대 논문으로 보지 않는다**
- 채택 전 **제목 대조**: Crossref 제목 단어의 50% 이상이 PDF 첫 2쪽에 있어야 한다(본문에 인용된 남의 DOI 오인 방지)
- **본문 문장으로 Crossref 검색하는 방식은 금지.** 과거에 견적서·공지 PDF 수십 개를 엉뚱한 논문으로 판정해 옮긴 사고가 두 번 있었다
- 판정 규칙을 고치면 **서버 재시작 전에** 건너뜀 목록의 파일 몇 개로 `intake.resolve_meta()` 를 오프라인 시험. 재시작하면 수집이 바로 다운로드 폴더를 다시 훑는다
- 파일을 지우는 코드는 없다. 이동·이름 변경만 하고 `수집\수집기록.txt` 에 남긴다. 재다운로드는 `논문모음\_중복사본\` 으로

### 라벨
새 논문은 들어올 때 `classify_with_claude` 가 분류하고, 카탈로그에 없는 핵심 재료·공정은 `apply_new_labels` 가 알맞은 묶음·하위 분류 아래 새 라벨로 추가한다(검사: 존재하는 묶음, 고정 묶음 제외, 영문 30자, 최대 2개). 체계 편집·Claude 체계 제안·전체 재분류는 `/labels` 창.
- **배포판은 빈 체계**(고정 묶음 `유형` 만)로 나간다 — 원작자의 기계가공 체계를 남에게 주지 않는다. 빈 체계인 동안(`catalog_is_initial`)은 새 논문에 Claude 분류를 걸지 않는다(제목만 기록). 논문이 10편 이상이면 홈에 "체계를 만들자" 권유(`catalog_nudge` → `/api/data.catalog_nudge`), 체계를 적용하거나 전체 재분류를 하면 그때 편수를 `_체계.분류기준편수` 에 적고, 그보다 `max(10, 기준/2)` 편 이상 늘면 "다시 분류" 권유. 홈의 "나중에" 는 `_체계.알림보류편수`.

### 탐색 (`_run_smart`)
말로 적은 주제(한국어 가능) → `plan_search` 가 Claude 로 **검색 계획**(개념 2~5개, 개념마다 영어 동의어·약어 3~8개, 필수/선택, 제외어) → `build_boolean` 이 OpenAlex `title_and_abstract.search` 불리언식 `("built-up edge" OR BUE) AND ("in situ" OR …) NOT (…)` 로 → **검색 사다리**(전체 → 필수만 → 필수 하나씩 뺀 것) 결과를 앞 단계 우선으로 합침(≤150) → Claude 가 필수 개념을 실제로 다루지 않는 것을 제외하고 소주제로 묶으며 선택 개념의 `hits`(LPBF, DSS …)를 붙임. 결과의 `plan` 을 화면에서 고쳐 `POST /api/smart {plan}` 으로 다시 검색(계획 단계 생략). OpenAlex 불리언은 AND/OR/NOT/따옴표/괄호만 되고 와일드카드(`*`)는 400.

### 외부 API
- OpenAlex: 과거에 검색 수백 회를 몰아 보내 429 가 계속된 적이 있다. **일괄 작업은 search 대신 DOI/ID 조회로**, polite pool(mailto), 요청 간격, 연속 실패 시 회로 차단기가 들어 있다
- **OpenAlex 일일 한도(2026-09-22 발견)**: 키 없는 요청은 같은 네트워크(IP)의 모두가 나눠 쓰는 무료 일일 한도에 걸린다(429 본문에 `Insufficient budget`, 자정 UTC 리셋). `mapper._get` 은 이 429 를 보면 재시도하지 않고 리셋 때까지 `_breaker["until"]` 로 바로 실패시키며 안내문(`budget_message`)을 돌려준다. **API 키**(무료, https://help.openalex.org/api/authentication/)는 `paper-search\설정.json` 의 `openalex_api_key` — ⚙ 환경설정에서 입력(`/api/settings`), git·배포판 제외. 키를 넣으면 차단이 풀리고 모든 요청에 `api_key` 가 붙는다. 홈의 "맵 서비스" 표시가 한도 소진을 알린다
- Crossref: User-Agent 지정, 타임아웃

---

## 5. 화면·디자인 규칙

- **새 UI 는 `ui.css` 의 토큰만** 쓴다. 하드코딩 색·반경 금지, 장식·AI 이모지 금지
- 성격: "밀도 높은 전문 연구 워크스테이션" (참고: Linear, Readwise Reader). 카드·알약·버튼 남용 대신 1px 구분선 목록, 동작은 고스트 텍스트(`.act`), 강조색은 파랑 하나(`--accent`), 앰버 = 불확실·제안, 초록 = 정상
- 글꼴: 본문·UI = Pretendard(`paper-search\fonts\` 동봉), **논문 제목·읽기 절 제목만 세리프**(`--font-serif`)
- 다크 모드: `:root[data-theme=dark]` 토큰. 환경설정(⚙)은 `ui.js` 의 `openSettings`
- 로고: 앤티크 골드 `#C9A227` 스와시 A. `logo\make_logo.py` 로 재생성. 한 SVG path 안의 조각은 회전 방향을 같게(아니면 겹친 곳이 구멍남)
- 모든 기다림은 **예상 시간 기반 진행 막대**(`ui.js`: `progressInfo`·`pbarHtml`·`animateBars`)
- 사용자에게 보이는 판단 표시("읽음/안 읽음" 등)는 하지 않는다. 시각·시간·위치 같은 사실만

---

## 6. 커밋·배포

- 기능 단위로 커밋, 메시지는 한국어로 **무엇을 왜** 바꿨는지. 커밋 후 `git push origin main`
- 저장소는 **공개**다. 개인 데이터(논문·요약·번역·메모·원고·단어장·수집 기록·`인수인계.md`)는 절대 커밋하지 않는다(.gitignore 확인)
- 포터블 ZIP 사용자는 코드가 그 시점에 얼어 있다. **ZIP 은 사용자가 "새 ZIP 올려서 릴리스하자" 고 할 때만 만든다** — 코드를 고칠 때마다 `make_release.py` 를 돌리지 말 것(사용자 지시, 2026-09-22). 릴리스 때: `paper-search\VERSION` 올림 → `make_release.py` → ZIP 을 **풀어서 `Athenaeum 설치.bat --test <폴더>`·실행·삭제 bat 을 실제로 돌려 본 뒤** → 릴리스 노트 초안 → 사용자가 GitHub Releases 에 새 태그(`v0.9.2` 식)로 올림(웹에서, gh CLI 없음)
- 포터블판 맨 위 bat 3개: `설치`(setup.py — 기능 안내·기존 설치 업데이트/정리·Claude Code 확인·바탕화면 아이콘·자동 시작) · `실행` · `삭제`(uninstall.bat — 서버 끄고 바로가기 제거, 데이터는 안 지움). 설치 도우미의 업데이트는 기존 폴더의 프로그램 파일(paper-search 의 데이터 파일 제외·python·맨 위 bat)만 바꾸고, `.git` 이 있는 폴더(개발용)는 건드리지 않는다. 설치 기록은 `%APPDATA%\Athenaeum\install.json`
- 코드를 바꿔도 **이미 만든 요약·번역은 그대로**다. 필요하면 읽기 화면 ↻ 다시 생성 또는 `batch_generate.py` 로 재생성
- 라이선스 MIT
