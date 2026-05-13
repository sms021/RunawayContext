# RunawayContext v3 — clean-install verifier (HR-15).
#
# Builds a minimal python:3.11-slim image, installs the package and its dev
# extras at build time (network egress is allowed during BUILD only), and
# runs `docker/clean-install.sh` at runtime to exercise the public CLI
# surface end-to-end in an isolated sandbox. The runtime container makes NO
# network calls (HR-1).
FROM python:3.11-slim AS build

WORKDIR /app
COPY . /app

# Install the package in editable mode along with the `dev` extras (pytest,
# coverage, ruff). All network I/O happens here at build time only.
RUN pip install --no-cache-dir -e ".[dev]"

# Make the src layout importable without relying on the editable shim.
ENV PYTHONPATH=/app/src

CMD ["bash", "docker/clean-install.sh"]
