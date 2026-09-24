.PHONY: dev test compose-up compose-full compose-down lab-catalogue-test lab-reconciliation-test lab-regression lab-regression-full

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

lab-reconciliation-test:
	./lab/scripts/lab-reconciliation-test.sh

lab-regression:
	./scripts/lab-cumulative-regression.sh

lab-regression-full:
	AMP_REGRESSION_RESET=1 AMP_REGRESSION_PROFILE=full ./scripts/lab-cumulative-regression.sh
