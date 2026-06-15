.PHONY: help install install-dev install-all test test-cov lint security hooks clean

VENV   = env
PYTHON = $(VENV)/bin/python
PIP    = $(VENV)/bin/pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------
# Setup
# ------------------------------------------------------------------

$(VENV)/bin/python:  ## Create virtual environment if missing
	python3 -m env $(VENV)
	$(PIP) install --upgrade pip setuptools wheel

install: $(VENV)/bin/python  ## Install package in editable mode
	$(PIP) install -e .

install-dev: install  ## Install with dev dependencies
	$(PIP) install -e ".[dev]"

install-all: install-dev  ## Install with all optional provider deps
	$(PIP) install -e ".[dev,gemini,anthropic]"

# ------------------------------------------------------------------
# Testing
# ------------------------------------------------------------------

test: install-dev  ## Run all tests
	$(PYTHON) -m pytest orka/tests/ -v

test-cov: install-dev  ## Run tests with coverage report
	$(PYTHON) -m pytest orka/tests/ --cov=orka --cov-report=term-missing

# ------------------------------------------------------------------
# Quality
# ------------------------------------------------------------------

lint: install-dev  ## Check for syntax and import issues
	$(PYTHON) -m py_compile orka/config.py
	$(PYTHON) -m py_compile orka/clients.py
	$(PYTHON) -m py_compile orka/cli.py
	$(PYTHON) -m py_compile orka/orchestrator.py
	$(PYTHON) -c "from orka.config import settings; print('config OK')"
	$(PYTHON) -c "from orka.clients import OrkaClientFactory; print('clients OK')"

# ------------------------------------------------------------------
# Security
# ------------------------------------------------------------------

security: install-dev  ## Run full security audit (Bandit + pip-audit + Safety)
	@echo "=== Bandit (static analysis) ==="
	$(PYTHON) -m bandit -r orka/ -q
	@echo ""
	@echo "=== pip-audit (dependency vulnerabilities) ==="
	$(PYTHON) -m pip_audit --desc
	@echo ""
	@echo "=== Safety CLI (dependency monitoring) ==="
	$(PYTHON) -m safety scan
	@echo ""
	@echo "=== TruffleHog (secret leak scan - requires Docker) ==="
	@docker run --rm -v "$$(pwd):/repo" trufflesecurity/trufflehog:latest git file:///repo --no-update 2>&1 || echo "(skipped - Docker not available)"

hooks:  ## Install git pre-push hook (gitleaks secret scanner)
	@if [ ! -x env/bin/gitleaks ]; then \
		echo "Installing gitleaks into venv..."; \
		curl -sL https://github.com/gitleaks/gitleaks/releases/download/v8.25.1/gitleaks_8.25.1_linux_x64.tar.gz | tar -xzf - -C env/bin/ gitleaks; \
		chmod +x env/bin/gitleaks; \
	fi
	git config core.hooksPath .githooks
	@echo "✅ Git hooks installed — pre-push will scan for secrets before pushing."

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete
	rm -rf *.egg-info dist build