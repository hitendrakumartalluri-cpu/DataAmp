# Platform Operations Module

## Responsibility

Packages, configures, observes and operates AMP services and their dependencies.

## Current Beta 5

- Docker Compose full lab.
- kind/Kubernetes starter.
- Helm deployment starter.
- PostgreSQL, MinIO, Kafka, Solr, Tika and Hop lab integration.
- Health/readiness endpoints plus event, audit and job history.
- Scheduler workers and reconciliation schedules.
- Cumulative regression model with quick/full/soak profiles and retained test evidence.
- Lab execution logs remain repository-only; the Wiki documents the validation model and what each cycle proves.

## Complete-product scope

- Production Helm profiles and secure configuration.
- OIDC/RBAC and secrets management.
- Prometheus/OpenTelemetry metrics and traces.
- SLOs, dashboards and alerts.
- HA/failover, backup/restore and disaster recovery.
- Upgrade/rollback compatibility.
- Capacity, WAN, load and soak testing.
- Security assessment and operational runbooks.

## Primary locations

- `deployments/helm/`
- `services/control-plane/app/config.py`
- `services/control-plane/app/scheduler_worker.py`
- `services/control-plane/app/event_worker.py`

## Validation model

AMP uses cumulative regression as a release gate. Every increment reruns the previously proven Gateway, Catalogue, package, versioning, response-policy, migration and event-capture behavior before a capability is promoted from **Implemented** to **Verified**.

See [Validation Model](../VALIDATION_MODEL.md) for the architectural verification model and [Testing Cycles](../testing/TEST_CYCLES.md) for the executable repository workflow.
