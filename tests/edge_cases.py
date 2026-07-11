#!/usr/bin/env python3
"""엣지케이스·악성입력·경계값 테스트 (ultraqa — 심사 전 견고성).

실사용에서 발견된 버그 유형(영문 enum, 열린 시간 범위, 요일 파싱, 빈 문자열 비트)을
일반화한 회귀 방지 스위트. 실행: python3 tests/edge_cases.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import server  # noqa: E402
from build_db import (  # noqa: E402
    parse_date, parse_fee, parse_time, parse_weekdays, weekday_bits,
    region_from_filename,
)

PASS = FAIL = 0
FAILURES: list[str] = []
ALL_STATUS = ["접수중", "예정", "상시", "마감", "미상"]


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}")
        print(f"  FAIL: {name} {detail}")


def total_of(out: str) -> int:
    m = re.search(r"결과 (\d+)건", out)
    return int(m.group(1)) if m else 0


def is_valid_response(out: str) -> bool:
    return len(out.encode("utf-8")) <= 24 * 1024 and "데이터 기준일" in out


# ── 1. 파서 경계값 ──────────────────────────────────────────
def test_parsers() -> None:
    print("[1] 파서 경계값")
    cases = {
        "": "", "월~금": "월,화,수,목,금", "금~월": "월,금,토,일",  # 역순 범위 = 순환(금토일월)
        "매일": "월,화,수,목,금,토,일", "평일": "월,화,수,목,금",
        "월·수·금": "월,수,금", "월요일~금묘일": "월,화,수,목,금",
        "토일": "토,일", "  월  ": "월",
    }
    for raw, want in cases.items():
        got = parse_weekdays(raw)
        check(f"parse_weekdays({raw!r})", got == want, f"got={got!r} want={want!r}")

    check("weekday_bits('')", weekday_bits("") == 0)
    check("weekday_bits('월')", weekday_bits("월") == 1)
    check("weekday_bits('일')", weekday_bits("일") == 64)

    for raw, want in {"9:00": "09:00", "09:30": "09:30", "25:00": None,
                      "9시30": "09:30", "": None, "abc": None}.items():
        got = parse_time(raw)
        check(f"parse_time({raw!r})", got == want, f"got={got!r}")

    for raw, want in {"2026-04-06": "2026-04-06", "2026.04.06": "2026-04-06",
                      "20260406": "2026-04-06", "2026-13-01": None,
                      "2026-02-30": None, "": None}.items():
        got = parse_date(raw)
        check(f"parse_date({raw!r})", got == want, f"got={got!r}")

    for raw, want in {"": (0, 1), "0": (0, 1), "무료": (0, 1),
                      "30,000원": (30000, 0), "삼만원": (None, 0)}.items():
        got = parse_fee(raw)
        check(f"parse_fee({raw!r})", got == want, f"got={got!r}")

    check("region 교육청(시군구 없음 설계)", region_from_filename("서울특별시교육청_남부_평생학습강좌") == ("서울특별시", ""))
    check("region 기관", region_from_filename("문화체육관광부_국립국악원_평생학습강좌") == ("", ""))


# ── 2. search_courses 악성/경계 입력 ─────────────────────────
def test_search_adversarial() -> None:
    print("[2] search_courses 악성·경계 입력")
    # SQL 메타문자 — 크래시 없이 유효 응답
    for kw in ["'; DROP TABLE courses;--", '" OR 1=1 --', "강좌') UNION SELECT 1--"]:
        out = server.search_courses(keyword=kw)
        check(f"SQL메타 keyword {kw[:20]!r}", is_valid_response(out))

    # LIKE 와일드카드는 리터럴 취급되어야 함
    n_pct = total_of(server.search_courses(keyword="%", status=ALL_STATUS))
    n_us = total_of(server.search_courses(keyword="_", status=ALL_STATUS))
    n_all = total_of(server.search_courses(status=ALL_STATUS))
    check("keyword '%' 리터럴", n_pct < n_all, f"'%'={n_pct} vs all={n_all}")
    check("keyword '_' 리터럴", n_us < n_all, f"'_'={n_us} vs all={n_all}")

    # 페이지 경계
    check("page=0", is_valid_response(server.search_courses(page=0)))
    check("page=-5", is_valid_response(server.search_courses(page=-5)))
    out = server.search_courses(page=99999)
    check("page=99999 빈페이지 안내", "없습니다" in out or "생략" in out or is_valid_response(out))

    # 요일 변형 입력
    n_full = total_of(server.search_courses(weekday=["월요일"], status=ALL_STATUS))
    n_char = total_of(server.search_courses(weekday=["월"], status=ALL_STATUS))
    n_en = total_of(server.search_courses(weekday=["mon", "MON"], status=ALL_STATUS))
    check("weekday '월요일'=='월'", n_full == n_char, f"{n_full} vs {n_char}")
    check("weekday en 'mon'=='월'", n_en == n_char, f"{n_en} vs {n_char}")
    check("weekday 무효값 → 필터 미적용", total_of(server.search_courses(weekday=["x"], status=ALL_STATUS)) == n_all)

    # 시간 범위 변형
    n_all_gn = total_of(server.search_courses(region="강남구", status=ALL_STATUS))
    n_open = total_of(server.search_courses(region="강남구", time_range="19:00-", status=ALL_STATUS))
    n_tilde = total_of(server.search_courses(region="강남구", time_range="19:00~", status=ALL_STATUS))
    n_upto = total_of(server.search_courses(region="강남구", time_range="-09:00", status=ALL_STATUS))
    check("open range 적용", 0 < n_open < n_all_gn, f"open={n_open} all={n_all_gn}")
    check("~ 구분자 동일", n_open == n_tilde)
    check("-09:00 상한", n_upto < n_all_gn)
    check("무효 time_range 'aa-bb' 무시", total_of(server.search_courses(region="강남구", time_range="aa-bb", status=ALL_STATUS)) == n_all_gn)
    check("25:00-26:00 빈결과(크래시X)", is_valid_response(server.search_courses(region="강남구", time_range="25:00-26:00", status=ALL_STATUS)))

    # status 변형
    n_closed_ko = total_of(server.search_courses(region="영천시", status=["마감"]))
    n_closed_en = total_of(server.search_courses(region="영천시", status=["closed"]))
    n_closed_up = total_of(server.search_courses(region="영천시", status=["CLOSED"]))
    check("status 영문 alias", n_closed_ko == n_closed_en == n_closed_up, f"{n_closed_ko}/{n_closed_en}/{n_closed_up}")
    n_default = total_of(server.search_courses(region="영천시"))
    check("status 무효값 → 기본", total_of(server.search_courses(region="영천시", status=["없는값"])) == n_default)
    check("status [] → 기본", total_of(server.search_courses(region="영천시", status=[])) == n_default)

    # sort 무효값 → 크래시 없이 기본 정렬
    check("sort 무효값", is_valid_response(server.search_courses(region="서울", sort="invalid_sort")))

    # 초장문·이모지 키워드
    check("10k 키워드", is_valid_response(server.search_courses(keyword="가" * 10000)))
    check("이모지 키워드", is_valid_response(server.search_courses(keyword="🧘요가")))

    # 존재하지 않는 지역
    out = server.search_courses(region="화성시비둘기동구름마을")
    check("없는 지역 안내", "없습니다" in out)

    # region 오탐 회귀 (인간 QA 웹 프론트 발견 2026-07-11): 건물명 '새서울프라자'(경기 과천)가
    # region='서울'에 substring 매치되던 결함 — 교육장소는 지역 판별에서 제외됨
    out = server.search_courses(keyword="요가", region="서울", status=ALL_STATUS)
    check("region=서울 요가에 과천 오탐 없음", "과천" not in out)
    # 시도·시군구 빈 행은 도로명주소 폴백으로 계속 매치되어야 함 (시도 빈값 481건 손실 금지)
    conn = server.db()
    n_struct = conn.execute(
        "SELECT COUNT(*) FROM courses WHERE 시도 LIKE '%서울%' OR 시군구 LIKE '%서울%'"
    ).fetchone()[0]
    n_addr_fb = conn.execute(
        "SELECT COUNT(*) FROM courses WHERE (시도 IS NULL OR 시도='' OR 시군구 IS NULL OR 시군구='')"
        " AND 교육장도로명주소 LIKE '%서울%'"
        " AND NOT (시도 LIKE '%서울%' OR 시군구 LIKE '%서울%')"
    ).fetchone()[0]
    n_region = total_of(server.search_courses(region="서울", status=ALL_STATUS))
    check(
        "region=서울 = 구조화필드 + 주소폴백",
        n_region == n_struct + n_addr_fb,
        f"region={n_region} struct={n_struct} addr_fb={n_addr_fb}",
    )


# ── 3. 나머지 5개 tool 경계 ──────────────────────────────────
def test_other_tools() -> None:
    print("[3] 나머지 tool 경계값")
    check("compare 1개 거부", "2~5개" in server.compare_courses([1]))
    check("compare 6개 거부", "2~5개" in server.compare_courses([1, 2, 3, 4, 5, 6]))
    out = server.compare_courses([1, 1])
    check("compare 중복 ID", "2개 이상" in out or "비교" in out)
    check("compare 전부 무효 ID", "유효한 강좌" in server.compare_courses([99999991, 99999992]))
    check("compare 음수 ID", is_valid_response(server.compare_courses([-1, 0])))

    check("detail id=0", "찾을 수 없습니다" in server.get_course_detail(0))
    check("detail 음수", "찾을 수 없습니다" in server.get_course_detail(-1))
    check("detail 10^9", "찾을 수 없습니다" in server.get_course_detail(10**9))

    check("calendar 인자 없음 안내", "필요합니다" in server.get_enrollment_calendar())
    check("calendar months=0 클램프", is_valid_response(server.get_enrollment_calendar(region="서울", months_ahead=0)))
    check("calendar months=-5", is_valid_response(server.get_enrollment_calendar(region="서울", months_ahead=-5)))
    check("calendar months=100 클램프", is_valid_response(server.get_enrollment_calendar(region="서울", months_ahead=100)))
    check("calendar 없는 지역", is_valid_response(server.get_enrollment_calendar(region="아틀란티스")))
    cal = server.get_enrollment_calendar(region="강남구")
    check("calendar 예상 표기 필수", ("예상" in cal and "확정" in cal) or "없음" in cal)

    check("center 인자 없음 안내", "필요합니다" in server.list_courses_by_center())
    check("center 없는 기관", "찾지 못했습니다" in server.list_courses_by_center(center_name="존재하지않는기관XYZ"))
    check("center page 초과", is_valid_response(server.list_courses_by_center(center_name="강남구청", page=999)))
    check("center SQL메타", is_valid_response(server.list_courses_by_center(center_name="'; DROP--")))

    check("options 기본", is_valid_response(server.get_filter_options()))
    check("options 없는 지역", is_valid_response(server.get_filter_options(region="아틀란티스")))
    check("options SQL메타", is_valid_response(server.get_filter_options(region="%' OR '1'='1")))


# ── 4. 24KB·응답 불변식 (모든 tool 광역 입력) ─────────────────
def test_invariants() -> None:
    print("[4] 응답 불변식 (24KB·푸터·kakao 금지)")
    outputs = [
        server.search_courses(status=ALL_STATUS),
        server.search_courses(keyword="강", status=ALL_STATUS),
        server.get_enrollment_calendar(region="경기도", months_ahead=6),
        server.list_courses_by_center(region="경기도"),
        server.get_filter_options(),
        server.get_course_detail(1),
        server.compare_courses([1, 2, 3, 4, 5]),
    ]
    for i, out in enumerate(outputs):
        check(f"불변식 #{i}: 24KB", len(out.encode("utf-8")) <= 24 * 1024)
        check(f"불변식 #{i}: 푸터", "데이터 기준일" in out and "공공데이터포털" in out)
        check(f"불변식 #{i}: kakao 금지", "kakao" not in out.lower().replace("kakaocloud", ""))
    # finalize 절단 시 UTF-8 유효성
    cut = server.finalize("한" * 30000)
    check("finalize UTF-8 유효", cut.encode("utf-8").decode("utf-8") is not None)
    check("finalize 절단 안내", "생략" in cut)


def main() -> int:
    test_parsers()
    test_search_adversarial()
    test_other_tools()
    test_invariants()
    print(f"\nEDGE SUITE: {PASS} pass / {FAIL} fail")
    if FAILURES:
        print("실패 목록:")
        for f in FAILURES:
            print(" -", f)
        return 1
    print("EDGE SUITE: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
