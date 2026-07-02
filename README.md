# 배움알리미 (Baeum-Alrimi) — 평생학습 강좌 필터·접수캘린더 MCP

전국 지자체 평생학습 강좌를 **요일·시간대·대상·무료여부·접수상태로 복합 필터링**하고,
**"접수가 언제 열리는지"(접수 캘린더·기관별 오픈 패턴)** 를 알려주는 MCP 서버.
AGENTIC PLAYER 10 예선 출품작.

## 왜 필요한가

- 접수창은 평균 약 2주밖에 열리지 않는데(실측), 주민들은 언제 열리는지 몰라서 놓친다
- 원 사이트들은 지역별로 흩어져 있고 요일×시간×연령×무료여부 복합 필터가 없다
- 전국 15,752개 강좌(205개 기관 파일)의 교차 집계·정형 필드 정확 반환·접수상태
  계산은 LLM 웹검색으로 재현 불가능한 결정론 연산이다

## 아키텍처

```
[오프라인 배치 - 로컬]
  공공데이터포털 전국평생학습강좌표준데이터(#15013110, 205개 기관 CSV)
    → scripts/download_courses.py (수집)
    → scripts/build_db.py (정규화 + 파생컬럼 + 적재 리포트)
    → scripts/build_patterns.py (기관별 접수 오픈 패턴·다음 오픈 예상)
    → data/courses.db (SQLite, 레포에 커밋)

[서빙 - 카카오클라우드 PlayMCP in KC]
  Dockerfile (COPY courses.db)
    → FastMCP (Python, Streamable HTTP, stateless)
    → tool 호출 = SQLite 읽기 전용 조회만 (외부 호출 0, avg ~6ms)
```

## Tools (6개)

| name | 설명 |
|---|---|
| `search_courses` | 키워드·지역·요일·시간대·대상·무료·접수상태 복합 필터 + 정렬 |
| `get_enrollment_calendar` | ① 데이터에 명시된 예정 접수창 ② 과거 이력 기반 다음 오픈 예상(근거 표시) |
| `compare_courses` | 강좌 2~5개 비교표 |
| `get_course_detail` | 강좌 전체 상세 + 기관 연락처 |
| `list_courses_by_center` | 기관별 강좌 목록 / 지역 내 기관 목록 |
| `get_filter_options` | 유효 필터값 안내 |

## 실행

```bash
# 로컬
pip install -r requirements.txt
python server.py            # http://0.0.0.0:8000/mcp (Streamable HTTP)

# Docker
docker build -t baeum-alrimi .
docker run -p 8000:8000 baeum-alrimi

# 데이터 재빌드 (분기 갱신 시)
python scripts/download_courses.py
python scripts/build_db.py
python scripts/build_patterns.py

# 수용 테스트
python tests/acceptance.py
```

## 데이터 출처

- **전국평생학습강좌표준데이터** — 공공데이터포털 dataset #15013110, 이용허락범위:
  제한 없음(공공데이터). 분기 갱신. 수집일 2026-07-02, 데이터 기준일 2026-01-22~2026-07-01.
- 모든 응답에 데이터 기준일·출처·"실제 접수는 기관 확인 필요" 고지 포함.
- 접수 오픈 "예상"은 과거 접수시작일 이력 기반 통계 추정이며, 근거(과거 N회)와 함께
  항상 "예상이며 확정 아님"으로 표기.
