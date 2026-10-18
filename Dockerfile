FROM python:3.13-slim

ARG EXTRAS=collect
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY tgstore ./tgstore
RUN pip install ".[${EXTRAS}]" && useradd --create-home --uid 1000 tgstore && mkdir /inbox /session \
    && chown tgstore /inbox /session

USER tgstore
EXPOSE 8080
ENTRYPOINT ["tgstore"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
