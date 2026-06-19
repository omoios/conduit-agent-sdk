# Conduit sandbox image (seed v1 "Local Docker sandbox", deterministic path).
#
# Multi-stage: stage 1 compiles a LINUX conduit_sdk wheel with maturin; stage 2
# is a minimal runtime that pip-installs it and runs a real AgentServer (ACP over
# stdio). This is the deterministic, credential-free in-container ACP agent that
# the host SDK drives via `acp_agent(["docker","run","-i","--rm","conduit-sandbox",...])`.
#
# The build CONTEXT must contain: Cargo.toml, Cargo.lock, pyproject.toml,
# README.md, src/, python/ (with any HOST-built *.so removed -- a macOS Mach-O
# .so would poison the Linux wheel), and echo_agent.py. See infra/sandbox/build.sh
# (assembled clean context) -- do not build straight from the repo root.

FROM ghcr.io/pyo3/maturin AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock pyproject.toml README.md ./
COPY src ./src
COPY python ./python
RUN maturin build --release --out /wheels

FROM python:3.12-slim AS runtime
COPY --from=build /wheels/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl
COPY echo_agent.py /opt/echo_agent.py
# Default command: the deterministic ACP echo agent, speaking JSON-RPC over stdio.
ENTRYPOINT ["python", "/opt/echo_agent.py"]
