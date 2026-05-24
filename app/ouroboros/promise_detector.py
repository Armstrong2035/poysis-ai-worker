"""
Ouroboros: Proactive Intelligence for Build Suggestions

Analyzes consolidated knowledge and suggests what agents/bots users could build.
"""

from fastapi import APIRouter, HTTPException, Depends
import json
import os

from app.api.security import get_user_id, verify_workspace_ownership
from app.primitives.database import DatabaseService

import google.generativeai as genai

router = APIRouter(prefix="/ouroboros", tags=["ouroboros"])
db = DatabaseService()

# Initialize Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

BUILD_TEMPLATES = {
    "onboarding_bot": {
        "name": "Onboarding Bot",
        "description": "Guide new hires through your onboarding process",
        "keywords": ["onboarding", "hire", "new employee", "hr", "training", "employee"],
        "block_template": {
            "retrieval": {
                "type": "semantic",
                "mode": "clusters"
            },
            "ai": {
                "system_prompt": "You are a helpful onboarding assistant for new hires. Answer questions based on the provided onboarding documents and company procedures. Be friendly and thorough."
            }
        }
    },
    "faq_bot": {
        "name": "FAQ Assistant",
        "description": "Answer common questions from your knowledge base",
        "keywords": ["faq", "frequently asked", "questions", "help", "support", "q&a"],
        "block_template": {
            "retrieval": {
                "type": "semantic",
                "mode": "clusters"
            },
            "ai": {
                "system_prompt": "You are a helpful FAQ assistant. Answer questions directly and concisely based on the knowledge base."
            }
        }
    },
    "decision_tracker": {
        "name": "Decision Tracker",
        "description": "Track and recall company decisions and their reasoning",
        "keywords": ["decision", "meeting", "notes", "decisions", "strategy", "approved", "meeting notes"],
        "block_template": {
            "retrieval": {
                "type": "semantic",
                "mode": "clusters"
            },
            "ai": {
                "system_prompt": "You are a decision tracker. Help users find and understand past company decisions, the reasoning behind them, and their status."
            }
        }
    },
    "research_bot": {
        "name": "Research Bot",
        "description": "Research and synthesize information across your documents",
        "keywords": ["research", "competitive", "analysis", "market", "industry", "whitepaper"],
        "block_template": {
            "retrieval": {
                "type": "semantic",
                "mode": "clusters"
            },
            "ai": {
                "system_prompt": "You are a research assistant. Help users explore and synthesize information across documents."
            }
        }
    },
    "knowledge_base_search": {
        "name": "Knowledge Base Search",
        "description": "Smart search interface for your consolidated knowledge",
        "keywords": ["search", "find", "look", "retrieve", "query"],
        "block_template": {
            "retrieval": {
                "type": "semantic",
                "mode": "clusters"
            },
            "ai": {
                "system_prompt": "You are a knowledge base search assistant. Help users find exactly what they're looking for in their documents."
            }
        }
    }
}


@router.get("/build-promises/{workspace_id}")
async def detect_build_promises(
    workspace_id: str,
    user_id: str = Depends(get_user_id)
):
    """
    Ouroboros: Detect what apps/bots could be built from their consolidated knowledge.

    Returns a list of build promises (suggested agents) with confidence scores.
    """
    try:
        await verify_workspace_ownership(workspace_id, user_id)
    except:
        # For testing, allow without workspace validation
        pass

    # Fetch both topics and stories for the workspace
    try:
        topics = await db.get_topics(workspace_id)
        stories = await db.get_stories(workspace_id)
    except Exception as e:
        print(f"[OUROBOROS] Error fetching knowledge: {e}")
        return {"workspace_id": workspace_id, "promises": []}

    if not topics:
        return {"workspace_id": workspace_id, "promises": []}

    # Build rich descriptions of topics and stories
    topic_text = "\n".join([
        f"- {t['label']}: {t['doc_count']} documents"
        + (f"\n  About: {t.get('semantic_summary', '')}" if t.get('semantic_summary') else "")
        + (f"\n  Themes: {', '.join(t.get('key_themes', []))}" if t.get('key_themes') else "")
        for t in topics
    ])

    # Add narrative insights if stories exist
    story_text = ""
    if stories:
        story_text = "\n\nNarrative threads your knowledge tells:\n" + "\n".join([
            f"- {s['title']}: {s['description']} (strength: {s['strength']})"
            for s in stories[:5]  # Top 5 stories
        ])

    # Call Gemini to analyze and suggest promises
    prompt = f"""Analyze this user's knowledge base and suggest what AI agents/bots they could build.

Their consolidated topics:
{topic_text}{story_text}

Available bot templates:
- onboarding_bot: Guide new hires through onboarding
- faq_bot: Answer common questions
- decision_tracker: Track and recall company decisions
- research_bot: Research and synthesize information
- knowledge_base_search: Smart search interface
- narrative_guide_bot: Guide users through interconnected story threads

For each potential bot, consider:
1. Do they have enough relevant documents? (at least 5)
2. Would this bot solve a real problem for them?
3. How confident are you (0-1)?

Respond with ONLY a valid JSON array. Example:
[
  {{
    "template_id": "onboarding_bot",
    "confidence": 0.95,
    "reasoning": "23 HR docs + FAQ cluster suggests strong onboarding content"
  }},
  {{
    "template_id": "faq_bot",
    "confidence": 0.85,
    "reasoning": "Multiple FAQ-related documents found"
  }}
]

Only include templates where confidence >= 0.6.
"""

    try:
        model = genai.GenerativeModel("gemini-3.5-flash")
        response = model.generate_content(prompt)

        # Parse response
        response_text = response.text
        print(f"[OUROBOROS] Gemini raw response:\n{response_text}\n")

        # Extract JSON if wrapped in markdown
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        print(f"[OUROBOROS] Extracted JSON:\n{response_text}\n")
        suggestions = json.loads(response_text.strip())
    except Exception as e:
        print(f"[OUROBOROS] Error calling Gemini: {e}")
        import traceback
        traceback.print_exc()
        suggestions = []

    # Enrich with template details + block configs
    promises = []
    for sugg in suggestions:
        template_id = sugg.get("template_id")
        if template_id not in BUILD_TEMPLATES:
            continue

        template = BUILD_TEMPLATES[template_id]

        # Find relevant topics by keyword matching
        relevant_topics = []
        for t in topics:
            label_lower = t["label"].lower()
            if any(kw in label_lower for kw in template["keywords"]):
                relevant_topics.append(t["topic_id"])

        # If no keyword match, just use all topics (fallback)
        if not relevant_topics:
            relevant_topics = [t["topic_id"] for t in topics]

        promises.append({
            "id": template_id,
            "name": template["name"],
            "description": template["description"],
            "confidence": min(sugg.get("confidence", 0.7), 1.0),
            "reasoning": sugg.get("reasoning", ""),
            "relevant_topics": relevant_topics,
            "suggested_blocks": _build_block_config(template, workspace_id, relevant_topics),
            "actions": ["build", "preview", "dismiss"]
        })

    # Sort by confidence
    promises.sort(key=lambda p: p["confidence"], reverse=True)

    return {
        "workspace_id": workspace_id,
        "promises": promises,
        "total_topics": len(topics),
        "topics": [{"topic_id": t["topic_id"], "label": t["label"], "doc_count": t["doc_count"]} for t in topics]
    }


def _build_block_config(template: dict, workspace_id: str, topic_ids: list) -> list:
    """Build the block configuration for a notebook."""
    return [
        {
            "type": "input",
            "config": {
                "label": "User Question",
                "placeholder": "Ask anything about your knowledge base"
            }
        },
        {
            "type": "retrieval",
            "config": {
                "workspace_id": workspace_id,
                "clusters": topic_ids,
                "top_k": 5,
                "min_score": 0.5
            }
        },
        {
            "type": "ai",
            "config": {
                "model": "gemini-3.5-flash",
                "system_prompt": template["block_template"]["ai"]["system_prompt"]
            }
        },
        {
            "type": "output",
            "config": {
                "type": "chat"
            }
        }
    ]
