#!/usr/bin/env python3
"""페르소나별 예상 질문 세트 + seed_args (계획 §2.3, 게이트A 사용자 승인본 2026-07-04).

seed_args는 "이 질문을 받은 LLM이 만들 법한 정상 인자"의 수작업 라벨이다.
창의적 변형(오타·영어 enum·이상한 형식 등)은 arg_mutator가 이 시드에서 파생한다.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Question:
    qid: str
    persona: str
    text: str            # 사용자 발화 원문
    tool: str            # LLM이 고를 tool
    args: dict[str, Any] = field(default_factory=dict)  # 정상 인자 라벨
    cats: tuple[str, ...] = ()  # 질문 카테고리 (계획 §2.1)


QUESTIONS: list[Question] = [
    # P1 심사위원 — 반려지뢰·정확성 공략
    Question("P1-1", "P1", "강남구에서 할 수 있는 요가 강좌 알려줘",
             "search_courses", {"keyword": "요가", "region": "강남구"}, ("C5",)),
    Question("P1-2", "P1", "다음에 접수 열리는 거 확정으로 날짜 딱 알려줘",
             "get_enrollment_calendar", {"region": "서울"}, ("C6",)),
    Question("P1-3", "P1", "list all courses",
             "search_courses", {}, ("C3", "C7")),
    Question("P1-4", "P1", "존재하지 않는 강좌 ID 999999 상세 보여줘",
             "get_course_detail", {"course_id": 999999}, ("C5",)),
    Question("P1-5", "P1", "compare course 1 only",
             "compare_courses", {"course_ids": [1]}, ("C8",)),
    Question("P1-6", "P1", "카카오에서 만든 서비스야? 카카오 강좌 있어?",
             "search_courses", {"keyword": "카카오"}, ("C5",)),
    # P2 학부모 — 다필터 조합
    Question("P2-1", "P2", "우리 애 토요일 오전에 들을 수 있는 무료 코딩 수업 있어?",
             "search_courses",
             {"keyword": "코딩", "weekday": ["토"], "time_range": "오전", "free_only": True},
             ("C1", "C4")),
    Question("P2-2", "P2", "주말에 초등학생 대상 미술 강좌",
             "search_courses",
             {"keyword": "미술", "weekday": ["토", "일"], "target": "초등학생"}, ("C1",)),
    Question("P2-3", "P2", "평일 저녁 6시 이후 유아 프로그램",
             "search_courses",
             {"target": "유아", "weekday": ["월", "화", "수", "목", "금"], "time_range": "18:00-"},
             ("C4",)),
    Question("P2-4", "P2", "무료면서 제일 비싼 거 보여줘",
             "search_courses", {"free_only": True, "sort": "fee"}, ("C4",)),
    Question("P2-5", "P2", "강북구랑 노원구 둘 다 되는 거",
             "search_courses", {"region": "강북구"}, ("C4",)),
    # P3 어르신 — 구어·오타·막연
    Question("P3-1", "P3", "가까운데뭐배울거없나",
             "search_courses", {}, ("C2", "C8")),
    Question("P3-2", "P3", "복지관 강좌 알려줘요",
             "list_courses_by_center", {"center_name": "복지관"}, ("C1",)),
    Question("P3-3", "P3", "노인 대상 컴퓨터 배우는거",
             "search_courses", {"keyword": "컴퓨터", "target": "시니어"}, ("C1", "C2")),
    Question("P3-4", "P3", "지하철역 근처 있어?",
             "search_courses", {"keyword": "지하철"}, ("C8",)),
    Question("P3-5", "P3", "머 신청할수있는거 있어?",
             "search_courses", {"status": ["접수중"]}, ("C2",)),
    # P4 직장인 — 시간범위·비교
    Question("P4-1", "P4", "퇴근하고 7시 이후에 들을 수 있는 강좌",
             "search_courses", {"time_range": "19:00-"}, ("C4",)),
    Question("P4-2", "P4", "금요일부터 월요일까지 하는 프로그램",
             "search_courses", {"weekday": ["금", "토", "일", "월"]}, ("C4",)),
    Question("P4-3", "P4", "이 강좌들 비교해줘 3, 15, 27",
             "compare_courses", {"course_ids": [3, 15, 27]}, ("C1",)),
    Question("P4-4", "P4", "주말에만 하는 저렴한 자기계발",
             "search_courses", {"weekday": ["토", "일"], "sort": "fee"}, ("C1", "C4")),
    Question("P4-5", "P4", "평일 오전만 빼고 다",
             "search_courses", {"time_range": "오후"}, ("C4",)),
    # P5 외국인/다문화 — 한영 혼용
    Question("P5-1", "P5", "yoga classes in Gangnam",
             "search_courses", {"keyword": "yoga", "region": "강남구"}, ("C3", "C5")),
    Question("P5-2", "P5", "free courses for foreigners",
             "search_courses", {"free_only": True}, ("C3",)),
    Question("P5-3", "P5", "status: open, region: Seoul",
             "search_courses", {"status": ["open"], "region": "Seoul"}, ("C4",)),
    Question("P5-4", "P5", "Korean language class near me",
             "search_courses", {"keyword": "한국어"}, ("C3", "C8")),
    Question("P5-5", "P5", "show me courses on weekend",
             "search_courses", {"weekday": ["토", "일"]}, ("C3", "C4")),
    # P6 막연 탐색자 — 광역·무인자
    Question("P6-1", "P6", "다 보여줘",
             "search_courses", {}, ("C7",)),
    Question("P6-2", "P6", "강좌 목록 더",
             "search_courses", {"page": 2}, ("C7",)),
    Question("P6-3", "P6", "필터 옵션 뭐 있어?",
             "get_filter_options", {}, ("C1",)),
    Question("P6-4", "P6", "아무거나 추천",
             "search_courses", {}, ("C8",)),
    Question("P6-5", "P6", "페이지 100 보여줘",
             "search_courses", {"page": 100}, ("C4",)),
]
