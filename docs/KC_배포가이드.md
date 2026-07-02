# PlayMCP in KC 배포 가이드 (사용자 작업 절차)

> 소요 ~20분. 선행: PlayMCP 회원(playmcp.kakao.com 카카오 로그인) 필수 —
> 회원이어야 playmcp.kakaocloud.io 진입 가능.

## 0. GitHub 레포 준비 (Claude가 푸시 대행 가능)

1. github.com에서 새 레포 생성 (**public 권장** — private면 KC에 PAT 등록 필요)
2. 레포 URL을 Claude에게 전달 → `git remote add origin <URL> && git push -u origin main`
   - Dockerfile이 저장소 루트에 있음 (KC 요구사항 충족)
   - data/courses.db(10MB)가 커밋되어 있어 빌드 시 COPY됨

## 1. KC 서버 생성 (Git 소스 빌드 방식)

1. https://playmcp.kakaocloud.io 접속 → 서버 생성 (계정당 2대)
2. 소스: GitHub 레포 URL 지정 (루트 Dockerfile 자동 인식)
3. 포트: **8000** (Dockerfile EXPOSE 기준. KC가 PORT env를 주입하는 경우에도
   server.py가 `PORT` 환경변수를 읽으므로 자동 대응)
4. 빌드·기동 후 상태 **Active** 확인 → **Endpoint URL** 복사
   - MCP 경로: `<Endpoint>/mcp` (Streamable HTTP)

## 2. 배포 검증 (Claude가 원격 스모크 대행 가능)

```bash
# Endpoint를 Claude에게 전달하면 아래 검증을 자동 수행:
# tools/list 6개 + 대표 tool 호출 + 24KB/푸터 확인
```

## 3. PlayMCP 콘솔 등록

1. https://playmcp.kakao.com/console → MCP 등록 → Endpoint 입력(`/mcp`까지)
2. **"정보 불러오기"** 성공 확인 (tool 6개 표시)
3. **"임시 등록"** (⚠️ "등록 및 심사요청" 아님 — 테스트 전 심사요청 금지)
4. 도구함 추가 → PlayMCP AI채팅에서 `docs/AI채팅_테스트시나리오.md`의 5개 시나리오 실행
5. 전부 통과 후 → **심사 요청** (마지노선 7/7 월요일)

## 트러블슈팅

- 빌드 실패 시: KC 로그에서 pip 단계 확인 (requirements.txt는 mcp==1.26.0 단일 의존성)
- "정보 불러오기" 실패 시: Endpoint 끝에 `/mcp` 포함했는지, Active 상태인지 확인
- Tool 정보 변경 후엔 "정보 불러오기" 재실행 = 재심사 발생 주의
