# Poysis AI Worker Roadmap

This document outlines the migration from the legacy `product-scout` Shopify app to the domain-agnostic **Poysis AI Worker** (Microservice).

## ✅ Phase 1: The Purge (Completed)
- [x] Deleted all Shopify-specific services (`shopify_service.py`).
- [x] Removed legacy React/Next.js frontend assets (`static/`, `test_overlay.html`).
- [x] Gutted `main.py` of OAuth flows, credit-gating, and static file serving.
- [x] Purged legacy diagnostic and testing scripts to reduce noise.

## ✅ Phase 2-5: Generalization & Modularization (Completed)
- [x] **Multi-tenancy Swap**: Renamed `shop_url` to `workspace_id` across the database and analytics layers.
- [x] **Generic Ingestion**: Rewrote the Indexer to accept raw JSON text blocks instead of scraping Shopify GraphQL.
- [x] **Pure RAG Retrieval**: Simplified the `/search` endpoint to return raw vector chunks, removing the forced LLM chat generation.
- [x] **Domain-Driven Re-structure**: 
    - Moved atomic engines to `app/primitives/`.
    - Encapsulated Product tools in `app/blocks/retrieval/`.

## 🏗️ Phase 6: The Poysis Kernel (The "Golden Quad")
We are implementing the four core AI blocks that encapsulate 80% of business value. Each block is a standalone product tool powered by the **Embedder Primitive**.

- [ ] **Primitive: Knowledge Engine**: Unified "Memory" service (Embedder + VectorStore).
- [ ] **Pipeline: Multi-Format Ingestion**: Support for PDF, CSV, and Spreadsheets.
- [ ] **Block: Categorization (The Intent Sorter)**: Mapping natural language to flags via JSON schema.

## ⚙️ Phase 7: The Orchestration Layer
- [ ] **Sequential Pipeline Orchestrator**: The "Glue" that chains the Golden Quad blocks together via a JSON Blueprint.
- [ ] **Standardized Block Protocol**: Ensuring every block follows the `process(JSON) -> JSON` standard.
- [ ] **Parameterization**: Moving all hardcoded rules into `config/prompts.json` and a database-backed Rules Engine.

---
**Vision**: Poysis AI Worker will serve as the "Heavy Lifting" layer for the Poysis No-Code platform, providing the raw primitive execution for complex, multi-turn AI workflows.
