#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
wiki_dir="${1:-$repo_root/build/wiki}"

mkdir -p "$wiki_dir"
find "$wiki_dir" -mindepth 1 -maxdepth 1 -type f -name '*.md' -delete

cat > "$wiki_dir/Home.md" <<'EOF'
# DataAmp — Archive Modernization Platform

This Wiki is the product and architecture guide for AMP.

Use it to understand:

- what AMP contains in the current Beta 5 implementation;
- how the platform is architected;
- which boundaries and design decisions are locked;
- what capabilities are planned for the complete enterprise product.

## Recommended reading order

1. [Product Vision](Product-Vision)
2. [Current vs Complete Product](Product-Capability-Map)
3. [Architecture](Architecture)
4. [Feature Tracker](Feature-Tracker)
5. [Architecture Decisions](ADR-0001-catalogue-boundary)

## Documentation boundary

The Wiki contains only architecture and feature definitions. Delivery administration, lab setup, test execution, project status and operational working notes remain in the main repository.

The repository Markdown is canonical. This Wiki is generated automatically and direct Wiki edits are overwritten.
EOF

cp "$repo_root/docs/PRODUCT_VISION.md" "$wiki_dir/Product-Vision.md"
cp "$repo_root/docs/PRODUCT_CAPABILITY_MAP.md" "$wiki_dir/Product-Capability-Map.md"
cp "$repo_root/docs/ARCHITECTURE.md" "$wiki_dir/Architecture.md"
cp "$repo_root/docs/GLOSSARY.md" "$wiki_dir/Glossary.md"
cp "$repo_root/docs/features/FEATURE_TRACKER.md" "$wiki_dir/Feature-Tracker.md"
cp "$repo_root/docs/decision-records/DECISION_MATRIX.md" "$wiki_dir/Decision-Matrix.md"

cp "$repo_root/docs/reference/BACKEND_AUTHORITY.md" "$wiki_dir/Backend-Authority.md"
cp "$repo_root/docs/reference/CATALOGUE_SHARDING.md" "$wiki_dir/Catalogue-Sharding.md"
cp "$repo_root/docs/reference/CHANGE_CAPTURE.md" "$wiki_dir/Change-Capture.md"
cp "$repo_root/docs/reference/GATEWAY_RESPONSE_POLICY.md" "$wiki_dir/Gateway-Response-Policy.md"
cp "$repo_root/docs/reference/HCP_REST_COMPATIBILITY.md" "$wiki_dir/HCP-REST-Compatibility.md"
cp "$repo_root/docs/reference/S3_COMPATIBILITY.md" "$wiki_dir/S3-Compatibility.md"
cp "$repo_root/docs/reference/SECURITY.md" "$wiki_dir/Security-Architecture.md"

for adr in "$repo_root"/docs/adr/ADR-*.md; do
  cp "$adr" "$wiki_dir/$(basename "$adr")"
done

cat > "$wiki_dir/_Sidebar.md" <<'EOF'
## AMP Product

- [Home](Home)
- [Product vision](Product-Vision)
- [Current vs complete product](Product-Capability-Map)
- [Feature tracker](Feature-Tracker)
- [Glossary](Glossary)

## Architecture

- [Architecture overview](Architecture)
- [Backend authority](Backend-Authority)
- [Catalogue sharding](Catalogue-Sharding)
- [Change capture](Change-Capture)
- [Gateway response policy](Gateway-Response-Policy)
- [HCP REST compatibility](HCP-REST-Compatibility)
- [S3 compatibility](S3-Compatibility)
- [Security architecture](Security-Architecture)
- [Open decision matrix](Decision-Matrix)

## Architecture decisions

- [ADR-0001: Catalogue boundary](ADR-0001-catalogue-boundary)
- [ADR-0002: Catalogue sharding](ADR-0002-catalogue-sharding)
- [ADR-0003: Backend authority](ADR-0003-backend-authority)
- [ADR-0004: Search-plane separation](ADR-0004-search-plane-separation)
- [ADR-0005: Object identity and package](ADR-0005-object-identity-and-package)
- [ADR-0006: Migration registration](ADR-0006-migration-registration)
- [ADR-0007: Gateway response policy](ADR-0007-gateway-response-policy)
- [ADR-0008: Change capture](ADR-0008-change-capture)
- [ADR-0009: Repository governance](ADR-0009-repository-governance)
- [ADR-0010: Beta 5 authentication](ADR-0010-beta5-authentication-boundary)
EOF

cat > "$wiki_dir/_Footer.md" <<'EOF'
Architecture and feature documentation generated from the canonical DataAmp repository. Direct Wiki edits are overwritten.
EOF

echo "Built architecture and feature Wiki in $wiki_dir"
