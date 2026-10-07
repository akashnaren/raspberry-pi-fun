WEB := pi-pair/web
.PHONY: check lint fmt test test-py test-web build build-check
check: lint test build-check
lint:
	ruff check pi-pair
	ruff format --check pi-pair
	cd $(WEB) && npm run -s lint && npm run -s lint:js
	shellcheck -x pi-pair/install.sh pi-pair/start.sh pi-pair/mesh-hello.sh pi-pair/ci/deploy_pi3.sh
fmt:
	ruff check --select I,RUF100 --fix pi-pair
	ruff format pi-pair
test: test-py test-web
test-py:
	python3 -m unittest discover -s pi-pair/tests -t pi-pair
test-web:
	cd $(WEB) && npm test
build:
	cd $(WEB) && npm run -s build
build-check: build
	git diff --exit-code -- pi-pair/static
