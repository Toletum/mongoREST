FROM docker.io/library/python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir -r requirements.txt \
    && groupadd --system --gid 10001 mongorest \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app mongorest \
    && chown mongorest:mongorest /app

COPY --chown=10001:10001 app ./app

FROM base AS test
COPY requirements-dev.txt ./requirements-dev.txt
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY --chown=10001:10001 tests ./tests
USER 10001:10001
CMD ["pytest", "-q"]

FROM base AS runtime
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
