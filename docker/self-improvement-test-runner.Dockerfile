ARG PYTHON_BASE
FROM ${PYTHON_BASE}

ARG PYTHON_BASE
RUN python -c "import re,sys; assert re.fullmatch(r'(?:python|docker\\.io/library/python):[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}', sys.argv[1]), 'PYTHON_BASE must be a digest-pinned official Python image'" "${PYTHON_BASE}"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir ".[dev]"

WORKDIR /workspace
