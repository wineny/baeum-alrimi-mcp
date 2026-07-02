#!/usr/bin/env python3
"""기관별 접수시작일 이력 → 접수 패턴·다음 오픈 예상 테이블 빌드 (US-002).

- 과거 고유 접수시작일 2회 이상인 기관만 예상 생성 — 거짓 예측 방지
- 월중최빈일은 강좌 건수 가중(실측: 강남구 28일 149건·6일 135건 집중과 부합해야 함)
- 예측은 '대규모 오픈 이벤트'(해당 일자 강좌 수가 기관 전체의 10% 이상 또는 5건 이상)
  간격을 우선 사용, 이벤트가 2회 미만이면 고유 일자 간격 중앙값 사용
- 근거 문자열에 '과거 N회 기준' + '예상이며 확정 아님' 필수 (확정형 표현 금지, PRD §9)
- 명시된 미래 접수창은 courses 테이블에서 질의 시점에 직접 조회(별도 저장 불필요)
"""
import sqlite3
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "courses.db"

SCHEMA = """
DROP TABLE IF EXISTS enrollment_patterns;
CREATE TABLE enrollment_patterns (
  기관 TEXT, 시도 TEXT, 시군구 TEXT,
  회차수 INTEGER,          -- 과거 고유 접수시작일 개수
  최근접수시작 TEXT,
  월중최빈일 INTEGER,       -- 강좌 건수 가중 day-of-month 최빈값
  월중최빈비율 REAL,
  중앙주기일 INTEGER,       -- 예측에 사용된 간격(일)
  다음오픈예상 TEXT,        -- YYYY-MM-DD (예상)
  근거 TEXT,               -- '과거 N회 기준 ... 예상이며 확정 아님'
  PRIMARY KEY (기관, 시도, 시군구)
);
CREATE INDEX idx_patterns_region ON enrollment_patterns(시도, 시군구);
"""


def build_basis(
    n_events: int, event_dates: list[date], gap: int,
    dom: int, dom_ratio: float, last: date, mode: str,
) -> str:
    dom_note = (
        f" 접수시작일은 월중 {dom}일에 집중(전체 강좌의 {dom_ratio:.0%})."
        if dom_ratio >= 0.3 else ""
    )
    if mode == "event":
        recent = ", ".join(d.isoformat() for d in event_dates[-3:])
        return (
            f"과거 {n_events}회 대규모 접수 오픈({recent}) 간격 약 {gap}일 기준."
            f"{dom_note} 예상이며 확정 아님"
        )
    return (
        f"과거 {n_events}회 접수시작일 간격 중앙값 약 {gap}일 기준"
        f" (최근 시작일 {last.isoformat()}).{dom_note} 예상이며 확정 아님"
    )


def main() -> int:
    con = sqlite3.connect(DB_PATH)
    today_s = con.execute("SELECT value FROM meta WHERE key='build_date'").fetchone()[0]
    today = date.fromisoformat(today_s)
    con.executescript(SCHEMA)

    orgs = con.execute(
        "SELECT 운영기관명, 시도, 시군구 FROM courses"
        " WHERE 운영기관명 != '' GROUP BY 운영기관명, 시도, 시군구"
    ).fetchall()

    inserted = skipped = 0
    for org, sido, sigungu in orgs:
        rows = con.execute(
            "SELECT 접수시작일자, COUNT(*) FROM courses"
            " WHERE 운영기관명=? AND 시도=? AND 시군구=?"
            " AND 접수시작일자 IS NOT NULL AND 접수시작일자 <= ?"
            " GROUP BY 접수시작일자 ORDER BY 접수시작일자",
            (org, sido, sigungu, today_s),
        ).fetchall()
        if len(rows) < 2:
            skipped += 1
            continue
        dates = [date.fromisoformat(d) for d, _ in rows]
        counts = [c for _, c in rows]
        total_courses = sum(counts)
        last = dates[-1]

        # 건수 가중 월중 최빈일
        dom_counter: Counter[int] = Counter()
        for d, c in zip(dates, counts):
            dom_counter[d.day] += c
        dom, dom_cnt = dom_counter.most_common(1)[0]
        dom_ratio = dom_cnt / total_courses

        # 대규모 오픈 이벤트: 기관 전체 강좌의 10% 이상이면서 5건 이상
        threshold = max(5, total_courses * 0.1)
        events = [d for d, c in zip(dates, counts) if c >= threshold]

        if len(events) >= 2:
            gaps = [(b - a).days for a, b in zip(events, events[1:]) if (b - a).days > 0]
            gap = int(median(gaps)) if gaps else 90
            anchor, mode, n_basis = events[-1], "event", len(events)
            basis_dates = events
        else:
            gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
            gap = int(median(gaps)) if gaps else 30
            anchor, mode, n_basis = last, "distinct", len(dates)
            basis_dates = dates

        expected = anchor + timedelta(days=gap)
        while expected <= today:
            expected += timedelta(days=max(gap, 7))
        basis = build_basis(n_basis, basis_dates, gap, dom, dom_ratio, last, mode)

        con.execute(
            "INSERT INTO enrollment_patterns VALUES (?,?,?,?,?,?,?,?,?,?)",
            (org, sido, sigungu, len(dates), last.isoformat(), dom,
             round(dom_ratio, 3), gap, expected.isoformat(), basis),
        )
        inserted += 1

    con.commit()
    print(f"patterns={inserted} skipped(이력<2회)={skipped}")
    for row in con.execute(
        "SELECT 기관, 회차수, 월중최빈일, 월중최빈비율, 중앙주기일, 다음오픈예상, 근거"
        " FROM enrollment_patterns WHERE 시군구='강남구'"
    ):
        print("강남구 스팟체크:", row)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
