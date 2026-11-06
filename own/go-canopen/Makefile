# The canopen-interop-otfabric image, from the repository root:
#   make -f own/go-canopen/Makefile image smoke interop
#
# Apart from the top-level Makefile on purpose: this image is not in ADAPTERS
# and not in a release of the harness. smoke needs no CAN interface; interop
# runs on Linux with a vcan interface (make vcan; on macOS in a Linux VM, see
# TESTING.md).

IMAGE ?= ghcr.io/otfabric/canopen-interop-otfabric:dev
# The harness release of the reference images to test against.
VERSION ?= $(shell git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -1)
REFERENCES ?= canopennode lely pythoncanopen
BASE_REGISTRY ?= docker.io/library
VCAN ?= vcan0
DOCKER ?= docker

# The latest go-canopen release, as the workflow builds it.
UPSTREAM_VERSION ?= $(shell git ls-remote --tags --refs https://github.com/otfabric/go-canopen.git 'v[0-9]*' | sed 's,.*refs/tags/,,' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$$' | sort -V | tail -1)
UPSTREAM_SHA ?= $(shell git ls-remote --tags https://github.com/otfabric/go-canopen.git 'refs/tags/$(UPSTREAM_VERSION)^{}' | cut -f1)

export VERSION VCAN DOCKER
export ADAPTER_IMAGE_otfabric = $(IMAGE)

.PHONY: image smoke interop

image:
	$(DOCKER) build --build-arg BASE_REGISTRY=$(BASE_REGISTRY) --build-arg UPSTREAM_VERSION=$(UPSTREAM_VERSION) --build-arg UPSTREAM_SHA=$(UPSTREAM_SHA) --build-arg ADAPTER_VERSION=$(UPSTREAM_VERSION) --tag $(IMAGE) --file own/go-canopen/Dockerfile .

smoke:
	scripts/smoke.sh otfabric

interop:
	scripts/interop.sh otfabric $(REFERENCES)
