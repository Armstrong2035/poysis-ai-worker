# Poysis backend architecture reference

Prepared from the current working tree on 14 September 2026, including local changes. Backend only. Infrastructure is documented from CDK, not verified against live AWS resources. No application code or infrastructure was changed for this documentation.

Start with the [full architecture](BACKEND_ARCHITECTURE.md), then use the [FigJam blueprint](FIGJAM_BLUEPRINT.md) to recreate it visually.

| Document | Contents |
|---|---|
| [Backend architecture](BACKEND_ARCHITECTURE.md) | Runtime/deployment, identity and scope, ingestion, embedding, retrieval/chat, clustering, MCP, data, marketing, interpretation, jobs, costs, proposed platform boundaries. |
| [FigJam blueprint](FIGJAM_BLUEPRINT.md) | 17 frames, 62 component cards, overview connections, detailed flow instructions, and visual legend. |
| [API inventory](API_INVENTORY.md) | 60 explicitly mounted routes, handler inputs, and router-local request fields. |
| [Data contracts](DATA_CONTRACTS.md) | Shared source/chunk models, marketing rules/reports, interpretation inputs/outputs and evidence. |
| [Database declarations](DATA_SCHEMA.md) | PostgreSQL migration declarations and the separate host-owned SQLite schema. |
| [Findings](FINDINGS.md) | Wiring differences, execution/compatibility issues, runtime unknowns, and platform design questions. |
| [Component CSV](FIGJAM_COMPONENTS.csv) | Component IDs, labels, card content, evidence status. |
| [Connection CSV](FIGJAM_CONNECTIONS.csv) | Overview endpoints, arrow labels, and relationship style. |

The API and field inventories were extracted using Python AST parsing without importing/running the application. SQL declarations were read without executing migrations. Local document links and fenced blocks were checked. This validates the documentation structure, not the runtime correctness of the system. Model-provider access, live schema/index state, actual spend, and production deployment remain unverified.
