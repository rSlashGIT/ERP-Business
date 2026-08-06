.PHONY: demo demo-erp demo-smartstock demo-fit demo-serve test bench up down logs fmt

demo-smartstock: ## SmartStock on real M5 data: seed + fit + run + serve
	python3 demo/run_demo.py

demo-fit:        ## refit the CMA-ES policy and print the benchmark table
	python3 demo/run_demo.py --fit --generations 60

demo-serve:      ## serve an already-seeded demo db
	python3 demo/run_demo.py --serve

demo-reset:      ## rebuild the demo database from the M5 dataset
	rm -f demo/demo.db demo/demo.db-journal demo/policy.json
	python3 demo/run_demo.py --reseed

verify: test audit audit-pg audit-upgrade validate-m5 validate-clean validate-dirty validate-pricing validate-bigmart demo-isolation demo-check verify-inventory verify-ui mutate  ## FULL verification

mutate:          ## break the code on purpose, prove the tests notice
	python3 scripts/mutate_check.py

demo-isolation:  ## two-tenant apparel isolation demo (the "what works now" demo)
	python3 demo/tenant_isolation_demo.py

audit-uniqueness: ## global-uniqueness defect audit
	python3 scripts/audit_uniqueness.py

audit-routes:    ## route-level scope_query coverage audit
	python3 scripts/audit_route_scoping.py

audit: audit-uniqueness audit-routes  ## every defect-class auditor

demo demo-erp:   ## ← THE DEMO: full apparel ERP on http://127.0.0.1:8500
	python3 demo/erp_server.py

demo-reseed:     ## rebuild the ERP demo data and serve
	python3 demo/erp_server.py --reseed

demo-check:      ## boot the ERP demo, exercise every API, exit non-zero on failure
	python3 demo/verify_erp_demo.py

verify-ui:       ## render every screen and modal, fail on undefined/NaN/raw markup
	@python3 demo/erp_server.py --port 8599 >/tmp/erp-ui.log 2>&1 & \
	 for i in $$(seq 1 30); do curl -s -m 2 -o /dev/null http://127.0.0.1:8599/api/v1/tenants && break; sleep 1; done; \
	 node demo/verify_ui.js 8599; rc=$$?; pkill -f "erp_server.py --port 8599" >/dev/null 2>&1; exit $$rc

validate-pricing: ## backtest the price engine on real M5 retail data
	python3 scripts/validate_pricing.py

audit-pg:        ## static Postgres-dialect audit of the migration
	python3 scripts/audit_pg_dialect.py

audit-upgrade:   ## can an EXISTING database reach the current schema?
	python3 scripts/audit_upgrade_path.py

typecheck-web:   ## typecheck apps/web against shims (NOT the production build)
	@cd apps/web && PATH=/usr/local/lib/node_modules_global/bin:$$PATH \
	  tsc -p tsconfig.shim.json > /tmp/tw-all.txt 2>&1; \
	  grep -vE 'TS7006|TS7031' /tmp/tw-all.txt > /tmp/tw.txt || true; \
	  if [ -s /tmp/tw.txt ]; then \
	    echo "  REAL type defects:"; cat /tmp/tw.txt; exit 1; \
	  else \
	    echo "  no real type defects  ($$(wc -l < /tmp/tw-all.txt) shim artefacts: TS7006/TS7031 only)"; \
	    echo "  NOTE: this is NOT the production build. npm install is 403 here."; \
	  fi

export-prod:     ## zip the workspace + instructions for the network-blocked jobs
	bash scripts/export_for_production.sh

verify-inventory: ## stocktakes + transfers end to end, reading the DB back
	python3 demo/verify_inventory_ops.py

validate-bigmart: ## second dataset: BigMart India 2013, split by product
	python3 scripts/validate_pricing_bigmart.py

diagnose-elasticity: ## why the M5 fit is weak — estimator ladder
	python3 scripts/diagnose_elasticity.py

test: test-engine test-adapter test-nomocks test-gst test-import test-security test-tenancy migrate-verify  ## every suite

test-gst:        ## GST slab, place-of-supply and rounding
	cd services/erp-api && python3 tests/test_gst.py

test-import:     ## column matching, cleaners and style grouping on messy sheets
	cd services/erp-api && python3 tests/test_importing.py

test-nomocks:    ## prove the runtime forecast path uses real models
	cd services/smartstock && python3 tests/test_no_mocks.py

validate-m5:     ## E2E on M5 Walmart (Kaggle), 30 SKUs
	python3 scripts/validate_dataset.py --csv demo/data/m5_multi_sku.csv \
		--label "M5 Walmart (Kaggle) 30 SKUs"

validate-clean:  ## E2E on a differently-shaped single-SKU extract
	python3 scripts/validate_dataset.py --csv demo/data/m5_clean_singlesku.csv \
		--single-sku FOODS_3_090_CA1 --qty-col demand --date-col date --price-col price \
		--label "M5 clean single-SKU (different schema)"

validate-dirty:  ## E2E on a deliberately dirty apparel extract
	python3 scripts/validate_dataset.py --csv demo/data/dirty_apparel_sample.csv \
		--sku-col "Item Code" --date-col "Txn Date" --qty-col "Units Sold" \
		--price-col "Unit Price" --label "Dirty apparel extract"

test-engine:     ## simulator, policy, lead-time, segmentation assertions
	cd services/smartstock && python3 tests/test_engine.py

test-adapter:    ## LegacyModelAdapter contract + failure paths
	cd services/smartstock && python3 tests/test_adapter.py

test-security:   ## JWT, roles, approval threshold, tenant isolation
	cd services/erp-api && JWT_SECRET=$${JWT_SECRET:-dev-secret-key-at-least-32-characters!!} \
		python3 tests/test_security.py

test-tenancy:    ## cross-tenant isolation: inventory + dashboard routes
	cd services/erp-api && JWT_SECRET=$${JWT_SECRET:-dev-secret-key-at-least-32-characters!!} \
		python3 tests/test_tenancy.py

bench-forecast:  ## walk-forward forecast backtest vs the v2.0 baseline
	python3 scripts/forecast_bench.py

up:              ## full production stack
	docker compose -f infra/docker-compose.yml up --build -d

down:
	docker compose -f infra/docker-compose.yml down -v

logs:
	docker compose -f infra/docker-compose.yml logs -f smartstock erp-api worker

migrate:         ## apply migrations (real alembic if installed, else verify)
	@if python3 -c "import alembic" 2>/dev/null; then \
		cd services/erp-api && alembic upgrade head; \
	else \
		echo "alembic not installed -- running the offline verifier instead."; \
		echo "Install with: pip install alembic psycopg2-binary sqlalchemy"; \
		python3 scripts/verify_migration.py; \
	fi

migrate-verify:  ## execute the migration chain against SQLite + coverage cross-check
	python3 scripts/verify_migration.py

migrate-gen:     ## regenerate 0001_initial from db/models.py
	python3 scripts/gen_migration.py && python3 scripts/verify_migration.py

fmt:
	ruff check --fix services/ && ruff format services/
