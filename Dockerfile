# Hermetic-ish container for the claim certificate pipeline.
#
# Rebuild:
#   docker build -t cacophony-cert .
#
# Run the certificate (default ENTRYPOINT):
#   docker run --rm cacophony-cert
#
# Run with arguments forwarded to claim_certificate.py:
#   docker run --rm cacophony-cert --quick
#
# Mount the working tree to inspect outputs in place:
#   docker run --rm -v "$PWD":/work -w /work cacophony-cert
#
# Notes:
#   - This image carries the certificate pipeline only. It does
#     NOT include LaTeX or the paper/ build chain.
#   - Python version is pinned to match
#     ci/claim_certificate.json provenance.dependencies.python.version_short.

FROM python:3.13.4-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /repo

COPY requirements.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY . .

ENTRYPOINT ["python", "ci/claim_certificate.py"]
