# Local gates and stack control for the evaluation service.
#
# The three CI gates are NOT here. They live in .github/workflows/ci.yml and are
# run with the same plain commands on both sides -- putting them behind a make
# target would create a second spelling of them, and then a way for the two to
# disagree. `make gates` below runs the CI commands verbatim, as a convenience,
# not as a definition.
#
# Design §6 splits the suite by what it needs:
#   offline, CI-safe      tests/test_api_contract.py  (schema units + OpenAPI snapshot)
#   needs Postgres        tests/service/              (repository, SKIP LOCKED, 422)
#   needs the whole stack make smoke
# Everything in the second and third group SKIPS cleanly without its dependency,
# so a bare `pytest tests/ -q` on a fresh checkout is green rather than red.

PY           ?= .venv/bin/python
SERVICE_PY   ?= .venv-service/bin/python
COMPOSE      := docker compose -f docker-compose.service.yml
COMPOSE_TEST := $(COMPOSE) -f docker-compose.service.test.yml
PGPASS        = $(shell cat secrets/pg_password 2>/dev/null)
TEST_DB_URL  ?= postgresql://rca:$(PGPASS)@127.0.0.1:5433/rca

.PHONY: help service-up service-down service-logs smoke smoke-agent \
        test-contract test-db test gates readme snapshot

help:
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | expand -t22

service-up:            ## start db + api + worker (loopback only)
	$(COMPOSE) up -d --build
service-down:          ## stop and remove the stack (the named volume survives)
	$(COMPOSE) down
service-logs:          ## follow api + worker logs
	$(COMPOSE) logs -f api worker

smoke:                 ## end-to-end on the rules arm -- $0.00, run it as often as you like
	$(PY) service/smoke.py --arm rules
smoke-agent:           ## same chain on the agent arm -- about $0.07, MANUAL ONLY
	$(PY) service/smoke.py --arm agent

test-contract:         ## offline contract tests (this is what becomes CI gate 4)
	$(SERVICE_PY) -m pytest tests/test_api_contract.py -q
snapshot:              ## regenerate tests/fixtures/openapi.json -- COMMIT THE RESULT
	$(SERVICE_PY) -m pytest tests/test_api_contract.py --update-snapshot -q

test-db:               ## repository + 422 tests against a real Postgres (publishes 127.0.0.1:5433)
	$(COMPOSE_TEST) up -d db
	@RCA_TEST_DB_URL="$(TEST_DB_URL)" $(SERVICE_PY) -m pytest tests/service -q; \
	 rc=$$?; $(COMPOSE) up -d db >/dev/null 2>&1; exit $$rc

test:                  ## every test, with the database available
	$(COMPOSE_TEST) up -d db
	@RCA_TEST_DB_URL="$(TEST_DB_URL)" $(SERVICE_PY) -m pytest tests -q; \
	 rc=$$?; $(COMPOSE) up -d db >/dev/null 2>&1; exit $$rc

gates:                 ## the three CI gates plus readme_check, verbatim
	$(PY) -m pytest tests/ -q
	$(PY) scripts/scenarios/generate.py --check
	$(PY) -m pytest tests/test_clean_window_negative_control.py -q
	$(PY) scripts/tools/readme_check.py --check
