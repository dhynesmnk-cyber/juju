# One image for the API, the worker and the migrations (docs/deploy.md).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY juju ./juju
RUN pip install .
COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --system --uid 10001 --home-dir /app juju
USER juju

EXPOSE 8000
CMD ["uvicorn", "juju.api.app:app", "--host", "::", "--port", "8000", "--proxy-headers"]
