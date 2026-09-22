.PHONY: dev test compose-up compose-full compose-down lab-catalogue-test lab-hcp-ingest-test lab-s3-interop-test lab-package-placement-test lab-native-version-test lab-response-policy-test lab-migration-hydration-test lab-beta5-readiness lab-regression lab-regression-full lab-acceptance

dev:
	cd services/control-plane && AMP_DEMO_MODE=true uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

test:
	cd services/control-plane && PYTHONPATH=. python -m pytest -q

compose-up:
	docker compose up --build

compose-full:
	docker compose --profile extended up --build

compose-down:
	docker compose --profile extended down

lab-catalogue-test:
	./lab/scripts/lab-catalogue-lifecycle-test.sh

lab-hcp-ingest-test:
	./lab/scripts/lab-hcp-rest-ingest-test.sh

lab-s3-interop-test:
	./lab/scripts/lab-s3-interop-test.sh

lab-package-placement-test:
	./lab/scripts/lab-package-placement-test.sh

lab-native-version-test:
	./lab/scripts/lab-native-version-test.sh

lab-response-policy-test:
	./lab/scripts/lab-response-policy-test.sh

lab-migration-hydration-test:
	./lab/scripts/lab-migration-hydration-test.sh

lab-beta5-readiness:
	./lab/scripts/lab-beta5-readiness.sh

lab-regression:
	./scripts/lab-cumulative-regression.sh

lab-regression-full:
	AMP_REGRESSION_RESET=1 AMP_REGRESSION_PROFILE=full ./scripts/lab-cumulative-regression.sh

lab-acceptance:
	@echo "Run lab/docs/ACCEPTANCE_TEST_PLAN.md one step at a time; this target intentionally does not hide the first failing stage."
