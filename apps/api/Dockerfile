FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/models/huggingface \
    TRANSFORMERS_OFFLINE=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 用于快速依赖解析和安装
RUN pip install --no-cache-dir uv

WORKDIR /app/apps/api

# 复制项目元数据
COPY apps/api/pyproject.toml ./

# 使用 uv pip install --system 直接安装到系统 Python，不创建虚拟环境
# 安装 [rerank] 和 dev extra 到系统 Python：支持 Cross-Encoder 与容器内 pytest 验收
RUN uv pip install --system --no-cache -e ".[rerank,dev]"

# 复制应用代码
COPY apps/api/app ./app
COPY apps/api/tests ./tests
COPY .env.example /app/.env.example

# 构建时预下载 cross-encoder 模型（如果网络可用）
# 模型约 22MB；若构建时无网络，将在容器首次启动时自动下载
RUN python -c "import os; os.environ['TRANSFORMERS_OFFLINE']='0'; from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512); print('Cross-encoder model pre-downloaded')" 2>/dev/null || echo "Model pre-download skipped (no network or already cached)"

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=20 \
    CMD curl -f http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
