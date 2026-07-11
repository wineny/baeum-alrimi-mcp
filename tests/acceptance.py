#!/usr/bin/env python3
"""수용 테스트 (PRD §7): AC2 골든셋30 / AC11 접수상태≥95% / AC3 성능 / AC4 24KB.

골든셋·접수상태는 SQL 구현과 독립된 순수 Python 재구현으로 교차 검증한다.
실행: python3 tests/acceptance.py
"""
import random
import re
import sqlite3
import statistics
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "courses.db"
TODAY = server.today_kst()


def rows_all() -> list[sqlite3.Row]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM courses").fetchall()
    con.close()
    return rows


def py_status(r: sqlite3.Row) -> str:
    """SQL(STATUS_SQL)과 독립된 접수상태 재구현."""
    if r["상시여부"] == 1:
        return "상시"
    if not r["접수시작일자"] or not r["접수종료일자"]:
        return "미상"
    if TODAY < r["접수시작일자"]:
        return "예정"
    if TODAY > r["접수종료일자"]:
        return "마감"
    return "접수중"


def py_filter(
    rows: list[sqlite3.Row],
    keyword: str | None = None,
    region: str | None = None,
    weekday: list[str] | None = None,
    time_range: str | None = None,
    target: str | None = None,
    free_only: bool = False,
    status: list[str] | None = None,
) -> int:
    """search_courses의 SQL WHERE와 독립된 순수 Python 필터 재구현 — 건수 반환."""
    statuses = status or ["접수중", "예정", "상시"]
    n = 0
    for r in rows:
        if py_status(r) not in statuses:
            continue
        if keyword:
            # 카테고리 확장 규칙(expand_keyword)은 서버와 공유하되 매칭 자체는
            # SQL과 독립 재구현. lower()는 SQL LIKE의 ASCII 대소문자 무시 대응.
            terms = server.expand_keyword(keyword)
            hit = any(
                t.lower() in (r[c] or "").lower()
                for t in terms
                for c in ("강좌명", "강좌내용", "교육장소")
            )
            if not hit:
                continue
        if region:
            # 서버 region 스펙 재구현: 토큰별 AND, 시도·시군구 우선,
            # 도로명주소는 구조화 필드가 빈 행의 폴백만. 교육장소(건물명)는
            # 제외 — '새서울프라자'(과천) 오탐 인간 QA 반영.
            struct_empty = not (r["시도"] or "") or not (r["시군구"] or "")
            ok = True
            for tok in region.split():
                struct_hit = tok in (r["시도"] or "") or tok in (r["시군구"] or "")
                addr_hit = struct_empty and tok in (r["교육장도로명주소"] or "")
                if not (struct_hit or addr_hit):
                    ok = False
                    break
            if not ok:
                continue
        if weekday:
            have = set((r["요일_정규화"] or "").split(","))
            if not (set(weekday) & have):
                continue
        if time_range:
            if time_range in ("morning", "afternoon", "evening"):
                if r["시간대_버킷"] != time_range:
                    continue
            else:
                t1, t2 = time_range.split("-")
                t = r["교육시작시각"]
                if not t or not (t1 <= t <= t2):
                    continue
        if target and target not in (r["교육대상구분"] or ""):
            continue
        if free_only and r["무료여부"] != 1:
            continue
        n += 1
    return n


GOLDEN = [
    {},
    {"region": "서울특별시"},
    {"region": "강남구"},
    {"region": "영천시"},
    {"region": "과천시"},
    {"region": "광산구"},
    {"free_only": True},
    {"region": "서울", "free_only": True},
    {"weekday": ["월"]},
    {"weekday": ["토", "일"]},
    {"weekday": ["월", "수", "금"]},
    {"time_range": "morning"},
    {"time_range": "afternoon"},
    {"time_range": "evening"},
    {"time_range": "18:00-21:00"},
    {"target": "성인"},
    {"target": "아동"},
    {"keyword": "요가"},
    {"keyword": "수영"},
    {"keyword": "컴퓨터"},
    {"keyword": "국악"},
    {"keyword": "운동"},
    {"keyword": "운동", "region": "서울", "status": ["접수중", "예정", "상시", "마감", "미상"]},
    {"keyword": "음악", "status": ["마감"]},
    {"status": ["마감"]},
    {"status": ["상시"]},
    {"status": ["접수중"]},
    {"status": ["접수중", "예정", "상시", "마감", "미상"]},
    {"region": "경기도", "weekday": ["화"], "free_only": True},
    {"region": "부산", "time_range": "evening"},
    {"keyword": "영어", "status": ["접수중", "예정", "상시", "마감", "미상"]},
    {"region": "전북", "target": "성인"},
    {"weekday": ["금"], "time_range": "morning", "free_only": True},
]


def search_total(**kw) -> int:
    """server.search_courses 응답 헤더의 총 건수 파싱 (0건 응답 포함)."""
    out = server.search_courses(**kw)
    m = re.search(r"강좌 검색 결과 (\d+)건", out)
    return int(m.group(1)) if m else 0


def test_golden(rows) -> None:
    fails = []
    for i, q in enumerate(GOLDEN, 1):
        got = search_total(**q)
        want = py_filter(rows, **q)
        if got != want:
            fails.append((i, q, got, want))
    assert not fails, f"골든셋 불일치 {len(fails)}건: {fails[:5]}"
    print(f"AC2 골든셋 {len(GOLDEN)}건 필터 정합 100%: PASS")


def test_status_accuracy(rows) -> None:
    random.seed(20260702)
    sample = random.sample(rows, 30)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ok = 0
    for r in sample:
        sql_status = con.execute(
            f"SELECT {server.STATUS_SQL} FROM courses WHERE id=:id",
            {"id": r["id"], "today": TODAY},
        ).fetchone()[0]
        if sql_status == py_status(r):
            ok += 1
    con.close()
    acc = ok / 30
    assert acc >= 0.95, f"접수상태 정확도 {acc:.2%} < 95%"
    print(f"AC11 접수상태 표본 30건 대조 {acc:.0%}: PASS")


BENCH = [
    lambda: server.search_courses(region="서울특별시"),
    lambda: server.search_courses(keyword="요가", free_only=True),
    lambda: server.search_courses(weekday=["월", "수"], time_range="evening"),
    lambda: server.search_courses(region="경기도", status=["마감"], page=3),
    lambda: server.search_courses(target="성인", sort="fee"),
    lambda: server.get_enrollment_calendar(region="서울"),
    lambda: server.get_enrollment_calendar(region="강남구"),
    lambda: server.get_enrollment_calendar(center_name="국악원"),
    lambda: server.compare_courses([9478, 9479, 9480]),
    lambda: server.compare_courses([1, 2]),
    lambda: server.get_course_detail(9478),
    lambda: server.get_course_detail(15000),
    lambda: server.list_courses_by_center(center_name="강남구청"),
    lambda: server.list_courses_by_center(region="부산"),
    lambda: server.list_courses_by_center(center_name="영천시청"),
    lambda: server.get_filter_options(),
    lambda: server.get_filter_options(region="서울"),
    lambda: server.search_courses(keyword="수영", region="인천"),
    lambda: server.search_courses(sort="start_date", page=2),
    lambda: server.search_courses(region="제주"),
]


def test_perf() -> None:
    for f in BENCH:  # 워밍업 1회
        f()
    times = []
    for f in BENCH:
        t0 = time.perf_counter()
        f()
        times.append((time.perf_counter() - t0) * 1000)
    avg = statistics.mean(times)
    p99 = sorted(times)[max(0, int(len(times) * 0.99) - 1)]
    worst = max(times)
    assert avg < 100, f"평균 {avg:.1f}ms >= 100ms"
    assert worst < 3000, f"최대 {worst:.1f}ms >= 3000ms"
    print(f"AC3 성능 20질의 avg={avg:.1f}ms max={worst:.1f}ms (p99<{worst:.0f}ms): PASS")


def test_24kb() -> None:
    # 24KB·푸터 판정 규칙의 SSOT는 tests/persona_qa/invariants.py — 여기서 import해 공유
    from tests.persona_qa import invariants as inv

    huge = "가나다라마바사아자차" * 10000
    out = server.finalize(huge)
    inv.assert_24kb(out)
    inv.assert_footer(out)
    assert "생략" in out and "데이터 기준일" in out
    wide = server.search_courses(
        status=["접수중", "예정", "상시", "마감", "미상"], page=1
    )
    inv.assert_24kb(wide)
    inv.assert_footer(wide)
    cal = server.get_enrollment_calendar(region="경기도", months_ahead=6)
    inv.assert_24kb(cal)
    inv.assert_footer(cal)
    print("AC4 24KB 상한 (finalize 절단 + 광역 질의, invariants SSOT): PASS")


def main() -> int:
    rows = rows_all()
    test_golden(rows)
    test_status_accuracy(rows)
    test_perf()
    test_24kb()
    print("\nACCEPTANCE: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
