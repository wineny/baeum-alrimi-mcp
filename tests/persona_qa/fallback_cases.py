#!/usr/bin/env python3
"""빈 결과 4-tier 폴백 회귀 케이스 (계획 baeum-expansion-3 §2 기능2).

발동: 마감 tier 2 + 미상 tier 1 + 지역확장(tier3a) 1 + 타지역 안내(tier3b) 2.
미발동: 딥페이지·명시status·미존재지역 3종 + tier3b 최후수단 성격(tier1/2/3a
선점 케이스에서 미발동) 4종 + status=[] 대칭 1종.
발동 케이스는(카드형인 마감·미상·tier3a) card_ids 비어있지 않음 + INV 위반 0을
단언해 카드 형식이 앵커 파손형으로 재퇴행하면 시끄럽게 실패한다. tier3b는
집계 응답이라 card_ids 없음 — INV-EMPTY-QUALITY(없음+대안 문구)로 대체 검증.
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
    assert "[타지역 안내]" not in out, f"{ctx}: tier3b가 tier1/2보다 먼저 나오면 안 됨"
    assert inv.card_ids(out), f"{ctx}: card_ids 빈 값 — 카드 앵커 파손"
    v = inv.violations(out, "search_courses")
    assert not v, f"{ctx}: INV 위반 {v}"


def assert_not_fired(out: str, ctx: str) -> None:
    assert "[범위 확장]" not in out, f"{ctx}: 폴백이 발동하면 안 됨"
    assert "[마감]" not in out and "[미상]" not in out, f"{ctx}: 폴백 마커 유출"
    assert "[지역 확장]" not in out, f"{ctx}: tier3a 마커 유출"
    assert "[타지역 안내]" not in out, f"{ctx}: tier3b 마커 유출"


def assert_tier3a(out: str, region: str, broad: str, ctx: str) -> None:
    assert "결과" not in out.split("\n")[0], f"{ctx}: 헤더에 '결과' 단어 (오라클 충돌)"
    assert "[지역 확장]" in out, f"{ctx}: tier3a 마커 없음"
    assert f"'{region}' 0건 → '{broad}' 전체" in out, f"{ctx}: 헤더 지역 라벨 형식 불일치"
    assert "[타지역 안내]" not in out, f"{ctx}: tier3b가 tier3a보다 먼저 나오면 안 됨"
    assert inv.card_ids(out), f"{ctx}: card_ids 빈 값 — 카드 앵커 파손"
    v = inv.violations(out, "search_courses")
    assert not v, f"{ctx}: INV 위반 {v}"


def assert_tier3b(out: str, ctx: str) -> None:
    assert "결과" not in out.split("\n")[0], f"{ctx}: 헤더에 '결과' 단어 (오라클 충돌)"
    assert "[타지역 안내]" in out, f"{ctx}: tier3b 마커 없음"
    assert "0건입니다" not in out, f"{ctx}: tier1/2 전용 문구 유출"
    assert "region을 바꿔" in out, f"{ctx}: 재호출 파라미터 힌트 없음"
    assert not inv.card_ids(out), f"{ctx}: tier3b는 카드 없이 집계만이어야 함"
    v = inv.violations(out, "search_courses")
    assert not v, f"{ctx}: INV 위반 {v}"


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
    # 발동 4 — 강남구 요가 (tier3a 지역확장 골든): 커버리지 갭 메움(§2-1, 무조건)
    out = server.search_courses(region="강남구", keyword="요가")
    assert_tier3a(out, "강남구", "서울특별시", "강남구 요가(지역확장)")
    # 발동 5 — 세종특별자치시 운동 (tier3b 실쌍 골든, 프로브 확정 §2-2)
    out = server.search_courses(region="세종특별자치시", keyword="운동")
    assert_tier3b(out, "세종 운동(타지역 안내)")
    assert "전북특별자치도(43)" in out, "세종 운동: 보유 현황 집계값 불일치"
    assert 'region="전북특별자치도"' in out, "세종 운동: 재호출 힌트 시도 불일치"
    out2 = server.search_courses(region="세종특별자치시", keyword="운동")
    assert out == out2, "세종 운동: tier3b 비결정적 응답 (ORDER BY 안정성 붕괴)"
    # 발동 6 — 제주특별자치도 디지털 (tier3b 실쌍 골든 #2, 다른 타지역 분포 교차확인)
    out = server.search_courses(region="제주특별자치도", keyword="디지털")
    assert_tier3b(out, "제주 디지털(타지역 안내)")
    assert "전북특별자치도(64)" in out, "제주 디지털: 보유 현황 집계값 불일치"
    # 미발동 4 — 노원구 요가(마감 tier 선점): tier3b는 최후수단이라 미발동
    out = server.search_courses(region="노원구", keyword="요가")
    assert_fired(out, "[마감]", "노원구 요가")
    # 미발동 5 — 미존재 지역 + keyword: region 존재 확인 가드로 tier3b도 미발동
    out = server.search_courses(region="화성외계신도시구", keyword="요가")
    assert_not_fired(out, "미존재 지역+키워드")
    assert "없습니다" in out
    # params 재사용 크래시 방지 — region+keyword+time_range 동시 0건에서도
    # :bucket 바인딩 누락 없이 tier3b가 정상 발화(§2-2 INV-CRASH 가드)
    out = server.search_courses(
        region="세종특별자치시", keyword="운동", time_range="morning"
    )
    assert_tier3b(out, "세종 운동 오전(params 재사용)")
    print("FALLBACK CASES: 14/14 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
