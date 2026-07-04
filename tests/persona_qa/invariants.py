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
    """0건 응답이 안내(없음 고지 + 대안 제시)를 갖추었는가."""
    notice = (("없습니다" in text) or ("찾지 못" in text) or ("없어요" in text)
              or ("없음" in text) or ("필요합니다" in text))
    alternative = any(k in text for k in ("필터", "옵션", "다른", "좁히", "넓혀", "get_filter_options", "추천"))
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
