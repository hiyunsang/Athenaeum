# 절삭 도식 생성기 (시제품)

`proto_turning_zones.py` — 선삭 변형 구역(PSZ/SSZ/TSZ) 도식 4패널을 전부 코드로 생성.
결정립 = scipy Voronoi, 변형 = 변위장(표면 쪽으로 갈수록 강한 전단), 구역·색 = 규칙, 기계적/열적 관점 = 같은 형상에 팔레트만 교체.

실행: `python proto_turning_zones.py` → `proto.svg`
PNG 렌더: PyMuPDF 는 SVG 그라데이션을 못 그림 → **Edge headless** 사용
`msedge.exe --headless=new --disable-gpu --hide-scrollbars --window-size=1400,1990 --screenshot=out.png file:///.../proto.svg`

아직 프로그램(Athenaeum)에 연결되지 않은 시제품. 사용자 판단 후 파라미터 입력 UI + 형상 틀(직교/선삭/밀링)로 확장 예정.
