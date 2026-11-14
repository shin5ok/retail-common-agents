# Lightweight Dockerfile for AIOps Multi-Agent Platform on Google Cloud Run
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

# 依存関係定義と同期
COPY pyproject.toml README.md uv.lock* ./
RUN uv sync --frozen --default-index https://pypi.org/simple

# アプリケーションコードと静的フロントエンド
COPY app ./app
COPY frontend/dist ./frontend/dist
COPY agents-cli-manifest.yaml ./
COPY .env* ./

ENV PORT=8080
EXPOSE 8080

CMD ["uv", "run", "--frozen", "python", "-m", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
