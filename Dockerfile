FROM docker.io/library/python:3.12-slim

# numpy backs the grader's planner DP; boto3 backs the game's Bedrock sidecar
# path. One image runs the game, every bundled player and the grader.
RUN pip install --no-cache-dir fastapi==0.115.5 uvicorn[standard]==0.34.2 websockets==15.0.1 \
    pydantic==2.11.5 numpy==2.2.6 boto3==1.38.27

ENV PYTHONPATH=/app
WORKDIR /app

# Mirror the source-tree layout so absolute imports
# (`coworld.examples.commons_family.*`) resolve identically here and in tests.
# The `coworld` and `coworld.examples` packages contribute nothing else to the
# runtime image, so empty stub __init__.py files suffice.
RUN mkdir -p /app/coworld/examples && \
    touch /app/coworld/__init__.py /app/coworld/examples/__init__.py

COPY src/coworld/examples/commons_family /app/coworld/examples/commons_family

# Two shims, so the manifest runnables and tools/ci/docker_smoke.sh have real
# entrypoints at the names the scaffold expects.
RUN printf '#!/bin/sh\nexec python -m coworld.examples.commons_family.game.server "$@"\n' \
      > /bin/commons-family && \
    printf '#!/bin/sh\nexec python -m coworld.examples.commons_family.player.player "$@"\n' \
      > /bin/commons-family-player && \
    chmod +x /bin/commons-family /bin/commons-family-player

CMD ["/bin/commons-family"]
