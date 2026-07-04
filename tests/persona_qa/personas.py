#!/usr/bin/env python3
"""페르소나 6종 정의 (계획 §2.2, 게이트A 승인본)."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    style: str          # 말투·성향
    interests: str      # 관심사
    quirk: str          # 엉뚱 입력 성향
    target_cats: list[str] = field(default_factory=list)  # 주 표적 카테고리


PERSONAS: dict[str, Persona] = {
    p.id: p
    for p in [
        Persona("P1", "심사위원", "냉정·의도적 엣지", "반려지뢰·정확성",
                "없는 지역 강좌 캐묻기, 예측 확정요구, 영어질문", ["C5", "C6", "C3"]),
        Persona("P2", "학부모", "다정·조건 많음", "자녀·주말·무료",
                "여러 필터 동시, 특정 시간대", ["C1", "C4", "C7"]),
        Persona("P3", "어르신", "구어·오타·비정형", "가까운 곳·쉬운 것",
                "띄어쓰기 없음, 방언, 막연", ["C2", "C8"]),
        Persona("P4", "직장인", "효율·바쁨", "저녁·주말·빠른 비교",
                "열린 시간범위('7시 이후'), compare", ["C4", "C1"]),
        Persona("P5", "외국인/다문화", "영어·한영혼용", "지역 강좌·언어",
                "영어 enum, 'yoga in Gangnam'", ["C3", "C4", "C5"]),
        Persona("P6", "막연 탐색자", "광범위·수동", "'뭐든 보여줘'",
                "필터 0개 광역질의, '다 보여줘'", ["C7", "C8"]),
    ]
}
