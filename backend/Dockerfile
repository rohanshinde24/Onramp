# One image for every Onramp process role. The role is chosen by the container command.
FROM python:3.13-slim AS build
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install '.[embeddings,anthropic]'

FROM python:3.13-slim
RUN useradd --create-home --uid 10001 onramp
COPY --from=build /install /usr/local
COPY --chown=onramp alembic.ini /home/onramp/alembic.ini
COPY --chown=onramp migrations /home/onramp/migrations
USER onramp
WORKDIR /home/onramp
ENV PYTHONUNBUFFERED=1 \
    ONRAMP_ALEMBIC_INI=/home/onramp/alembic.ini \
    FASTEMBED_CACHE_PATH=/home/onramp/models \
    HF_HUB_OFFLINE=0
# Bake the embedding model into the image (ADR-011) so no process downloads it at runtime.
# The model host fails transiently, so the download is retried before the build gives up.
RUN for attempt in 1 2 3 4; do \
        python -c "from onramp.policy.embeddings import FastEmbedder; FastEmbedder()" && exit 0; \
        sleep $((attempt * 15)); \
    done; exit 1
ENV HF_HUB_OFFLINE=1
EXPOSE 8000
CMD ["onramp-api"]
