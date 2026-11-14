VENV := .venv
PYTHON := $(VENV)/bin/python
# TradingAgents needs Python 3.11 or later; use the newest one installed.
BASE_PYTHON ?= $(shell for p in python3.13 python3.12 python3.11 python3; do command -v $$p >/dev/null 2>&1 && { echo $$p; break; }; done)

.PHONY: venv install install-claude test lint web-install web-dev web-backend web-test web-build fetch sync push status backtest backtest-grid login-claude

venv:
	@if [ -x $(PYTHON) ] && ! $(PYTHON) -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then \
		echo "$(VENV) runs Python $$($(PYTHON) -c 'import platform; print(platform.python_version())'), but TradingAgents needs 3.11 or later."; \
		echo "Recreate it: rm -rf $(VENV) && make install web-install"; \
		exit 1; \
	fi
	@# A venv writes its own path into activate and every installed command. Made
	@# before the repo folder was renamed, `source .venv/bin/activate` puts a folder
	@# that is gone on PATH ("command not found: tradingagents"): point them here.
	@if [ -f $(VENV)/bin/activate ] && ! grep -q "VIRTUAL_ENV=$(CURDIR)/$(VENV)$$" $(VENV)/bin/activate; then \
		old=$$(sed -n 's/^ *export VIRTUAL_ENV=//p' $(VENV)/bin/activate | head -1); \
		echo "$(VENV) was made at $$old; pointing it at $(CURDIR)/$(VENV)"; \
		grep -lI "$$old" $(VENV)/bin/* | xargs sed -i.bak "s#$$old#$(CURDIR)/$(VENV)#g"; \
		rm -f $(VENV)/bin/*.bak; \
	fi
	@[ -x $(PYTHON) ] || $(BASE_PYTHON) -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip

install: venv
	$(PYTHON) -m pip install -e ".[dev]"

# Claude Code, bundled by the Agent SDK, for the Claude plan provider on a
# machine that has no Claude Code installed. Resolved together with the console's
# pins: on its own, the SDK's mcp dependency upgrades Starlette past FastAPI's range.
install-claude: venv
	$(PYTHON) -m pip install -e ".[dev,claude]" -r backend/requirements-dev.txt

# Sign Claude Code in to your Claude Pro or Max plan (Claude Code's own login).
login-claude:
	$(PYTHON) -m cli.main login claude

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

# Score past decisions on dates you choose (scripts/backtest.py, upstream's
# backtest engine): each date is a full multi-agent run, entered at the first
# close after it and scored on alpha against the regional benchmark. The sweep
# keeps a memory log of its own, so your live memory log is never written to.
#
# Usage:
#   make backtest SYMBOL=NVDA DATES="2025-01-15 2025-02-14 2025-03-17"
#
# Defaults to NVDA over three sample dates if SYMBOL/DATES are not set.
SYMBOL ?= NVDA
DATES ?= 2025-01-15 2025-02-14 2025-03-17

backtest:
	$(PYTHON) scripts/backtest.py $(SYMBOL) $(DATES)

# The same engine over a regular grid of tickers and dates (tradingagents backtest):
#   make backtest-grid SYMBOLS=NVDA,AAPL START=2026-06-01 END=2026-08-01 EVERY=7
SYMBOLS ?= NVDA
START ?= 2026-06-01
END ?= 2026-08-01
EVERY ?= 7

backtest-grid:
	$(PYTHON) -m cli.main backtest $(SYMBOLS) --start $(START) --end $(END) --every $(EVERY)
