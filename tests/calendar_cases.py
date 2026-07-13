#!/usr/bin/env python3
"""구글 캘린더 등록 링크 회귀 케이스 (계획 baeum-expansion-3 §2 기능1 · §4 F1).

gcal_link 유닛 + get_enrollment_calendar ①/② 부착 규칙 + 링크 바이트 안전망 +
search_courses '예정' 카드 한정 부착을 기계 판정한다.
실행: python3 tests/calendar_cases.py
"""
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402
from tests.persona_qa import invariants as inv  # noqa: E402

GCAL_URL_RE = re.compile(
    r"^https://calendar\.google\.com/calendar/render\?action=TEMPLATE"
    r"&text=[^&]+&dates=\d{8}/\d{8}$"
)


def test_gcal_link_unit() -> None:
    url = server.gcal_link("배움알리미 · 관악문화센터 접수 시작", "2026-07-20")
    assert url is not None, "정상 입력에서 None 반환"
    assert GCAL_URL_RE.match(url), f"URL 형식 불일치: {url}"
    assert "dates=20260720/20260721" in url, "종료일 != 시작일+1(exclusive)"
    assert "kakao" not in url.lower(), "URL에 kakao 문자열 유입"
    assert len(url) < 2000, f"URL {len(url)}자 >= 2000"

    assert server.gcal_link("title", None) is None, "day=None 처리 실패"
    assert server.gcal_link("title", "not-a-date") is None, "잘못된 포맷 처리 실패"
    assert server.gcal_link("title", "2026-02-30") is None, "존재하지 않는 날짜 처리 실패"
    assert server.gcal_link("title", "2026-13-01") is None, "잘못된 월 처리 실패"

    # [:24] 캡 — 24자 넘는 한글 title은 24자까지만 인코딩된다
    long_title = "가" * 30
    capped = server.gcal_link(long_title, "2026-07-20")
    assert capped is not None
    assert quote("가" * 24, safe="") in capped, "[:24] 캡 미적용"
    assert quote("가" * 25, safe="") not in capped, "24자 초과분이 인코딩에 유입"
    print("F1 gcal_link 유닛(종료일+1·인코딩·None·<2000자·kakao 부재·[:24]캡): PASS")


def test_calendar_attachment() -> None:
    out = server.get_enrollment_calendar(region="부천시", months_ahead=6)
    assert "render?action=TEMPLATE" in out, "캘린더 출력에 등록 링크 없음"

    # ② 다음오픈예상 title은 '[예상]' 포함 + CERTAINTY_RE 무매치(예측 라인 규약)
    if "**②" in out:
        sec2 = out.split("**②", 1)[1]
        for m in re.finditer(r"text=([^&]+)&dates", sec2):
            decoded = quote("[예상]", safe="")
            # 인코딩된 채로도 '[예상]' 접두는 항상 존재해야 한다(모든 ② 링크)
            assert decoded in m.group(0) or "%5B%EC%98%88%EC%83%81%5D" in m.group(0), (
                "② 링크 title에 '[예상]' 누락"
            )
        assert not inv.prediction_violations(out), "② 예측 라인에 확정형 표현 유입"
    print("F1 캘린더 링크 부착(render?action=TEMPLATE·②[예상]·CERTAINTY_RE 무매치): PASS")


def test_link_cap() -> None:
    # ① 링크 상한 검증. 기준일이 지날수록 미래 접수창이 줄어 "엔트리 > 12" 전제가
    # 날짜에 따라 깨지므로(2026-07-13 실측: 최대 12개), 상한 상수를 엔트리 수보다
    # 작게 낮춰 상한 메커니즘 자체를 날짜와 무관하게 검증한다.
    # region='시'는 단일 토큰 LIKE 매칭이라 다수 시군구를 광범위하게 히트.
    out = server.get_enrollment_calendar(region="시", months_ahead=6)
    sec1 = out.split("**②", 1)[0] if "**②" in out else out
    entries = sec1.count(" · 강좌 ")
    assert entries >= 2, (
        f"① 상한 테스트 전제 실패 — 미래 접수창 엔트리 {entries}개 (<2, 데이터 갱신 필요)"
    )
    test_cap = min(server.CAL_LINK_CAP, entries) - 1
    original_cap = server.CAL_LINK_CAP
    server.CAL_LINK_CAP = test_cap
    try:
        capped = server.get_enrollment_calendar(region="시", months_ahead=6)
    finally:
        server.CAL_LINK_CAP = original_cap
    sec1_capped = capped.split("**②", 1)[0] if "**②" in capped else capped
    links = sec1_capped.count("일정 추가:")
    assert links == test_cap, f"① 링크 상한 위반 — {links}개 부착 (기대 {test_cap})"
    # 프로덕션 상수(12) 경로: 부착 수가 상한을 절대 초과하지 않음도 확인
    prod_links = sec1.count("일정 추가:")
    assert prod_links <= server.CAL_LINK_CAP, (
        f"① 프로덕션 상한 초과 — {prod_links}개 > {server.CAL_LINK_CAP}"
    )
    print(
        f"F1 ① 부착 상한(엔트리 {entries}개, 상한 {test_cap} 강제 시 {links}개 부착"
        f" · 프로덕션 {prod_links}≤{server.CAL_LINK_CAP}): PASS"
    )


def test_link_safety_net() -> None:
    """바이트 안전망: 예산 근접 시 링크만 생략, 엔트리 텍스트·URL 완전성은 보존."""
    original_max = server.MAX_BYTES
    original_gcal = server.gcal_link
    try:
        server.gcal_link = lambda title, day: None  # 텍스트만의 본문 크기 측정
        no_link_out = server.get_enrollment_calendar(region="경기도", months_ahead=6)
    finally:
        server.gcal_link = original_gcal

    footer_bytes = len(server.footer().encode("utf-8"))
    no_link_body_bytes = len(no_link_out.encode("utf-8")) - footer_bytes
    # 링크 1개 분량 실측 — 텍스트 여유분(margin)을 데이터 변화에 안전하게 산정
    sample_url = server.gcal_link("배움알리미 · 샘플기관 접수 시작", "2026-07-20")
    sample_cost = len(f"  - 일정 추가: {sample_url}".encode("utf-8")) + 1

    try:
        # 텍스트만으로는 여유 있으나(margin) 링크 1~2개만 들어갈 만큼만 근접시켜
        # "일부는 포함·나머지는 생략"되는 부분 절단 상황을 재현한다.
        margin = int(sample_cost * 2.5)
        server.MAX_BYTES = footer_bytes + no_link_body_bytes + margin
        out = server.get_enrollment_calendar(region="경기도", months_ahead=6)
    finally:
        server.MAX_BYTES = original_max

    assert "…(이하 일정 추가 링크 생략" in out, "안전망 미발동 — 생략 안내문 없음"

    # 잘린 URL 조각 부재: 존재하는 모든 '일정 추가:' 링크는 완전한 형태여야 한다
    urls = re.findall(r"일정 추가: (\S+)", out)
    assert urls, "안전망 테스트 전제 실패 — 부분 포함 링크가 0개(임계값 재조정 필요)"
    for url in urls:
        assert GCAL_URL_RE.match(url), f"절단된/불완전 URL: {url}"

    # 이후 엔트리 텍스트 보존: 링크가 생략돼도 엔트리 자체(선두 '- ')는 삭제되지 않음
    full_entries = len(re.findall(r"^- ", no_link_out, re.MULTILINE))
    tight_entries = len(re.findall(r"^- ", out, re.MULTILINE))
    assert full_entries == tight_entries, (
        f"안전망이 엔트리 텍스트까지 삭제함 ({tight_entries} != {full_entries})"
    )
    print("F1 링크 바이트 안전망(생략 안내·URL 완전성·엔트리 텍스트 보존): PASS")


def test_budget_regression() -> None:
    for region in ("경기도", "시"):
        cal = server.get_enrollment_calendar(region=region, months_ahead=6)
        inv.assert_24kb(cal)
        inv.assert_footer(cal)
    print("F1 예산 회귀(최대 캘린더 응답 UTF-8 <= 24KB): PASS")


def test_search_only_upcoming() -> None:
    # 예정 카드에는 링크 부착
    out = server.search_courses(region="부천시", status=["예정"])
    assert "일정 추가:" in out, "'예정' 카드에 링크 미부착"
    # 접수중·마감·상시·미상 카드에는 링크 미부착 — 각 상태 카드에서 확인
    for status in ("접수중", "마감", "상시", "미상"):
        out = server.search_courses(status=[status], keyword="요가")
        if "일정 추가:" in out:
            # search_courses 응답에서 카드 블록별로 접수상태 마커를 확인해
            # '(예정)'이 아닌 카드 뒤에 링크가 붙었는지 정밀 검사
            for block in re.split(r"(?=^- \*\*\[)", out, flags=re.MULTILINE)[1:]:
                if "일정 추가:" in block:
                    assert f"({status})" not in block or status == "예정", (
                        f"폴백/비예정 카드({status})에 링크 부착"
                    )
    print("F1 검색 결과 '예정' 카드 한정 부착: PASS")


def main() -> int:
    test_gcal_link_unit()
    test_calendar_attachment()
    test_link_cap()
    test_link_safety_net()
    test_budget_regression()
    test_search_only_upcoming()
    print("\nCALENDAR CASES: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
