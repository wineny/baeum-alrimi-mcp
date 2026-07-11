#!/usr/bin/env python3
"""멀티턴 dead-end 러너 (계획 dead-end-zero-loop v4 단계 5).

시드(question_bank 30 동결 + dead_end_seeds) × ≤3턴 궤적을 followup_policy로
구동하고, INV-DEAD-END(MUST 주 오라클)를 판정한다:

    dead_end(traj) ≔ NOT intent_satisfied(traj)
                     AND (인접 턴 novelty=∅ [stall] OR 종말적 비행동가능 종료)

- novelty: 전달 콘텐츠(카드ID·org·유효전화·(예상)일자)만 — invariants.novelty SSOT
- intent_satisfied: 페르소나별 기계 판정식(계획 v4 단계 3). 토큰 스캔 금지.
- terminal(종말적 비행동가능): 정책 소진(None) 종료이면서 마지막 턴에
  load-bearing 콘텐츠(카드·유효전화·예상일자)와 미소진 tier3b 파라미터가
  모두 없는 경우 (v4 델타 2: alt_regions 미소진이면 terminal 아님)
- 매 턴 기존 INV-* MUST(24KB·FOOTER·PREDICTION·HALLUCINATION·BANNED·CRASH) 수집
- AC4 자기검증: 정적 캡처 픽스처(fixtures/)에서 stall 오라클 발화/미발화를
  본 실행 전에 assert — 오라클이 둔감하면 시끄럽게 즉사한다.

exit 0 ⇔ dead-end 궤적 0 AND MUST 위반 0. 실행:
  python3 tests/persona_qa/dead_end_loop.py [--baseline]
--baseline: 리포트를 dead_end_baseline.md에도 복사(단계 2 산출물).
"""
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import server  # noqa: E402
from tests.persona_qa import invariants as inv  # noqa: E402
from tests.persona_qa import followup_policy as policy  # noqa: E402
from tests.persona_qa.dead_end_seeds import SEEDS, Seed  # noqa: E402
from tests.persona_qa.question_bank import QUESTIONS  # noqa: E402

MAX_TURNS = 3
HERE = Path(__file__).resolve().parent
REPORT_PATH = HERE / "dead_end_report.md"
BASELINE_PATH = HERE / "dead_end_baseline.md"
FIXTURE_DIR = HERE / "fixtures"


@dataclass
class Turn:
    tool: str
    args: dict[str, Any]
    text: str
    content: dict[str, Any]
    must: list[str] = field(default_factory=list)


@dataclass
class Trajectory:
    qid: str
    persona: str
    cls: str
    utterance: str
    turns: list[Turn] = field(default_factory=list)
    ended_by_policy: bool = False   # 정책이 None을 반환해 종료(3턴 캡 아님)
    intent: bool = False
    stall: bool = False
    terminal: bool = False

    @property
    def dead_end(self) -> bool:
        return (not self.intent) and (self.stall or self.terminal)

    @property
    def reason(self) -> str:
        if not self.dead_end:
            return ""
        return "stall" if self.stall else "terminal"

    @property
    def must_violations(self) -> list[str]:
        return [v for t in self.turns for v in t.must]


def _call(tool: str, args: dict[str, Any]) -> tuple[str, list[str]]:
    """서버 함수 직접 호출. 크래시는 INV-CRASH로 수집(러너는 죽지 않는다)."""
    try:
        return getattr(server, tool)(**args), []
    except Exception as e:  # noqa: BLE001 — 크래시 포착이 목적
        return f"(CRASH) {type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}", ["INV-CRASH"]


def intent_satisfied(persona: str, turns: list[Turn]) -> bool:
    """페르소나별 의도-충족 판정식 (계획 v4 단계 3, 궤적 레벨).

    load-bearing 콘텐츠로만 환원 — 토큰 스캔 금지:
    - cards: 어느 턴이든 카드 ID 획득 (환각 ID는 INV-NO-HALLUCINATION이 별도 MUST)
    - predicts: 유효 (예상) 일자 획득 + 그 턴에 INV-PREDICTION 위반 없음
    - phones: 유효 전화(숫자 앵커 — '번호 미상' 자동 배제)
    - P1의 '명확 없음+대안'은 대안이 그 자체로 카드/유효전화를 산출해야 충족
      (안내 문구·get_filter_options 포인터 단독 불충족 — v3 필수수정 1)
    - P2·P5·P6의 확장([지역 확장]/[타지역 안내]) 경유 카드는 마커·라벨이 응답에
      존재할 때만 도달 가능한 경로라 별도 검사 없이 provenance가 보장된다
    """
    # 메타 질의 예외: 사용자가 '필터 옵션'을 물었고(첫 툴 = get_filter_options)
    # 옵션 응답 헤더를 받았으면 그 자체가 답 — 카드 요구는 오탐(P6-3 QA).
    # 정책 유도로 뒤늦게 filter_options를 '거친' 궤적에는 적용되지 않는다.
    if turns and turns[0].tool == "get_filter_options" and any(
        t.text.startswith("### 필터 옵션") for t in turns
    ):
        return True
    cards = any(t.content["card_ids"] for t in turns)
    phones = any(t.content["phones"] for t in turns)
    predicts = any(
        t.content["predict_dates"] and "INV-PREDICTION" not in t.must for t in turns
    )
    notice_none = any(("없습니다" in t.text) or ("없음" in t.text) for t in turns)
    if persona == "P1":
        return cards or predicts or (notice_none and phones)
    if persona in ("P2", "P5", "P6"):
        return cards
    if persona == "P3":
        return cards or phones
    if persona == "P4":
        return cards or predicts
    return cards or phones or predicts


def judge(traj: Trajectory) -> None:
    """novelty·terminal 판정을 궤적에 기록 (INV-DEAD-END 구성요소)."""
    traj.intent = intent_satisfied(traj.persona, traj.turns)
    for prev, cur in zip(traj.turns, traj.turns[1:]):
        if not inv.novelty(cur.content, prev.content):
            traj.stall = True
            break
    last = traj.turns[-1]
    no_load_bearing = not (
        last.content["card_ids"] or last.content["phones"]
        or last.content["predict_dates"]
    )
    # v4 델타 2: tier3b 재호출 파라미터가 미소진이면 terminal 아님
    unconsumed_alt = bool(last.content["alt_regions"])
    traj.terminal = traj.ended_by_policy and no_load_bearing and not unconsumed_alt


def run_trajectory(qid: str, persona: str, cls: str, utterance: str,
                   tool: str, args: dict[str, Any]) -> Trajectory:
    traj = Trajectory(qid, persona, cls, utterance)
    cur: tuple[str, dict[str, Any]] | None = (tool, dict(args))
    for i in range(MAX_TURNS):
        t, a = cur
        text, crash = _call(t, a)
        must = crash or [v for v in inv.violations(text, t) if v in inv.MUST]
        traj.turns.append(Turn(t, a, text, inv.response_content(text), must))
        if crash:
            traj.ended_by_policy = True  # 크래시 궤적은 즉시 종말 판정 대상
            break
        cur = policy.next_turn(t, a, text, persona, i)
        if cur is None:
            traj.ended_by_policy = True
            break
    judge(traj)
    return traj


def selftest_ac4() -> None:
    """AC4: 정적 캡처 픽스처에서 오라클 발화/미발화 자기검증 (본 실행 전 필수).

    픽스처는 동결 서버(c4de4ff)에서 1회 캡처한 정적 텍스트 — followup_policy로
    라이브 생성하지 않는다(오라클-시뮬레이터 재결합 금지, v3 권장수정 6).
    """
    stall_texts = [
        (FIXTURE_DIR / f"known_stall_turn{i}.md").read_text(encoding="utf-8")
        for i in (1, 2, 3)
    ]
    traj = Trajectory("FX-STALL", "P2", "FIXTURE", "known-stall 순환")
    traj.turns = [
        Turn("search_courses", {}, t, inv.response_content(t)) for t in stall_texts
    ]
    traj.ended_by_policy = True
    judge(traj)
    assert traj.dead_end and traj.stall, (
        "AC4 실패: known-stall 픽스처에서 오라클 미발화 — 오라클 둔감, 루프 무의미"
    )

    prog_texts = [
        (FIXTURE_DIR / f"progress_turn{i}.md").read_text(encoding="utf-8")
        for i in (1, 2)
    ]
    traj2 = Trajectory("FX-PROG", "P2", "FIXTURE", "정상 진전 궤적")
    traj2.turns = [
        Turn("search_courses", {}, t, inv.response_content(t)) for t in prog_texts
    ]
    traj2.ended_by_policy = True
    judge(traj2)
    assert not traj2.dead_end, "AC4 실패: 정상 진전 궤적이 dead-end로 오판(거짓양성)"


def run_all() -> list[Trajectory]:
    trajs = [
        run_trajectory(q.qid, q.persona, "QBANK", q.text, q.tool, q.args)
        for q in QUESTIONS
    ]
    trajs += [
        run_trajectory(s.qid, s.persona, s.cls, s.text, s.tool, s.args)
        for s in SEEDS
    ]
    return trajs


def write_report(trajs: list[Trajectory], baseline: bool) -> tuple[int, int]:
    dead = [t for t in trajs if t.dead_end]
    must_total = sum(len(t.must_violations) for t in trajs)
    by_cls: dict[str, int] = {}
    for t in dead:
        by_cls[t.cls] = by_cls.get(t.cls, 0) + 1

    lines = [
        "# dead-end 궤적 리포트 (≤3턴, INV-DEAD-END)",
        "",
        f"- 궤적: {len(trajs)}개 (question_bank {len(QUESTIONS)} + seeds {len(SEEDS)})",
        f"- **dead-end: {len(dead)}** / MUST 위반(전 턴): {must_total}",
        f"- 클래스 분포: {by_cls or '—'}",
        "",
    ]
    if BASELINE_PATH.exists() and not baseline:
        first = BASELINE_PATH.read_text(encoding="utf-8").splitlines()
        base_n = next((ln for ln in first if ln.startswith("- **dead-end:")), "?")
        lines.insert(4, f"- baseline 대비: {base_n.strip('- *')} → 현재 {len(dead)}")
    if dead:
        lines.append("## dead-end 궤적")
        lines.append("")
        for t in dead:
            lines.append(
                f"### {t.qid} · {t.persona} · {t.cls} · 사유={t.reason} — “{t.utterance}”"
            )
            for i, turn in enumerate(t.turns, 1):
                c = turn.content
                lines.append(
                    f"- 턴{i} `{turn.tool}` `{turn.args}` → cards={len(c['card_ids'])}"
                    f" ph={len(c['phones'])} pred={len(c['predict_dates'])}"
                    f" alt={c['alt_regions']}"
                    + (f" MUST={turn.must}" if turn.must else "")
                )
            lines.append("")
    violated = [t for t in trajs if t.must_violations]
    if violated:
        lines.append("## MUST 위반 궤적")
        lines.extend(f"- {t.qid}: {t.must_violations}" for t in violated)
    verdict = (
        "**INV-DEAD-END 0 ✅**" if not dead and not must_total
        else "**미수렴 — 개선 라운드 필요**"
    )
    lines.append(verdict)
    body = "\n".join(lines)
    REPORT_PATH.write_text(body, encoding="utf-8")
    if baseline:
        BASELINE_PATH.write_text(
            body.replace("# dead-end 궤적 리포트",
                         "# Stage-0 baseline (동결 서버 측정)"),
            encoding="utf-8",
        )
    return len(dead), must_total


def main() -> int:
    baseline = "--baseline" in sys.argv
    selftest_ac4()
    print("AC4 selftest: stall 발화 ✓ / 진전 미발화 ✓")
    trajs = run_all()
    dead_n, must_n = write_report(trajs, baseline)
    print(f"dead-end loop: 궤적 {len(trajs)}개 → dead-end {dead_n}, MUST 위반 {must_n}")
    print(f"report: {REPORT_PATH}" + (f" (+baseline)" if baseline else ""))
    return 0 if dead_n == 0 and must_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
