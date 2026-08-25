VENV := .venv
PYTHON := $(VENV)/bin/python

.PHONY: venv install test lint web-install web-dev web-backend web-test web-build fetch sync push status backtest

venv:
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip

install: venv
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

web-install:
	$(PYTHON) -m pip install -r backend/requirements-dev.txt
	cd frontend && npm install

web-dev:
	$(PYTHON) -m backend.app.dev_all

web-backend:
	$(PYTHON) -m backend.app.dev

web-test:
	$(PYTHON) -m pytest backend/tests

web-build:
	cd frontend && npm run build

# Fetch latest changes from your fork (origin) and the original repo (upstream)
fetch:
	git fetch origin
	git fetch upstream

# Pull upstream's main into your local main, then push the result to your fork
sync: fetch
	git merge upstream/main
	git push origin main

# Push your local commits to your fork
push:
	git push origin main

status:
	git status
	git remote -v

# Run the historical-decision smoke test (scripts/backtest.py): runs the full
# agent pipeline on each given date and scores the decision against the
# actual subsequent price move. Each date is a full multi-agent LLM run, so
# keep DATES short against a free-tier provider quota.
#
# Usage:
#   make backtest SYMBOL=NVDA DATES="2025-01-15 2025-02-14 2025-03-17"
#
# Defaults to NVDA over three sample dates if SYMBOL/DATES are not set.
SYMBOL ?= NVDA
DATES ?= 2025-01-15 2025-02-14 2025-03-17

backtest:
	$(PYTHON) scripts/backtest.py $(SYMBOL) $(DATES)
