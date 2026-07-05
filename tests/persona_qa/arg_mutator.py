#!/usr/bin/env python3
"""창의-인자 변형 시뮬레이터 (계획 §3). seed_args → LLM이 만들 법한 변형 리스트.

변형 클래스와 실행 경로 (Architect 판정 확정):
- M1 잘못된 타입   → 경로2 전용 (mcp.call_tool, pydantic 강제 층). 직접 호출은 거짓 신호.
- M2 없는 enum     → 경로1 (직접 함수호출)
- M3 이상한 형식   → 경로1. dropped 필드는 INV-SILENT-DROP 차등 검사 대상.
- M4 조합·경계     → 경로1

기존 버그 4건의 회귀 잠금: 영문 status enum(M2) · 열린 시간범위 '19:00-'(M3) ·
LIKE %/_ 이스케이프(M3 keyword) · 역순 요일범위 '금~월'(M3 weekday).
실LLM 탐사 스팟체크(S3.5)에서 수확한 신규 패턴은 이 파일에 규칙으로 환류한다.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Variant:
    cls: str                  # M1 | M2 | M3 | M4
    path: int                 # 1=직접 함수호출, 2=mcp.call_tool(pydantic)
    tool: str
    args: dict[str, Any]
    note: str
    dropped: str | None = None  # INV-SILENT-DROP 차등 검사 시 제거할 필드


def _v(out: list[Variant], cls: str, path: int, tool: str, args: dict[str, Any],
       note: str, dropped: str | None = None) -> None:
    out.append(Variant(cls, path, tool, args, note, dropped))


def mutate(tool: str, args: dict[str, Any]) -> list[Variant]:
    out: list[Variant] = []
    if tool == "search_courses":
        # M1 — 타입 위반 (경로2)
        _v(out, "M1", 2, tool, {**args, "weekday": "월,화"}, "weekday를 list 아닌 str로")
        _v(out, "M1", 2, tool, {**args, "page": "first"}, "page를 str로")
        _v(out, "M1", 2, tool, {**args, "free_only": "예"}, "free_only를 str로")
        _v(out, "M1", 2, tool, {**args, "status": "open"}, "status를 list 아닌 str로")
        # M2 — 없는 enum
        _v(out, "M2", 1, tool, {**args, "status": ["D-day", "active"]}, "정의 안 된 status 값")
        # 'always-open' 하이픈 변형: S9 실LLM 실측(7/5) — 상시 탈락으로 거짓 0건이던 결함 회귀 잠금
        _v(out, "M2", 1, tool, {**args, "status": ["open", "upcoming", "always-open"]},
           "status 구분자 변형(hyphen)")
        _v(out, "M2", 1, tool, {**args, "status": ["Always Open"]}, "status 공백+대문자 변형")
        _v(out, "M2", 1, tool, {**args, "sort": "cheapest"}, "정의 안 된 sort 값")
        _v(out, "M2", 1, tool, {**args, "target": "kids"}, "영문 target")
        _v(out, "M2", 1, tool, {**args, "time_range": "night"}, "영문 time_range 버킷")
        # M3 — 이상한 형식 (기존 버그 ②·③·④ 일반화)
        for tr in ("19:00-", "-12:00", "오후7시", "저녁 7시 이후"):
            _v(out, "M3", 1, tool, {**args, "time_range": tr},
               f"time_range 비정형 '{tr}'", dropped="time_range")
        for wd in (["금~월"], ["주말"], ["weekend"], ["월~수", "금"]):
            _v(out, "M3", 1, tool, {**args, "weekday": wd},
               f"weekday 비정형 {wd}", dropped="weekday")
        # '서울 강북구' 복합 지역명: 확인 스팟체크 실측 패턴(7/4) — 토큰 AND 매칭 회귀 잠금
        for rg in ("강남 근처", "서울시 강남구청 옆", "서울 강북구", "서울특별시 강남구"):
            _v(out, "M3", 1, tool, {**args, "region": rg},
               f"region 비정형 '{rg}'", dropped="region")
        for kw in ("%", "_", "100%할인", "C++", "요가%"):
            _v(out, "M3", 1, tool, {**args, "keyword": kw}, f"keyword LIKE 메타문자 '{kw}'")
        # M4 — 경계·조합
        _v(out, "M4", 1, tool, {**args, "page": 0}, "page=0")
        _v(out, "M4", 1, tool, {**args, "page": -3}, "page 음수")
        _v(out, "M4", 1, tool, {**args, "page": 100000}, "page 초과")
        _v(out, "M4", 1, tool, {**args, "free_only": True, "keyword": "프리미엄"},
           "무료+유료성 키워드 모순 조합")
    elif tool == "compare_courses":
        _v(out, "M1", 2, tool, {"course_ids": "1,2,3"}, "course_ids를 str로")
        _v(out, "M1", 2, tool, {"course_ids": [1.5, 2.5]}, "course_ids를 float로")
        _v(out, "M4", 1, tool, {"course_ids": []}, "빈 리스트")
        _v(out, "M4", 1, tool, {"course_ids": [1]}, "1개 (최소 미달)")
        _v(out, "M4", 1, tool, {"course_ids": [1, 2, 3, 4, 5, 6]}, "6개 (최대 초과)")
        _v(out, "M4", 1, tool, {"course_ids": [1, 1]}, "중복 ID")
        _v(out, "M4", 1, tool, {"course_ids": [-1, 999999]}, "음수+존재 안 함")
        _v(out, "M4", 1, tool, {"course_ids": [999999, 999998]}, "전부 존재 안 함")
    elif tool == "get_course_detail":
        _v(out, "M1", 2, tool, {"course_id": "abc"}, "course_id를 str로")
        _v(out, "M1", 2, tool, {"course_id": 3.7}, "course_id를 float로")
        _v(out, "M4", 1, tool, {"course_id": -1}, "음수 ID")
        _v(out, "M4", 1, tool, {"course_id": 0}, "0 ID")
        _v(out, "M4", 1, tool, {"course_id": 10**12}, "초대형 ID")
    elif tool == "get_enrollment_calendar":
        _v(out, "M1", 2, tool, {**args, "months_ahead": "two"}, "months_ahead를 str로")
        _v(out, "M2", 1, tool, {"region": "Seoul"}, "영문 region")
        _v(out, "M3", 1, tool, {"center_name": "주민센터%"}, "center_name LIKE 메타문자")
        _v(out, "M4", 1, tool, {}, "region·center_name 둘 다 없음 (C8)")
        _v(out, "M4", 1, tool, {**args, "months_ahead": 0}, "months_ahead=0")
        _v(out, "M4", 1, tool, {**args, "months_ahead": 99}, "months_ahead 초과")
    elif tool == "list_courses_by_center":
        _v(out, "M1", 2, tool, {**args, "page": "one"}, "page를 str로")
        _v(out, "M3", 1, tool, {"center_name": "%"}, "center_name LIKE 와일드카드 단독")
        _v(out, "M4", 1, tool, {}, "center_name·region 둘 다 없음 (C8)")
        _v(out, "M4", 1, tool, {**args, "page": 9999}, "page 초과")
    elif tool == "get_filter_options":
        _v(out, "M1", 2, tool, {"region": 123}, "region을 int로")
        _v(out, "M3", 1, tool, {"region": "강남 근처"}, "region 비정형")
        _v(out, "M3", 1, tool, {"region": "%"}, "region LIKE 와일드카드")
    return out


def smoke() -> None:
    """회귀 잠금 증명: 기존 버그 4건 패턴이 변형본으로 생성되는지."""
    vs = mutate("search_courses", {})
    reprs = [f"{v.cls}:{v.args}" for v in vs]
    joined = "\n".join(reprs)
    assert any("'time_range': '19:00-'" in r for r in reprs), "버그② 열린 시간범위 미생성"
    assert any("'keyword': '%'" in r for r in reprs), "버그③ LIKE 와일드카드 미생성"
    assert any("금~월" in r for r in reprs), "버그④ 역순 요일범위 미생성"
    vs_status = mutate("search_courses", {"status": ["접수중"]})
    assert any(v.cls == "M1" and v.args.get("status") == "open" for v in vs_status), \
        "버그① 영문 status(str) 미생성"
    assert any(v.cls == "M2" and "D-day" in str(v.args.get("status")) for v in vs_status), \
        "없는 enum status 미생성"
    print(f"MUTATOR SMOKE PASS — search_courses 기준 변형 {len(vs)}개 생성\n{joined[:400]}…")


if __name__ == "__main__":
    smoke()
