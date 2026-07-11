#!/usr/bin/env python3
"""dead-end 유발 시드 (계획 dead-end-zero-loop v4 단계 3).

question_bank(게이트A 승인본)는 동결 — 골든 라벨 오염 방지를 위해 신규 시드는
이 파일로 분리한다(R7). 시드는 동결 서버(c4de4ff) 프로브로 실측 검증된
dead-end 후보 클래스에서 선정:

- FINAL-BRANCH: keyword가 데이터에 존재하나 접수가능(접수중·예정·상시) 0건이라
  tier3b(접수가능만 집계)가 무발동 → 최종 '없습니다' 분기 낙하
  (수영·축구·발레·주짓수 실측)
- TIER3B: 시도 전체 0건 + 타 시도 접수가능 보유 → [타지역 안내] 실쌍
  (세종+요가→경기도, 제주+코딩→전북 실측)
- CAL-NOPRED: enrollment_patterns 공백 지역 캘린더 — 예측 0, 문의처(전화)만
  (관악구·세종 실측)
- PRESSURE: P1 확정 날짜 압박 (INV-PREDICTION 표면 재검증)
- MULTI-FILTER: 다필터 0건 → 완화 래더 전 단계 통과 검증

axis는 기대 충족축 라벨(리포트·AC5용) — 판정 자체는 dead_end_loop의
페르소나 규칙(intent_satisfied)이 한다.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Seed:
    qid: str
    persona: str
    text: str            # 사용자 발화 원문
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    axis: tuple[str, ...] = ()   # 기대 충족축: card | predict | phone
    cls: str = ""                # dead-end 후보 클래스 (리포트 집계용)


SEEDS: list[Seed] = [
    # FINAL-BRANCH — 커버리지 갭/접수가능 전무 keyword
    Seed("D1", "P2", "강남구에서 수영 배울 수 있는 데 있어?",
         "search_courses", {"region": "강남구", "keyword": "수영"},
         ("card",), "FINAL-BRANCH"),
    Seed("D2", "P2", "강남구 애들 축구 교실 알려줘",
         "search_courses", {"region": "강남구", "keyword": "축구"},
         ("card",), "FINAL-BRANCH"),
    Seed("D3", "P4", "울산에서 발레 배우고 싶은데",
         "search_courses", {"region": "울산", "keyword": "발레"},
         ("card", "predict"), "FINAL-BRANCH"),
    Seed("D4", "P3", "서울에 주짓수 도장 같은 거 있나",
         "search_courses", {"region": "서울", "keyword": "주짓수"},
         ("card", "phone"), "FINAL-BRANCH"),
    Seed("D5", "P1", "강남구 지하철역 근처 강좌만 보여줘",
         "search_courses", {"region": "강남구", "keyword": "지하철"},
         ("card", "phone"), "FINAL-BRANCH"),
    # TIER3B — 타지역 교차 대안 실쌍 (프로브 검증)
    Seed("D6", "P5", "세종에서 요가 클래스 찾아줘",
         "search_courses", {"region": "세종", "keyword": "요가"},
         ("card",), "TIER3B"),
    Seed("D7", "P5", "제주 코딩 수업 있어?",
         "search_courses", {"region": "제주", "keyword": "코딩"},
         ("card",), "TIER3B"),
    Seed("D8", "P2", "세종에서 애 요가 시키고 싶어",
         "search_courses", {"region": "세종", "keyword": "요가"},
         ("card",), "TIER3B"),
    # CAL-NOPRED — 예측 공백 지역 캘린더
    Seed("D9", "P4", "관악구 다음 접수 언제 열려?",
         "get_enrollment_calendar", {"region": "관악구"},
         ("predict", "card"), "CAL-NOPRED"),
    Seed("D10", "P4", "세종 접수 일정 알려줘",
         "get_enrollment_calendar", {"region": "세종"},
         ("predict", "card"), "CAL-NOPRED"),
    Seed("D11", "P3", "관악구 문의 전화번호라도 줘요",
         "get_enrollment_calendar", {"region": "관악구"},
         ("phone",), "CAL-NOPRED"),
    # PRESSURE — P1 확정 요구 (INV-PREDICTION 표면)
    Seed("D12", "P1", "서울 다음 접수 확정 날짜만 딱 말해",
         "get_enrollment_calendar", {"region": "서울"},
         ("predict",), "PRESSURE"),
    # MULTI-FILTER — 완화 래더 전 단계 통과
    Seed("D13", "P2", "서울 토요일 저녁 무료 코딩 수업",
         "search_courses",
         {"region": "서울", "keyword": "코딩", "free_only": True,
          "weekday": ["토"], "time_range": "저녁"},
         ("card",), "MULTI-FILTER"),
    # 영문 keyword + 지역 (P5 실사용 패턴)
    Seed("D14", "P5", "yoga class in Sejong",
         "search_courses", {"region": "세종", "keyword": "yoga"},
         ("card",), "FINAL-BRANCH"),
]
