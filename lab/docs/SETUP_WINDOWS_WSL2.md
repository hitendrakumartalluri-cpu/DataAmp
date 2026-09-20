# AMP Enterprise Beta — Clean Lab Build on Windows + WSL2

This guide assumes a **clean Windows 11 machine** and creates an AMP test lab without relying on any previous AMP, MinIO, PostgreSQL, Solr or HOP installation.

The recommended progression is:

1. Windows + WSL2 foundation
2. Docker full lab — functional validation
3. Kubernetes/kind full lab — deployment validation
4. Replace the two S3 simulators with real HCP/AWS endpoints

Do not start with real enterprise credentials. Prove the complete AMP lifecycle against the self-contained lab first.

---

## 1. Lab topology

```text
Browser
  |
  | http://localhost:8080
  v
AMP Enterprise Beta
  |
  +--> PostgreSQL catalogue
  +--> Tika extraction
  +--> Solr 10 target service
  +--> Apache Hop 2.19 server
  |
  +--> Primary S3 simulator       :9000 / console :9001
  |
  +--> Legacy HCP-S3 simulator   :9100 / console :9101
```

The two object stores are deliberately separate. This lets you test:

- discover data already present in legacy storage
- create SQL catalogue entries without moving objects
- run the common extraction/index lifecycle
- migrate legacy -> primary
- perform primary-first reads
- read through from secondary when the primary copy is absent
- hydrate the primary on read
- reconcile catalogue vs physical copies

For the lab only, AMP pins the last readily available 2025 MinIO community container as an **S3 protocol simulator**. It is not the recommended production object store. Production testing should use your HCP/HCP-S3 and AWS S3 systems.

---

## 2. Suggested machine resources

The application can run with less, but for the complete lab a practical target is:

- Windows 11 x64
- hardware virtualisation enabled
- 8 CPU threads available to WSL/Docker; 12+ is more comfortable
- 12 GB RAM available to the container environment; 16 GB is more comfortable
- 25 GB free disk for images, volumes and test data

The lightweight AMP + PostgreSQL path requires significantly less. Solr, Tika, Hop and two object stores are what make the full lab heavier.

---

## 3. Install WSL2

Open **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-24.04
wsl --update
wsl --set-default-version 2
```

Restart Windows if requested.

Then verify:

```powershell
wsl --version
wsl -l -v
```

Ubuntu should show version `2`.

Launch Ubuntu once and create your Linux username/password.

---

## 4. Install Docker Desktop with WSL integration

Use Docker Desktop's WSL2 backend.

In Docker Desktop:

1. **Settings -> General -> Use WSL 2 based engine**
2. **Settings -> Resources -> WSL Integration**
3. Enable your Ubuntu distribution
4. Apply and restart Docker Desktop

Do **not** also install a second Docker Engine inside the same WSL distribution when using Docker Desktop integration. Keeping one Docker daemon avoids context/socket conflicts.

Inside Ubuntu verify:

```bash
docker version
docker compose version
docker run --rm hello-world
```

---

## 5. Prepare Ubuntu

Inside WSL Ubuntu:

```bash
sudo apt update
sudo apt install -y git curl jq unzip make ca-certificates python3
```

Keep the source code inside the Linux filesystem rather than under `/mnt/c`:

```bash
mkdir -p ~/projects
cd ~/projects
```

Copy/extract the beta so the final directory is:

```text
~/projects/amp-enterprise-beta
```

Then:

```bash
cd ~/projects/amp-enterprise-beta
./scripts/lab-preflight.sh
```

---

# Part A — Full Docker lab

## 6. Create development credentials

```bash
cd ~/projects/amp-enterprise-beta
cp .env.example .env
nano .env
```

The defaults are sufficient on a private laptop. Change them before using a shared network or committing the file anywhere.

`.env` is not intended for production secrets.

---

## 7. Start the entire lab

```bash
./scripts/lab-up.sh
```

The script performs all of the following:

1. builds the AMP image
2. starts PostgreSQL
3. starts Kafka (single-node KRaft for the lab)
4. creates the normalized, DLQ and storage-specific raw MinIO topics
5. starts Primary S3 simulator
6. starts Legacy HCP-S3 simulator
7. starts Solr
8. starts Tika
9. starts Hop Server
10. starts AMP and the event/scheduler workers
11. creates the primary and legacy buckets
12. uploads sample objects into the legacy store before change capture is enabled
13. registers both object stores and independent Catalogue Groups
14. configures MinIO bucket notifications -> Kafka change capture
15. performs the one-time baseline discovery
16. creates PostgreSQL catalogue records
17. processes search/AI artifacts independently from the catalogue
18. creates an AI-ready dataset
19. runs reconciliation

No prior database or object-store setup is required.

---

## 8. Open the components

| Component | Address |
|---|---|
| AMP UI | `http://localhost:8080` |
| AMP OpenAPI | `http://localhost:8080/docs` |
| Primary S3 API | `http://localhost:9000` |
| Primary S3 console | `http://localhost:9001` |
| Legacy/HCP-S3 API | `http://localhost:9100` |
| Legacy/HCP-S3 console | `http://localhost:9101` |
| Solr | `http://localhost:8983` |
| Tika | `http://localhost:9998` |
| Hop Server | `http://localhost:8182` |
| Kafka host listener | `localhost:29092` |

Default lab object-store credentials are in `.env`.

---

## 9. Validate the deployment

```bash
./scripts/lab-smoke-test.sh
```

You should see:

- `/healthz` returns OK
- overview contains discovered objects
- `termination` returns the contract sample
- reconciliation completes

Check running containers:

```bash
docker compose --env-file .env -f docker-compose.yml ps
```

Check AMP logs:

```bash
docker compose --env-file .env -f docker-compose.yml logs -f amp
```

Check all logs:

```bash
docker compose --env-file .env -f docker-compose.yml logs -f
```

Then validate the continuous outside-AMP change path:

```bash
./scripts/lab-event-smoke-test.sh
```

That script writes and deletes an object **directly in the legacy MinIO bucket**, then waits for:

```text
MinIO bucket notification
  -> storage-specific raw Kafka topic
  -> AMP MinIO adapter
  -> amp.storage.changes
  -> AMP catalogue consumer
  -> targeted HEAD/validation
  -> catalogue ACTIVE / TOMBSTONED state
```

---

## 10. Test the existing-data path

The bootstrap puts objects directly into the legacy S3 store **before AMP sees them**.

That deliberately simulates data which did not pass through the Gateway.

The path under test is intentionally split into two independent paths:

```text
ADMIN / RECON PATH
existing payload
    |
    v
Catalogue discovery
    |
    +--> one Catalogue Group for this storage + bucket
    +--> generation history
    +--> hash-routed catalogue_objects
    +--> audit / tombstones / reconciliation

SEARCH / AI PATH
existing payload
    |
    v
HOP (lab simulator in beta)
    |
    +--> Tika / extraction
    +--> deterministic recon_id
    +--> search / AI artifacts
```

HOP does not consume catalogue-object rows. AMP reconciles the two paths afterwards using the shared deterministic recon ID.

---

## 11. Test read-through hydration

In the AMP UI open **Catalogue** and select an object in the Legacy catalogue. Hydration is a copy from the source Catalogue Group into the target Catalogue Group.

```text
GET source object
   |
   +--> read from source bucket
   +--> return bytes
   +--> write target bucket
   +--> after successful target write only:
            create target catalogue record
            assign target recon_id
            retain origin_recon_id = source recon_id
```

The source and target catalogue rows are intentionally separate so either catalogue can later be archived/decommissioned independently.

---

## 12. Test a migration

Use **Migrations** in the AMP UI:

- Source catalogue: `Legacy / legacy-hcp`
- Target catalogue: `Primary / amp-primary`
- Prefix: empty, or use `contracts/`

First use dry-run, then run the real migration.

After completion verify the object exists in both physical stores. AMP should show one source catalogue record and one independent target catalogue record with different recon IDs linked through migration lineage.

---

## 13. Stop / restart / reset Docker lab

Stop while preserving data:

```bash
./scripts/lab-down.sh
```

Restart:

```bash
./scripts/lab-up.sh
```

Completely destroy test data and rebuild from zero:

```bash
./scripts/lab-reset.sh
```

The reset command removes PostgreSQL, object-store and Solr Docker volumes.

---

# Part B — Kubernetes lab with kind

Use this only after the Docker lab is healthy.

## 14. Install Kubernetes tools inside WSL

Automated installer:

```bash
./scripts/install-k8s-tools-wsl.sh
```

Then verify:

```bash
kubectl version --client
kind version
helm version
```

The installer uses the current stable kubectl release, kind v0.33.0, and Helm 4.

---

## 15. Shut down Docker Compose lab before kind

Both labs intentionally use the same localhost ports, so do not run them simultaneously.

```bash
./scripts/lab-down.sh
```

---

## 16. Create and deploy the Kubernetes lab

```bash
./scripts/kind-lab-up.sh
```

The script:

1. creates `kind-amp-lab`
2. builds `amp-enterprise:0.9.0-beta.3.1`
3. loads the image directly into the kind node
4. deploys namespace `amp-lab`
5. deploys PostgreSQL, two S3 stores, Solr, Tika and Hop
6. deploys AMP
7. waits for rollouts
8. executes the same AMP bootstrap path

Verify:

```bash
kubectl get nodes
kubectl -n amp-lab get pods
kubectl -n amp-lab get svc
```

All core pods should be `Running` and AMP should be `Ready`.

The host addresses are the same as the Docker lab because kind maps NodePorts to the same localhost ports.

---

## 17. Kubernetes troubleshooting

AMP:

```bash
kubectl -n amp-lab logs deploy/amp -f
```

PostgreSQL:

```bash
kubectl -n amp-lab logs deploy/postgres
```

Tika:

```bash
kubectl -n amp-lab logs deploy/tika
```

Hop:

```bash
kubectl -n amp-lab logs deploy/hop
```

Describe a failed pod:

```bash
kubectl -n amp-lab describe pod <pod-name>
```

Check events:

```bash
kubectl -n amp-lab get events --sort-by=.lastTimestamp
```

Restart AMP:

```bash
kubectl -n amp-lab rollout restart deploy/amp
kubectl -n amp-lab rollout status deploy/amp
```

---

## 18. Destroy the Kubernetes lab

```bash
./scripts/kind-lab-down.sh
```

The lab uses node-local hostPath data inside the kind node. Deleting the kind cluster intentionally removes the entire Kubernetes lab dataset.

---

# Part C — Replace simulators with real systems

Do this after both discovery and migration work in the self-contained lab.

## 19. Real AWS S3 target

Create an AMP storage system with:

```text
Kind: AWS_S3
Role: PRIMARY
Bucket: <your test bucket>
Region: <AWS region>
```

For the current beta, access key/secret key can be supplied in the storage configuration. The production design should replace this with IAM roles/workload identity or an enterprise secrets manager.

Recommended sequence:

```text
Legacy simulator -> AWS
then
HCP test namespace -> AWS
```

Do not start with production HCP data.

---

## 20. Real HCP/HCP-S3 source

For HCP S3 compatibility:

```text
Kind: HCP_S3
Role: SECONDARY
Endpoint: https://<hcp-s3-endpoint>
Bucket: <test bucket/namespace mapping>
Credentials: dedicated AMP test credentials
```

Validate:

1. LIST/discovery
2. HEAD
3. GET
4. existing-data SQL registration
5. extraction/indexing
6. reconciliation
7. dry-run migration
8. migration of a small prefix
9. checksum/size validation
10. read-through hydration

Native HCP REST compatibility is a separate adapter/API validation track from HCP's S3 endpoint.

---

# Part D — Acceptance checks before increasing data volume

Do not move to large datasets until all of these pass:

- existing objects discovered without copying
- one logical Catalogue Group per storage system + namespace/bucket
- deterministic recon IDs and stable virtual-shard routing
- Tika extraction works for representative files
- indexing state reaches `INDEXED`
- AI state reaches `READY` where text is extractable
- retrieval returns the expected objects
- reconciliation produces zero unexpected findings
- migration dry-run counts match expectations
- migration creates an independent target catalogue record linked to the source recon ID
- primary-first reads work
- missing-primary fallback works
- hydration records the target only after successful write
- restart of AMP does not lose catalogue state
- restart of PostgreSQL preserves catalogue state
- source object removal is detected by reconciliation

Once these are green, increase from tens -> thousands -> millions of objects in controlled stages.

---

# Important beta limitations

The lab starts Solr and Hop because they are part of the target platform. In 0.9.0-beta.3.1, AMP includes a HOP-compatible lab simulator that scans storage directly and writes a separate search/AI simulator store. It does not read catalogue-object rows. The production workstream is to replace that simulator with real HOP pipelines writing Solr/vector indexes while preserving the same recon-ID contract.

Likewise, the Kubernetes lab is a development/pilot topology, not an HA production topology. PostgreSQL, Solr and the object-store simulators are single-instance deployments.

## Registry troubleshooting: MinIO Docker Hub archive

The MinIO Community container repository on Docker Hub is archived and fresh anonymous pulls can fail with `pull access denied`. The AMP lab therefore uses the official Quay image:

```bash
quay.io/minio/minio:RELEASE.2025-05-24T17-08-30Z
```

Before starting the lab you can validate every dependency independently:

```bash
./scripts/lab-pull-images.sh
```

If this succeeds, `./scripts/lab-up.sh` can proceed without registry ambiguity.

For the detailed event, checkpoint, DLQ and scheduling model see `docs/CHANGE_CAPTURE.md`.
