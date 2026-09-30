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
    manuscript.py    (2.7k줄) 원고: 근거 카드·개요·초안·검토·영문화·.docx 입출력·교수님 주석 논의·도식·본보기·리비전·워드 반영
    mathtex.py       (0.8k줄) 수식: LaTeX(부분집합) → MathML(화면 미리보기)·OMML(워드 수식), OMML → LaTeX(워드에서 가져오기)
    wordsync.py      (0.5k줄) 워드 문단 ↔ 글 변환(가져오기와 공유), 바뀐 글만 원본 문단에 갈아 끼우기
    vocab.py         (360줄)  단어장: 논문에서 담기·목록 담기·Anki 내보내기
    mapper.py                  관련 논문 맵 (OpenAlex)
    rules.py                   규칙 기반 라벨 분류 (Claude 실패 시 폴백)
    batch_generate.py          일괄 요약·번역 (대기열 JSON)
    schematic\                 도식 그리기 (lib.py 그리기 함수, generate.py 가 Claude 에게 스크립트를 쓰게 함)
    index.html  reader.html  explore.html  mapview.html  manuscript.html  labels.html  vocab.html
    ui.css  ui.js              공통 디자인 토큰·환경설정·진행 막대
    map.js                     논문 맵 그리기 (탐색 결과 맵·관련맵 공유)
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
- **업데이트 뒤 옛 서버가 남는 문제(2026-09-29, 친구 PC 에서 '변한 게 없다')**: 실행 bat 이 서버를 켜기 전에 `launch_check.py` 를 불러 같은 포트의 **다른 판** 서버(옛 판은 `/api/version` 이 없어 `?`)를 `Get-NetTCPConnection` 으로 찾아 끈다(명령줄에 server.py 가 있는 것만). 서버는 판을 **켤 때 한 번만** 읽는다(`APP_VERSION`) — 파일이 바뀌어도 옛 프로세스는 옛 판을 알려야 교체된다. 설치 도우미의 업데이트도 `stop_port(8770)` 로 한 번 더 끈다. 어느 판이 도는지는 ⚙ 환경설정 머리에 표시
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
- 원고 시험은 사본(`원고\ms_test_*.json`)으로만. 끝나고 지울 때는 **내가 만든 파일의 정확한 경로만** — `*시험*` 같은 와일드카드로 지우다 사용자의 옛 내보내기 파일 두 개를 같이 지운 적이 있다(2026-09-30)
- 브라우저로 원고 화면을 시험할 때 `openDoc` 뒤 `doc.id` 가 시험 사본인지 확인하고 진행(불러오기에 실패하면 화면이 실제 원고를 연다)

---

## 3. 이 PC 개발 환경의 함정

- **PowerShell 도구는 단순 명령에도 행**이 걸린다 → Bash(Git Bash) 사용
- **한글 경로를 Git Bash 인자로 넘기면 깨진다** → 파이썬 스크립트나 Read/Glob 도구로
- Bash heredoc 은 백슬래시를 뭉갠다(`\b` → 백스페이스, `\v` → 세로탭). **백슬래시·따옴표 많은 패치는 Write 도구로 .py 파일을 만들어 실행.** 파이썬 heredoc 첫 줄에 `# -*- coding: utf-8 -*-`
- 이 함정에 실제로 두 번 당했다(탐색의 AND/OR/NOT 낱말 경계, 원고의 키워드 정규식 — 백슬래시-b 자리에 백스페이스 문자가 들어가 정규식이 조용히 안 맞음). 패치 뒤 파일에 `chr(8)` 이 0 개인지 확인할 것
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
- **탐색은 항상 따로 큰 창**(홈 「논문 탐색」 → `window.open` 1280×900, 이미 열려 있으면 앞으로). 홈 옆 분할창(iframe)은 목록 재배치로 끊기고 버튼이 헷갈려 없앴다(2026-09-23)
- **결과 맵**(`map.js` 의 `renderResultMap(m, host, opts)` — 탐색 결과 맵과 **관련맵(`mapview.html`)이 공유**, CSS 는 `ui.css` 의 '논문 맵' 절. 관련맵은 `fixedColor: "year"`(색 = 연도 한 가지, 사용자 선택 2026-09-25)·`noCohesion`·시드 노드(`seed: true` → 한가운데·굵은 검은 테두리), 뿌리·후속 논문은 맵 아래 목록; 캔버스): `results_map` 이 준 노드·선·유사도로 배치(관계대로 ↔ 소주제로 슬라이더; 관계대로일 땐 연결 강도로 반지름을 정해 연결 많은 논문이 가운데). 색 = 소주제(같은 소주제 안에서 최근일수록 진하게, 「색: 연도」 토글), 원 크기 = 피인용, 겹침 허용, 처음엔 튀어나오는 애니메이션, 휠 확대·빈 곳 드래그 이동, 원을 누르면 오른쪽 정보 패널(초록은 노드의 `abstract`), 아래 오른쪽 연도 띠. 팔레트 상수는 `mapColor` 안에 둔다(복원이 선언보다 먼저 불러 전역 const 는 TDZ 오류). 내장 브라우저는 rAF 가 안 돌아 애니메이션은 setTimeout 폴백으로 끝 상태를 그린다
- **초록**: OpenAlex 는 Elsevier(ScienceDirect) 논문의 초록을 안 준다(Crossref·Semantic Scholar 도 없음, 2026-09-24 확인). 정보 패널의 「찾기」·요약·번역은 초록이 없으면 `find_abstract` 로 보유 PDF 첫 2쪽(`a b s t r a c t` 띄어쓰기 포함) → Scopus(키가 있으면) → Crossref → Semantic Scholar 순으로 찾고. **Elsevier 무료 API 키**(환경설정 `elsevier_api_key`, `설정.json`)로 되는 것: Scopus 초록 API `/content/abstract/doi/` (모든 출판사 초록·저자 키워드·피인용, 주 10,000회), Scopus 검색 API(주 20,000회), ScienceDirect 검색 API. 안 되는 것: ScienceDirect 기사 API(META_ABS 도 403, 기관 IP 필요). 스마트 탐색은 키가 있으면 **사다리 단계마다 Scopus 검색도 나란히**(`build_scopus` → `scopus_search`, TITLE-ABS-KEY, 1~2쪽×25편, view=COMPLETE 로 초록·저자 키워드 동봉; `merge_scopus_into` 가 OpenAlex 에 없던 DOI 를 OpenAlex 에 50편씩 DOI 조회해 인용 관계를 붙이고, 거기도 없으면 `scopus:EID` 노드) 돌리고, Claude 선별 전에 초록 없는 후보를 `fill_abstracts_scopus` 로 채운다(4갈래 병렬, `관련맵\초록캐시.json` DOI 캐시). 단계당 1~3초 추가. 찾은 초록은 `관련맵\탐색요약.json` 에 캐시(키 `id|abs`, 요약 `id`, 번역 `id|tr`). 검색 결과의 초록은 2500자까지 보관

### 원고 '본보기' (`manuscript.py` `exemplars` → `POST /api/ms/exemplars {section}`)
부분(`SECTION_KINDS`): 초록·결론 = 문장 단위 역할, 서론·방법·결과·논의 = 문단 단위 역할 + 한국어 한 줄 요지, 제목 = 목록(단어 수·쌍점). 본문 부분은 전문이 필요해 **내 서재 PDF 에서만**: `_pdf_sections` 가 `pdf_body_and_asides` 본문을 1단계 절 제목(`1. Introduction`, `Conclusions` …)으로 나누고 소절 제목은 문단의 `sub` 로, 감사의 글·참고문헌 뒤는 버림. 내 원고 비교는 개요에서 제목이 맞는 1단계 절과 그 소절들의 초안(`_MINE_RX`). 결과·논의는 논문마다 30문단 넘어 느림(3편 2분) → 기본 4편. 개요 노드 편집의 '잘 쓴 ○○ 보기 →' 는 절 제목으로 부분을 짐작. 초록은:
고른 저널(기본 IJMTM·JMPT·IJEM + 원고의 투고 저널)의 초록을 **내 서재 PDF**(`extract_abstract`, 원고 주제어와 겹치는 제목·최근 순)와 **Scopus**(Elsevier 키, 저널마다 고르게 — 한 번에 섞으면 IJMTM 만 나옴, 주제어로 모자라면 그 저널 피인용 상위)에서 모아, 문장마다 역할(배경·공백·목적·방법·결과·의의)을 Claude sonnet 한 번에 붙인다(캐시 `원고\_본보기캐시.json`, 실패하면 단서 규칙). 내 초록도 같은 기준으로 나눠 빠진 역할을 알린다. 첫 호출 1~2분, 캐시 뒤엔 빠름. 서버가 `claude_text`·`extract_abstract`·`elsevier_key` 를 `ms.init` 으로 주입

### 원고 '리비전' (`manuscript.py` `rev_*` → `POST /api/ms/revision {op}`)
심사 결과 파일(.docx·.pdf·.txt)이나 붙여 넣은 글 → `rev_import` 가 지적마다 나눔: Claude 에게는 **경계만**(누구·번호·첫 낱말들) 받아 원문에서 잘라 글이 그대로 남고, 안 되면 규칙(`Reviewer #1` 머리 + 번호, 번호 앞 총평은 `.0`). 라운드(`doc.revision.rounds[]`)는 그 시점 본문을 `base` 로 기억 → `export_docx(mark=라운드)` 가 달라진 문단을 파란색으로(수정 표시 원고). 지적 = `{id R1.2, text, status todo|plan|applied|done, gist, work, suggest[절], plan, nodes[절], thread, response, changes[{node,key,type,before,after}]}`. op: `triage`(뜻·작업 종류·고칠 절·순서) · `discuss`(한국어 논의) · `propose`(절의 문단 번호로 edits/inserts/deletes JSON → `{type edit|insert|delete, para, before, after, prev}`, 반영은 화면에서 `before` 치환; 삭제는 `prev`(앞 문단) 뒤에 되살림. deletes 가 없던 때는 Claude 가 문단을 하나씩 앞으로 밀어 써서 비교가 엉망이었다. 앞 문단이 먼저 고쳐지거나 지워지면 화면이 남은 안의 기준(`before`·`prev`)을 새 글로 옮긴다) · `response`(영문 답변) · `export_response`(지적 기울임 → 답변 → 바뀐 글 파란색) · `export_marked`. 화면은 서버를 부르기 전에 `saveNow()` 로 내 글을 먼저 저장하고 돌려받은 라운드·지적을 끼워 넣는다(안 그러면 서버가 옛 본문을 보고, 내 자동 저장이 서버 변경을 덮는다). 고칠 대상은 개요의 절과 **초록**(절 id `front` → `doc.front.abstract`, 키 `abstract`; `_rev_node`·`_rev_text`). `_node` 는 없는 절에 예외를 던지므로 리비전 코드는 `_rev_node`(없으면 None)만 쓴다. 2026-09-30 에 시험용 사본으로 다섯 단계를 실제 호출해 확인(나누기 ~30초, 정리 ~50초, 논의 ~80초, 고칠 안 ~50초, 답변 ~40초).
- **기다리는 동안 쓴 글 지키기**: Claude 를 기다리는 1분 남짓 동안 사용자는 본문·방침을 계속 고친다. 서버는 요청 첫머리에 읽은 doc 을 저장하지 않고 `_rev_commit` 이 **파일을 다시 읽어 결과 필드만 얹어** 저장하고(`_REV_LOCK`, `/api/ms/save` 도 같은 잠금), 응답에 `fields`(지적)·`round_fields`·`item_fields`(정리)를 실어 화면(`revMerge`)이 그 필드만 끼운 뒤 다시 저장한다. 통째로 바꿔 끼우면 기다리는 동안 친 방침·답변이 사라진다.
- **저장이 겹쳐 파일이 깨진 사고(2026-09-30)**: `save_json` 이 모든 호출에서 같은 `경로.tmp` 에 써서, 원고 화면이 저장을 연달아 보내면(반영·되돌리기 연타) 두 스레드의 글이 섞여 JSON 뒤에 옛 글 꼬리가 남았다(`Extra data`) → 원고가 목록에서 사라짐. 지금은 `_SAVE_LOCK` + 호출마다 다른 임시 파일 이름, `load_json` 은 바꿔치기 순간의 PermissionError 를 잠깐 기다려 다시 읽는다. 화면도 저장을 한 번에 하나씩 순서대로(`pushSave`) 보낸다. 새 저장 코드를 만들 때 공용 `.tmp` 이름을 쓰지 말 것.
- **규칙 나누기**(Claude 실패 시): 심사위원 블록 단위 — 한 줄 머리(`Reviewer #1`)와 글과 붙은 머리(`Reviewer #1: This manuscript …`, Editorial Manager) 모두, 번호 없는 심사위원은 문단마다, 글머리표 목록(`Minor points:` 아래 `- …`)은 표마다, 편집자 편지는 통째로 하나. Claude 나누기도 조각 끝의 맺음말(`Yours sincerely`)·다음 묶음 머리를 뗀다(`_REV_TAIL`).
- **워드 가져오기**: 수식(`m:oMath`)은 글자만 `⟦…⟧` 로(구조는 못 옮김), 표는 `| a | b |` 글로 그 절에, 한국어 캡션(그림 1., 표 1.)·머리부(초록·키워드). `/api/ms/import {into: id}` = 열려 있는 원고에 덮어쓰기(`merge_docx`: 절 번호·제목으로 맞춰 글만 갱신, 카드·리비전·그림 유지, 언어가 다르면 거절). `list_ms` 는 `_` 로 시작하는 보조 파일을 건너뜀.
- **Claude 호출 문제 알림**: `_note_claude` 가 로그인 만료(`OAuth session expired`)·한도를 `CLAUDE_STATE` 에 적고 `claude_error()` 로 알림 → 홈 `claudeProblem` 안내, 원고 오류 글 뒤에 이유. 조용히 None 만 돌려주지 말 것.

### 원고 '고른 글' — 드래그 → 메모 · Claude 에게 (`manuscript.ask_selection`, `POST /api/ms/ask_sel`)
원고의 글 칸(textarea)에서 글을 드래그하면 마우스 옆에 「메모」「Claude 에게」(`#selBar`, Ctrl+K)가 뜨고, 누르면 오른쪽 아래 창(`#selPop`)에서 메모를 남기거나 고른 부분을 놓고 묻는다·고쳐 달라고 한다·제 생각을 말한다. 서버는 고른 글이 든 문단(고른 곳을 ⟪ ⟫ 로 표시)·대화를 주고 `{answer, alternatives}` 를 받는다 — 대안은 **고른 부분과 같은 범위**를 대체하는 글이고, 화면이 고른 글과 낱말 단위로 견줘 보여 주며 「이걸로 바꾸기」 는 글 칸에 `execCommand("insertText")` 로 넣는다(Ctrl+Z 가능). 모델은 창 아래에서 고른다: **Opus · 엑스트라**(`claude -p --model opus --effort xhigh`, 기본, 약 40초) / Opus · 보통 / Sonnet · 빠름(약 10초) — `ask_claude_json(prompt, model=, effort=)`. 메모와 대화는 `doc.memos = [{id, node, key, quote, start, note, thread[{role, text, alts, base}], t, done}]` 에 남고(서버는 상태를 갖지 않는다 — 화면이 저장), 「메모·피드백」 탭 맨 위에 목록, 원고 모드의 절 아래에 '메모 n'. 대안으로 바꾸면 메모의 `quote` 도 새 글로 바뀌어 이어서 물을 수 있다.
- **서랍 탭 줄이 사라지던 문제(2026-09-30)**: `#drawer` 는 세로 flex 인데 `.tabs` 에 `overflow-x: auto` 를 준 뒤로 최소 높이가 0 이 되어, 내용이 긴 탭(리비전·본보기)에서 탭 줄이 0 높이로 눌려 **다른 탭으로 갈 수 없었다** → `.tabs { flex: none }`. 세로 flex 안의 고정 줄에 overflow 를 줄 때는 `flex: none` 을 같이.

### 원고 '자료' — 작업 자료(.md 등)를 원고에 같이 넣기 (`manuscript.source_op`·`_src`, `POST /api/ms/source {op: add|get|delete}`)
사용자가 Claude(claude.ai 프로젝트 등)와 논문 작업을 하며 정리해 둔 글(.md·.txt·.docx·.pdf)을 서랍 「자료」 탭에 넣는다. 목록은 `doc.sources = [{id, name, chars, on, t}]`, 글은 `원고\자료\<원고 id>\<id>.txt` (원고 JSON 은 0.7초마다 통째로 저장되므로 글을 거기 넣지 않는다). 「Claude 에게」 를 켜 둔 자료는 `_src(doc, 물음)` 이 프롬프트 **앞**에 붙인다: 합쳐서 `_SRC_BUDGET`(2만 자) 이하면 통째로, 넘으면 제목·문단 묶음 단위로 나눠 물음과 낱말이 많이 겹치는 대목만(한글은 조사 때문에 낱말 앞 2·3글자도 견줌). **폴더 연결**(`doc.source_dirs = [{path, on}]`, `op: scan`): 그 폴더(바로 아래 + 한 단계 아래)의 .md·.txt 를 **Claude 를 부를 때마다 새로 읽는다**(`_dir_files`, 최근 40개·파일당 2MB) — Claude 데스크톱이 대화 끝에 정리 파일을 그 폴더에 쓰게 해 두면 늘 최신이 간다(claude.ai 프로젝트 지식은 클라우드에만 있어 직접 읽을 수 없다). 파일 넣기는 넣은 때의 사본이라 갱신되지 않는다. 붙는 곳: 고른 글에 묻기, 문단 초안·다시 쓰기·영문화·검토, 피드백 논의·고쳐 보기, 리비전의 논의·고칠 안·답변. 붙지 않는 곳: 개요 검토·전체 검토·할 일 정리·머리부 생성·본보기. 자료 안의 지시문보다 우리 지시가 우선하도록 머리말에 적어 둔다. `ask_selection` 은 JSON 없이 온 답("숫자만 말해" 같은 물음)도 그 글을 답으로 받는다 — `claude_json` 이 None 을 주면 '응답 없음'으로 보였다.

### 원고 '수식' (`mathtex.py`)
글 속의 `$LaTeX$`(줄 안)·`$$LaTeX$$`(따로 한 줄, 뒤에 `(3)` 을 붙이면 번호) 가 수식이다. **화면**: 글 칸(textarea) 아래에 그 안의 수식을 그려 보여 준다(`attachMath`) — `POST /api/ms/math` 가 MathML 을 주고 브라우저가 직접 그린다(외부 라이브러리 없음. MathML Core 는 mathvariant 를 거의 안 받아 굵게·필기체는 유니코드 수학 글자로, 그냥 쓴 괄호는 `stretchy=false` — 안 그러면 분수 높이만큼 늘어난다). 머리줄 「수식」(Alt+=) = 편집 창(LaTeX 입력·바로 그림·틀 단추). 미리보기의 수식을 누르면 그 수식을 고친다. 글 칸에 넣을 때는 `execCommand("insertText")` (Ctrl+Z 가 살아 있게). **내보내기**: `_runs` → `wordsync.runs_xml` 이 `$…$` 를 워드 수식(OMML)으로, `^x^`·`_x_` 를 위·아래 첨자로. 번호 붙은 문단 수식은 테두리 없는 표(빈칸 | 수식 | 번호, `wordsync.display_block`) — 수식과 번호를 한 문단에 두면 워드가 줄 안 수식으로 작게 그린다. **가져오기**: 워드 수식 → `$LaTeX$`(`wordsync.math_token`; 변환이 안 되면 예전처럼 글자만 `⟦…⟧`), 위 표 배치는 `$$…$$ (3)` 한 문단으로 읽는다(`eq_table_md`). 예전에 가져와 `⟦…⟧` 로 남은 수식은 미리보기 줄의 「고칠 수 있는 수식으로 바꾸기」(`upgrade_equations`: 가져온 워드를 다시 읽어 문단을 맞추고 그 문단의 수식 순서대로 바꿈). 변환을 고치면 확인할 것: 워드 수식 → LaTeX → 워드 수식 왕복에서 글자가 같고, 두 번째 변환에서 LaTeX 가 더 안 변하는지. Claude 프롬프트(초안·다시 쓰기·리비전)에는 '수식은 그대로 둔다'가 들어 있다.
- **워드에서 실제로 어떻게 보이는지 확인하는 법**: `win32com.client.DispatchEx("Word.Application")` 로 **따로 띄운 보이지 않는 워드**에서 읽기 전용(`OpenAndRepair=False` — 깨진 문서면 오류)으로 열어 PDF 로 뽑고 PyMuPDF 로 이미지를 만들어 본다. 사용자가 쓰는 워드 창(`Dispatch`·`GetActiveObject`)에는 붙지 말 것. 시간 제한을 걸고, 멈추면 새로 뜬 WINWORD pid 만 끈다.

### 원고 '워드 반영' (`manuscript.sync_docx` + `wordsync.py`, `POST /api/ms/wordsync {op: run|check|open|upgrade}`)
여기서 고친 글을 **가져온 워드 파일의 사본**에 반영한다(서식·그림·수식·표는 워드 것 그대로). 원본(`doc.source_docx`)을 매번 저장 없이 다시 가져와(`import_docx(dry=True, trace=…)` — 문단마다 원문 위치와 역할 head·body·table·abstract·title·figcap) 지금 원고와 문단 단위로 맞추고(`_align`: 수식 표기만 다른 것은 같은 것으로, 바뀐 구간은 닮은 것끼리 짝짓고 남은 것은 자리 순서대로), 바뀐 문단만 `wordsync.patch_paragraph` 로 고쳐 쓴다: 문단의 자식(런·수식·필드 덩어리)의 글과 새 글을 글자 단위로 견줘 **글자가 그대로인 자식은 원문 XML 그대로** 두고 달라진 곳만 새 런으로. 안 바뀐 문단·표·그림·구역은 한 글자도 바뀌지 않는다. 새 문단은 이웃 문단의 문단 속성·글꼴을 본뜨고, 지운 문단은 없앤다(구역 나누기·그림이 든 문단은 껍데기만 남김). 제목·캡션·초록 머리처럼 서식 표시 없이 글만 있는 자리는 `inherit`(앞 글 모양을 이어받음). 원본에서 매번 다시 만들므로 여러 번 돌려도 어긋남이 쌓이지 않는다. 결과 XML 은 `ET.fromstring` 으로 확인한 뒤에만 쓴다. 출력: 기본 `원고\워드반영\<원본 이름>`, 사용자가 경로를 정하면 거기(원본과 같은 파일은 거절, 내가 만든 적 없는 파일을 처음 덮어쓸 때는 옆에 백업 — `원고\_워드반영기록.json`). 워드가 그 파일을 열고 있으면 PermissionError → `locked`(화면이 10초마다 다시 시도). 화면: 내보내기 탭의 패널, 「저장할 때마다 자동으로」(`pushSave` 뒤 2.5초 → `wsRun`), 머리줄 `#wsState`, 「달라진 글자를 파란색으로」. 원고를 열면 `check`(쓰지 않고 살펴보기만)를 한 번 돈다.
- **하지 않는 것**: 표·참고문헌·키워드 반영, 절 순서 바꾸기, 열려 있는 워드 창에 글자가 실시간으로 바뀌는 것(COM 으로 가능하지만 워드 창이 포커스를 뺏고 사용자의 열린 문서를 건드려 위험해 넣지 않았다).
- **과거에 깨졌던 곳**: (1) 캡션 판정이 'Fig. 10과 Fig. 11은 …' 으로 시작하는 **본문 문장을 그림 캡션으로 삼켜** 본문에서 빠졌다 → `_CAP_RE`(번호 바로 뒤가 한글이거나, 구두점 없이 소문자 낱말이 오면 본문). 그때 가져온 원고는 반영 결과의 `swallowed` 로 알려 「본문에 되살리기」. 이 문단을 워드에서 지우면 안 된다(사용자가 지운 게 아니다). (2) 문단 하나에 고친 곳이 둘이면 그 사이가 통째로 다시 만들어져 파란색도 통째로 → 여러 구간 diff. (3) 새 제목이 굵지 않게 들어감 → `inherit`. (4) 다시 가져온 글의 수식 앞뒤 빈칸·표기 차이(`V_s` ↔ `V_{s}`)로 매번 '바뀜' → `_eqv` 로 견줌.

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
- **글꼴 모양**(환경설정): 「읽기 글꼴 모양」→ `--read-font`(읽기 화면 `#read`), 「원고 글꼴 모양」→ `--ms-font`(원고의 글 칸). 고르지 않으면 변수를 지워 화면 기본을 쓴다. 목록 = 이 PC 에 깔린 것만(`uiFontInstalled`: 캔버스로 폭을 재서 판별) + 사용자가 추가한 것 — 글꼴 파일을 넣으면 저장소 옆 `글꼴\` 폴더에 저장되고(`POST /api/fonts`, `/userfont/<파일>`, git·배포판 제외) `@font-face` 로 등록, 설치된 글꼴 이름을 적으면 localStorage `ui_fonts_custom`.
- **읽기 화면의 영문(PDF) 크기**: PDF 위 − + · 「폭 맞춤」 · Ctrl+휠 · 환경설정의 「영문(PDF) 크기」 가 모두 `ui_pdf_zoom` 하나를 쓴다(기억됨; `applyUi` → `window.onUiChange` → `setZoom`). 한국어 글과 PDF 사이 막대(`#split`)를 끌어 폭을 나눈다(`reader_pdf_frac`).
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
