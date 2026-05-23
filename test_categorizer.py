import json
import os
from dotenv import load_dotenv

load_dotenv(override=True)

import google.generativeai as genai
from app.primitives.knowledge.vector_store import VectorService

WORKSPACE_ID = "test123"
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"


_GENERIC_TITLES = {"untitled", "sheet1", "sheet2", "document", "copy", "page", "file", "unnamed"}


def _is_descriptive(title: str) -> bool:
    if not title or len(title) < 5:
        return False
    lower = title.lower().strip()
    if lower in _GENERIC_TITLES:
        return False
    # Looks like a UUID or hash (long hex string)
    if len(lower) > 20 and all(c in "0123456789abcdef-_" for c in lower):
        return False
    return True


def build_prompt(docs: list) -> str:
    lines = []
    for d in docs:
        title = d["title"]
        snippet = d["snippet"]
        sid = d["source_id"]

        if _is_descriptive(title):
            # Title is clear — add a short snippet for extra context
            preview = " ".join(snippet.split()[:30])
            lines.append(f'[{sid}] "{title}" — {preview}')
        else:
            # No useful title — rely entirely on content
            preview = snippet if snippet else "(no content)"
            lines.append(f'[{sid}] (untitled) — {preview}')

    doc_list = "\n".join(lines)

    return f"""You are organizing a personal knowledge base. Below is a list of documents with their IDs and titles (or a short content preview if untitled).

Group them into meaningful, specific categories — the kind a person would actually use to navigate their files. Good examples: "Bible Study Notes", "Bank Statements", "AI Business Research", "Real Estate", "Meeting Notes", "CRM Contacts".

Avoid vague categories like "Miscellaneous" or "General". If a document clearly belongs to a specific topic, put it there.

Return ONLY a valid JSON object in this exact format:
{{
  "Category Name": ["source_id_1", "source_id_2", ...],
  "Another Category": ["source_id_3", ...]
}}

Documents:
{doc_list}"""


SUB_CLUSTER_THRESHOLD = 10


def parse_json_response(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  Failed to parse JSON: {e}")
        print(f"  Raw: {raw[:300]}")
        return None


def categorize(model, docs: list, parent: str | None = None) -> dict:
    if parent:
        context = f'These documents all belong to the category "{parent}". Break them into more specific sub-categories.'
    else:
        context = 'Group them into meaningful, specific categories — the kind a person would actually use to navigate their files. Good examples: "Bible Study Notes", "Bank Statements", "AI Business Research", "Real Estate", "Meeting Notes", "CRM Contacts".'

    doc_list = "\n".join(
        f'[{d["source_id"]}] "{d["title"]}" — {" ".join(d["snippet"].split()[:30])}'
        if _is_descriptive(d["title"])
        else f'[{d["source_id"]}] (untitled) — {d["snippet"]}'
        for d in docs
    )

    prompt = f"""You are organizing a personal knowledge base.

{context}

Avoid vague categories like "Miscellaneous". Every document must be assigned to exactly one category.

Return ONLY a valid JSON object:
{{
  "Category Name": ["source_id_1", "source_id_2", ...],
  ...
}}

Documents:
{doc_list}"""

    response = model.generate_content(prompt)
    return parse_json_response(response.text) or {}


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY not set in .env")
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    vs = VectorService()
    namespace = f"consolidation_{WORKSPACE_ID}"
    doc_index = {d["source_id"]: d for d in vs.list_documents_with_snippets(namespace)}

    print(f"{len(doc_index)} documents found.\n")
    print(f"Pass 1: top-level categorization...")
    top_level = categorize(model, list(doc_index.values()))

    print(f"\n{'=' * 60}")
    print(f"CATEGORY TREE  ({len(top_level)} top-level categories)")
    print("=" * 60)

    for cat_name, ids in sorted(top_level.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"\n{cat_name}  ({len(ids)} docs)")

        if len(ids) > SUB_CLUSTER_THRESHOLD:
            sub_docs = [doc_index[sid] for sid in ids if sid in doc_index]
            print(f"  → sub-clustering {len(sub_docs)} docs...")
            sub_cats = categorize(model, sub_docs, parent=cat_name)

            for sub_name, sub_ids in sorted(sub_cats.items(), key=lambda x: len(x[1]), reverse=True):
                print(f"  └─ {sub_name}  ({len(sub_ids)} docs)")
                for sid in sub_ids[:3]:
                    d = doc_index.get(sid)
                    label = d["title"] if d and _is_descriptive(d["title"]) else f"(untitled) {d['snippet'][:50]}..." if d else sid
                    print(f"       - {label}")
                if len(sub_ids) > 3:
                    print(f"       ... and {len(sub_ids) - 3} more")
        else:
            for sid in ids[:5]:
                d = doc_index.get(sid)
                label = d["title"] if d and _is_descriptive(d["title"]) else f"(untitled) {d['snippet'][:50]}..." if d else sid
                print(f"  - {label}")
            if len(ids) > 5:
                print(f"  ... and {len(ids) - 5} more")


main()
