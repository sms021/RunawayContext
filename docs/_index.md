# Documentation Index

Navigation for the v3 documentation tree.

## Top-level documents

- [README.md](../README.md) — orientation, what's new in v3, privacy section
- [BOOTSTRAP.md](../BOOTSTRAP.md) — manual walkthrough of every tier T0..T5
- [RUNAWAYCONTEXT.md](../RUNAWAYCONTEXT.md) — full theory + reference guide
- [CHANGELOG.md](../CHANGELOG.md) — v3.0.0 entry + preserved v2 history
- [INSTALL_PROMPT.md](../INSTALL_PROMPT.md) — the canonical prompt for an adopter's AI
- [RETRIEVAL.md](../RETRIEVAL.md) — paste-once T0 template
- [MIGRATION_V2_TO_V3.md](../MIGRATION_V2_TO_V3.md) — v2 to v3 walkthrough

## Reference documents (`docs/`)

- [HARD_RULES.md](HARD_RULES.md) — HR-1..HR-15 charter with links to enforcers
- [ARCHITECTURE.md](ARCHITECTURE.md) — full mermaid + principle-to-enforcement map
- [MCP.md](MCP.md) — the canonical 13-tool MCP surface reference
- [PYTHON_API.md](PYTHON_API.md) — Client class reference

## Specs (`docs/specs/`)

What we don't build. Adopters' AIs build these against the spec; the contract tests verify conformance.

- [SSO_INTEGRATION.md](specs/SSO_INTEGRATION.md) — S1: identity provider binding to `author_id`
- [FEDERATION.md](specs/FEDERATION.md) — S2: read-only upstream source refresh
- [OTLP_EXPORT.md](specs/OTLP_EXPORT.md) — S3: opt-in OpenTelemetry export
- [AIR_GAPPED_INSTALL.md](specs/AIR_GAPPED_INSTALL.md) — S4: offline bundle composition
- [FINE_GRAINED_GRANTS.md](specs/FINE_GRAINED_GRANTS.md) — S5: per-(slug, action) grants over visibility
- [COMPLIANCE.md](specs/COMPLIANCE.md) — S6: SOC2 / GDPR control mapping
- [MULTI_TENANT_ROLLOUT.md](specs/MULTI_TENANT_ROLLOUT.md) — S7: OS user provisioning
- [IMPORTERS.md](specs/IMPORTERS.md) — S8: Mem0 / OpenMemory / raw-markdown ingest
- [DASHBOARD.md](specs/DASHBOARD.md) — S9: loopback-only FastAPI
- [CROSS_PLATFORM.md](specs/CROSS_PLATFORM.md) — S10: Windows / macOS adaptation

## AI tool integrations

- [skills/runaway-context/SKILL.md](../skills/runaway-context/SKILL.md) — Claude Code skill
- [.cursor/rules/runaway-retrieval.md](../.cursor/rules/runaway-retrieval.md) — Cursor rule

## Schema and tests

- `schema/` — SQL migrations (additive-only by HR-4)
- `tests/contract/` — HR-1..HR-15 contract tests
- `tests/unit/` — per-feature tests E1..E24
- `tests/fixtures/` — v1, v2-clean, v2-with-data frozen DBs

## Reading order

For a new adopter:

1. [README.md](../README.md) — overview, what you get
2. [INSTALL_PROMPT.md](../INSTALL_PROMPT.md) — paste to your AI
3. [BOOTSTRAP.md](../BOOTSTRAP.md) — what your AI will do (or what you can do manually)
4. [HARD_RULES.md](HARD_RULES.md) — what the contracts guarantee
5. [MCP.md](MCP.md) — once installed, how to use the surface

For an existing v2 user:

1. [CHANGELOG.md](../CHANGELOG.md) — what changed
2. [MIGRATION_V2_TO_V3.md](../MIGRATION_V2_TO_V3.md) — how to migrate non-destructively
3. [HARD_RULES.md](HARD_RULES.md) — the new contract surface
4. [RUNAWAYCONTEXT.md](../RUNAWAYCONTEXT.md) — full theory

For someone building an integration (adopter's AI implementing a spec):

1. The relevant spec under [specs/](specs/)
2. [ARCHITECTURE.md](ARCHITECTURE.md) — where the integration fits
3. [PYTHON_API.md](PYTHON_API.md) — what the Client provides
4. [HARD_RULES.md](HARD_RULES.md) — what you must not violate
