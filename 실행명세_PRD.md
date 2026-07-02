# 실행 명세 (PRD) — 평생학습 강좌 필터·접수캘린더 MCP

> **상태: 승인됨 (2026-07-02, 사용자 승인)** — 다음 세션에서 ralph로 실행
> 근거 문서: `공모전_MCP_요구사항_분석.md` (룰북), `.omc/plans/agentic-player10-lifelong-mcp-plan.md` (합의 계획 + 7/2 실측)
> 데이터 자산: `data/` 폴더 (아래 §5)

---

## 0. 한 줄 정의

전국 지자체 평생학습 강좌를 **요일·시간·연령·무료여부로 복합 필터링**하고, **"접수가 언제 열리는지"(접수 캘린더·패턴)** 를 알려주는 카카오 PlayMCP용 MCP 서버. AGENTIC PLAYER 10 예선 출품작.

**핵심 서사**: 접수창은 평균 2주밖에 안 열리는데(실측), 주민들은 언제 열리는지 몰라서 놓친다. 원 사이트들은 복합 필터도 없다.

## 1. 마감 (절대 변경 불가)

- **7/7(월): PlayMCP 심사 요청 마지노선** (7/10까지 심사 완료 보장 커트라인)
- 7/14(화): 전체 공개 전환 + 비즈폼 예선 접수 마감
- 오늘 기준 7/2 실측(원래 7/3 몫)이 완료됨 → 하루 여유 확보

## 2. 기술 스택 & 아키텍처 (합의 확정)

```
[오프라인 배치 - 로컬에서 실행]
  data.go.kr #15013110 (강좌 205개 파일) + #15107727 (시설)
    → 다운로드(§5 레시피) → 정규화(요일/시간/연령/수강료/접수상태 파싱)
    → 접수 패턴 파생 테이블(기관별 접수시작일 이력·주기·다음 예상)
    → SQLite .db 생성 → **레포에 커밋**

[서빙 - 카카오클라우드]
  Dockerfile (COPY .db) → PlayMCP in KC (playmcp.kakaocloud.io) 배포
    → FastMCP (Python, Streamable HTTP, stateless, 무세션)
    → tool 호출 = SQLite 읽기 전용 조회만 (외부 호출 0)
    → 마크다운 정제 응답, UTF-8 byte ≤ 24KB assert
```

- MCP 스펙 2025-03-26~2025-11-25, Tools만 (Resource/Prompt 미지원)
- 성능 요건: 평균 100ms / p99 3,000ms (캐시 조회라 여유)
- SQLite `mode=ro`, 요청당 커넥션. 데이터 갱신 = 재빌드·재배포

## 3. Tool 스펙 (6개 확정)

공통: annotations 5종 전부 (readOnlyHint=true, destructiveHint=false, idempotentHint=true, openWorldHint=false, title). description은 영문 + 서비스명 병기(예: "...from Baeum-Alrimi(배움알리미)" — 서비스명은 개발 시작 시 확정, "kakao" 절대 금지). 모든 응답 끝에 "데이터 기준일 + 공공데이터포털 출처 + 실제 접수는 기관 확인 필요" 푸터.

| # | name | 입력 | 출력 | 비고 |
|---|---|---|---|---|
| 1 | `search_courses` | keyword?, region?, weekday[]?, time_range?(morning/afternoon/evening 또는 HH:MM범위), target?, free_only?, status?(기본: 접수중+예정+상시), sort?(deadline/fee/start_date), page? | 강좌 카드 리스트(강좌명·기관·요일·시간·수강료·접수기간·상태) | 핵심 필터 엔진 |
| 2 | `get_enrollment_calendar` | region 또는 center_name, months_ahead? | ① 데이터에 명시된 예정 접수창 ② 과거 회차 기반 "다음 오픈 예상"(기관별 접수시작일 이력·주기 근거 표시) | **차별화 스타 기능.** 예상은 "예상"으로 명확 표기 |
| 3 | `compare_courses` | course_ids[2~5] | 수강료·요일·시간·대상·접수상태 비교표 | 고유 연산 시연 |
| 4 | `get_course_detail` | course_id | 전체 상세 + 기관 주소·전화·홈페이지 | |
| 5 | `list_courses_by_center` | center_name/region | 시설별 강좌 + 시설정보(#15107727) | |
| 6 | `get_filter_options` | region? | 유효 지역/카테고리/연령 enum | LLM 질의 보조 |

## 4. 데이터 스키마 (실측 확인된 25컬럼)

`강좌명, 강사명, 교육시작일자, 교육종료일자, 교육시작시각, 교육종료시각, 강좌내용, 교육대상구분, 교육방법구분, 운영요일, 교육장소, 강좌정원수, 수강료, 교육장도로명주소, 운영기관명, 운영기관전화번호, 접수시작일자, 접수종료일자, 접수방법구분, 선정방법구분, 홈페이지주소, 직업능력개발훈련비지원강좌여부, 학점은행제평가(학점)인정여부, 평생학습계좌제평가인정여부, 데이터기준일자`

- 날짜는 ISO(YYYY-MM-DD) — 파일럿 11개 지역에서 포맷 오염 없음 확인. 단 전 파일 파싱 성공률 리포트는 필수(AC11)
- 파생 컬럼: `접수상태`(접수중/예정/마감/상시/미상 — 접수종료 2026-12-31류는 상시 후보), `요일_정규화`, `시간대_버킷`, `무료여부`, `지역(시도/시군구)` ← 파일명에서 추출

## 5. 데이터 접근 레시피 (2026-07-02 실증 완료 — 그대로 사용)

**공통 헤더**: `User-Agent: Mozilla/5.0` + `Referer: https://www.data.go.kr/data/15013110/standard.do`

1. **파일 목록**: `data/파일카탈로그_uddi목록.json` 에 **205개 전체 {uddi: 파일명}** 이미 저장됨. 재열거 필요 시: `standard.do?pageIndex=1..41` 순회, 정규식 `fn_fileDataDetail\('(uddi:[a-f0-9-]+)'\).*?title="상세보기 : ([^"]+)"`
2. **파일 다운로드 (2단계, 로그인 불필요)**:
   - `GET https://www.data.go.kr/tcs/dss/selectFileDataDownload.do?publicDataPk=15013110&publicDataDetailPk={uddi}&fileDetailSn=1` → JSON의 `fileDataRegistVO.atchFileId`
   - `GET https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId={atchFileId}&fileDetailSn=1` → CSV 바이트
   - 인코딩: `utf-8-sig` 우선, 실패 시 `cp949` (둘 다 실측 존재). 요청 간 0.3s 딜레이
3. **시설 데이터(#15107727)**: 같은 방식, publicDataPk만 교체. **아직 미검증 — 첫 태스크에서 확인**
4. **서울시 실시간 API (선택 레이어)**: `http://openapi.seoul.go.kr:8088/{KEY}/json/ListPublicReservationEducation/1/1000/` — SVCSTATNM(접수중/접수종료) 실시간, AREANM(자치구). **API 키 발급은 사용자 작업(§8)**. 키 확보 시에만 일배치로 흡수, hot-path 금지

**실측 수치 (전수조사, `data/전수조사결과_20260702.json`)**:
- 205파일 / 15,752강좌 / 오늘 접수가능 334건(2.1%) / 접수가능 지역 23곳
- 서울 15개 구 등록(강남구 포함 322강좌). 강남구 접수시작일: 월말 28일(149건)·월초 6일(135건) 집중, 접수창 대부분 ~2주 → 접수 캘린더 기능의 근거
- 데모 유망 지역: 국립국악원(open 68), 영천시(45), 광주 광산구(39), 과천시(38), 서울 강북구(8)

## 6. 태스크 분해 (ralph 실행 순서)

1. **[P0] 수집 파이프라인**: 카탈로그 → 205파일 다운로드 → 정규화 → 파생컬럼 → SQLite 빌드. 파싱 성공률/필드 null 리포트 출력. 시설 데이터(#15107727)도 동일 감사 후 가치 있으면 포함, 아니면 tool 5에서 시설정보 축소
2. **[P0] 접수 패턴 테이블**: 기관별 접수시작일 이력 → 주기·요일·월중 위치 추정 → `다음_오픈_예상` (근거 문자열 포함)
3. **[P0] FastMCP 서버**: 6 tool, Streamable HTTP stateless, 24KB byte-assert, 응답 푸터, 영문 description
4. **[P0] 테스트**: 골든셋 30건 필터 정합성 / 접수상태 계산 표본 30건 대조 ≥95% / 성능 벤치 20질의 / 24KB 단위테스트
5. **[P0] Dockerfile + KC 스모크 배포** (hello-world 1 tool 먼저 → 성공 시 실제 이미지) → Endpoint 확보
6. **[P0] PlayMCP 임시등록** → "정보 불러오기" 성공 → 도구함 추가 → AI채팅 5+ 시나리오 테스트
7. **[P0] 심사 요청** (셀프체크 통과 후, 늦어도 7/7)
8. **[P1] 증빙 문서**: dataset ID·이용허락범위 캡처·수집일 (§8 사용자 작업 포함)
9. **[P2] 서울 실시간 레이어** (키 확보 + 여유 시에만)

## 7. 수용 기준 (AC — 계획서 §6의 15개 그대로, 요약)

AC1 적재 리포트 / AC2 골든셋30 필터 100% / AC3 avg<100ms·p99<3s / AC4 24KB byte assert / AC5 MCP 규격+annotations / AC6 stateless / AC7 KC Active / AC8 정보불러오기 성공 / AC9 AI채팅 5시나리오 / AC10 반려 셀프체크(룰북 §4) / AC11 접수상태 정확도≥95% / AC12 라이선스 캡처 / AC13 다운로드 확정 기록(✅ 7/2 완료) / AC14 기준일 고지 / AC15 고유연산 설명문

## 8. 사용자(사람)가 직접 해야 하는 작업 — Claude가 못 하는 것

- [ ] **PlayMCP 회원가입/로그인 확인** (playmcp.kakao.com, 카카오 계정)
- [ ] **PlayMCP in KC 접속 확인** (playmcp.kakaocloud.io — PlayMCP 회원이어야 진입 가능)
- [ ] GitHub 레포 생성(public 권장 — private면 PAT 필요)
- [ ] (선택) 서울 열린데이터광장 API 키 발급 (data.seoul.go.kr 회원가입)
- [ ] data.go.kr #15013110·#15107727 페이지에서 "이용허락범위" 화면 캡처 (AC12 증빙)
- [ ] 서비스명 결정 (한글+영문, "kakao" 금지. 후보 논의: 배움알리미/동네배움/우리동네강좌 등)
- [ ] 7/7 심사요청 버튼, 7/14 비즈폼 접수 버튼 클릭 (본인 계정)

## 9. 하지 말 것 (반려 지뢰)

- 강남구 life.gangnam.go.kr 숨은 API 사용 금지 (예선 배제 확정)
- hot-path 외부 API 호출 금지 / raw API 응답 그대로 반환 금지
- "kakao" 명칭, 상업 링크, 확정처럼 보이는 예측 표현("~예상, 과거 N회 기준" 필수)
- 임시등록 상태에서 "등록 및 심사요청" 클릭 금지 (테스트 다 끝난 후에)
