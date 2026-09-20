# Security Notes — Beta

This release is for controlled testing.

- Set `AMP_API_KEY` to enable basic API authentication.
- Do not expose MinIO, Solr, Tika, HOP or PostgreSQL directly to untrusted networks.
- Replace all Docker Compose credentials.
- The S3 storage form currently stores credentials in the catalogue for beta convenience. Production must use a secrets provider and persist only a secret reference.
- Solr JWT deployments must explicitly block unknown/anonymous users according to the security configuration of the Solr version you deploy.
- Retrieval security must be implemented as a pre-filter before vector/lexical search results are returned.
- Tika processes untrusted content; run it as an isolated service with CPU/memory/time limits.
- Never allow HOP workflows to bypass Registration Service to insert logical object identity directly.
