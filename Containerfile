# Dev toolchain image: every Python, uv and Node step runs in here, never on
# the Mac itself. Built and run with Apple's `container` CLI via the Makefile:
#
#   make image      # build this image (tag cardboardcad-dev)
#   make test       # engine pytest
#   make runtime    # cross-build the macOS Python runtime into build/python-runtime
#
# Swift, Xcode and codesign steps stay on the Mac (see Makefile).
# Bump pins together; digests are the multi-arch index digests.

FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv

FROM docker.io/library/node:22.23.5-bookworm-slim@sha256:be979e5523c76fa4d5ca4ebd3099078f220aa47ec39c48ad5b5df9314debd6eb

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git make file \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/

# uv-managed CPython, the same version as the bundled runtime (3.12.13), so
# bytecode compiled here matches the macOS interpreter's .pyc magic number.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/work/build/cache/uv \
    UV_PROJECT_ENVIRONMENT=/work/build/container-venv \
    CARDBOARD_IN_CONTAINER=1
RUN uv python install 3.12.13 \
 && ln -s "$(uv python find 3.12.13)" /usr/local/bin/python3 \
 && python3 --version \
 && git config --system --add safe.directory /work

WORKDIR /work
