# Meridian API (docs/DECISIONS.md D23).
#
#   docker build -t meridian .
#   docker run --rm -p 8000:8000 meridian   # then open http://127.0.0.1:8000/docs
#   docker run --rm meridian python -m pytest -q -p no:cacheprovider   # tests, on Linux
#
# No system GDAL or PROJ: the pyogrio and pyproj wheels bundle both, including the LIBKML
# driver the KML loader relies on, so the slim image needs no apt packages.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MERIDIAN_DATA_DIR=/data

WORKDIR /srv/meridian

# Dependencies first, so editing the code does not reinstall them on every build.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Tests, scripts and samples are included so the suite can run inside the image: some
# guards (backslash zip-slip) only do real work on Linux.
COPY pyproject.toml ./
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts
COPY samples ./samples

# Run as an unprivileged user. /data holds the SQLite database and upload temp folders.
RUN useradd --create-home --uid 1000 meridian \
    && mkdir /data \
    && chown meridian /data
USER meridian
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# One process on purpose: background tasks live inside it, and startup recovery assumes
# no other worker is mid-processing (docs/DECISIONS.md D17).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
