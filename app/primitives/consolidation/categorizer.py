import asyncio
import json
import os
from typing import Dict, Any, List, Optional
import google.generativeai as genai

from app.primitives.database import DatabaseService
from app.primitives.knowledge.vector_store import VectorService

CATEGORIZER_MODEL = "gemini-3.1-flash-lite-preview"
SUB_CLUSTER_THRESHOLD = 25
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
        context = (
            f'These documents all belong to the category "{parent}". '
            'Break them into 3–5 more specific sub-categories. '
            'Prefer broader sub-groups over many narrow ones — only split out a sub-category '
            'if it has at least 3 documents that genuinely don\'t fit the others.'
        )
    else:
        context = (
            'Group them into 6–8 meaningful, specific top-level categories — the kind a person '
            'would use to navigate their files. '
            'Good examples: "Bible Study Notes", "Bank Statements", "AI Business Research", "Real Estate", "Meeting Notes", "CRM Contacts". '
            'Prefer broader, well-populated groups over many narrow ones. '
            'Only create a separate category if it has at least 3 documents that genuinely don\'t fit elsewhere. '
            'Hard cap: 8 categories. Avoid vague categories like "Miscellaneous". '
            'Every document must be assigned to exactly one category.'
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

    async def _detect_stories(
        self,
        topics_data: List[Dict],
        model
    ) -> List[Dict[str, Any]]:
        """Detect narrative threads connecting topics."""
        if not topics_data:
            return []

        # Build rich topic descriptions with IDs for story detection
        topic_descriptions = "\n".join([
            f"- ID {t['topic_id']}: {t['label']} ({t['doc_count']} docs)"
            for t in topics_data
        ])

        # Create label-to-ID mapping for fallback
        label_to_id = {t['label']: t['topic_id'] for t in topics_data}

        # Ask Gemini to identify story threads
        prompt = f"""Analyze this knowledge base and identify the major narrative threads —
implicit stories that connect multiple topics together.

For EACH major story, describe:
- What is the narrative arc? (which topics form the story in order)
- What's the central theme connecting them?
- How compelling/coherent is this story (0-1)?

Topics (use the numeric IDs):
{topic_descriptions}

Respond with ONLY a valid JSON array (up to 7 major stories):
[
  {{
    "title": "Story title (e.g., 'Tech & Society')",
    "description": "1-2 sentence description of what this story explores",
    "topic_sequence": [1, 5, 8],
    "reasoning": "Why these topics form a coherent narrative",
    "strength": 0.95
  }},
  ...
]

Only include stories with strength >= 0.7. Order by strength descending."""

        try:
            response = await asyncio.to_thread(model.generate_content, prompt)
            stories = _parse_response(response.text)

            if not isinstance(stories, list):
                print(f"[Stories] Invalid response format")
                return []

            # Validate and enrich stories
            validated_stories = []
            for idx, story in enumerate(stories):
                if all(k in story for k in ["title", "topic_sequence", "strength"]):
                    topic_seq = story.get("topic_sequence", [])

                    # Map labels to IDs if needed (fallback for when Gemini returns labels)
                    mapped_seq = []
                    for item in topic_seq:
                        if isinstance(item, int):
                            mapped_seq.append(item)
                        elif isinstance(item, str) and item in label_to_id:
                            mapped_seq.append(label_to_id[item])

                    story["story_id"] = idx
                    story["topic_sequence"] = mapped_seq
                    story["doc_count"] = sum(
                        t["doc_count"] for t in topics_data
                        if t["topic_id"] in mapped_seq
                    )
                    validated_stories.append(story)
                    print(f"[Stories] {story['title']}: {story['strength']} ({story['doc_count']} docs)")

            return validated_stories

        except Exception as e:
            print(f"[Stories] Error detecting narratives: {e}")
            return []

    async def _analyze_semantic_content(
        self,
        all_docs: List[Dict],
        topics_data: List[Dict],
        source_to_cat: Dict[str, Dict],
        model
    ) -> Dict[int, Dict[str, Any]]:
        """Analyze semantic content of each topic using Gemini."""
        semantic_results = {}

        # Build topic -> docs mapping
        topic_docs = {}
        for topic in topics_data:
            topic_id = topic["topic_id"]
            topic_docs[topic_id] = [
                d for d in all_docs
                if source_to_cat.get(d["source_id"], {}).get("id") == topic_id
            ]

        # Analyze each topic's semantic content
        for topic in topics_data:
            topic_id = topic["topic_id"]
            label = topic["label"]
            docs = topic_docs.get(topic_id, [])

            if not docs:
                continue

            # Sample docs (up to 5)
            sample_size = min(5, len(docs))
            sample = docs[:sample_size]

            # Build sample text
            doc_previews = []
            for doc in sample:
                title = doc.get("title", "Untitled")
                snippet = doc.get("snippet", "")[:400]
                doc_previews.append(f'[{title}]\n{snippet}')

            sample_text = "\n---\n".join(doc_previews)

            # Ask Gemini about this topic
            prompt = f"""Analyze these {len(sample)} document samples from a knowledge cluster labeled "{label}".

What is this cluster actually about? What are the core themes and topics?
What would this be useful for?

Samples:
{sample_text}

Respond with ONLY valid JSON:
{{
  "semantic_summary": "1-2 sentence summary of what this cluster is really about",
  "key_themes": ["theme1", "theme2", "theme3"],
  "suggested_use_cases": ["use case 1", "use case 2"]
}}"""

            try:
                response = await asyncio.to_thread(model.generate_content, prompt)
                analysis = _parse_response(response.text)
                if analysis:
                    semantic_results[topic_id] = analysis
                    print(f"[Semantic] {label}: {analysis.get('semantic_summary', 'N/A')[:80]}...")
                else:
                    print(f"[Semantic] Failed to parse response for topic {topic_id}")
            except Exception as e:
                print(f"[Semantic] Error analyzing topic {topic_id}: {e}")

        return semantic_results

    def _get_model(self):
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

        # Topics the owner has locked (topic_overrides.locked = true) must
        # survive reclustering untouched — exclude their documents from
        # categorization entirely rather than letting Gemini re-sort them.
        locked_topic_ids = set(await self.db.get_locked_topic_ids(workspace_id))
        existing_category_map = await asyncio.to_thread(self.vector_service.fetch_category_assignments, namespace)
        locked_source_ids = {sid for sid, cid in existing_category_map.items() if cid in locked_topic_ids}
        docs_for_gemini = [d for d in docs if d["source_id"] not in locked_source_ids]

        if docs_for_gemini:
            top_cats = await asyncio.to_thread(_call_gemini, model, docs_for_gemini)
            if not top_cats:
                return {"status": "failed", "reason": "top-level categorization returned nothing", "workspace_id": workspace_id}
        else:
            print(f"[Categorizer] All {len(docs)} document(s) are in locked topics — nothing to recategorize")
            top_cats = {}

        # Gemini isn't guaranteed to place every document despite the prompt's
        # instruction to — bucket any it dropped instead of leaving them
        # assigned to nothing (first cluster) or a stale category_id from a
        # previous run (recluster), either of which looks like "wrong topic".
        all_source_ids = {d["source_id"] for d in docs_for_gemini}
        assigned_ids = {sid for ids in top_cats.values() for sid in ids}
        missing_ids = all_source_ids - assigned_ids
        if missing_ids:
            print(f"[Categorizer] {len(missing_ids)} document(s) not placed by top-level categorization — bucketing into 'Unsorted'")
            top_cats.setdefault("Unsorted", []).extend(missing_ids)

        # Preserve topic_id across reclusters. cluster_ceilings and
        # topic_overrides (client-side) key visibility/lock rules off this id
        # as if it permanently identifies one topic — but it's really just a
        # counter over whatever order Gemini listed categories in, which
        # changes every run. A category keeps its old topic_id as long as its
        # label matches last run's; only a genuinely new label gets a fresh
        # id that's never been used before (so ceiling/override rows for a
        # topic that later disappears can't silently get reassigned to an
        # unrelated new topic that happens to reuse the same number).
        existing_topics = await self.db.get_topics(workspace_id)
        existing_top_ids = {
            t["label"].strip().lower(): t["topic_id"]
            for t in existing_topics
            if t.get("parent_topic_id") is None and t["topic_id"] not in locked_topic_ids
        }
        existing_sub_ids = {
            (t["parent_topic_id"], t["label"].strip().lower()): t["topic_id"]
            for t in existing_topics
            if t.get("parent_topic_id") is not None and t["topic_id"] not in locked_topic_ids
        }
        next_id = max((t["topic_id"] for t in existing_topics), default=-1) + 1

        def _resolve_id(existing_map: Dict[Any, int], key: Any) -> int:
            nonlocal next_id
            if key in existing_map:
                return existing_map[key]
            assigned = next_id
            next_id += 1
            return assigned

        topics_data = []
        source_to_cat: Dict[str, Dict] = {}

        for cat_name, source_ids in top_cats.items():
            top_id = _resolve_id(existing_top_ids, cat_name.strip().lower())
            cat_docs = [d for d in docs if d["source_id"] in set(source_ids)]

            if len(source_ids) > SUB_CLUSTER_THRESHOLD:
                print(f"[Categorizer]   Sub-clustering '{cat_name}' ({len(source_ids)} docs)...")
                sub_cats = await asyncio.to_thread(_call_gemini, model, cat_docs, parent=cat_name)

                assigned_sub_ids = {sid for ids in sub_cats.values() for sid in ids}
                missing_sub_ids = set(source_ids) - assigned_sub_ids
                if missing_sub_ids:
                    print(f"[Categorizer]   {len(missing_sub_ids)} document(s) not placed by sub-clustering '{cat_name}' — bucketing into 'Other'")
                    sub_cats.setdefault("Other", []).extend(missing_sub_ids)

                total = 0
                for sub_name, sub_ids in sub_cats.items():
                    sub_id = _resolve_id(existing_sub_ids, (top_id, sub_name.strip().lower()))
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

        # Locked topics are carried over exactly as they were — label,
        # keywords, doc_count, semantic fields, and their documents'
        # category_id all preserved untouched instead of recomputed.
        existing_topics_by_id = {t["topic_id"]: t for t in existing_topics}
        for tid in locked_topic_ids:
            topic = existing_topics_by_id.get(tid)
            if not topic:
                continue
            topics_data.append({
                "topic_id": topic["topic_id"],
                "label": topic["label"],
                "keywords": topic.get("keywords", []),
                "doc_count": topic.get("doc_count", 0),
                "parent_topic_id": topic.get("parent_topic_id"),
                "semantic_summary": topic.get("semantic_summary"),
                "key_themes": topic.get("key_themes", []),
                "suggested_use_cases": topic.get("suggested_use_cases", []),
            })
        for sid in locked_source_ids:
            cid = existing_category_map[sid]
            topic = existing_topics_by_id.get(cid)
            if topic:
                source_to_cat[sid] = {"id": cid, "label": topic["label"]}

        # Persist categories (clear previous clustering)
        await self.db.clear_topics(workspace_id)
        await self.db.clear_stories(workspace_id)

        # Run semantic analysis AND story detection in parallel. Locked topics
        # already carry their semantic fields from the passthrough above —
        # re-analyzing them would waste calls and could drift their summary
        # even though their documents didn't change.
        new_topics_data = [t for t in topics_data if t["topic_id"] not in locked_topic_ids]
        print(f"[Categorizer] Analyzing semantic content and detecting stories...")
        semantic_task = asyncio.create_task(
            self._analyze_semantic_content(docs, new_topics_data, source_to_cat, model)
        )
        stories_task = asyncio.create_task(
            self._detect_stories(topics_data, model)
        )

        semantic_data = await semantic_task
        stories_data = await stories_task

        # Enrich topics with semantic analysis
        for topic in new_topics_data:
            if topic["topic_id"] in semantic_data:
                topic.update(semantic_data[topic["topic_id"]])

        # Save both topics and stories
        await self.db.save_topics(workspace_id, topics_data)
        if stories_data:
            await self.db.save_stories(workspace_id, stories_data)

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
