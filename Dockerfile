# Multi-stage image for kazemo.
#
#   docker build --target test -t kazemo-test .   # dependencies + tests, used by CI to run the tests
#   docker build -t kazemo .                       # small runtime image (default target)
#
#   docker run --rm kazemo --help
#   docker run --rm -v "$PWD/data:/data" --env-file .env kazemo run /data/pipeline.toml
#   docker run --rm -p 8000:8000 --entrypoint /app/deploy/demo.sh kazemo   # demo dashboard

ARG PYTHON_VERSION=3.12

# ---------- base: shared Python settings -------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---------- test: source + dev dependencies; CI runs pytest/ruff in this image -----------------
FROM base AS test
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install -e ".[dev]"
COPY tests ./tests
COPY examples ./examples
COPY pipeline.example.toml ./
CMD ["pytest", "-q"]

# ---------- build: wheel of the package ------------------------------------------------------
FROM base AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip wheel --no-deps --wheel-dir /dist .

# ---------- runtime: only the installed package, non-root user --------------------------------
FROM base AS runtime
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="kazemo" \
      org.opencontainers.image.description="Collection and pre-processing of Kazakh social-media text for emotion research" \
      org.opencontainers.image.source="https://github.com/tenajuro12/kazemo" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

RUN useradd --create-home --uid 10001 kazemo \
    && mkdir -p /data && chown kazemo:kazemo /data
COPY --from=build /dist/*.whl /tmp/
RUN --mount=type=cache,target=/root/.cache/pip pip install /tmp/*.whl && rm /tmp/*.whl

# synthetic demo data and config (no real posts are ever baked into the image)
COPY examples/demo_raw.jsonl /app/examples/
COPY deploy/demo.toml deploy/demo.sh /app/deploy/
RUN chmod +x /app/deploy/demo.sh

USER kazemo
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000
ENTRYPOINT ["kazemo"]
CMD ["--help"]
