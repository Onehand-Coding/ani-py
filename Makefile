.PHONY: test smoke tools build

test:
	./scripts/run-tests.sh

smoke:
	./scripts/smoke-help.sh

tools:
	./scripts/check-tools.sh

build:
	./scripts/build-standalone.sh
