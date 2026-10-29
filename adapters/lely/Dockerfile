# SPDX-License-Identifier: MIT
# canopen-interop adapter: Lely CANopen (lely-core v2.3.5, Apache-2.0).
#
#   lely-coi     adapters/lely/lely-coi.cpp on liblely-coapp/liblely-co:
#                BasicSlave (device), AsyncMaster with the coapp Sdo client
#                (expedited, segmented, block), the NMT command request, the
#                heartbeat and EMCY consumers and the Receive-PDO service.
#   dcf          Lely's Python dcf-tools (the "dcfchk" checker) for eds-check.
#
# adapter.py only translates the container contract to lely-coi's command line
# and its output to JSON.
# Build context: the repository root (make image-lely).

ARG BASE_REGISTRY=docker.io/library

FROM ${BASE_REGISTRY}/debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8 AS build
ARG LELY_VERSION=v2.3.5
ARG LELY_SHA256=38bd773ae92bb59087759696cede81ca825577fa3ee09c67ae126c0238ee1ce8
RUN apt-get update \
    && apt-get install -y --no-install-recommends autoconf automake ca-certificates curl g++ libtool make pkg-config \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSLo lely.tar.gz "https://gitlab.com/lely_industries/lely-core/-/archive/${LELY_VERSION}/lely-core-${LELY_VERSION}.tar.gz" \
    && echo "${LELY_SHA256}  lely.tar.gz" | sha256sum -c - \
    && tar xzf lely.tar.gz \
    && cd "lely-core-${LELY_VERSION}" \
    && autoreconf -i \
    && ./configure --prefix=/opt/lely --disable-static --disable-python --disable-cython --disable-doc \
        --disable-tests --disable-unit-tests --disable-tools \
    && make -j"$(nproc)" \
    && make install \
    && mkdir -p /out/src /out/licenses /out/dcf \
    && cp /src/lely.tar.gz "/out/src/lely-core-${LELY_VERSION}.tar.gz" \
    && cp LICENSE NOTICE /out/licenses/ \
    && cp -a python/dcf-tools/dcf /out/dcf/dcf
COPY adapters/lely/lely-coi.cpp /src/lely-coi.cpp
RUN mkdir -p /opt/lely/bin && g++ -std=c++14 -O2 -Wall -o /opt/lely/bin/lely-coi /src/lely-coi.cpp \
        $(PKG_CONFIG_PATH=/opt/lely/lib/pkgconfig pkg-config --cflags --libs liblely-coapp) \
        -Wl,-rpath,/opt/lely/lib

FROM ${BASE_REGISTRY}/python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753
ARG ADAPTER_VERSION=dev
ENV UPSTREAM_VERSION=v2.3.5 \
    UPSTREAM_REVISION=9e3267d26018f6f6babd50786f6ae2af89cc57ea \
    ADAPTER_VERSION=${ADAPTER_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/canopen-interop:/opt/lely/dcf-tools \
    PATH=/opt/lely/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
COPY --from=build /opt/lely/lib/ /opt/lely/lib/
COPY --from=build /opt/lely/bin/lely-coi /opt/lely/bin/lely-coi
COPY --from=build /out/dcf/ /opt/lely/dcf-tools/
COPY --from=build /out/src/ /usr/share/src/lely/
COPY --from=build /out/licenses/ /usr/share/doc/lely-core/
COPY adapters/common/coi.py adapters/lely/adapter.py /opt/canopen-interop/
COPY fixtures /fixtures
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/canopen-interop/
COPY licenses /usr/share/doc/canopen-interop/licenses
RUN rm -rf /opt/lely/lib/pkgconfig /opt/lely/lib/*.la \
    && printf '#!/bin/sh\nexec python3 /opt/canopen-interop/adapter.py "$@"\n' > /usr/local/bin/canopen-interop \
    && chmod 0755 /usr/local/bin/canopen-interop \
    && lely-coi 2>&1 | grep -q usage \
    && useradd --system --no-create-home --uid 10001 canopen
USER canopen
WORKDIR /tmp

LABEL org.opencontainers.image.source="https://github.com/otfabric/canopen-interop" \
      org.opencontainers.image.description="canopen-interop adapter: Lely CANopen lely-core v2.3.5" \
      org.opencontainers.image.version="${ADAPTER_VERSION}" \
      org.opencontainers.image.licenses="MIT AND Apache-2.0"

ENTRYPOINT ["canopen-interop"]
CMD ["print-capabilities"]
