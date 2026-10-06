.DEFAULT_GOAL := check

# macOS ships Make 3.81, which ignores .SHELLFLAGS and .ONESHELL.
SHELL := /bin/bash -euo pipefail
# Use the same sanitizer/compiler family on macOS and Linux. The firmware uses Xtensa GCC.
CXX := clang++

PRETTIER := bun run --silent prettier
TAPLO := bun run --silent taplo
ACTIONLINT := bun run --silent github-actionlint
CPP_FILES := $(wildcard firmware/src/*.cpp firmware/src/*.h tests/*.cpp)
CPP_TESTS := $(wildcard tests/*.cpp)
CPP_FLAGS := -std=c++17 -Wall -Wextra -Werror -pedantic
JSON_INCLUDE := .pio/libdeps/m5stickc-plus2/ArduinoJson/src
# renovate: datasource=custom.platformio depName=platformio/tool/tool-cppcheck
CPPCHECK_VERSION := 1.22200.0
CPPCHECK := uv run --locked pio pkg exec --package platformio/tool-cppcheck@$(CPPCHECK_VERSION) -- cppcheck
# renovate: datasource=npm depName=renovate
RENOVATE_VERSION := 44.140.0

.PHONY: install-deps
install-deps:
	uv sync --all-groups --locked
	bun install --frozen-lockfile
	uv run --locked pio pkg install
	$(CPPCHECK) --version

.PHONY: install-hooks
install-hooks:
	uv run --locked prek install

.PHONY: lint
lint:
	$(PRETTIER) -u -c .
	$(TAPLO) fmt --check
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked clang-format --dry-run --Werror $(CPP_FILES)

.PHONY: lint-fix
lint-fix:
	$(PRETTIER) -u -w .
	$(TAPLO) fmt
	uv run --locked ruff check --fix .
	uv run --locked ruff format .
	uv run --locked clang-format -i $(CPP_FILES)

.PHONY: check-types
check-types:
	uv run --locked ty check .

.PHONY: check-deps
check-deps:
	uv run --locked deptry .

.PHONY: check-unused
check-unused:
	uv run --locked vulture

.PHONY: check-security
check-security:
	git ls-files --cached --others --exclude-standard -z -- '*.py' | xargs -0 uv run --locked bandit -c pyproject.toml

.PHONY: check-vulns
check-vulns:
	uv run --locked pysentry-rs .

.PHONY: check-hooks
check-hooks:
	uv run --locked prek validate-config prek.toml

.PHONY: check-config
check-config:
	uv run --locked pio project config --lint

.PHONY: check-workflows
check-workflows:
	$(ACTIONLINT)

.PHONY: check-renovate
check-renovate:
	bunx --package renovate@$(RENOVATE_VERSION) renovate-config-validator --strict --no-global renovate.json

.PHONY: check-cpp
check-cpp:
	$(CPPCHECK) --enable=warning,style,performance,portability --error-exitcode=1 \
		--std=c++17 --platform=unspecified --check-level=exhaustive firmware/src $(CPP_TESTS)
	@if [ "$$(uname -s)" = Darwin ]; then \
		sdk="$$(xcrun --show-sdk-path)"; \
		uv run --locked clang-tidy $(CPP_TESTS) -- $(CPP_FLAGS) -isystem $(JSON_INCLUDE) \
			-isysroot "$$sdk" -isystem "$$sdk/usr/include/c++/v1"; \
	else \
		uv run --locked clang-tidy $(CPP_TESTS) -- $(CPP_FLAGS) -isystem $(JSON_INCLUDE); \
	fi

.PHONY: test-python
test-python:
	uv run --locked coverage erase
	uv run --locked coverage run -m unittest discover -s tests -v
	uv run --locked coverage combine
	uv run --locked coverage report

.PHONY: test-cpp
test-cpp:
	@mkdir -p artifacts
	@for source in $(CPP_TESTS); do \
		binary="artifacts/$$(basename "$$source" .cpp)-test"; \
		$(CXX) $(CPP_FLAGS) -UNDEBUG -g -O1 -fno-omit-frame-pointer \
			-fsanitize=address,undefined -fno-sanitize-recover=all \
			-isystem $(JSON_INCLUDE) "$$source" -o "$$binary"; \
		"$$binary"; \
	done

.PHONY: test
test: test-python test-cpp

.PHONY: build
build:
	uv run --locked pio run

.PHONY: check
check: lint check-hooks check-types check-deps check-vulns check-unused check-security check-config check-cpp check-renovate test build check-workflows

.PHONY: check-fix
check-fix: lint-fix
	$(MAKE) check
