# SPDX-License-Identifier: MIT
# Self-documented Makefile (https://marmelab.com/blog/2016/02/29/auto-documented-makefile.html)
# Run 'make' or 'make help' to list targets.
#
# Pre-push gate (Linux with the CAN modules):  make ci
# Without CAN (macOS, Docker Desktop):          make validate lint images smoke selftest-eds

.DEFAULT_GOAL := help

REGISTRY  ?= ghcr.io/otfabric
VERSION   ?= dev
PLATFORMS ?= linux/amd64,linux/arm64

# Registry of the base images (debian, python), pinned by digest in the
# Dockerfiles (the .NET SDK image of the canopennode build comes from
# mcr.microsoft.com). The workflows set BASE_REGISTRY=mirror.gcr.io/library because
# anonymous pulls from Docker Hub are rate limited per runner address.
BASE_REGISTRY ?= docker.io/library
# Registry of the tooling images (BuildKit, QEMU, shellcheck, Python).
TOOLS_REGISTRY ?= docker.io

# Repeats a registry or network operation that failed transiently.
RETRY := scripts/retry.sh

BUILDER ?= canopen-interop

# Every adapter. Override to work on one: make smoke ADAPTERS=lely
ADAPTERS ?= canopennode lely pythoncanopen
# Every adapter's image is published (all upstream licences allow it).
PUBLISHED_ADAPTERS ?= $(ADAPTERS)

# The vcan interface of the self-test and the command that may change it.
VCAN  ?= vcan0
ADMIN ?= sudo
# Docker command of the self-test (e.g. DOCKER="sudo docker").
DOCKER ?= docker

image = $(REGISTRY)/canopen-interop-$(1)
IMAGE_TEMPLATE = $(REGISTRY)/canopen-interop-{adapter}:$(VERSION)

export REGISTRY VERSION

.PHONY: help ci validate lint images smoke selftest documents vcan fixtures fixtures-check \
	print-adapters print-published-adapters capabilities buildx-setup buildx publish publish-arch publish-manifest \
	digests release-notes clean FORCE
FORCE:

help: ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_%-]+:.*?## / {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

ci: validate lint images smoke vcan selftest documents ## Full local CI on Linux: validate, lint, build, smoke, self-test, validate documents
	@echo ""
	@echo "All local CI checks passed."

# ── Validation ───────────────────────────────────────────────────────────────

validate: fixtures-check ## Check versions.yaml against the build inputs and the fixtures against their generator
	@scripts/check-versions.sh

fixtures: ## Regenerate fixtures/eds from scripts/gen-eds-fixtures.py
	@python3 scripts/gen-eds-fixtures.py

fixtures-check: ## Fail when fixtures/eds differs from scripts/gen-eds-fixtures.py
	@python3 scripts/gen-eds-fixtures.py --check

documents: ## Validate every document the smoke test and the self-test kept in reports/ against schemas/
	@if python3 -c 'import jsonschema' 2>/dev/null; then \
		python3 scripts/validate-documents.py reports; \
	else \
		$(RETRY) docker run --rm -v "$(CURDIR):/work:ro" -w /work $(TOOLS_REGISTRY)/library/python:3.12-slim \
			sh -c 'pip install -q --root-user-action=ignore jsonschema && python scripts/validate-documents.py reports'; \
	fi

lint: ## Shellcheck the scripts, byte-compile the Python adapters and scripts
	@echo "Linting scripts..."
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck --severity=warning -x -P scripts scripts/*.sh; \
	else \
		$(RETRY) docker run --rm -v "$(CURDIR):/mnt:ro" $(TOOLS_REGISTRY)/koalaman/shellcheck:stable --severity=warning -x -P scripts scripts/*.sh; \
	fi
	@python3 -m py_compile adapters/*/*.py scripts/*.py && rm -rf adapters/*/__pycache__ scripts/__pycache__
	@sh -n adapters/canopennode/od-counts.sh

# ── Images (host architecture, loaded into Docker) ──────────────────────────

images: $(addprefix image-,$(ADAPTERS)) ## Build every adapter image for the host architecture

image-%: FORCE ## Build one adapter image, e.g. image-lely
	@echo "Building $* image ($(VERSION))..."
	@$(RETRY) docker build \
		--build-arg BASE_REGISTRY=$(BASE_REGISTRY) --build-arg ADAPTER_VERSION=$(VERSION) \
		--tag $(call image,$*):$(VERSION) \
		--file adapters/$*/Dockerfile \
		.

# ── Tests (images must be built) ────────────────────────────────────────────

smoke: ## Contract smoke test of every adapter (no CAN interface needed)
	@DOCKER="$(DOCKER)" IMAGE_TEMPLATE='$(IMAGE_TEMPLATE)' REPORTS=reports scripts/smoke.sh $(ADAPTERS)

vcan: ## Load the CAN modules and create $(VCAN) (needs root through ADMIN)
	@$(ADMIN) scripts/setup-vcan.sh $(VCAN)

selftest: ## Reference-to-reference self-test of all suites on vcan (Linux; make vcan first)
	@DOCKER="$(DOCKER)" IMAGE_TEMPLATE='$(IMAGE_TEMPLATE)' ADAPTERS="$(ADAPTERS)" VCAN=$(VCAN) \
		python3 scripts/selftest.py --reports reports

selftest-%: FORCE ## One suite of the self-test: selftest-od, -sdo, -nmt, -emcy, -pdo, -eds (eds needs no CAN)
	@DOCKER="$(DOCKER)" IMAGE_TEMPLATE='$(IMAGE_TEMPLATE)' ADAPTERS="$(ADAPTERS)" SUITES=$* VCAN=$(VCAN) \
		python3 scripts/selftest.py --reports reports

print-adapters: ## Print the adapters (ADAPTERS), one line
	@echo $(ADAPTERS)

print-published-adapters: ## Print the adapters whose images are published, one line
	@echo $(PUBLISHED_ADAPTERS)

capabilities: ## Print the capability document of every adapter
	@for a in $(ADAPTERS); do docker run --rm $(call image,$$a):$(VERSION) print-capabilities; done

release-notes: ## Print the RELEASE.md section of VERSION (the GitHub release body)
	@scripts/release-notes.sh $(VERSION)

# ── Multi-arch publish ──────────────────────────────────────────────────────
# The release workflow builds every adapter natively on an amd64 and an arm64
# runner, self-tests it there and pushes what it tested as
# <image>:<version>-<arch>; publish-manifest joins both into <image>:<version>.

buildx-setup: ## Prepare emulated multi-arch builds: QEMU handlers and a docker-container builder
	@$(RETRY) docker run --privileged --rm $(TOOLS_REGISTRY)/tonistiigi/binfmt:latest --install arm64,amd64 >/dev/null
	@docker buildx rm $(BUILDER) >/dev/null 2>&1 || true
	@$(RETRY) docker buildx create --name $(BUILDER) --driver docker-container \
		--driver-opt image=$(TOOLS_REGISTRY)/moby/buildkit:buildx-stable-1 --bootstrap >/dev/null

builder_flag = $(shell docker buildx inspect $(BUILDER) >/dev/null 2>&1 && echo --builder $(BUILDER))

buildx: ## Build every image for $(PLATFORMS) under emulation, without pushing (local check only)
	@for a in $(ADAPTERS); do \
		echo "Building $$a for $(PLATFORMS)..."; \
		$(RETRY) docker buildx build $(builder_flag) --platform $(PLATFORMS) \
			--build-arg BASE_REGISTRY=$(BASE_REGISTRY) --build-arg ADAPTER_VERSION=$(VERSION) \
			--file adapters/$$a/Dockerfile . || exit 1; \
	done

ARCH ?= $(shell uname -m | sed -e 's/^x86_64$$/amd64/' -e 's/^aarch64$$/arm64/')
ARCHS ?= amd64 arm64

check-release-version = @echo "$(VERSION)" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$$' || { echo "Error: VERSION must be v<major>.<minor>.<patch>"; exit 1; }

publish-arch: ## Push the local, tested images as <image>:$(VERSION)-$(ARCH)
	$(check-release-version)
	@for a in $(PUBLISHED_ADAPTERS); do \
		got=$$(docker image inspect --format '{{.Architecture}}' $(call image,$$a):$(VERSION)) || exit 1; \
		if [ "$$got" != "$(ARCH)" ]; then echo "Error: $$a:$(VERSION) is $$got, not $(ARCH)"; exit 1; fi; \
		echo "Pushing $$a $(VERSION) for linux/$(ARCH)..."; \
		docker tag $(call image,$$a):$(VERSION) $(call image,$$a):$(VERSION)-$(ARCH) || exit 1; \
		$(RETRY) docker push --quiet $(call image,$$a):$(VERSION)-$(ARCH) || exit 1; \
	done

publish-manifest: ## Join the per-architecture images into the multi-arch <image>:$(VERSION)
	$(check-release-version)
	@for a in $(PUBLISHED_ADAPTERS); do \
		echo "Creating $$a $(VERSION) from: $(ARCHS)"; \
		$(RETRY) docker buildx imagetools create --tag $(call image,$$a):$(VERSION) \
			$(foreach arch,$(ARCHS),$(call image,$$a):$(VERSION)-$(arch)) || exit 1; \
		for arch in $(ARCHS); do \
			$(RETRY) docker buildx imagetools inspect $(call image,$$a):$(VERSION) --format '{{json .Manifest}}' \
				| tr -d ' \n' | grep -q "\"architecture\":\"$$arch\"" || { echo "Error: $$a:$(VERSION) has no linux/$$arch image"; exit 1; }; \
		done; \
	done

digests: ## Print "<adapter> <digest>" of the published multi-arch images of VERSION
	@for a in $(PUBLISHED_ADAPTERS); do \
		d=$$($(RETRY) docker buildx imagetools inspect $(call image,$$a):$(VERSION) --format '{{.Manifest.Digest}}') || exit 1; \
		echo "$$a $$d"; \
	done

clean: ## Remove local images and reports
	@for a in $(ADAPTERS); do docker rmi $(call image,$$a):$(VERSION) 2>/dev/null || true; done
	@rm -rf reports
