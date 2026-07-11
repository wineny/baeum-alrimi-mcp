#!/usr/bin/env python3
"""INV-* 판정 SSOT (계획 §4.2). persona_loop와 acceptance.py가 공용으로 import한다.

- 경로1(직접 함수호출)은 str, 경로2(mcp.call_tool)는 ContentBlock 시퀀스/dict/tuple을
  반환하므로 판정 전 to_text()로 통일한다.
- INV-PREDICTION 스캔 범위는 예측 엔트리 라인(`- {날짜}경 (예상)`)만이다. 서버 자신의
  면책 헤더 "② … 다음 오픈 예상 (확정 아님)"(server.py)의 `확정`은 위반이 아니므로
  부정 전방탐색과 스캔 범위 한정으로 이중 방어한다.
- INV-NO-HALLUCINATION의 ID 추출은 카드 선두 앵커(`^- **[id]`)만 — 강좌명 안의
  "[2024]" 같은 대괄호 숫자를 오탐하지 않는다. 캘린더의 기관명 출력은 ID 앵커가
  없어 이 검사 밖(저위험 수용, 스팟체크 육안 보완).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server  # noqa: E402

LIMIT_BYTES = 24 * 1024  # 규정 상한 (서버 내부 마진은 server.MAX_BYTES = 23KB)
FOOTER_SIGNATURE = "실제 접수 가능 여부는 운영기관에 확인이 필요합니다"
CARD_ID_RE = re.compile(r"^- \*\*\[(\d+)\]", re.MULTILINE)
PREDICTION_ENTRY_RE = re.compile(r"^- .*\(예상\)")
CERTAINTY_RE = re.compile(r"확정(?!\s*아님)|보장(?!\s*하지)|반드시\s*열|틀림없|100%")

# ── dead-end 오라클 앵커 (계획 dead-end-zero-loop v4 §추출 앵커 규약) ──
# MUST 오라클(INV-DEAD-END)의 입력은 전부 고정 앵커 정규식 — 자유 텍스트 스캔 금지.
# 숫자 앵커라 '☎ 번호 미상'/'☎ 미상'은 자동 제외된다(별도 필터 불필요).
PHONE_RE = re.compile(r"☎\s*(\d[\d\-]+)")
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# org 앵커 2종: org_followup 라인(`- {기관}: 다음 오픈…/최근 접수 시작…`)과
# 캘린더 ③ 문의처 라인(`- {기관} · ☎ …`). 카드의 기관은 들여쓴 하위 라인이라
# 여기서 안 잡히며 card_ids/phones가 대리한다(novelty에 보수적 — 누락은 안전).
ORG_FOLLOWUP_RE = re.compile(r"^- (.+?): (?:다음 오픈|최근 접수 시작)", re.MULTILINE)
ORG_CONTACT_RE = re.compile(r"^- ([^:\n]+?) · ☎", re.MULTILINE)  # followup 라인(콜론 포함) 배제
# tier3b [타지역 안내]가 제시하는 재호출 파라미터 (server.py: `region="{시도}"`)
ALT_REGION_RE = re.compile(r'region="([^"]+)"')
# 캘린더 등록 링크 라인 (C1) — SHOULD 참고치 전용, MUST 판정에 미사용
CALENDAR_LINK_RE = re.compile(r"^  - 일정 추가: https://calendar\.google\.com/", re.MULTILINE)
# compare_courses 성공 테이블 헤더(`| 항목 | [id] 강좌명 … |`)의 강좌 ID —
# 비교표는 카드 앵커가 없지만 실질 강좌 콘텐츠를 전달한다(P4-3 오탐 QA).
# INV-NO-HALLUCINATION의 card_ids()는 불변 — dead-end 오라클 전용 확장.
TABLE_HEADER_ID_RE = re.compile(r"^\| 항목 \|.*", re.MULTILINE)
_BRACKET_ID_RE = re.compile(r"\[(\d+)\]")

MUST = ("INV-CRASH", "INV-24KB", "INV-FOOTER", "INV-PREDICTION",
        "INV-NO-HALLUCINATION", "INV-NO-BANNED")
# 빈 결과 검사는 카드 목록형 응답에만 의미가 있다
EMPTY_CHECK_TOOLS = ("search_courses", "list_courses_by_center")


def to_text(resp) -> str:
    """경로2 응답 정규화 어댑터: ContentBlock 시퀀스 | dict | (blocks, dict) → str."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, tuple) and len(resp) == 2:  # (unstructured, structured)
        resp = resp[0]
    if isinstance(resp, dict):
        return "\n".join(str(v) for v in resp.values())
    if isinstance(resp, (list, tuple)):
        return "\n".join(getattr(b, "text", str(b)) for b in resp)
    return str(resp)


def card_ids(text: str) -> list[int]:
    return [int(m) for m in CARD_ID_RE.findall(text)]


# 성공 헤더('### 강좌 검색 결과 N건') 전용 카운트 파서 — persona_loop에서 이전(SSOT).
# 주의: acceptance.py의 search_total(`강좌 검색 결과 (\d+)건`)은 골든셋 총건수 검증용
# 별도 파서다 — 목적이 달라 병합하지 말 것. 폴백 헤더는 '결과' 단어를 규약상 피하므로
# (server.py 폴백 헤더 주석) 이 파서는 폴백 응답에서 None을 반환하며, 폴백의 novelty는
# card_ids로 측정한다.
_COUNT_RE = re.compile(r"결과 (\d+)건")


def result_count(text: str) -> int | None:
    m = _COUNT_RE.search(text)
    return int(m.group(1)) if m else None


def response_content(text: str) -> dict:
    """dead-end 오라클용 load-bearing 콘텐츠 추출 (계획 v4 단계 1).

    novelty(진전) 측정의 입력 — 전부 고정 앵커 파싱. calendar_links는 SHOULD
    참고치이고 alt_regions는 tier3b 재호출 파라미터(terminal 게이트 입력)다.
    """
    table_ids: set[int] = set()
    for line in TABLE_HEADER_ID_RE.findall(text):
        table_ids.update(int(m) for m in _BRACKET_ID_RE.findall(line))
    return {
        "card_ids": set(card_ids(text)) | table_ids,
        "orgs": set(ORG_FOLLOWUP_RE.findall(text)) | set(ORG_CONTACT_RE.findall(text)),
        "phones": set(PHONE_RE.findall(text)),
        "predict_dates": {
            d
            for line in text.splitlines()
            if PREDICTION_ENTRY_RE.match(line)
            for d in ISO_DATE_RE.findall(line)
        },
        "expansion_open": ("[범위 확장]" in text) or ("[지역 확장]" in text),
        "alt_regions": ALT_REGION_RE.findall(text) if "[타지역 안내]" in text else [],
        "calendar_links": len(CALENDAR_LINK_RE.findall(text)),
    }


NOVELTY_KINDS = ("card_ids", "orgs", "phones", "predict_dates")


def novelty(cur: dict, prev: dict | None) -> set[str]:
    """cur가 prev 대비 새로 전달한 콘텐츠 종류 (집합 차집합).

    전달된 콘텐츠(카드ID·org·유효전화·(예상)일자)에만 측정한다 — '잔여 완화
    옵션'은 novelty 성분이 아니라 terminal 게이트 전용(계획 v4 필수수정 2:
    완화옵션-as-novelty는 3턴 캡에서 stall을 무력화하는 거짓 음성 통로).
    """
    if prev is None:
        return {k for k in NOVELTY_KINDS if cur[k]}
    return {k for k in NOVELTY_KINDS if cur[k] - prev[k]}


def assert_24kb(text: str) -> None:
    size = len(text.encode("utf-8"))
    assert size <= LIMIT_BYTES, f"응답 {size}B > {LIMIT_BYTES}B"


def assert_footer(text: str) -> None:
    assert FOOTER_SIGNATURE in text, "푸터 시그니처 없음 (finalize 미경유)"


def hallucinated_ids(text: str) -> list[int]:
    ids = card_ids(text)
    if not ids:
        return []
    con = server.db()
    marks = ",".join("?" for _ in ids)
    found = {r[0] for r in con.execute(f"SELECT id FROM courses WHERE id IN ({marks})", ids)}
    con.close()
    return sorted(set(i for i in ids if i not in found))


def prediction_violations(text: str) -> list[str]:
    bad = []
    for line in text.splitlines():
        if PREDICTION_ENTRY_RE.match(line) and CERTAINTY_RE.search(line):
            bad.append(line.strip())
    return bad


def empty_quality_ok(text: str) -> bool:
    """0건 응답이 안내(없음 고지 + 대안 제시)를 갖추었는가 (SHOULD, 단일 판정식).

    내부를 response_content 기반으로 재구성(계획 v4 단계 1) — load-bearing
    대안(유효전화·예상일자·타지역 재호출 파라미터)이 있으면 문구와 무관하게
    대안 충족, 없으면 기존 텍스트 포인터 검사로 폴백. 시그니처·SHOULD 심각도
    불변. 이 함수는 응답 '문구 품질' 검사이며, 궤적 진전(MUST)은 novelty가 담당.
    """
    notice = (("없습니다" in text) or ("찾지 못" in text) or ("없어요" in text)
              or ("없음" in text) or ("필요합니다" in text))
    rc = response_content(text)
    alternative = (
        bool(rc["phones"] or rc["predict_dates"] or rc["alt_regions"])
        or "조건을 빼면" in text  # 필터 완화 힌트(실측 프로브) — 구체 대안
        or any(k in text for k in ("필터", "옵션", "다른", "좁히", "넓혀",
                                   "get_filter_options", "추천"))
    )
    return notice and alternative


def violations(text: str, tool: str) -> list[str]:
    """성공 응답 1건에 대한 INV 위반 목록 (INV-CRASH·TYPE-COERCION·SILENT-DROP은 러너에서 판정)."""
    v: list[str] = []
    if len(text.encode("utf-8")) > LIMIT_BYTES:
        v.append("INV-24KB")
    if FOOTER_SIGNATURE not in text:
        v.append("INV-FOOTER")
    if "kakao" in text.lower():
        v.append("INV-NO-BANNED")
    if prediction_violations(text):
        v.append("INV-PREDICTION")
    if hallucinated_ids(text):
        v.append("INV-NO-HALLUCINATION")
    if tool in EMPTY_CHECK_TOOLS and not card_ids(text) and not empty_quality_ok(text):
        v.append("INV-EMPTY-QUALITY")
    return v
