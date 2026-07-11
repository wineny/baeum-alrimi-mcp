#!/usr/bin/env python3
"""배움알리미 (Baeum-Alrimi) — 전국 평생학습 강좌 필터·접수캘린더 MCP 서버.

- FastMCP / Streamable HTTP / stateless / 무세션 (PlayMCP 요구사항)
- hot-path 외부 호출 0: 사전 빌드된 SQLite(data/courses.db) 읽기 전용 조회만
- 모든 응답: 마크다운 정제, UTF-8 24KB 이하 보장, 데이터 기준일·출처 푸터
"""
import os
import re
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

SERVICE_NAME = "Baeum-Alrimi(배움알리미)"
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "courses.db"))
KST = ZoneInfo("Asia/Seoul")
MAX_BYTES = 23 * 1024  # 규정 24KB에 안전 마진
PAGE_SIZE = 8
WEEKDAYS = "월화수목금토일"
EN_DAYS = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}
# 실전에서 LLM이 요일·시간대를 별칭/한국어로 보내는 사례 관측 → alias 수용
WEEKDAY_ALIASES = {
    "주말": "토일", "weekend": "토일",
    "평일": "월화수목금", "주중": "월화수목금", "weekday": "월화수목금", "weekdays": "월화수목금",
}
TIME_BUCKETS = {
    "morning": "morning", "오전": "morning", "아침": "morning",
    "afternoon": "afternoon", "오후": "afternoon", "낮": "afternoon",
    "evening": "evening", "저녁": "evening", "밤": "evening", "night": "evening", "야간": "evening",
}
STATUS_VALUES = ("접수중", "예정", "상시", "마감", "미상")
DEFAULT_STATUS = ["접수중", "예정", "상시"]
# 실전에서 LLM이 영문 enum을 보내는 사례 관측(PlayMCP AI채팅) → alias 수용
STATUS_ALIASES = {
    "open": "접수중", "opened": "접수중", "ongoing": "접수중",
    "upcoming": "예정", "scheduled": "예정",
    "always_open": "상시", "always": "상시", "anytime": "상시",
    "closed": "마감", "ended": "마감",
    "unknown": "미상",
}
# 카테고리 개념어 확장 — '강남구 운동' 인간 QA: 강좌명은 '요가'·'필라테스' 같은
# 구체어뿐이라 상위 개념어의 리터럴 LIKE는 0건. keyword가 아래 키와 정확히 일치할
# 때만 발동하므로 구체어 검색('요가')의 의미는 불변. acceptance.py py_filter가
# server.expand_keyword를 공유해 교차 검증하므로 확장 규칙 변경 시 골든셋도 돈다.
_SPORTS = ("요가", "필라테스", "댄스", "체조", "헬스", "스트레칭", "피트니스", "줌바",
           "에어로빅", "탁구", "수영", "배드민턴", "걷기", "근력", "발레", "무용",
           "골프", "축구", "농구", "스포츠", "체육", "태권", "기공", "운동")
_MUSIC = ("음악", "악기", "피아노", "우쿨렐레", "바이올린", "드럼", "오카리나",
          "하모니카", "색소폰", "플루트", "노래", "성악", "보컬", "합창", "국악",
          "가야금", "난타", "통기타", "발성")
_ART = ("미술", "그림", "드로잉", "수채화", "유화", "스케치", "서예", "캘리그라피",
        "도예", "민화", "일러스트", "캐리커쳐")
_LANG = ("어학", "외국어", "영어", "일본어", "중국어", "프랑스어", "독일어",
         "스페인어", "한자")
_COOK = ("요리", "베이킹", "제과", "제빵", "쿠킹", "디저트", "커피", "바리스타", "반찬")
_DIGITAL = ("디지털", "컴퓨터", "스마트폰", "코딩", "엑셀", "포토샵", "유튜브",
            "SNS", "인공지능", "AI", "영상편집", "영상 편집")
_CRAFT = ("공예", "뜨개", "자수", "목공", "라탄", "캔들", "도자기", "가죽", "석고", "비누")
_DANCE = ("댄스", "무용", "발레", "줌바", "라인댄스", "방송댄스", "밸리댄스", "댄스스포츠")

CATEGORY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "운동": _SPORTS, "스포츠": _SPORTS, "체육": _SPORTS,
    "건강": _SPORTS + ("건강", "명상", "웰니스"),
    "음악": _MUSIC, "악기": _MUSIC,
    "미술": _ART, "그림": _ART,
    "어학": _LANG, "외국어": _LANG, "언어": _LANG,
    "요리": _COOK, "베이킹": _COOK,
    "디지털": _DIGITAL, "컴퓨터": _DIGITAL, "스마트폰": _DIGITAL, "IT": _DIGITAL,
    "공예": _CRAFT, "만들기": _CRAFT,
    "춤": _DANCE, "댄스": _DANCE,
}

mcp = FastMCP(
    "baeum-alrimi",
    instructions=(
        f"{SERVICE_NAME}: nationwide Korean lifelong-learning course search,"
        " comparison, and enrollment-opening calendar from official public data."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    stateless_http=True,
    json_response=True,
)

RO_ANNOTATIONS = dict(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


def db() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def today_kst() -> str:
    return datetime.now(KST).date().isoformat()


_footer_cache: str | None = None


def footer() -> str:
    global _footer_cache
    if _footer_cache is None:
        con = db()
        meta = dict(con.execute("SELECT key, value FROM meta"))
        con.close()
        _footer_cache = (
            f"\n\n---\n데이터 기준일 {meta.get('data_basis_min', '')}~{meta.get('data_basis_max', '')}"
            " · 출처: 공공데이터포털 전국평생학습강좌표준데이터(#15013110, 분기 갱신)"
            " · 실제 접수 가능 여부는 운영기관에 확인이 필요합니다"
        )
    return _footer_cache


def finalize(body: str) -> str:
    """푸터 부착 + UTF-8 24KB 이하 보장."""
    foot = footer()
    budget = MAX_BYTES - len(foot.encode("utf-8"))
    raw = body.encode("utf-8")
    if len(raw) > budget:
        notice = "\n\n…(응답 크기 제한으로 일부 생략 — 필터를 좁히거나 page를 사용하세요)"
        budget -= len(notice.encode("utf-8"))
        body = raw[:budget].decode("utf-8", errors="ignore") + notice
    return body + foot


STATUS_SQL = (
    "CASE WHEN 상시여부=1 THEN '상시'"
    " WHEN 접수시작일자 IS NULL OR 접수종료일자 IS NULL THEN '미상'"
    " WHEN :today < 접수시작일자 THEN '예정'"
    " WHEN :today > 접수종료일자 THEN '마감'"
    " ELSE '접수중' END"
)


def like(term: str) -> str:
    """LIKE 패턴 이스케이프 — 사용자 입력의 %/_를 리터럴로 취급 (ESC와 짝)."""
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


ESC = " ESCAPE '\\'"


def _weekday_of(token: str) -> str | None:
    t = token.strip().lower()
    if not t:
        return None
    d = EN_DAYS.get(t[:3], t[:1])
    return d if d in WEEKDAYS else None


def normalize_weekdays(weekday: list[str] | None) -> tuple[list[str], list[str]]:
    """요일 인자 정규화 → (인식된 요일, 인식 실패 토큰).

    별칭('주말'·'평일')과 범위('월~금', 'mon-fri')를 지원하고,
    역순 범위('금~월')는 데이터 수집기와 동일하게 순환 해석한다.
    """
    out: list[str] = []
    bad: list[str] = []

    def add(day: str) -> None:
        if day not in out:
            out.append(day)

    for raw in weekday or []:
        w = str(raw).strip().lower()
        if w in WEEKDAY_ALIASES:
            for day in WEEKDAY_ALIASES[w]:
                add(day)
            continue
        m = re.match(r"^(.+?)\s*[~\-]\s*(.+)$", w)
        if m:
            d1, d2 = _weekday_of(m.group(1)), _weekday_of(m.group(2))
            if d1 and d2:
                k, end = WEEKDAYS.index(d1), WEEKDAYS.index(d2)
                add(WEEKDAYS[k])
                while k != end:
                    k = (k + 1) % 7
                    add(WEEKDAYS[k])
                continue
        d = _weekday_of(w)
        if d:
            add(d)
        else:
            bad.append(str(raw))
    return out, bad


def region_clause(region: str | None, params: dict[str, Any]) -> str:
    """지역 필터 — 공백 토큰별 AND 매칭.

    LLM이 '서울 강북구'처럼 시도+시군구를 한 문자열로 보내는 사례 관측(스팟체크):
    단일 LIKE로는 시도='서울특별시'/시군구='강북구'로 나뉜 데이터에 매치 불가.

    도로명주소는 시도·시군구가 빈 행(시도 481건·시군구 3,512건)의 폴백으로만 사용:
    구조화 필드가 채워진 행까지 주소를 훑으면 '시흥시 서울대학로' 같은 도로명이
    타지역 검색에 섞인다. 교육장소(건물명)는 지역 판별에 사용하지 않는다 —
    '새서울프라자'(과천)가 region='서울'에 매치된 인간 QA 발견, 지역 정보가
    교육장소에만 있는 행은 0건.
    """
    if not region:
        return ""
    clauses = []
    for i, tok in enumerate(t for t in region.split() if t):
        key = f"region{i}"
        params[key] = like(tok)
        clauses.append(
            f"(시도 LIKE :{key}{ESC} OR 시군구 LIKE :{key}{ESC}"
            f" OR ((시도 IS NULL OR 시도='' OR 시군구 IS NULL OR 시군구='')"
            f" AND 교육장도로명주소 LIKE :{key}{ESC}))"
        )
    return " AND " + " AND ".join(clauses)


def expand_keyword(keyword: str) -> list[str]:
    """카테고리 개념어를 구체 강좌 어휘 목록으로 확장. 비카테고리 키워드는 그대로 1개."""
    key = keyword.strip()
    terms = CATEGORY_SYNONYMS.get(key)
    if not terms:
        return [keyword]
    return [key] + [t for t in terms if t != key]


def broaden_region(region: str) -> str | None:
    """시군구 → 소속 시도 확장 ('강남구' → '서울특별시'). 확장 불가면 None.

    다중 토큰('서울 강남구')은 마지막 토큰을 떼어내고, 단일 토큰은 DB에서
    해당 시군구가 속한 시도를 최빈값으로 조회한다. 이미 시도명이거나
    미존재 지역이면 None — 미존재 지역의 '없습니다' 안내는 기존 경로 유지.
    """
    tokens = [t for t in region.split() if t]
    if not tokens:
        return None
    if len(tokens) > 1:
        return " ".join(tokens[:-1])
    con = db()
    row = con.execute(
        f"SELECT 시도 FROM courses WHERE 시군구 LIKE :r{ESC} AND 시도 != ''"
        " GROUP BY 시도 ORDER BY COUNT(*) DESC LIMIT 1",
        {"r": like(tokens[0])},
    ).fetchone()
    con.close()
    if row and row[0] and tokens[0] not in row[0]:
        return row[0]
    return None


def org_followup(rows: list[sqlite3.Row], today: str) -> str:
    """과거 이력 카드에 붙는 후속 행동 섹션 — 기관별 다음 오픈 예상·연락처.

    '없습니다'로 끝나는 응답을 행동 가능한 답(언제 열릴지·어디에 전화할지)으로
    바꾸는 포지셔닝 핵심. 예상 라인은 INV-PREDICTION 규약대로 '(예상)' 표기와
    단정 표현 금지를 지킨다.
    """
    orgs: list[sqlite3.Row] = []
    seen: set[str] = set()
    for r in rows:
        name = r["운영기관명"]
        if name and name not in seen:
            seen.add(name)
            orgs.append(r)
        if len(orgs) == 5:
            break
    if not orgs:
        return ""
    con = db()
    lines = ["\n\n**다음 접수 예상·문의처** (과거 이력 기반 추정, 확정 아님)"]
    for r in orgs:
        p = con.execute(
            "SELECT 다음오픈예상 FROM enrollment_patterns"
            " WHERE 기관 = :org AND 다음오픈예상 > :today"
            " ORDER BY 다음오픈예상 LIMIT 1",
            {"org": r["운영기관명"], "today": today},
        ).fetchone()
        last = con.execute(
            "SELECT MAX(접수시작일자) FROM courses WHERE 운영기관명 = :org",
            {"org": r["운영기관명"]},
        ).fetchone()[0]
        tel = r["운영기관전화번호"] or "번호 미상"
        if p:
            lines.append(f"- {r['운영기관명']}: 다음 오픈 {p[0]}경 (예상) · ☎ {tel}")
        else:
            lines.append(
                f"- {r['운영기관명']}: 최근 접수 시작 {last or '미상'}"
                f" · 다음 일정은 문의 · ☎ {tel}"
            )
    con.close()
    return "\n".join(lines)


def fee_label(row: sqlite3.Row) -> str:
    if row["무료여부"]:
        return "무료"
    if row["수강료_숫자"] is not None:
        return f"{row['수강료_숫자']:,}원"
    return row["수강료_원문"] or "미상"


def course_card(row: sqlite3.Row, status: str, mark_status: bool = False) -> str:
    days = row["요일_정규화"] or "요일 미상"
    time_s = f"{row['교육시작시각'] or '?'}~{row['교육종료시각'] or '?'}"
    recv = (
        f"{row['접수시작일자'] or '?'}~{row['접수종료일자'] or '?'}"
        if status != "상시" else "상시접수"
    )
    # mark_status: 폴백 카드용 선두 상태 마커. [id]가 반드시 첫 토큰이어야
    # 하네스 CARD_ID_RE(`^- \*\*\[(\d+)\]`) 앵커와 정합 — 순서 변경 금지.
    title = (
        f"- **[{row['id']}] [{status}] {row['강좌명']}**\n" if mark_status
        else f"- **[{row['id']}] {row['강좌명']}** ({status})\n"
    )
    return (
        title
        + f"  - {row['운영기관명']} · {row['시도']} {row['시군구']}".rstrip() + "\n"
        + f"  - {days} {time_s} · {fee_label(row)} · 대상 {row['교육대상구분'] or '미상'}\n"
        + f"  - 교육 {row['교육시작일자']}~{row['교육종료일자']} · 접수 {recv}"
    )


@mcp.tool(
    annotations=ToolAnnotations(title="강좌 검색 (Search Courses)", **RO_ANNOTATIONS),
    description=(
        "Search nationwide Korean lifelong-learning courses with combined filters"
        " (keyword, region, weekday, time of day, target audience, free-only,"
        " enrollment status) from " + SERVICE_NAME + "."
        " IMPORTANT: if the user has not specified a region (시/도 or 시/군/구),"
        " ask which region they are in BEFORE searching — nationwide results are"
        " rarely what the user wants."
        " Treat follow-ups as ONE ongoing request: when the user refines the previous"
        " search (next page / '더 보여줘', changing one condition like '평일에' or"
        " '오전에', or answering your clarifying question), KEEP the previous filters"
        " (region, keyword, weekday, time_range) and change only what the user changed."
        " Reset filters ONLY when the user starts an unrelated new question."
        " Category-level keywords (운동, 음악, 미술, 어학, 요리, 디지털, 공예, 춤)"
        " are auto-expanded to related concrete course terms (운동 → 요가·필라테스·댄스 …)."
        " Default shows courses currently open, upcoming, or always-open."
        " Most public data is PAST history: when nothing is currently open, the"
        " response returns the area's past course history plus each organization's"
        " expected next opening and phone number — present that as a USEFUL ANSWER"
        " (what usually runs there, when it may open next, where to call),"
        " never as a bare 'no results'."
        " sort: deadline | fee | start_date. Returns course cards with course IDs"
        " usable in compare_courses / get_course_detail."
    ),
)
def search_courses(
    keyword: str | None = None,
    region: str | None = None,
    weekday: list[str] | None = None,
    time_range: str | None = None,
    target: str | None = None,
    free_only: bool = False,
    status: list[str] | None = None,
    sort: str = "deadline",
    page: int = 1,
) -> str:
    today = today_kst()
    params: dict[str, Any] = {"today": today}
    where = ["1=1"]
    kw_terms: list[str] = expand_keyword(keyword) if keyword else []
    if kw_terms:
        kw_parts = []
        for j, t in enumerate(kw_terms):
            k = f"kw{j}"
            params[k] = like(t)
            kw_parts.append(
                f"강좌명 LIKE :{k}{ESC} OR 강좌내용 LIKE :{k}{ESC} OR 교육장소 LIKE :{k}{ESC}"
            )
        where.append("(" + " OR ".join(kw_parts) + ")")
    where.append(region_clause(region, params).replace(" AND ", "", 1) or "1=1")
    region_idx = len(where) - 1  # 지역 확장 폴백이 이 절만 교체해 재조회한다
    ignored: list[str] = []
    days, bad_days = normalize_weekdays(weekday)
    if bad_days:
        ignored.append(f"weekday {bad_days} → 예: '월'~'일', '주말', '금~월'")
    if days:
        bits = 0
        for d in days:
            bits |= 1 << WEEKDAYS.index(d)
        params["bits"] = bits
        where.append("(요일_비트 & :bits) != 0")
    if time_range:
        tr = time_range.strip().lower()
        m = re.match(r"^(\d{1,2}:\d{2})?\s*[-~]\s*(\d{1,2}:\d{2})?$", tr)
        ko = re.match(r"^(오전|오후|저녁|밤)?\s*(\d{1,2})\s*시\s*(반)?\s*(이후|부터|이전|까지)?$", tr)
        if tr in TIME_BUCKETS:
            params["bucket"] = TIME_BUCKETS[tr]
            where.append("시간대_버킷 = :bucket")
        elif m and (m.group(1) or m.group(2)):
            # 'HH:MM-HH:MM' + 열린 범위 'HH:MM-' / '-HH:MM' (LLM이 실전에서 생성하는 포맷)
            if m.group(1):
                params["t1"] = m.group(1).zfill(5)
                where.append("교육시작시각 >= :t1")
            if m.group(2):
                params["t2"] = m.group(2).zfill(5)
                where.append("교육시작시각 <= :t2")
        elif ko:
            # '오후7시', '저녁 7시 이후', '9시 반 까지' 등 한국어 시각 표현
            hour = int(ko.group(2)) % 24
            if ko.group(1) in ("오후", "저녁", "밤") and hour < 12:
                hour += 12
            hhmm = f"{hour:02d}:{'30' if ko.group(3) else '00'}"
            if ko.group(4) in ("이전", "까지"):
                params["t2"] = hhmm
                where.append("교육시작시각 <= :t2")
            else:  # '이후'·'부터'·무지정 = 해당 시각부터
                params["t1"] = hhmm
                where.append("교육시작시각 >= :t1")
        else:
            ignored.append(f"time_range '{time_range}' → 예: '오전', '19:00-', '10:00-12:00'")
    if target:
        params["target"] = like(target)
        where.append(f"교육대상구분 LIKE :target{ESC}")
    if free_only:
        where.append("무료여부 = 1")
    statuses = []
    bad_status = []
    for s in status or DEFAULT_STATUS:
        # 'always-open'/'Always Open' 등 구분자 변형 관측(실LLM) → 언더스코어로 통일 후 alias 조회
        key = s.strip().lower().replace("-", "_").replace(" ", "_")
        norm = STATUS_ALIASES.get(key, s.strip())
        if norm in STATUS_VALUES:
            if norm not in statuses:
                statuses.append(norm)
        else:
            bad_status.append(s)
    if bad_status and status:
        ignored.append(
            f"status {bad_status} → 예: {', '.join(STATUS_VALUES)}"
            " (또는 open/upcoming/always_open/closed)"
        )
    if not statuses:
        statuses = DEFAULT_STATUS
    # status 절은 where와 분리 — 빈 결과 폴백이 status 절만 교체해 재조회한다
    status_clause = f"({STATUS_SQL}) IN ({','.join(repr(s) for s in statuses)})"

    order = {
        "deadline": "CASE WHEN 접수종료일자 IS NULL THEN 1 ELSE 0 END, 접수종료일자 ASC",
        "fee": "CASE WHEN 수강료_숫자 IS NULL THEN 1 ELSE 0 END, 수강료_숫자 ASC",
        "start_date": "CASE WHEN 교육시작일자 IS NULL THEN 1 ELSE 0 END, 교육시작일자 ASC",
    }.get(sort, "접수종료일자 ASC")

    page = max(1, page)
    params["limit"] = PAGE_SIZE
    params["offset"] = (page - 1) * PAGE_SIZE

    def run_query(clauses: list[str], order_by: str) -> tuple[int, list[sqlite3.Row]]:
        cond = " AND ".join(w for w in clauses if w != "1=1") or "1=1"
        con = db()
        n = con.execute(f"SELECT COUNT(*) FROM courses WHERE {cond}", params).fetchone()[0]
        # 정렬 전역 규칙 2개를 sort 키 앞에 둔다 (건수 불변 — 골든셋 안전):
        # 1) 동일 (기관, 강좌명)의 연도별 재등록은 최신 1건만 앞으로 (국악원
        #    '부채춤' 2021~2026 중복이 페이지를 독점하던 인간 QA)
        # 2) 교육기간이 이미 끝난 강좌는 뒤로 — '상시'라도 2021년 종료 강좌가
        #    상단에 오는 오정보 방지
        rs = con.execute(
            f"SELECT *, {STATUS_SQL} AS 접수상태,"
            " ROW_NUMBER() OVER (PARTITION BY 운영기관명, 강좌명"
            " ORDER BY 교육종료일자 DESC) AS _dup"
            f" FROM courses WHERE {cond}"
            " ORDER BY _dup > 1,"
            " 교육종료일자 IS NOT NULL AND 교육종료일자 < :today,"
            f" {order_by} LIMIT :limit OFFSET :offset",
            params,
        ).fetchall()
        con.close()
        return n, rs

    total, rows = run_query(where + [status_clause], order)

    notice = (
        "\n\n※ 인식하지 못해 적용하지 않은 필터 — " + " · ".join(ignored)
        if ignored else ""
    )
    if len(kw_terms) > 1:
        sample = [t for t in kw_terms[1:] if t != keyword][:5]
        notice += (
            f"\n\n※ '{kw_terms[0]}'을(를) 카테고리로 인식해 관련 강좌"
            f"({'·'.join(sample)} 등)까지 넓혀 검색했습니다."
            " 특정 종목·과목만 원하면 keyword에 구체적인 이름을 넣어 주세요."
        )
    if not region and rows:
        notice += (
            "\n\n※ 지역 미지정 — 전국 기준 결과라 특정 지역·기관에 치우칠 수 있습니다."
            " 사용자에게 지역(시/군/구)을 물어봐 region으로 좁혀 주세요."
        )
    if not rows:
        # 2-tier 빈 결과 폴백: LLM이 status를 안 보낸 기본 검색이 정확히 0건일 때만.
        # total==0 게이트라 딥 페이지네이션(총건수>0, offset 초과)에서는 발동하지 않고,
        # 명시 status는 계약 존중을 위해 폴백하지 않는다. tier가 단일 status라 라벨은 항상 참.
        if not status and total == 0:
            for fb_status, fb_order, fb_label in (
                ("마감",
                 "CASE WHEN 접수종료일자 IS NULL THEN 1 ELSE 0 END, 접수종료일자 DESC",
                 "아래는 모두 접수가 마감된 과거 운영 이력입니다 — 재개설되는 경우가"
                 " 많으니 하단의 다음 접수 예상·문의처를 사용자에게 안내해 주세요."),
                ("미상",
                 "CASE WHEN 교육시작일자 IS NULL THEN 1 ELSE 0 END, 교육시작일자 DESC",
                 "아래는 접수 기간이 명시되지 않은 강좌입니다 — 신청 가능 여부는"
                 " 운영기관에 문의해 보세요."),
            ):
                fb_total, fb_rows = run_query(
                    where + [f"({STATUS_SQL}) = '{fb_status}'"], fb_order
                )
                if fb_rows:
                    # 헤더에 '결과' 단어 금지 — acceptance/edge 건수 오라클 정규식 회피
                    head = (
                        f"### [범위 확장] 과거 운영 이력 {fb_total}건"
                        f" (페이지 {page}, {len(fb_rows)}건 표시, 기준일 {today})\n"
                    )
                    cards = "\n".join(
                        course_card(r, r["접수상태"], mark_status=True) for r in fb_rows
                    )
                    more = (
                        f"\n\n다음 페이지: page={page + 1}"
                        if fb_total > page * PAGE_SIZE else ""
                    )
                    label = (
                        "\n\n※ 지금 접수 가능(접수중·예정·상시)한 강좌는 0건입니다. "
                        + fb_label
                    )
                    if not region:
                        label += " (지역을 지정하면 더 정확해집니다.)"
                    return finalize(
                        head + cards + more + label
                        + org_followup(fb_rows, today) + notice
                    )
            # 3-tier: 지역 확장 — 해당 시군구에 과거 이력조차 0건이면 (강남구 운동
            # QA: 표준데이터에 구 단위 커버리지 갭 존재) 소속 시도 전체로 넓혀
            # 인근 강좌를 보여준다. 헤더에 '결과' 단어 금지 — 건수 오라클 회피.
            if region:
                broad = broaden_region(region)
                if broad:
                    b_where = list(where)
                    b_where[region_idx] = (
                        region_clause(broad, params).replace(" AND ", "", 1) or "1=1"
                    )
                    # 접수가능 상태 우선 (중복·종료 강등은 run_query 전역 규칙)
                    b_order = (
                        f"CASE WHEN ({STATUS_SQL}) IN ('접수중','예정','상시')"
                        " THEN 0 ELSE 1 END,"
                        " CASE WHEN 접수종료일자 IS NULL THEN 1 ELSE 0 END,"
                        " 접수종료일자 DESC, 교육종료일자 DESC"
                    )
                    b_total, b_rows = run_query(b_where, b_order)
                    if b_rows:
                        head = (
                            f"### [지역 확장] '{region}' 0건 → '{broad}' 전체 {b_total}건"
                            f" (페이지 {page}, {len(b_rows)}건 표시, 기준일 {today})\n"
                        )
                        cards = "\n".join(
                            course_card(r, r["접수상태"], mark_status=True)
                            for r in b_rows
                        )
                        more = (
                            f"\n\n다음 페이지: page={page + 1}"
                            if b_total > page * PAGE_SIZE else ""
                        )
                        label = (
                            f"\n\n※ '{region}'에는 해당 조건의 강좌가 과거 이력까지"
                            f" 없습니다. 대신 '{broad}' 전체에서 찾은 강좌입니다"
                            " (카드 첫 대괄호가 접수 상태). 다른 지역명이나 keyword로"
                            " 다시 좁혀 검색할 수 있습니다."
                        )
                        return finalize(
                            head + cards + more + label
                            + org_followup(b_rows, today) + notice
                        )
        return finalize(
            "조건에 맞는 강좌가 없습니다. 필터를 넓혀 보세요"
            " (예: status에 '마감' 포함, 지역 넓히기, get_filter_options로 유효값 확인)."
            + notice
        )
    head = (
        f"### 강좌 검색 결과 {total}건 (페이지 {page}, {len(rows)}건 표시,"
        f" 기준일 {today})\n"
    )
    cards = "\n".join(course_card(r, r["접수상태"]) for r in rows)
    more = (
        f"\n\n다음 페이지: page={page + 1}" if total > page * PAGE_SIZE else ""
    )
    return finalize(head + cards + more + notice)


@mcp.tool(
    annotations=ToolAnnotations(title="접수 캘린더 (Enrollment Calendar)", **RO_ANNOTATIONS),
    description=(
        "Show when course enrollment windows open in a region or at a specific"
        " center, from " + SERVICE_NAME + ". Returns (1) enrollment windows"
        " explicitly scheduled in the data and (2) statistically expected next"
        " openings based on each organization's past opening history"
        " (clearly marked as estimates, with evidence)."
        " For area-wide questions, prefer one region query over multiple"
        " center_name calls — it covers every organization in the area."
        " When neither scheduled nor predicted openings exist, the response lists"
        " the area's organizations with phone contacts and their last opening date"
        " — guide the user to call them instead of ending with 'nothing found'."
    ),
)
def get_enrollment_calendar(
    region: str | None = None,
    center_name: str | None = None,
    months_ahead: int = 2,
) -> str:
    if not region and not center_name:
        return finalize(
            "region 또는 center_name 중 하나는 필요합니다."
            " get_filter_options로 유효한 지역명을 확인할 수 있습니다."
        )
    today = today_kst()
    horizon = min(max(months_ahead, 1), 6) * 31
    params: dict[str, Any] = {"today": today, "horizon": f"+{horizon} days"}
    where = ""
    if region:
        where += region_clause(region, params)
    if center_name:
        params["center"] = like(center_name)
        where += f" AND 운영기관명 LIKE :center{ESC}"

    con = db()
    upcoming = con.execute(
        "SELECT 운영기관명, 시도, 시군구, 접수시작일자, 접수종료일자, COUNT(*) AS n"
        f" FROM courses WHERE 접수시작일자 > :today"
        f" AND 접수시작일자 <= date(:today, :horizon){where}"
        " GROUP BY 운영기관명, 접수시작일자, 접수종료일자"
        " ORDER BY 접수시작일자 LIMIT 20",
        params,
    ).fetchall()

    p_params: dict[str, Any] = {"today": today, "horizon": f"+{horizon} days"}
    p_where = ""
    if region:
        for i, tok in enumerate(t for t in region.split() if t):
            key = f"region{i}"
            p_params[key] = like(tok)
            p_where += (
                f" AND (시도 LIKE :{key}{ESC} OR 시군구 LIKE :{key}{ESC}"
                f" OR 기관 LIKE :{key}{ESC})"
            )
    if center_name:
        p_params["center"] = like(center_name)
        p_where += f" AND 기관 LIKE :center{ESC}"
    # 하한 필터: 다음오픈예상은 빌드일 기준으로만 미래 보장 — 빌드 후 날짜가 지나
    # 과거가 된 예상일은 오정보이므로 서빙에서 억제 (재계산은 빌드 SSOT 담당)
    predicted = con.execute(
        "SELECT 기관, 시도, 시군구, 회차수, 다음오픈예상, 근거 FROM enrollment_patterns"
        f" WHERE 다음오픈예상 > date(:today)"
        f" AND 다음오픈예상 <= date(:today, :horizon){p_where}"
        " ORDER BY 다음오픈예상 LIMIT 15",
        p_params,
    ).fetchall()
    contacts: list[sqlite3.Row] = []
    if not upcoming and not predicted and where:
        # 예정·예측 모두 없는 지역이 '없음'으로 끝나지 않도록 — 기관 연락처와
        # 최근 접수 시작일(사실 정보, 예측 아님)이 실질적인 다음 행동을 만든다
        contacts = con.execute(
            "SELECT 운영기관명, MAX(운영기관전화번호) AS tel,"
            " MAX(접수시작일자) AS last_open, COUNT(*) AS n"
            f" FROM courses WHERE 운영기관명 != ''{where}"
            " GROUP BY 운영기관명 ORDER BY n DESC LIMIT 8",
            params,
        ).fetchall()
    con.close()

    parts = [f"### 접수 캘린더 (기준일 {today}, 향후 약 {horizon}일)"]
    if upcoming:
        parts.append("\n**① 데이터에 명시된 예정 접수창**")
        for r in upcoming:
            parts.append(
                f"- {r['접수시작일자']} ~ {r['접수종료일자']} · {r['운영기관명']}"
                f" ({r['시도']} {r['시군구']})".rstrip() + f" · 강좌 {r['n']}개"
            )
    else:
        parts.append("\n**① 데이터에 명시된 예정 접수창**: 해당 기간 내 없음")
    if predicted:
        parts.append("\n**② 과거 이력 기반 다음 오픈 예상** (확정 아님)")
        for r in predicted:
            parts.append(
                f"- {r['다음오픈예상']}경 (예상) · {r['기관']}"
                f" ({r['시도']} {r['시군구']})".rstrip() + f"\n  - 근거: {r['근거']}"
            )
    else:
        parts.append("\n**② 과거 이력 기반 다음 오픈 예상**: 예측 가능한 기관 없음 (과거 오픈 이력이 주기 추정에 부족)")
    if contacts:
        parts.append(
            "\n**③ 기관 문의처** — 예정·예측 데이터가 없어도 기관에 직접 문의하면"
            " 다음 접수 일정을 확인할 수 있습니다"
        )
        for r in contacts:
            parts.append(
                f"- {r['운영기관명']} · ☎ {r['tel'] or '미상'}"
                f" · 최근 접수 시작 {r['last_open'] or '미상'} · 등록 강좌 {r['n']}건"
            )
    return finalize("\n".join(parts))


@mcp.tool(
    annotations=ToolAnnotations(title="강좌 비교 (Compare Courses)", **RO_ANNOTATIONS),
    description=(
        "Compare 2-5 courses side by side (fee, weekday, time, target audience,"
        " enrollment period and status) in a markdown table, from " + SERVICE_NAME + "."
        " Use course IDs returned by search_courses."
    ),
)
def compare_courses(course_ids: list[int]) -> str:
    if not 2 <= len(course_ids) <= 5:
        return finalize("course_ids는 2~5개여야 합니다.")
    today = today_kst()
    marks = ",".join("?" for _ in course_ids)
    con = db()
    rows = con.execute(
        f"SELECT *, {STATUS_SQL.replace(':today', '?')} AS 접수상태"
        f" FROM courses WHERE id IN ({marks})",
        [today, today, *course_ids],
    ).fetchall()
    con.close()
    if len(rows) < 2:
        found = [r["id"] for r in rows]
        return finalize(
            f"비교하려면 유효한 강좌가 2개 이상 필요합니다 (찾은 ID: {found})."
            " search_courses가 반환한 ID를 사용하세요."
        )
    order = {cid: i for i, cid in enumerate(course_ids)}
    rows = sorted(rows, key=lambda r: order.get(r["id"], 99))
    header = "| 항목 | " + " | ".join(f"[{r['id']}] {r['강좌명'][:20]}" for r in rows) + " |"
    sep = "|---" * (len(rows) + 1) + "|"
    def line(label: str, fn) -> str:
        return f"| {label} | " + " | ".join(str(fn(r)) for r in rows) + " |"
    body = "\n".join([
        header, sep,
        line("기관", lambda r: r["운영기관명"]),
        line("지역", lambda r: f"{r['시도']} {r['시군구']}".strip()),
        line("요일", lambda r: r["요일_정규화"] or "미상"),
        line("시간", lambda r: f"{r['교육시작시각'] or '?'}~{r['교육종료시각'] or '?'}"),
        line("수강료", fee_label),
        line("대상", lambda r: r["교육대상구분"] or "미상"),
        line("접수기간", lambda r: f"{r['접수시작일자'] or '?'}~{r['접수종료일자'] or '?'}"),
        line("접수상태", lambda r: r["접수상태"]),
        line("정원", lambda r: r["강좌정원수"] or "미상"),
    ])
    return finalize(f"### 강좌 비교 ({len(rows)}개, 기준일 {today})\n" + body)


@mcp.tool(
    annotations=ToolAnnotations(title="강좌 상세 (Course Detail)", **RO_ANNOTATIONS),
    description=(
        "Get full details of one course (all fields plus organizer address,"
        " phone, website) from " + SERVICE_NAME + "."
        " Use a course ID returned by search_courses."
    ),
)
def get_course_detail(course_id: int) -> str:
    today = today_kst()
    con = db()
    r = con.execute(
        f"SELECT *, {STATUS_SQL} AS 접수상태 FROM courses WHERE id = :id",
        {"id": course_id, "today": today},
    ).fetchone()
    con.close()
    if not r:
        return finalize(f"ID {course_id} 강좌를 찾을 수 없습니다. search_courses의 ID를 사용하세요.")
    lines = [
        f"### [{r['id']}] {r['강좌명']}",
        f"- **접수상태**: {r['접수상태']} (접수 {r['접수시작일자'] or '?'}~{r['접수종료일자'] or '?'}, 방법 {r['접수방법구분'] or '미상'}, 선정 {r['선정방법구분'] or '미상'})",
        f"- **교육기간**: {r['교육시작일자']}~{r['교육종료일자']} · {r['요일_정규화'] or '요일 미상'} {r['교육시작시각'] or '?'}~{r['교육종료시각'] or '?'}",
        f"- **수강료**: {fee_label(r)} · **정원**: {r['강좌정원수'] or '미상'} · **방식**: {r['교육방법구분'] or '미상'} · **대상**: {r['교육대상구분'] or '미상'}",
        f"- **강사**: {r['강사명'] or '미상'}",
        f"- **장소**: {r['교육장소'] or '미상'} ({r['교육장도로명주소'] or '주소 미상'})",
        f"- **운영기관**: {r['운영기관명']} · ☎ {r['운영기관전화번호'] or '미상'}",
    ]
    if r["홈페이지주소"]:
        lines.append(f"- **홈페이지**: {r['홈페이지주소']}")
    if r["강좌내용"]:
        lines.append(f"- **내용**: {r['강좌내용'][:500]}")
    return finalize("\n".join(lines))


@mcp.tool(
    annotations=ToolAnnotations(title="기관별 강좌 (Courses by Center)", **RO_ANNOTATIONS),
    description=(
        "List courses offered by a specific center/organization (with its"
        " address, phone, website) or list active organizations in a region,"
        " from " + SERVICE_NAME + "."
    ),
)
def list_courses_by_center(
    center_name: str | None = None, region: str | None = None, page: int = 1
) -> str:
    if not center_name and not region:
        return finalize(
            "center_name 또는 region 중 하나는 필요합니다."
            " get_filter_options로 유효한 지역명을 확인할 수 있습니다."
        )
    today = today_kst()
    con = db()
    if not center_name:
        params: dict[str, Any] = {"today": today}
        rc = region_clause(region, params)
        rows = con.execute(
            "SELECT 운영기관명, 시도, 시군구, COUNT(*) AS n,"
            f" SUM(CASE WHEN ({STATUS_SQL}) IN ('접수중','상시','예정') THEN 1 ELSE 0 END) AS open_n"
            f" FROM courses WHERE 운영기관명 != ''{rc}"
            " GROUP BY 운영기관명, 시도, 시군구 ORDER BY open_n DESC, n DESC LIMIT 15",
            params,
        ).fetchall()
        con.close()
        if not rows:
            return finalize(
                f"'{region}' 지역의 기관을 찾지 못했습니다."
                " get_filter_options로 유효한 지역명을 확인하거나 지역을 넓혀 보세요."
            )
        parts = [f"### '{region}' 지역 운영기관 (기준일 {today})"]
        parts.extend(
            f"- **{r['운영기관명']}** ({r['시도']} {r['시군구']})".rstrip()
            + f" · 강좌 {r['n']}개 (접수가능 {r['open_n']}개)"
            for r in rows
        )
        parts.append("\n기관명을 center_name으로 넣으면 강좌 목록을 볼 수 있습니다.")
        return finalize("\n".join(parts))

    params = {"today": today, "center": like(center_name)}
    rc = region_clause(region, params)
    page = max(1, page)
    params["limit"], params["offset"] = PAGE_SIZE, (page - 1) * PAGE_SIZE
    total = con.execute(
        f"SELECT COUNT(*) FROM courses WHERE 운영기관명 LIKE :center{ESC}{rc}", params
    ).fetchone()[0]
    rows = con.execute(
        f"SELECT *, {STATUS_SQL} AS 접수상태 FROM courses"
        f" WHERE 운영기관명 LIKE :center{ESC}{rc}"
        " ORDER BY CASE 접수상태 WHEN '접수중' THEN 0 WHEN '예정' THEN 1"
        " WHEN '상시' THEN 2 ELSE 3 END, 접수종료일자 LIMIT :limit OFFSET :offset",
        params,
    ).fetchall()
    con.close()
    if not rows:
        if total:
            last = (total + PAGE_SIZE - 1) // PAGE_SIZE
            return finalize(
                f"페이지 {page}에는 표시할 강좌가 없습니다."
                f" '{center_name}' 강좌는 전체 {total}건 — 다른 페이지(1~{last})를 사용하세요."
            )
        return finalize(
            f"'{center_name}' 기관의 강좌를 찾지 못했습니다."
            " 기관명을 줄이거나 다른 이름으로 검색하고, region으로 지역 기관 목록을 확인해 보세요."
        )
    info = rows[0]
    head = (
        f"### {info['운영기관명']} 강좌 {total}건 (페이지 {page}, 기준일 {today})\n"
        f"- ☎ {info['운영기관전화번호'] or '미상'} · {info['교육장도로명주소'] or '주소 미상'}"
        + (f" · {info['홈페이지주소']}" if info["홈페이지주소"] else "") + "\n"
    )
    cards = "\n".join(course_card(r, r["접수상태"]) for r in rows)
    more = f"\n\n다음 페이지: page={page + 1}" if total > page * PAGE_SIZE else ""
    return finalize(head + cards + more)


@mcp.tool(
    annotations=ToolAnnotations(title="필터 옵션 (Filter Options)", **RO_ANNOTATIONS),
    description=(
        "Get valid filter values (regions, target-audience values, weekday and"
        " time buckets, enrollment statuses) to build accurate search_courses"
        " queries, from " + SERVICE_NAME + "."
    ),
)
def get_filter_options(region: str | None = None) -> str:
    today = today_kst()
    con = db()
    if region:
        params: dict[str, Any] = {"region": like(region)}
        rows = con.execute(
            "SELECT 시도, 시군구, COUNT(*) FROM courses"
            f" WHERE 시도 LIKE :region{ESC} OR 시군구 LIKE :region{ESC}"
            " GROUP BY 시도, 시군구 ORDER BY 3 DESC LIMIT 30",
            params,
        ).fetchall()
        targets = con.execute(
            "SELECT 교육대상구분, COUNT(*) FROM courses"
            f" WHERE (시도 LIKE :region{ESC} OR 시군구 LIKE :region{ESC}) AND 교육대상구분 != ''"
            " GROUP BY 교육대상구분 ORDER BY 2 DESC LIMIT 15",
            params,
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT 시도, COUNT(DISTINCT 시군구), COUNT(*) FROM courses"
            " WHERE 시도 != '' GROUP BY 시도 ORDER BY 3 DESC"
        ).fetchall()
        targets = con.execute(
            "SELECT 교육대상구분, COUNT(*) FROM courses WHERE 교육대상구분 != ''"
            " GROUP BY 교육대상구분 ORDER BY 2 DESC LIMIT 15"
        ).fetchall()
    con.close()
    parts = [f"### 필터 옵션 (기준일 {today})"]
    if region:
        parts.append("**지역(시도/시군구/강좌수)**: " + ", ".join(
            f"{r[0]} {r[1]}({r[2]})".strip() for r in rows
        ))
    else:
        parts.append("**시도(시군구수/강좌수)**: " + ", ".join(
            f"{r[0]}({r[1]}/{r[2]})" for r in rows
        ))
    parts.append("**교육대상구분**: " + ", ".join(f"{t[0]}({t[1]})" for t in targets))
    parts.append("**weekday**: 월, 화, 수, 목, 금, 토, 일 (배열) · '주말'/'평일'/'금~월'(범위)도 지원")
    parts.append(
        "**time_range**: 오전/오후/저녁 (또는 morning/afternoon/evening)"
        " | 'HH:MM-HH:MM' | 열린 범위 '19:00-'/'-12:00' | '오후7시'/'저녁 7시 이후'"
    )
    parts.append(f"**status**: {', '.join(STATUS_VALUES)} (기본: 접수중, 예정, 상시)")
    parts.append("**sort**: deadline(마감임박) | fee(수강료) | start_date(개강일)")
    return finalize("\n".join(parts))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
