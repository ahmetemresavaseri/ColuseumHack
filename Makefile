PYTHON ?= python
PIP ?= pip
CDK ?= cdk
NPM ?= npm

.PHONY: install smoke deploy seed seed-ddb seed-kb brain-local simulate rag-smoke web-install web-dev test

install:
	$(PIP) install -r requirements.txt

smoke:
	$(PYTHON) smoke_test.py

deploy:
	$(CDK) deploy --app "python infrastructure/cdk_app.py" --all

seed: seed-ddb seed-kb

seed-ddb:
	$(PYTHON) scripts/seed_ddb.py

seed-kb:
	$(PYTHON) scripts/seed_kb.py

brain-local:
	$(PYTHON) scripts/run_brain_local.py

simulate:
	$(PYTHON) scripts/simulate_call_events.py

rag-smoke:
	$(PYTHON) scripts/rag_smoke_test.py

web-install:
	cd web && $(NPM) install

web-dev:
	cd web && $(NPM) run dev

test:
	$(PYTHON) -m pytest
