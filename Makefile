.PHONY: test smoke tools build install uninstall

test:
	./scripts/run-tests.sh

smoke:
	./scripts/smoke-help.sh

tools:
	./scripts/check-tools.sh

build:
	./scripts/build-standalone.sh

install:
	./install.sh

uninstall:
	./uninstall.sh
