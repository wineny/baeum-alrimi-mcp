#!/usr/bin/env python3
"""가상 페르소나 질문 루프 러너 (계획 §4.1).

질문(게이트A 승인본) × 변형(M1~M4) 배치 실행 → INV-* 위반 수집 → report.md.
- 경로1(M2·M3·M4·base): server 함수 직접 호출 — 타입강제 이후 함수 로직 층
- 경로2(M1): await mcp.call_tool — pydantic 스키마 강제 층 (ToolError=구조화 거부=정상)
- INV-SILENT-DROP: dropped 필드 지정 변형은 필터 제거본과 차등 비교 (경고, 판정 실패 아님)

종료 조건(계획 §4.3): MUST subset 위반 0 → exit 0. 실행:
  python3 tests/persona_qa/persona_loop.py
"""
import asyncio
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server  # noqa: E402
from tests.persona_qa import invariants as inv  # noqa: E402
from tests.persona_qa.arg_mutator import Variant, mutate  # noqa: E402
from tests.persona_qa.question_bank import QUESTIONS  # noqa: E402

try:
    from mcp.server.fastmcp.exceptions import ToolError
except ImportError:  # SDK 버전 차이 대비
    from mcp.shared.exceptions import McpError as ToolError  # type: ignore

REPORT_PATH = Path(__file__).resolve().parent / "report.md"
RECOGNITION_HINTS = ("인식", "형식", "지원하지 않", "무시", "올바른", "예:")


@dataclass
class CaseResult:
    qid: str
    case: str                 # "base" 또는 "M3:time_range 비정형 '19:00-'"
    tool: str
    args: dict[str, Any]
    path: int
    status: str               # ok | rejected | crash
    violations: list[str] = field(default_factory=list)
    silent_drop: bool = False
    detail: str = ""

    @property
    def must_violations(self) -> list[str]:
        return [v for v in self.violations if v in inv.MUST]


def call_direct(tool: str, args: dict[str, Any]) -> str:
    fn = getattr(server, tool)
    return fn(**args)


_COUNT_RE = re.compile(r"결과 (\d+)건")


def result_count(text: str) -> int | None:
    m = _COUNT_RE.search(text)
    return int(m.group(1)) if m else None


def run_path1(qid: str, case: str, tool: str, args: dict[str, Any],
              dropped: str | None = None) -> CaseResult:
    r = CaseResult(qid, case, tool, args, 1, "ok")
    try:
        text = call_direct(tool, args)
    except Exception as e:  # noqa: BLE001 — 크래시 포착이 목적
        r.status = "crash"
        r.violations.append("INV-CRASH")
        r.detail = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        return r
    r.violations = inv.violations(text, tool)
    if "INV-NO-HALLUCINATION" in r.violations:
        r.detail = f"없는 ID: {inv.hallucinated_ids(text)}"
    # INV-SILENT-DROP 차등 검사 (SHOULD·경고): 필터 제거본과 결과가 동일하고
    # 인식 실패 안내도 없으면 플래그. 정렬 탓에 1페이지 카드가 우연히 겹칠 수 있어
    # 총 건수 헤더까지 비교하고, 0건(구분 불가·INV-EMPTY-QUALITY 소관)은 제외한다.
    if dropped and dropped in args and r.status == "ok":
        try:
            without = call_direct(tool, {k: v for k, v in args.items() if k != dropped})
            same = (inv.card_ids(text) == inv.card_ids(without)
                    and result_count(text) == result_count(without))
            acknowledged = any(h in text for h in RECOGNITION_HINTS)
            if same and inv.card_ids(text) and not acknowledged:
                r.silent_drop = True
        except Exception:  # noqa: BLE001 — 차등용 보조 호출 실패는 본 판정에 영향 없음
            pass
    return r


async def run_path2(qid: str, case: str, tool: str, args: dict[str, Any]) -> CaseResult:
    r = CaseResult(qid, case, tool, args, 2, "ok")
    try:
        resp = await server.mcp.call_tool(tool, args)
    except ToolError as e:
        # pydantic 구조화 거부 = INV-TYPE-COERCION 통과 (500 크래시 아님)
        r.status = "rejected"
        r.detail = str(e)[:200]
        return r
    except Exception as e:  # noqa: BLE001 — 언핸들 예외 = 프로덕션 500 상당
        r.status = "crash"
        r.violations.append("INV-CRASH")
        r.violations.append("INV-TYPE-COERCION")
        r.detail = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        return r
    text = inv.to_text(resp)
    r.violations = inv.violations(text, tool)
    return r


def run_all() -> list[CaseResult]:
    results: list[CaseResult] = []
    path2_jobs: list[tuple[str, str, str, dict[str, Any]]] = []
    for q in QUESTIONS:
        results.append(run_path1(q.qid, "base", q.tool, q.args))
        for var in mutate(q.tool, q.args):
            case = f"{var.cls}: {var.note}"
            if var.path == 1:
                results.append(run_path1(q.qid, case, var.tool, var.args, var.dropped))
            else:
                path2_jobs.append((q.qid, case, var.tool, var.args))

    async def gather_path2() -> list[CaseResult]:
        return [await run_path2(*job) for job in path2_jobs]

    results.extend(asyncio.run(gather_path2()))
    return results


def write_report(results: list[CaseResult]) -> tuple[int, int, int]:
    total = len(results)
    offenders = [r for r in results if r.violations]
    must_count = sum(len(r.must_violations) for r in results)
    should_count = sum(len(r.violations) - len(r.must_violations) for r in results)
    drops = [r for r in results if r.silent_drop]
    rejected = sum(1 for r in results if r.status == "rejected")

    lines = [
        "# persona QA report",
        "",
        f"- 총 실행: {total}건 (경로2 pydantic 거부 {rejected}건 포함)",
        f"- **MUST 위반: {must_count}** / SHOULD 위반: {should_count}"
        f" / SILENT-DROP 경고: {len(drops)}",
        "",
    ]
    if offenders:
        lines.append("## 위반 목록")
        lines.append("")
        lines.append("| qid | case | tool | path | status | violations | detail |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in offenders:
            det = r.detail.replace("\n", " ")[:160]
            lines.append(
                f"| {r.qid} | {r.case} | {r.tool} | {r.path} | {r.status}"
                f" | {', '.join(r.violations)} | {det} `{r.args}` |"
            )
        lines.append("")
    if drops:
        lines.append("## INV-SILENT-DROP 경고 (필터 무음 무시 — 차등 검사)")
        lines.append("")
        for r in drops:
            lines.append(f"- {r.qid} · {r.case} · `{r.args}`")
        lines.append("")
    verdict = "**수렴: MUST subset 위반 0 ✅**" if must_count == 0 else "**미수렴 — 수정 필요**"
    lines.append(verdict)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return must_count, should_count, len(drops)


def main() -> int:
    results = run_all()
    must_count, should_count, drops = write_report(results)
    print(f"persona QA: {len(results)}건 실행 → MUST 위반 {must_count},"
          f" SHOULD 위반 {should_count}, SILENT-DROP 경고 {drops}")
    print(f"report: {REPORT_PATH}")
    return 0 if must_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
