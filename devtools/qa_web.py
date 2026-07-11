#!/usr/bin/env python3
"""인간 QA용 초경량 채팅 프론트엔드 (로컬 전용, 표준 라이브러리 + anthropic).

브라우저 채팅 → claude(sonnet-5) + 배움알리미 tool 6종 → 대화·툴콜 트레이스 표시.
tool 실행은 server.mcp.call_tool 경로 — pydantic 스키마 강제 층을 그대로 통과시켜
프로덕션과 동일한 검증을 거친다 (직접 함수 호출은 타입위반 거짓신호: persona_loop 교훈).

실행: ANTHROPIC_API_KEY=... python3 devtools/qa_web.py  →  http://localhost:8379
"""
import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
from tests.persona_qa import invariants as inv  # noqa: E402

import anthropic  # noqa: E402

PORT = int(os.environ.get("QA_WEB_PORT", "8379"))
MODEL = os.environ.get("LLM_GATE_MODEL", "claude-sonnet-5")
MAX_ROUNDS = 6
HTML_PATH = Path(__file__).resolve().parent / "qa_web.html"
SYSTEM = (
    "You are a helpful assistant that helps people find lifelong-learning courses"
    " in Korea using the provided Baeum-Alrimi (배움알리미) tools."
    " Answer in Korean."
)

# 일부 모델(claude-sonnet-5 등)은 temperature를 거부(deprecated) — 자동 폴백.
_use_temperature = True

client = anthropic.Anthropic()


def _create(client: anthropic.Anthropic, **kw: Any) -> Any:
    global _use_temperature
    if not _use_temperature:
        kw.pop("temperature", None)
    try:
        return client.messages.create(**kw)
    except Exception as e:  # noqa: BLE001 — temperature 미지원 폴백만 처리, 나머지 재발생
        if _use_temperature and "temperature" in str(e):
            _use_temperature = False
            kw.pop("temperature", None)
            return client.messages.create(**kw)
        raise


def tool_schemas() -> list[dict[str, Any]]:
    tools = asyncio.run(server.mcp.list_tools())
    return [
        {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in tools
    ]


TOOLS = tool_schemas()


def call_tool(name: str, args: dict[str, Any]) -> str:
    """프로덕션 동일 경로(pydantic 강제 층) 호출. 거부·크래시도 관측 대상으로 반환."""
    try:
        resp = asyncio.run(server.mcp.call_tool(name, args))
    except Exception as e:  # noqa: BLE001 — QA 도구: ToolError(구조화 거부)·크래시 모두 표출
        return f"tool error: {type(e).__name__}: {e}"
    return inv.to_text(resp)


def chat_turn(messages: list[dict[str, Any]], user_text: str) -> dict[str, Any]:
    """user 발화 1턴 처리 — 모델이 tool 호출을 멈출 때까지 실행.

    returns {"messages": 갱신된 전체 히스토리, "events": 이번 턴 표시용 이벤트}
    """
    messages = list(messages)
    messages.append({"role": "user", "content": user_text})
    events: list[dict[str, Any]] = []
    for _ in range(MAX_ROUNDS):
        resp = _create(
            client, model=MODEL, max_tokens=2048, temperature=0,
            system=SYSTEM, tools=TOOLS, messages=messages,
        )
        messages.append(
            {"role": "assistant", "content": [b.model_dump() for b in resp.content]}
        )
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        for b in resp.content:
            if b.type == "text" and b.text.strip():
                events.append({"kind": "text", "text": b.text})
        if not tool_uses:
            break
        results = []
        for tu in tool_uses:
            out = call_tool(tu.name, dict(tu.input))
            events.append(
                {"kind": "tool", "tool": tu.name, "input": dict(tu.input), "output": out}
            )
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": out}
            )
        messages.append({"role": "user", "content": results})
    return {"messages": messages, "events": events}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/chat":
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            result = chat_turn(payload.get("messages", []), payload["user"])
            body = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        except Exception as e:  # noqa: BLE001 — QA 도구: 오류를 UI에 그대로 표출
            err = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
            self._send(500, err.encode("utf-8"), "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[중단] ANTHROPIC_API_KEY 필요", file=sys.stderr)
        return 2
    print(f"배움알리미 인간 QA 프론트: http://localhost:{PORT}  (model={MODEL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
