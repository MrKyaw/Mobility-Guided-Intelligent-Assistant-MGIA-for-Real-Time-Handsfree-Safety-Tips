PYTHON ?= python3

.PHONY: help install test coverage demo check clean

help:
	@echo "MGIA real-time safety core"
	@echo "  make install   Install editable package with dev tools"
	@echo "  make test      Run the fast test suite"
	@echo "  make coverage  Run tests with the 100% line+branch gate"
	@echo "  make demo      Run the deterministic CLI demo"
	@echo "  make check     Coverage gate plus CLI smoke test"
	@echo "  make clean     Remove test/coverage artifacts"

install:
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

coverage:
	$(PYTHON) -m pytest --cov=mgia_realtime --cov-branch --cov-report=term-missing

demo:
	$(PYTHON) -m mgia_realtime --demo

check: coverage demo

clean:
	rm -rf .pytest_cache .coverage coverage.xml htmlcov \
		mgia_realtime/__pycache__ tests/__pycache__
