# One recipe for every Python service: docker build --build-arg SERVICE=llm-gateway .
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /bin/uv
ARG SERVICE
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY services services
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable --package ${SERVICE}

FROM python:3.12-slim
RUN useradd --system --uid 10001 app
COPY --from=build /opt/venv /opt/venv
COPY evals /app/evals
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001
