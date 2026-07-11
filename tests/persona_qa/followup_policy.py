#!/usr/bin/env python3
"""결정적 후속 턴 정책 (계획 dead-end-zero-loop v4 단계 4).

LLM이 tool description 지시를 따를 때의 후속 행동을 결정적으로 재현하는
reader. 멀티턴 시뮬레이션이 곧 "서버 유도 문구가 실제로 막다른 길을 여는가"의
검증이 된다. tool description(server.py)과 1:1 정렬 — description의 유도
문구를 바꾸면 여기도 함께 동기화한다.

오라클(invariants.novelty / dead_end_loop 판정)과 이 정책은 같은
response_content를 읽지만 역할이 다르다: 정책은 턴 생성기, 오라클은 진전
판정기. AC4의 known-stall 픽스처는 정적 캡처본이라 이 모듈을 거치지 않는다
(오라클-시뮬레이터 재결합 방지).
"""
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.persona_qa import invariants as inv  # noqa: E402

# 완화 래더 — 제약이 강한 필터부터 1개씩 제거 (계획 단계 4 순서 고정)
RELAX_LADDER = ("free_only", "weekday", "time_range", "keyword")
# 서버 최종 분기의 필터 완화 힌트: "{인자명} 조건을 빼면 … {인자명} 없이 …"
HINT_RE = re.compile(r"※ (\w+) 조건을 빼면")


def _relax(args: dict[str, Any]) -> dict[str, Any] | None:
    """가장 제약 강한 필터 1개를 제거한 인자. 제거할 게 없으면 None(소진)."""
    for f in RELAX_LADDER:
        if args.get(f):
            return {k: v for k, v in args.items() if k != f}
    return None


def next_turn(
    prev_tool: str,
    prev_args: dict[str, Any],
    prev_text: str,
    persona: str,
    turn_idx: int,
) -> tuple[str, dict[str, Any]] | None:
    """이전 턴 응답을 읽고 다음 턴 (tool, args)를 결정. None = 궤적 종료.

    분기 우선순위는 계획 v4 단계 4 순서를 따른다. tier3b(alt_regions) 분기가
    페르소나 quirk보다 앞이다 — 서버가 명시적 재호출 파라미터를 줬는데 무시하면
    stall 오탐(v4 델타 2)이 된다.
    """
    rc = inv.response_content(prev_text)
    region = prev_args.get("region")

    # tier3b [타지역 안내] → 제시된 타 시도로 region 교체 재검색 (v4 델타 2).
    # 이 파라미터가 미소진인 동안 러너는 terminal '완화 소진'으로 판정하지 않는다.
    if rc["alt_regions"]:
        new_args = dict(prev_args)
        new_args["region"] = rc["alt_regions"][0]
        return ("search_courses", new_args)

    # P6 막연 탐색자 quirk: 무완화 반복 — 조건을 안 바꾸고 같은 요청을 되풀이
    # (stall 유발 성향의 실사용 재현, 계획 단계 4)
    if persona == "P6":
        return (prev_tool, dict(prev_args))

    # P1 심사위원 quirk: "확정 날짜 딱 알려줘" 압박 — 캘린더로 넘어가
    # INV-PREDICTION(단정 금지) 표면을 반드시 한 번 노출시킨다
    if persona == "P1" and prev_tool != "get_enrollment_calendar" and region:
        return ("get_enrollment_calendar", {"region": region})

    if prev_tool == "search_courses":
        # 서버가 이미 완화함([범위 확장]/[지역 확장]) → 재완화 금지 (이중 완화 방지)
        if rc["expansion_open"]:
            if rc["card_ids"] and persona in ("P1", "P4") and region:
                # 예측 관심 페르소나는 카드 수용 후 예측축으로 심화
                return ("get_enrollment_calendar", {"region": region})
            return None  # 카드 수용 — 궤적 종료
        if rc["card_ids"]:
            return None  # 1차 카드 수용
        if rc["predict_dates"] and region:
            return ("get_enrollment_calendar", {"region": region})
        # 서버가 실측 프로브로 지목한 필터 완화 힌트 → 해당 인자만 제거 (래더보다 우선)
        m = HINT_RE.search(prev_text)
        if m and prev_args.get(m.group(1)):
            return (
                "search_courses",
                {k: v for k, v in prev_args.items() if k != m.group(1)},
            )
        # 0카드·미확장 → 완화 래더
        relaxed = _relax(prev_args)
        if relaxed is not None:
            return ("search_courses", relaxed)
        # 완화 소진 + filter_ptr → 실호출 (포인터가 실제 진전을 내는지 실측)
        if "get_filter_options" in prev_text:
            return ("get_filter_options", {"region": region} if region else {})
        return None

    if prev_tool in ("compare_courses", "get_course_detail"):
        # 서버 오류 안내가 search_courses를 명시 유도(무효 ID·개수 위반) →
        # description 정렬 원칙상 LLM은 검색으로 복귀한다
        if "search_courses" in prev_text and not rc["card_ids"]:
            return ("search_courses", {"region": region} if region else {})
        return None

    if prev_tool == "get_enrollment_calendar":
        # 예측이 안 나온 캘린더 → 지역 강좌 재검색으로 카드축 전환
        if not rc["predict_dates"] and region:
            return ("search_courses", {"region": region})
        return None

    if prev_tool == "get_filter_options":
        if region:
            return ("search_courses", {"region": region})
        return None

    return None
