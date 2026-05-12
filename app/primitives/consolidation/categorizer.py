import asyncio
import json
import os
from typing import Dict, Any, List, Optional

from app.primitives.database import DatabaseService
from app.primitives.knowledge.vector_store import VectorService

CATEGORIZER_MODEL = "gemini-3.1-flash-lite-preview"
SUB_CLUSTER_THRESHOLD = 10
_GENERIC_TITLES = {"untitled", "sheet1", "sheet2", "document", "copy", "page", "file", "unnamed"}


def _is_descriptive(title: str) -> bool:
    if not title or len(title) < 5:
        return False
    lower = title.lower().strip()
    if lower in _GENERIC_TITLES:
        return False
    if len(lower) > 20 and all(c in "0123456789abcdef-_" for c in lower):
        return False
    return True


def _build_prompt(docs: List[Dict], parent: Optional[str] = None) -> str:
    if parent:
        context = f'These documents all belong to the category "{parent}". Break them into more specific sub-categories.'
    else:
        context = (
            'Group them into meaningful, specific categories — the kind a person would use to navigate their files. '
            'Good examples: "Bible Study Notes", "Bank Statements", "AI Business Research", "Real Estate", "Meeting Notes", "CRM Contacts". '
            'Avoid vague categories like "Miscellaneous". Every document must be assigned to exactly one category.'
        )

    lines = []
    for d in docs:
        sid = d["source_id"]
        title = d.get("title", "")
        snippet = d.get("snippet", "")
        if _is_descriptive(title):
            preview = " ".join(snippet.split()[:30])
            lines.append(f'[{sid}] "{title}" — {preview}')
        else:
            lines.append(f'[{sid}] (untitled) — {snippet}')

    return f"""You are organizing a personal knowledge base.

{context}

Return ONLY a valid JSON object:
{{
  "Category Name": ["source_id_1", "source_id_2", ...],
  ...
}}

Documents:
{chr(10).join(lines)}"""


def _parse_response(raw: str) -> Optional[Dict[str, List[str]]]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return None


def _call_gemini(model, docs: List[Dict], parent: Optional[str] = None) -> Dict[str, List[str]]:
    prompt = _build_prompt(docs, parent)
    response = model.generate_content(prompt)
    result = _parse_response(response.text)
    if result is None:
        print(f"[Categorizer] Failed to parse Gemini response — skipping {'sub-' if parent else ''}categorization")
        return {}
    return result


class CategorizerEngine:
    def __init__(self, db: DatabaseService, vector_service: VectorService):
        self.db = db
        self.vector_service = vector_service

    def _get_model(self):
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        genai.configure(api_key=api_key)
        return genai.GenerativeModel(CATEGORIZER_MODEL)

    async def run_categorization(self, workspace_id: str) -> Dict[str, Any]:
        namespace = f"consolidation_{workspace_id}"

        docs = await asyncio.to_thread(self.vector_service.list_documents_with_snippets, namespace)
        if not docs:
            return {"status": "skipped", "reason": "no documents", "workspace_id": workspace_id}

        print(f"[Categorizer] Running on {len(docs)} documents...")
        model = self._get_model()

        top_cats = await asyncio.to_thread(_call_gemini, model, docs)
        if not top_cats:
            return {"status": "failed", "reason": "top-level categorization returned nothing", "workspace_id": workspace_id}

        topics_data = []
        source_to_cat: Dict[str, Dict] = {}
        cat_id = 0

        for cat_name, source_ids in top_cats.items():
            top_id = cat_id
            cat_id += 1
            cat_docs = [d for d in docs if d["source_id"] in set(source_ids)]

            if len(source_ids) > SUB_CLUSTER_THRESHOLD:
                print(f"[Categorizer]   Sub-clustering '{cat_name}' ({len(source_ids)} docs)...")
                sub_cats = await asyncio.to_thread(_call_gemini, model, cat_docs, parent=cat_name)

                total = 0
                for sub_name, sub_ids in sub_cats.items():
                    sub_id = cat_id
                    cat_id += 1
                    total += len(sub_ids)
                    topics_data.append({
                        "topic_id": sub_id,
                        "label": sub_name,
                        "keywords": [],
                        "doc_count": len(sub_ids),
                        "parent_topic_id": top_id,
                    })
                    for sid in sub_ids:
                        source_to_cat[sid] = {"id": sub_id, "label": sub_name}

                topics_data.append({
                    "topic_id": top_id,
                    "label": cat_name,
                    "keywords": [],
                    "doc_count": total or len(source_ids),
                    "parent_topic_id": None,
                })
            else:
                topics_data.append({
                    "topic_id": top_id,
                    "label": cat_name,
                    "keywords": [],
                    "doc_count": len(source_ids),
                    "parent_topic_id": None,
                })
                for sid in source_ids:
                    source_to_cat[sid] = {"id": top_id, "label": cat_name}

        # Persist categories (clear BERTopic topics first)
        await self.db.clear_topics(workspace_id)
        await self.db.save_topics(workspace_id, topics_data)

        # Update vector metadata with category assignment
        vector_refs = await asyncio.to_thread(self.vector_service.fetch_vector_source_ids, namespace)
        updates = []
        for v in vector_refs:
            cat = source_to_cat.get(v["source_id"])
            if cat:
                updates.append({
                    "id": v["id"],
                    "metadata": {
                        "category_id": cat["id"],
                        "category_label": cat["label"],
                    },
                })
        if updates:
            await asyncio.to_thread(self.vector_service.update_vector_metadata_batch, updates, namespace)

        top_count = len([t for t in topics_data if t["parent_topic_id"] is None])
        total_count = len(topics_data)
        print(f"[Categorizer] Done — {top_count} categories, {total_count} total (including sub-categories)")
        return {
            "status": "done",
            "workspace_id": workspace_id,
            "documents_categorized": len(docs),
            "categories": top_count,
            "total_topics": total_count,
        }
