# SPDX-License-Identifier: MIT
# canopen-interop adapter: python canopen 2.4.1 (MIT) on python-can 4.6.1
# (LGPL-3.0-only), socketcan. The CANopen work is done by python canopen;
# adapter.py only translates the container contract to its API.
# Build context: the repository root (make image-pythoncanopen).

ARG BASE_REGISTRY=docker.io/library
FROM ${BASE_REGISTRY}/python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753

ARG ADAPTER_VERSION=dev
ENV UPSTREAM_REVISION=317fd3d61513b33ddb1aaf8a3ea2c23384a275ee \
    ADAPTER_VERSION=${ADAPTER_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/canopen-interop

# Every package, including the transitive ones, is pinned.
COPY adapters/pythoncanopen/requirements.txt /opt/canopen-interop/requirements.txt
RUN pip install --no-cache-dir --root-user-action=ignore --no-deps -r /opt/canopen-interop/requirements.txt \
    && pip check

COPY adapters/common/coi.py adapters/pythoncanopen/adapter.py /opt/canopen-interop/
COPY fixtures /fixtures
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/canopen-interop/
COPY licenses /usr/share/doc/canopen-interop/licenses
RUN printf '#!/bin/sh\nexec python3 /opt/canopen-interop/adapter.py "$@"\n' > /usr/local/bin/canopen-interop \
    && chmod 0755 /usr/local/bin/canopen-interop \
    && useradd --system --no-create-home --uid 10001 canopen
USER canopen
WORKDIR /tmp

LABEL org.opencontainers.image.source="https://github.com/otfabric/canopen-interop" \
      org.opencontainers.image.description="canopen-interop adapter: python canopen 2.4.1 (python-can 4.6.1, socketcan)" \
      org.opencontainers.image.version="${ADAPTER_VERSION}" \
      org.opencontainers.image.licenses="MIT AND LGPL-3.0-only"

ENTRYPOINT ["canopen-interop"]
CMD ["print-capabilities"]
