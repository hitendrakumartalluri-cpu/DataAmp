# AMP 0.11.0-beta.2 — HCP content-intelligence demo

## Product focus

The immediate beta focuses on HCP alone: collect objects and native custom metadata, extract full text, transform metadata and load a searchable index. The purpose is an honest, repeatable demonstration of AMP as a potential HCI alternative. Existing multi-platform/archive work remains in the repository but is not a dependency for this demonstration. No NiFi migration or Hop dependency is introduced.

This build adds a native HCP REST read connector. Previously `HCP` selected the S3 adapter; now native REST is `HCP`/`HCP_REST`, and `HCP_S3` explicitly selects S3. Existing HCP-labelled S3 configurations must be changed to HCP_S3 before reuse. Do not silently assume that old connector definitions are compatible.

## No-Git installation in Ubuntu WSL

Use Ubuntu with Docker Desktop WSL integration, not the `docker-desktop` distribution. Download the beta branch ZIP into a NEW directory, preserving existing databases and volumes:

```bash
mkdir -p ~/projects/amp-hcp-beta2
cd ~/projects/amp-hcp-beta2
curl -fL https://github.com/hitendrakumartalluri-cpu/DataAmp/archive/refs/heads/beta/0.11-archive-platform.zip -o amp.zip
unzip amp.zip
cd DataAmp-beta-0.11-archive-platform
sed 's/127.0.0.1:8080:8080/127.0.0.1:8085:8080/' docker-compose.yml > docker-compose.hcp-lab.yml
docker compose -p amphcp2 -f docker-compose.hcp-lab.yml --profile hcp-demo up -d --build
```

Open http://localhost:8085. The health endpoint must report `0.11.0-beta.2`. Port 8085 and Solr port 8983 must be free; stop the prior beta containers if they occupy those ports, without removing volumes. The new Compose project name isolates volumes from the old beta. The HCP simulator is internal to the Compose network. SolrCloud is an optional real indexing engine under profile `hcp-demo`; the default reference index can demonstrate the local flow without it.

For a Python-only local demo, `bash scripts/run-hcp-demo.sh` starts the simulator on 18080 and AMP on 8085. It uses separate SQLite/data under `data-hcp-demo/`. Physical Solr requires a separate SolrCloud endpoint.

## Presenter sequence — approximately 15 minutes

1. **HCP demo studio → Create demo source.** The source is explicitly SIMULATOR, not a Hitachi VM. The dataset is generated locally: invoices, contracts, PDFs, text, DOCX, duplicates, encrypted PDF, synthetic email addresses and invalid metadata.
2. **HCP workflows → Preview stages.** Preview `invoices/record-1.pdf`. Show the HCP version and native annotation paths such as `business.record.date`; then show canonical business date in UTC and amount as an integer. Preview extracted text alongside metadata.
3. **Configure physical Solr indexes.** Use `http://solr:8983/solr`, `hcp_demo_meta`, `hcp_demo_text`, one shard. This provisions actual collections. If you skip it, the UI explicitly says Local reference index.
4. **Run workflow → Refresh runs.** Expected result: COMPLETE_WITH_ERRORS, 12 processed, 11 indexed, one invalid-date transformation error. Explain that an encrypted document retains metadata but cannot supply decrypted full text. A collection/index outage is a failed run, never a hidden switch to the local index.
5. **Search this workflow.** Search `payment dispute` with jurisdiction UK. Clear content words to demonstrate metadata-only physical routing. A result identifies the engine and selected physical collections. Tenant/access maps narrow IDs BEFORE Solr lookup.
6. **Demo studio cards.** Demonstrate duplicate hashes, encrypted detection, typed transformation, PII scan, classification, default-deny/grant/deny behavior, schema reprojection, English template query plan, cost estimates and dashboard suggestions.
7. **Exports and evidence.** The export card creates an owner-bound job and downloadable ZIP. Native HCP source documents are read using the indexed version and checked against their indexed SHA-256. Evidence packs contain indexed metadata, authorized audit and archive receipts where applicable; they do not claim signed compliance evidence for HCP retention.
8. **Index ledger and reconciliation.** Show the invalid-date record missing from the index and its failed attempt. Repeat a run and show that indexed record counts do not duplicate.
9. **Preview cards.** Show capacity planning/provisioning, optimizer, retention/hold candidate selection and bulk-action manifests. State clearly that autonomous rollover and native HCP mutations remain unqualified. They are product-design previews, not operational demonstrations.

The stage rail is a fixed processing sequence, not an arbitrary drag/drop workflow editor. Run metrics show aggregate outcomes, not independently timed processors. Full-workflow runs are queued; feature demonstration jobs execute bounded work synchronously and retain their durable job records.

## Every existing issue has a visible demo entry

| Issues | Demonstration in this build | Limit |
|---|---|---|
| #1, #3, #9 | Encrypted PDF, SHA duplicates, XML-to-canonical/date/integer transformation | Extraction and mappings require qualification against customer formats |
| #2 | New schema version and metadata reprojection without reading HCP/text | Local metadata projection; external Solr schema migration/rollback still pending |
| #4, #20 | Cost projection, metadata aggregates and dashboard recommendations | User-supplied pricing and deterministic recommendations; no billing/model integration |
| #5, #18 | Validated field requests; actual grant/deny filtering before search/export | Server-defined identities; source ACL synchronization/SSO not implemented |
| #6, #13 | Rule classification and scoped regex PII with redacted findings | No ML or native HCP tag writeback; Solr mirrors need rerun after local marking |
| #10, #16, #21 | Evidence/metadata/doc ZIP jobs, expiry, owner authorization and audit | Local audit store and bounded exports; not signed WORM evidence |
| #12, #24 | Index attempts, missing/stale local index findings, explicit failed record | Native MQE reconciliation, source deletes and distributed repair are pending |
| #15 | Two actual SolrCloud collections; metadata candidates before text query | Demo limited to 1000 authorized IDs; not proof of large-scale joins/federation |
| #17 | Capacity policy plan and manual physical collection provisioning | No autonomous telemetry-driven rollover/scaling controller |
| #19 | Known-field English template → controlled plan requiring confirmation | Deterministic grammar, not a general AI assistant |
| #25 | HCP-native read connector, annotation mapping, workflow, index and search | Real HCP VM acceptance and load tests are required |
| #7, #22, #26 | Optimizer/retention/bulk candidate plans and manifests | No native mutation is performed by these demos |
| #27 | Existing separate archive intake profile/status demonstration | HCP-native archive writes and broad source expansion are deferred |
| #8, #11, #14, #23 | Visible RETIRED entries | Gateway work is not revived |

A feature card is a coverage entry, not a claim that the full issue acceptance criteria are complete. In particular, heterogeneous multi-collection alias resolution, autonomous scaling, source ACL inheritance, model-based AI, native policy mutation and billion-document performance cannot be represented as finished HCI parity.

## HCP VM — required for a credible native-source demo

Prepare a disposable HCP VM with a dedicated tenant/namespace and an account with browse, read and annotation-read access. For loading synthetic examples, a separate test account additionally needs write/custom-metadata-write. Provide the HCP product/version and namespace DNS name, and ensure AMP's container can resolve it. Use the actual namespace hostname, not a tenant URL or a bare IP. Trust the namespace certificate through a CA bundle; do not disable TLS verification.

Set the full HCP authorization header/token locally in `.env` as `AMP_HCP_AUTH_TOKEN`. Do not paste passwords/tokens into chat. The documented HCP token is a Base64 username plus MD5 password digest; generate it using HCP's account tools. No credential generator or credential is committed to this beta.

For a private CA, use a small Compose override mounting `./certificates:/certificates:ro` into the `amp` service, and set the connection CA path to `/certificates/hcp-ca.pem`. The seed utility uses the host-side CA file instead. Docker hostname resolution can be configured with `extra_hosts` while keeping the namespace hostname in the HTTPS URL.

Generate fixture keys without writing:

```bash
python scripts/seed-hcp-demo.py --endpoint https://namespace.tenant.hcp.example
```

Upload only to the dedicated test namespace, using locally installed runtime dependencies:

```bash
python scripts/seed-hcp-demo.py --endpoint https://namespace.tenant.hcp.example --ca-bundle ./certificates/hcp-ca.pem --upload
```

The utility refuses to replace an existing object and does not modify native holds/retention. Simulator hold/retention values therefore will not automatically be reproduced on the VM. Configure approved sample native protection in HCP independently if it is part of your presentation.

In AMP: Connections → New connection → HCP namespace endpoint/auth variable/CA path → Add HCP_NAMESPACE scope → HCP workflows → New workflow. Use the fixture business mapping, or adapt paths to your own annotation XML. If directory browse is not available, configure explicit object keys through the connection's optional inventory field. Native directory listings are unpaged and the connector bounds them; large estates need the deferred MQE/checkpoint collector. Payload reads are bounded to 64 MiB by default; this demo is not large-document streaming qualification.

## Acceptance checks and evidence

Local automated checks cover the HCP wire contract, pinned annotations, XML safety, extraction/mapping failure isolation, all working feature-card actions, repeatable counts, source exports and tenant isolation. UI checks exercise demo setup → queued workflow → 11 indexed records → feature results using jsdom and actual local HTTP services. This is DOM/API QA, not a rendered-browser accessibility or layout assessment.

CI adds PostgreSQL execution of the HCP contracts and a real SolrCloud collection/query job. Consult PR #28's latest CI result for external engine evidence. Native HCP acceptance remains blocked until the VM is available: verify directory response shape, annotation reads for versions, DNS/TLS/auth, repeat runs, changed metadata/payload behavior, unauthorized access and native retrieval integrity.

Current limitations: one worker, no heartbeat/distributed workflow scheduler, no resume from RUNNING after crash, no atomic transaction across Solr pairs and SQL, no provider-certified native mutations, local search/aggregation scale limits. A Solr-pair partial failure is visible in workflow errors and needs a rerun; local receipt/metadata tables do not substitute for external index completeness.

## References

Hitachi documentation used to define the read contract:
- [HCP REST HEAD](https://docs.hitachivantara.com/r/en-us/mk-95hcph002-19/using-a-namespace/rest-api-reference/http-methods-supported-by-the-rest-api/head)
- [HCP REST response headers](https://docs.hitachivantara.com/r/en-us/mk-95hcph002/latest/using-a-namespace/rest-api-reference/rest-api-non-http-response-headers)
- [Directory listing](https://docs.hitachivantara.com/r/en-us/mk-95hcph002-19/using-a-namespace/rest-api/working-with-directories/listing-directory-contents)
- [Authorization token](https://docs.hitachivantara.com/r/en-us/mk-95hcph001-20/administering-hcp/account-administration/working-with-user-accounts/generating-a-user-authorization-token)
- [Solr collection management](https://solr.apache.org/guide/solr/10_1/deployment-guide/collection-management.html)
