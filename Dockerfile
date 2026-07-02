# 배움알리미 (Baeum-Alrimi) MCP 서버 — PlayMCP in KC 배포용
# 사전 빌드된 SQLite를 COPY — 런타임 외부 호출 없음 (성능·출처 요건)
FROM python:3.12-slim

ENV TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY data/courses.db data/courses.db

EXPOSE 8000

CMD ["python", "server.py"]
