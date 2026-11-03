# The tracker's app image: small, non-root, and holding no secrets (ADR-018, T21).
# Secrets arrive as environment variables at run time, and the owner's database login
# never does: migrations run from the same image in a separate, one-off container.
FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY tracker ./tracker
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev

FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin tracker
COPY --from=build --chown=root:root /app /app
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# The commit this image was built from, for the monitoring drift check (/healthz).
ARG TRACKER_VERSION=unknown
ENV TRACKER_VERSION=$TRACKER_VERSION
USER 10001
CMD ["python", "-m", "tracker.serve"]
