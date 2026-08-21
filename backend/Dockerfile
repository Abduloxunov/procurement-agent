# Playwright's own image, because the browser rung needs a real Chromium and
# getting its system libraries right by hand is a waste of an afternoon.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/hf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only chromium. The other engines are ~400 MB we never launch.
RUN python -m playwright install chromium

COPY app ./app
COPY scripts ./scripts

# Bake the embedding model in rather than downloading on first request --
# otherwise the first user waits two minutes for a 130 MB fetch.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

# SQLite and Qdrant are files. Mount a volume here or the data dies with
# the container.
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
