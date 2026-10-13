# One image for every Onramp process role. The role is chosen by the container command.
FROM python:3.13-slim AS build
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.13-slim
RUN useradd --create-home --uid 10001 onramp
COPY --from=build /install /usr/local
COPY --chown=onramp alembic.ini /home/onramp/alembic.ini
COPY --chown=onramp migrations /home/onramp/migrations
USER onramp
WORKDIR /home/onramp
ENV PYTHONUNBUFFERED=1 \
    ONRAMP_ALEMBIC_INI=/home/onramp/alembic.ini
EXPOSE 8000
CMD ["onramp-api"]
