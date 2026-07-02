#!/usr/bin/env python3
"""data/raw/*.csv (205개 전국평생학습강좌표준데이터) → data/courses.db 빌드.

- 25컬럼 정규화 + 파생컬럼(시도/시군구, 요일, 시간대버킷, 무료여부, 수강료, 접수일자, 상시여부)
- 접수상태는 서버에서 질의 시점 날짜로 동적 계산(스냅샷 컬럼은 리포트용)
- 적재 리포트(AC1): 파일별 행수, 날짜 파싱 성공률, 핵심 필드 null율 → data/build_report.json
"""
import csv
import json
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "courses.db"
REPORT_PATH = ROOT / "data" / "build_report.json"

EXPECTED_COLS = [
    "강좌명", "강사명", "교육시작일자", "교육종료일자", "교육시작시각", "교육종료시각",
    "강좌내용", "교육대상구분", "교육방법구분", "운영요일", "교육장소", "강좌정원수",
    "수강료", "교육장도로명주소", "운영기관명", "운영기관전화번호", "접수시작일자",
    "접수종료일자", "접수방법구분", "선정방법구분", "홈페이지주소",
    "직업능력개발훈련비지원강좌여부", "학점은행제평가(학점)인정여부",
    "평생학습계좌제평가인정여부", "데이터기준일자",
]

SIDO_SUFFIXES = ("특별시", "광역시", "특별자치시", "특별자치도", "도")
WEEKDAYS = "월화수목금토일"
DATE_RE = re.compile(r"^(\d{4})[-./]?(\d{1,2})[-./]?(\d{1,2})$")
TIME_RE = re.compile(r"^(\d{1,2})[:시]?(\d{0,2})")
ALWAYS_OPEN_ENDS = {"2026-12-31", "2099-12-31", "9999-12-31"}


def parse_date(raw: str) -> str | None:
    """YYYY-MM-DD ISO 문자열로 정규화. 실패 시 None."""
    if not raw:
        return None
    m = DATE_RE.match(raw.strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_time(raw: str) -> str | None:
    """HH:MM 정규화. 실패 시 None."""
    if not raw:
        return None
    m = TIME_RE.match(raw.strip())
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2)) if m.group(2) else 0
    if not (0 <= h <= 24 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"


def parse_weekdays(raw: str) -> str:
    """운영요일 원문 → '월,수,금' 형태. 범위(월~금)·매일·평일·주말 지원. 없으면 ''."""
    if not raw:
        return ""
    text = raw.strip()
    if "매일" in text:
        return ",".join(WEEKDAYS)
    # '월요일'의 '일'이 일요일로 오인되는 것 방지 (+원본 오타 '목용일'·'금묘일' 방어)
    text = re.sub(r"[요용묘]일", "", text)
    text = text.replace("평일", "월,화,수,목,금").replace("주말", "토,일")
    found: list[str] = []
    range_m = re.search(rf"([{WEEKDAYS}])\s*[~∼-]\s*([{WEEKDAYS}])", text)
    if range_m:
        a, b = WEEKDAYS.index(range_m.group(1)), WEEKDAYS.index(range_m.group(2))
        if a <= b:
            found.extend(WEEKDAYS[a : b + 1])
        text = text[: range_m.start()] + text[range_m.end() :]
    for ch in text:
        if ch in WEEKDAYS and ch not in found:
            found.append(ch)
    return ",".join(sorted(found, key=WEEKDAYS.index))


def weekday_bits(days: str) -> int:
    bits = 0
    for d in days.split(","):
        if d and d in WEEKDAYS:  # ''는 모든 문자열의 부분문자열 → 반드시 배제
            bits |= 1 << WEEKDAYS.index(d)
    return bits


def time_bucket(start_hhmm: str | None) -> str | None:
    if not start_hhmm:
        return None
    h = int(start_hhmm[:2])
    if h < 12:
        return "morning"
    if h < 18:
        return "afternoon"
    return "evening"


def parse_fee(raw: str) -> tuple[int | None, int]:
    """수강료 원문 → (숫자 원, 무료여부 0/1). 파싱 불가 시 (None, 0)."""
    if raw is None:
        return None, 0
    text = raw.strip()
    if text in ("", "0", "무료", "무상"):
        return 0, 1
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None, 0
    fee = int(digits)
    return fee, 1 if fee == 0 else 0


def region_from_filename(stem: str) -> tuple[str, str]:
    """파일명 → (시도, 시군구). 교육청/국립기관은 시군구=''."""
    parts = stem.replace("_평생학습강좌", "").split("_")
    first = parts[0]
    if first.endswith("교육청"):
        sido = first[: -len("교육청")]
        return sido, ""
    if first.endswith(SIDO_SUFFIXES):
        sigungu = parts[1] if len(parts) > 1 else ""
        return first, sigungu
    return "", ""  # 국립국악원·공사 등 기관 단위 파일 — 지역 검색은 주소 필드로 보완


def snapshot_status(
    recv_start: str | None, recv_end: str | None, always: int, today: str
) -> str:
    if always:
        return "상시"
    if not recv_start or not recv_end:
        return "미상"
    if today < recv_start:
        return "예정"
    if today > recv_end:
        return "마감"
    return "접수중"


def read_csv_rows(path: Path) -> tuple[list[dict[str, str]], str]:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeDecodeError("both", data, 0, 1, "utf-8-sig/cp949 모두 실패")
    rows = list(csv.DictReader(text.splitlines()))
    return rows, enc


SCHEMA = """
DROP TABLE IF EXISTS courses;
CREATE TABLE courses (
  id INTEGER PRIMARY KEY,
  강좌명 TEXT, 강사명 TEXT,
  교육시작일자 TEXT, 교육종료일자 TEXT,
  교육시작시각 TEXT, 교육종료시각 TEXT,
  강좌내용 TEXT, 교육대상구분 TEXT, 교육방법구분 TEXT,
  운영요일_원문 TEXT, 교육장소 TEXT, 강좌정원수 TEXT,
  수강료_원문 TEXT, 교육장도로명주소 TEXT,
  운영기관명 TEXT, 운영기관전화번호 TEXT,
  접수시작일자 TEXT, 접수종료일자 TEXT,
  접수방법구분 TEXT, 선정방법구분 TEXT, 홈페이지주소 TEXT,
  데이터기준일자 TEXT,
  시도 TEXT, 시군구 TEXT, 출처파일 TEXT,
  요일_정규화 TEXT, 요일_비트 INTEGER,
  시간대_버킷 TEXT,
  수강료_숫자 INTEGER, 무료여부 INTEGER,
  상시여부 INTEGER, 접수상태_스냅샷 TEXT
);
CREATE INDEX idx_courses_region ON courses(시도, 시군구);
CREATE INDEX idx_courses_recv ON courses(접수시작일자, 접수종료일자);
CREATE INDEX idx_courses_free ON courses(무료여부);
CREATE INDEX idx_courses_center ON courses(운영기관명);
DROP TABLE IF EXISTS meta;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def main() -> int:
    today = date.today().isoformat()
    files = sorted(RAW_DIR.glob("*.csv"))
    if not files:
        print("no raw csv files", file=sys.stderr)
        return 1
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)

    report: dict = {"build_date": today, "files": {}, "totals": {}}
    total_rows = 0
    date_ok = date_all = 0
    null_counts = {k: 0 for k in ("강좌명", "운영기관명", "운영요일", "교육시작시각", "접수시작일자", "접수종료일자", "수강료")}
    status_counts: dict[str, int] = {}
    basis_dates: list[str] = []

    insert_sql = (
        "INSERT INTO courses (강좌명, 강사명, 교육시작일자, 교육종료일자, 교육시작시각,"
        " 교육종료시각, 강좌내용, 교육대상구분, 교육방법구분, 운영요일_원문, 교육장소,"
        " 강좌정원수, 수강료_원문, 교육장도로명주소, 운영기관명, 운영기관전화번호,"
        " 접수시작일자, 접수종료일자, 접수방법구분, 선정방법구분, 홈페이지주소,"
        " 데이터기준일자, 시도, 시군구, 출처파일, 요일_정규화, 요일_비트, 시간대_버킷,"
        " 수강료_숫자, 무료여부, 상시여부, 접수상태_스냅샷)"
        " VALUES (" + ",".join(["?"] * 32) + ")"
    )

    for path in files:
        stem = path.stem
        try:
            rows, enc = read_csv_rows(path)
        except (UnicodeDecodeError, csv.Error) as e:
            report["files"][stem] = {"error": str(e)}
            continue
        sido, sigungu = region_from_filename(stem)
        file_date_ok = file_date_all = 0
        batch = []
        for r in rows:
            name = (r.get("강좌명") or "").strip()
            if not name:
                continue
            recv_start = parse_date(r.get("접수시작일자", ""))
            recv_end = parse_date(r.get("접수종료일자", ""))
            edu_start = parse_date(r.get("교육시작일자", ""))
            edu_end = parse_date(r.get("교육종료일자", ""))
            for raw_v, parsed in (
                (r.get("접수시작일자"), recv_start), (r.get("접수종료일자"), recv_end),
                (r.get("교육시작일자"), edu_start), (r.get("교육종료일자"), edu_end),
            ):
                if raw_v and raw_v.strip():
                    file_date_all += 1
                    if parsed:
                        file_date_ok += 1
            t_start = parse_time(r.get("교육시작시각", ""))
            t_end = parse_time(r.get("교육종료시각", ""))
            days = parse_weekdays(r.get("운영요일", ""))
            fee_num, is_free = parse_fee(r.get("수강료", ""))
            long_window = (
                recv_start is not None and recv_end is not None
                and (date.fromisoformat(recv_end) - date.fromisoformat(recv_start)).days > 300
            )
            always = 1 if (recv_end in ALWAYS_OPEN_ENDS or long_window) else 0
            status = snapshot_status(recv_start, recv_end, always, today)
            status_counts[status] = status_counts.get(status, 0) + 1
            for k in null_counts:
                if not (r.get(k) or "").strip():
                    null_counts[k] += 1
            basis = parse_date(r.get("데이터기준일자", ""))
            if basis:
                basis_dates.append(basis)
            batch.append((
                name, (r.get("강사명") or "").strip(), edu_start, edu_end,
                t_start, t_end, (r.get("강좌내용") or "").strip(),
                (r.get("교육대상구분") or "").strip(), (r.get("교육방법구분") or "").strip(),
                (r.get("운영요일") or "").strip(), (r.get("교육장소") or "").strip(),
                (r.get("강좌정원수") or "").strip(), (r.get("수강료") or "").strip(),
                (r.get("교육장도로명주소") or "").strip(), (r.get("운영기관명") or "").strip(),
                (r.get("운영기관전화번호") or "").strip(), recv_start, recv_end,
                (r.get("접수방법구분") or "").strip(), (r.get("선정방법구분") or "").strip(),
                (r.get("홈페이지주소") or "").strip(), basis,
                sido, sigungu, stem, days, weekday_bits(days),
                time_bucket(t_start), fee_num, is_free, always, status,
            ))
        con.executemany(insert_sql, batch)
        total_rows += len(batch)
        date_ok += file_date_ok
        date_all += file_date_all
        header_diff = sorted(set(rows[0].keys()) - set(EXPECTED_COLS)) if rows else []
        report["files"][stem] = {
            "rows": len(batch), "encoding": enc,
            "date_parse": f"{file_date_ok}/{file_date_all}",
            "extra_cols": header_diff,
        }

    data_min = min(basis_dates) if basis_dates else None
    data_max = max(basis_dates) if basis_dates else None
    con.executemany(
        "INSERT INTO meta VALUES (?,?)",
        [
            ("build_date", today),
            ("data_basis_min", data_min or ""),
            ("data_basis_max", data_max or ""),
            ("total_courses", str(total_rows)),
            ("source", "공공데이터포털 전국평생학습강좌표준데이터(#15013110)"),
        ],
    )
    con.commit()
    con.close()

    report["totals"] = {
        "files": len(files),
        "rows": total_rows,
        "date_parse_rate": round(date_ok / date_all, 4) if date_all else None,
        "null_rates": {k: round(v / total_rows, 4) for k, v in null_counts.items()},
        "status_snapshot": status_counts,
        "data_basis_range": [data_min, data_max],
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(report["totals"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
