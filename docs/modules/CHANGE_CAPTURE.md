# Change Capture Module

## Responsibility

Keeps catalogues current when objects change through AMP or directly in storage.

## Current Beta 5

- Kafka normalized event backbone.
- Storage-specific MinIO raw topics.
- Raw-to-normalized adapters.
- Targeted HEAD verification before catalogue mutation.
- Receipt ledger, idempotency and stale-event suppression.
- Dead-letter and replay foundations.
- Catalogue capture state, checkpoints and lag.
- AWS SQS/EventBridge and HCP MQE adapter scaffolds.

## Flow

```text
Backend event -> raw topic -> adapter -> normalized topic
              -> catalogue consumer -> verified targeted mutation
```

## Boundaries

Events provide freshness but not completeness. Periodic baseline or inventory reconciliation remains mandatory.

## Complete-product scope

Production AWS adapters, real HCP MQE windows/checkpoints, inventory baselines, ordering guarantees, failure injection, operational SLOs and large-backlog recovery.

## Primary code

- `services/control-plane/app/services/events.py`
- `services/control-plane/app/event_worker.py`
- `services/control-plane/app/services/scheduler.py`
