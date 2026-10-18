# syntax=docker/dockerfile:1.7
# TGO container image. Network is used at BUILD time only (fetching wheels); the runtime stage installs
# from a local wheelhouse with no index, and compose runs every pipeline service with `network_mode: none`.
#
# Targets:
#   runtime    (default) the `tgo` CLI, non-root, offline env baked in
#   dev        runtime + repo tests/docs/examples + pytest, for `docker compose run --rm test`
#   wheelhouse every wheel needed for an air-gapped install:
#              docker build --target wheelhouse --output type=local,dest=wheelhouse .
#
# Build args (all optional; see .env.example):
#   PYTHON_IMAGE   base image, pinned by digest
#   TGO_EXTRAS     pip extras, comma-separated: "" | scale | scale,docs,ids | all ...
#   APT_PACKAGES   extra Debian packages for the runtime, e.g. "tesseract-ocr tesseract-ocr-eng"
#   PIP_EXTRA      extra pip requirements baked into the image, e.g. "my-tgo-plugin==1.2"
ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY tgo ./tgo
RUN pip wheel . --no-deps -w /dist

FROM ${PYTHON_IMAGE} AS wheelhouse-build
ARG TGO_EXTRAS=scale
ARG PIP_EXTRA=
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY --from=build /dist /dist
RUN set -eu; whl=$(ls /dist/tgo-*.whl); spec="$whl"; \
    if [ -n "$TGO_EXTRAS" ]; then spec="$whl[$TGO_EXTRAS]"; fi; \
    pip wheel "$spec" $PIP_EXTRA -w /wheelhouse --prefer-binary

FROM scratch AS wheelhouse
COPY --from=wheelhouse-build /wheelhouse /

FROM ${PYTHON_IMAGE} AS runtime
ARG TGO_EXTRAS=scale
ARG PIP_EXTRA=
ARG APT_PACKAGES=
ARG UID=1000
ARG GID=1000
LABEL org.opencontainers.image.title="tgo" \
      org.opencontainers.image.description="Telegram Graph Ontology: offline ingestion, analysis and visualization" \
      org.opencontainers.image.source="https://github.com/simonedland/Ontology"
RUN set -eu; if [ -n "$APT_PACKAGES" ]; then \
      apt-get update; apt-get install -y --no-install-recommends $APT_PACKAGES; rm -rf /var/lib/apt/lists/*; fi; \
    groupadd -g "$GID" tgo; useradd -m -u "$UID" -g "$GID" -s /usr/sbin/nologin tgo; \
    mkdir -p /data /out /config /plugins /models /spill; chown tgo:tgo /out /spill
RUN --mount=type=bind,from=wheelhouse-build,source=/wheelhouse,target=/wh \
    set -eu; spec=tgo; if [ -n "$TGO_EXTRAS" ]; then spec="tgo[$TGO_EXTRAS]"; fi; \
    pip install --no-cache-dir --no-index --find-links /wh "$spec" $PIP_EXTRA; \
    python -I -m tgo offline-check
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/tgo-entrypoint
COPY --chmod=0755 docker/proxy.py /usr/local/bin/tgo-proxy.py
# Offline kill switches (tgo also sets these on import; baking them covers non-tgo processes too).
# PIP_NO_INDEX: nothing in a running container may reach a package index.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/plugins \
    PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 ORT_DISABLE_TELEMETRY=1 TZ=UTC \
    TGO_SCALE_TEMP_DIRECTORY=/spill
USER tgo
WORKDIR /out
VOLUME ["/out", "/spill"]
ENTRYPOINT ["tgo-entrypoint"]
CMD ["--help"]

FROM runtime AS dev
USER root
RUN PIP_NO_INDEX=0 pip install --no-cache-dir "pytest>=8.4"
WORKDIR /src
COPY --chown=tgo:tgo . .
USER tgo
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
