# Meridian API (docs/DECISIONS.md D23, D33).
#
#   docker build -t meridian .                          # the runtime image (last stage)
#   docker run --rm -p 8000:8000 meridian               # then open http://127.0.0.1:8000/docs
#   docker build --target test -t meridian-test .       # runtime plus tests and test tools
#   docker run --rm meridian-test                       # runs the test suite on Linux
#
# No system GDAL or PROJ: the pyogrio and pyproj wheels bundle both, including the LIBKML
# driver the KML loader relies on, so the slim image needs no apt packages.

# Shared by both targets: runtime dependencies, the app, and an unprivileged user.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MERIDIAN_DATA_DIR=/data

WORKDIR /srv/meridian

# Dependencies first, so editing the code does not reinstall them on every build.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# /data holds the SQLite database and upload temp folders. The code stays owned by root,
# so the app cannot modify itself; only /data is writable.
RUN useradd --create-home --uid 1000 meridian \
    && mkdir /data \
    && chown meridian /data


# The test suite on Linux, where some guards (backslash zip-slip) do real work. Built
# only on request, so the runtime image carries no test tools, tests or samples.
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY pyproject.toml ./
COPY tests ./tests
COPY scripts ./scripts
COPY samples ./samples
USER meridian
# The code folder is read-only to this user, so pytest must not try to write its cache.
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]


# The runtime image. Last, so a plain "docker build" produces it.
FROM base AS runtime
USER meridian
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# One process on purpose: background tasks live inside it, and startup recovery assumes
# no other worker is mid-processing (docs/DECISIONS.md D17).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
