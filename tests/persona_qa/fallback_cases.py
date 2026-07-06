#!/usr/bin/env python3
"""빈 결과 2-tier 폴백 회귀 케이스 (계획 empty-result-fallback-plan v3 §3.3).

발동 3종(마감 tier 2 + 미상 tier 1) · 미발동 3종 · status=[] 대칭 1종.
발동 케이스는 card_ids 비어있지 않음 + INV 위반 0을 단언해
카드 형식이 앵커 파손형으로 재퇴행하면 시끄럽게 실패한다.
실행: python3 tests/persona_qa/fallback_cases.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server  # noqa: E402
from tests.persona_qa import invariants as inv  # noqa: E402


def assert_fired(out: str, tier_marker: str, ctx: str) -> None:
    assert "[범위 확장]" in out and "과거 운영 이력" in out, f"{ctx}: 폴백 미발동"
    assert "결과" not in out.split("\n")[0], f"{ctx}: 헤더에 '결과' 단어 (오라클 충돌)"
    assert tier_marker in out, f"{ctx}: {tier_marker} 마커 없음"
    assert inv.card_ids(out), f"{ctx}: card_ids 빈 값 — 카드 앵커 파손"
    v = inv.violations(out, "search_courses")
    assert not v, f"{ctx}: INV 위반 {v}"


def assert_not_fired(out: str, ctx: str) -> None:
    assert "[범위 확장]" not in out, f"{ctx}: 폴백이 발동하면 안 됨"
    assert "[마감]" not in out and "[미상]" not in out, f"{ctx}: 폴백 마커 유출"


def main() -> int:
    # 발동 1 — 노원구 기본 검색 (전부 마감 지역): 마감 tier
    out = server.search_courses(region="노원구")
    assert_fired(out, "[마감]", "노원구 기본")
    # 발동 2 — 부산 요가 (마감 14건): 마감 tier + 필터(keyword) 유지 확인
    out = server.search_courses(region="부산", keyword="요가")
    assert_fired(out, "[마감]", "부산 요가")
    # 발동 3 — 김해시 (마감 0·미상 다수): 미상 tier
    out = server.search_courses(region="김해시")
    assert_fired(out, "[미상]", "김해시 기본")
    assert "접수 기간이 명시되지 않은" in out, "김해시: 미상 tier 라벨 아님"
    # 미발동 1 — 딥 페이지네이션 (기본 total>0, offset 초과): 기존 안내 유지
    out = server.search_courses(region="거창군", page=999)
    assert_not_fired(out, "거창군 딥페이지")
    assert "없습니다" in out, "딥페이지: 기존 안내 아님"
    # 미발동 2 — 명시 status 0건: 계약 존중, 폴백 금지
    out = server.search_courses(region="노원구", status=["접수중"])
    assert_not_fired(out, "노원구 명시 접수중")
    assert "없습니다" in out
    # 미발동 3 — 마감·미상도 0건인 미존재 지역: 기존 안내
    out = server.search_courses(region="화성외계신도시구")
    assert_not_fired(out, "미존재 지역")
    assert "없습니다" in out
    # 대칭 — status=[]는 기본 검색과 동일 취급: 폴백 발동
    out = server.search_courses(region="노원구", status=[])
    assert_fired(out, "[마감]", "노원구 status=[]")
    print("FALLBACK CASES: 7/7 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
