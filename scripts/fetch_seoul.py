#!/usr/bin/env python3
"""서울열린데이터광장 공공서비스예약(교육) API → data/raw_seoul/*.json 원본 저장.

실행명세_PRD.md §5-4 실증 엔드포인트:
  http://openapi.seoul.go.kr:8088/{KEY}/json/ListPublicReservation{Service}/{start}/{end}/
  (Service: Education | Sport | Culture — 이 씬슬라이스는 Education만 다룬다)

빌드타임 전용 스크립트다. MCP tool 실행 경로(hot-path)에서는 호출하지 않으며,
DB 빌드 스크립트나 메인 강좌 데이터베이스 파일을 참조·적재하지 않는다 —
C3a 커밋 경계는 fetch 산출물(`data/raw_seoul/`)에만 닿는다.

인증키: 환경변수 `SEOUL_API_KEY`. 미설정 시 서울열린데이터광장 공용 샘플키
"sample"로 폴백하며, 샘플키는 응답 행수가 제한된다는 안내를 stderr에 출력한다.

--probe-only: 소량(5행)만 호출해 스키마 필드 존재·매핑 가능성만 검증하고 보고한다.
파일 저장은 하지 않는다. 네트워크 실패도 실패로 간주하지 않고 그 사실을 보고에
그대로 기록한다(빌드타임 스크립트이므로 hot-path 무오류 규정과 무관).

PLACENM → 교육장소 매핑 계획 (계획서 §2 기능3 채택안 A, C3b에서 구현 예정)
------------------------------------------------------------------------
서울 API는 표준데이터(#15013110)의 `교육장소`에 대응하는 필드를 별도로 두지
않고, 시설/기관명을 `PLACENM` 한 컬럼에 담는다. 채택안 A는 `PLACENM`을
`운영기관명`과 `교육장소` **양쪽에 동일하게 직접 매핑**한다(신규 파생 컬럼이나
`강좌내용` 병합주입 없음):
  - `교육장소`는 두 검색 엔진(server.py의 region_clause ↔ tests의 py_filter)
    모두에서 keyword 매칭 컬럼이면서 region 판별에서는 제외되는 컬럼이므로,
    PLACENM을 여기 넣으면 서울 시설명("관악문화센터" 등) keyword 리콜을
    공짜로 회수하면서도 region 동치·detail 오염을 건드리지 않는다.
  - `교육장도로명주소`는 공백('')으로 유지한다. 서울 행은 `시도`/`시군구`가
    채워져 `struct_empty=False`가 되므로 도로명 폴백이 발동하지 않는다.
  - 이 매핑은 C3b(DB 빌드 스크립트 확장 + 메인 강좌 DB 재빌드)에서 구현하며,
    본 스크립트(C3a)는 fetch만 담당하고 메인 강좌 DB에는 관여하지 않는다.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "raw_seoul"

SAMPLE_KEY = "sample"
SERVICE_MAP = {
    "Education": "ListPublicReservationEducation",
    "Sport": "ListPublicReservationSport",
    "Culture": "ListPublicReservationCulture",
}

# 매핑 가능성 확인용: 표시명 → 서울 API 필드명
FIELD_CHECKS = {
    "강좌명": "SVCNM",
    "기관": "PLACENM",
    "지역": "AREANM",
    "접수시작": "RCPTBGNDT",
    "접수종료": "RCPTENDDT",
    "요금": "PAYATNM",
    "상태": "SVCSTATNM",
    "URL": "SVCURL",
}


def resolve_key(explicit: str | None) -> str:
    """인증키 결정: --key > SEOUL_API_KEY > 샘플키(제한 안내)."""
    if explicit:
        return explicit
    env_key = os.environ.get("SEOUL_API_KEY")
    if env_key:
        return env_key
    print(
        "[안내] SEOUL_API_KEY 미설정 — 샘플키(sample)로 폴백합니다. "
        "샘플키는 응답 행수가 제한되어 전수 확인에는 부적합합니다.",
        file=sys.stderr,
    )
    return SAMPLE_KEY


def build_url(key: str, service: str, start: int, end: int) -> str:
    endpoint = SERVICE_MAP[service]
    return f"http://openapi.seoul.go.kr:8088/{key}/json/{endpoint}/{start}/{end}/"


def fetch_json(url: str, timeout: int = 15) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def probe(key: str, service: str) -> dict[str, Any]:
    """소량(5행) 호출 후 스키마 필드 존재·매핑 가능성만 검증(파일 저장 없음)."""
    url = build_url(key, service, 1, 5)
    report: dict[str, Any] = {"service": service, "url": url, "ok": False}
    try:
        payload = fetch_json(url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        report["network_error"] = str(e)
        return report
    except json.JSONDecodeError as e:
        report["parse_error"] = str(e)
        return report

    endpoint = SERVICE_MAP[service]
    body = payload.get(endpoint, {})
    result = body.get("RESULT", {})
    rows = body.get("row", [])
    report["result_code"] = result.get("CODE")
    report["result_message"] = result.get("MESSAGE")
    report["row_count"] = len(rows)
    if not rows:
        return report

    first = rows[0]
    report["fields_present"] = {
        label: (api_field in first) for label, api_field in FIELD_CHECKS.items()
    }
    report["total_fields_in_row"] = len(first)
    report["sample_row"] = first
    report["ok"] = (
        result.get("CODE") == "INFO-000"
        and all(report["fields_present"].values())
    )
    return report


def fetch_all(key: str, service: str, page_size: int = 1000) -> list[dict[str, Any]]:
    """전량 fetch(페이지네이션). C3a 범위에서는 build_db와 연결하지 않는다."""
    rows: list[dict[str, Any]] = []
    start = 1
    endpoint = SERVICE_MAP[service]
    while True:
        end = start + page_size - 1
        url = build_url(key, service, start, end)
        payload = fetch_json(url)
        body = payload.get(endpoint, {})
        result = body.get("RESULT", {})
        if result.get("CODE") not in ("INFO-000", None):
            break
        page_rows = body.get("row", [])
        if not page_rows:
            break
        rows.extend(page_rows)
        if len(page_rows) < page_size:
            break
        start = end + 1
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default=None, help="서울열린데이터광장 인증키(생략 시 SEOUL_API_KEY 또는 샘플키)")
    ap.add_argument("--service", default="Education", choices=sorted(SERVICE_MAP))
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--probe-only", action="store_true", help="5행만 조회해 스키마 검증 후 보고, 파일 저장 없음")
    args = ap.parse_args()

    key = resolve_key(args.key)

    if args.probe_only:
        report = probe(key, args.service)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    args.out.mkdir(parents=True, exist_ok=True)
    try:
        rows = fetch_all(key, args.service)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[실패] 네트워크 오류: {e}", file=sys.stderr)
        return 1
    out_path = args.out / f"{args.service.lower()}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out_path} ({len(rows)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
