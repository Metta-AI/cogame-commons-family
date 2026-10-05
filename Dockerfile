FROM docker.io/library/python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN pip install --no-cache-dir .

# Two shims, so the manifest runnables and tools/ci/docker_smoke.sh have real
# entrypoints at the names the scaffold expects.
RUN printf '#!/bin/sh\nexec python -m commons_family.game.server "$@"\n' \
      > /bin/commons-family && \
    printf '#!/bin/sh\nexec python -m commons_family.player.player "$@"\n' \
      > /bin/commons-family-player && \
    chmod +x /bin/commons-family /bin/commons-family-player

CMD ["/bin/commons-family"]
