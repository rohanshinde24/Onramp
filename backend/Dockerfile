# Two images from one codebase. The role is chosen by the container command.
#
#   core: every role that does not embed text or call a model (API, migrations, publishers,
#         consumers, workflow worker, policy loading).
#   ml:   core plus the embedding model, the model provider SDK and the policy corpus, for the
#         policy indexer and the agent worker. Build it with the corpus as a named context:
#         docker build --target ml --build-context policies=policies backend
#
# The optional dependencies are imported lazily, so a core role never needs them; a unit test
# imports each core entrypoint with them blocked.
FROM python:3.13-slim AS build-core
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM build-core AS build-ml
RUN pip install --no-cache-dir --prefix=/install-ml '.[embeddings,anthropic]'

FROM python:3.13-slim AS runtime
RUN useradd --create-home --uid 10001 onramp
COPY --chown=onramp alembic.ini /home/onramp/alembic.ini
COPY --chown=onramp migrations /home/onramp/migrations
WORKDIR /home/onramp
ENV PYTHONUNBUFFERED=1 \
    ONRAMP_ALEMBIC_INI=/home/onramp/alembic.ini
EXPOSE 8000

FROM runtime AS ml
COPY --from=build-ml /install-ml /usr/local
# The corpus is loaded into Postgres at deploy time, like migrations, so it ships with the code.
COPY --from=policies --chown=onramp . /home/onramp/policies
USER onramp
ENV FASTEMBED_CACHE_PATH=/home/onramp/models \
    HF_HUB_OFFLINE=0
# Bake the embedding model into the image (ADR-011) so no process downloads it at runtime.
# The model host fails transiently, so the download is retried before the build gives up.
RUN for attempt in 1 2 3 4; do \
        python -c "from onramp.policy.embeddings import FastEmbedder; FastEmbedder()" && exit 0; \
        sleep $((attempt * 15)); \
    done; exit 1
ENV HF_HUB_OFFLINE=1
CMD ["onramp-agent-worker"]

# Last, so a build without --target produces the core image.
FROM runtime AS core
COPY --from=build-core /install /usr/local
USER onramp
CMD ["onramp-api"]
