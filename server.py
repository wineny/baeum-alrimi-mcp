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
    if not region:
        return ""
    params["region"] = like(region)
    return (
        f" AND (시도 LIKE :region{ESC} OR 시군구 LIKE :region{ESC}"
        f" OR 교육장도로명주소 LIKE :region{ESC} OR 교육장소 LIKE :region{ESC})"
    )


def fee_label(row: sqlite3.Row) -> str:
    if row["무료여부"]:
        return "무료"
    if row["수강료_숫자"] is not None:
        return f"{row['수강료_숫자']:,}원"
    return row["수강료_원문"] or "미상"


def course_card(row: sqlite3.Row, status: str) -> str:
    days = row["요일_정규화"] or "요일 미상"
    time_s = f"{row['교육시작시각'] or '?'}~{row['교육종료시각'] or '?'}"
    recv = (
        f"{row['접수시작일자'] or '?'}~{row['접수종료일자'] or '?'}"
        if status != "상시" else "상시접수"
    )
    return (
        f"- **[{row['id']}] {row['강좌명']}** ({status})\n"
        f"  - {row['운영기관명']} · {row['시도']} {row['시군구']}".rstrip() + "\n"
        f"  - {days} {time_s} · {fee_label(row)} · 대상 {row['교육대상구분'] or '미상'}\n"
        f"  - 교육 {row['교육시작일자']}~{row['교육종료일자']} · 접수 {recv}"
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
        " Build filters ONLY from the user's current message: do NOT carry over"
        " time_range/weekday/region from earlier turns unless the user restates them."
        " Default shows courses currently open, upcoming, or always-open."
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
    if keyword:
        params["kw"] = like(keyword)
        where.append(f"(강좌명 LIKE :kw{ESC} OR 강좌내용 LIKE :kw{ESC} OR 교육장소 LIKE :kw{ESC})")
    where.append(region_clause(region, params).replace(" AND ", "", 1) or "1=1")
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
    for s in status or DEFAULT_STATUS:
        s = STATUS_ALIASES.get(s.strip().lower(), s.strip())
        if s in STATUS_VALUES and s not in statuses:
            statuses.append(s)
    if not statuses:
        statuses = DEFAULT_STATUS
    where.append(f"({STATUS_SQL}) IN ({','.join(repr(s) for s in statuses)})")

    order = {
        "deadline": "CASE WHEN 접수종료일자 IS NULL THEN 1 ELSE 0 END, 접수종료일자 ASC",
        "fee": "CASE WHEN 수강료_숫자 IS NULL THEN 1 ELSE 0 END, 수강료_숫자 ASC",
        "start_date": "CASE WHEN 교육시작일자 IS NULL THEN 1 ELSE 0 END, 교육시작일자 ASC",
    }.get(sort, "접수종료일자 ASC")

    page = max(1, page)
    params["limit"] = PAGE_SIZE
    params["offset"] = (page - 1) * PAGE_SIZE
    sql = (
        f"SELECT *, {STATUS_SQL} AS 접수상태 FROM courses"
        f" WHERE {' AND '.join(w for w in where if w != '1=1') or '1=1'}"
        f" ORDER BY {order} LIMIT :limit OFFSET :offset"
    )
    count_sql = (
        f"SELECT COUNT(*) FROM courses"
        f" WHERE {' AND '.join(w for w in where if w != '1=1') or '1=1'}"
    )
    con = db()
    total = con.execute(count_sql, params).fetchone()[0]
    rows = con.execute(sql, params).fetchall()
    con.close()

    notice = (
        "\n\n※ 인식하지 못해 적용하지 않은 필터 — " + " · ".join(ignored)
        if ignored else ""
    )
    if not region and rows:
        notice += (
            "\n\n※ 지역 미지정 — 전국 기준 결과라 특정 지역·기관에 치우칠 수 있습니다."
            " 사용자에게 지역(시/군/구)을 물어봐 region으로 좁혀 주세요."
        )
    if not rows:
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
        p_params["region"] = like(region)
        p_where += f" AND (시도 LIKE :region{ESC} OR 시군구 LIKE :region{ESC} OR 기관 LIKE :region{ESC})"
    if center_name:
        p_params["center"] = like(center_name)
        p_where += f" AND 기관 LIKE :center{ESC}"
    predicted = con.execute(
        "SELECT 기관, 시도, 시군구, 회차수, 다음오픈예상, 근거 FROM enrollment_patterns"
        f" WHERE 다음오픈예상 <= date(:today, :horizon){p_where}"
        " ORDER BY 다음오픈예상 LIMIT 15",
        p_params,
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
        parts.append("\n**② 과거 이력 기반 다음 오픈 예상**: 예측 가능한 기관 없음 (이력 2회 미만)")
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
