#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
wiki_dir="${1:-$repo_root/build/wiki}"

mkdir -p "$wiki_dir"
find "$wiki_dir" -mindepth 1 -maxdepth 1 -type f -name '*.md' -delete

cp "$repo_root/README.md" "$wiki_dir/Home.md"
cp "$repo_root/PROJECT_STATUS.md" "$wiki_dir/Project-Status.md"
cp "$repo_root/ROADMAP.md" "$wiki_dir/Roadmap.md"
cp "$repo_root/docs/PRODUCT_VISION.md" "$wiki_dir/Product-Vision.md"
cp "$repo_root/docs/ARCHITECTURE.md" "$wiki_dir/Architecture.md"
cp "$repo_root/docs/GLOSSARY.md" "$wiki_dir/Glossary.md"
cp "$repo_root/docs/features/FEATURE_TRACKER.md" "$wiki_dir/Feature-Tracker.md"
cp "$repo_root/docs/decision-records/DECISION_MATRIX.md" "$wiki_dir/Decision-Matrix.md"
cp "$repo_root/docs/testing/BASELINE_VALIDATION.md" "$wiki_dir/Baseline-Validation.md"
cp "$repo_root/lab/README.md" "$wiki_dir/Lab.md"
cp "$repo_root/lab/docs/SETUP_WINDOWS_WSL2.md" "$wiki_dir/Lab-Setup-WSL2.md"
cp "$repo_root/lab/docs/ACCEPTANCE_TEST_PLAN.md" "$wiki_dir/Lab-Acceptance-Plan.md"

for adr in "$repo_root"/docs/adr/ADR-*.md; do
  cp "$adr" "$wiki_dir/$(basename "$adr")"
done

cat > "$wiki_dir/_Sidebar.md" <<'EOF'
## DataAmp

- [Home](Home)
- [Project status](Project-Status)
- [Roadmap](Roadmap)
- [Product vision](Product-Vision)
- [Architecture](Architecture)
- [Feature tracker](Feature-Tracker)
- [Decision matrix](Decision-Matrix)
- [Glossary](Glossary)

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

## Validation and lab

- [Baseline validation](Baseline-Validation)
- [Lab overview](Lab)
- [WSL2 setup](Lab-Setup-WSL2)
- [Acceptance plan](Lab-Acceptance-Plan)
EOF

cat > "$wiki_dir/_Footer.md" <<'EOF'
Generated from the canonical Markdown in the DataAmp repository. Do not edit the Wiki directly; submit repository changes instead.
EOF

echo "Built DataAmp Wiki pages in $wiki_dir"

