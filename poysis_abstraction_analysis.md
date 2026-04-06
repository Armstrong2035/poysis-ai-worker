# Poysis Abstraction Report: Domain-Agnostic Primitives

This report deconstructs the **Emerson Hair Care Co-Pilot** repository into generic software mechanics (Primitives) to identify reusable "Blocks" for the **Poysis** No-Code AI Engineering platform.

## 1. Core Architecture Abstractions

| Current Feature | The Poysis Primitive (Block) | Decoupling Effort |
| :--- | :--- | :--- |
| **Onboarding Quiz & Classifier** | **Dynamic Intent Classifier** | **Low.** The logic maps user answers to semantic tags (`routine_flags`). Decoupling requires replacing hair-specific dictionaries with a user-defined JSON mapping schema. |
| **Orchestrator** | **Sequential Pipeline Orchestrator** | **Moderate.** Currently hardcoded to run `input → routine → recommendation`. Abstraction involves creating a generic DAG (Directed Acyclic Graph) executor that chains blocks based on JSON configuration. |
| **Routine Generation** | **Constraint-Aware Prompt Generator** | **Low.** The "Goal vs. Constraints" prompt structure is a powerful generic pattern for any advisory AI. Abstraction involves templating the "Goal" and "Constraint" fields. |
| **Product Recommendation** | **Semantic Retrieval (RAG) Block** | **Very Low.** The Pinecone + Gemini Embedding bridge is already 90% agnostic. Decoupling simply requires parameterizing the index name and search namespaces. |
| **Empath Diagnostic Chat** | **Socratic State Machine** | **High.** The logic (max 2 questions, handoff checkpoints) is the heart of the co-pilot. Abstraction requires a state machine where "Diagnostic Goal" and "Exit Conditions" are programmable. |
| **Librarian (History)** | **Temporal Memory Manager** | **Moderate.** Currently tracks "Hair Events". Decoupling involves treating all history as generic "Observation Events" with a flexible JSON metadata column. |
| **Summarizer** | **Structured Context Compressor** | **Low.** This block turns messy chat history into a dense JSON summary. It is already highly reusable for maintaining long-term state across any LLM session. |
| **Weather/Scenarios** | **Environmental Reactive Trigger** | **Moderate.** This monitors external state (Weather API) to trigger AI advice. The primitive is a "Reactive Hook" that pipes external data into a prompt template. |

---

## 2. Deep Dive: High-Value Poysis Blocks

### A. The "Semantic Tagger" Block (from [classifier.py](file:///c:/Users/subar/OneDrive/Documents/projects/concierge-1/app/agents/input/lib/classifier.py))
*   **Current State:** Checks if a user said "Spring curls" and outputs `type_3c` and `strong_definition`.
*   **Poysis Abstraction:** A logic block that accepts a **JSON Schema** of keywords-to-tags. It allows a domain expert to define "Expert Rules" that convert natural language into "Condition Flags" for the rest of the AI pipeline.

### B. The "Socratic Diagnostic" Block (from [empath_diagnostic.py](file:///c:/Users/subar/OneDrive/Documents/projects/concierge-1/app/agents/empath_diagnostic.py))
*   **Current State:** Asks a maximum of 2 questions about hair moisture before requesting permission to continue or handing off to the routine builder.
*   **Poysis Abstraction:** A "Diagnostic Loop" block. The Poysis user defines:
    1.  **Target Confidence:** (e.g., "Identify the user's primary pain point").
    2.  **Question Limit:** (e.g., "Ask max 3 questions").
    3.  **Checkpoints:** The key labels the AI is looking for to trigger a "Handoff".

### C. The "Memory Librarian" Block (from [librarian_service.py](file:///c:/Users/subar/OneDrive/Documents/projects/concierge-1/app/services/librarian_service.py))
*   **Current State:** Fetches "Vitals Summary" for a specific user.
*   **Poysis Abstraction:** A "Historical Context Provider". It queries a database for past "Truths" (Events) about a user and formats them into a "Context Sandwich" (Memory) for the LLM prompt.

### D. The "Constraint-Based Generator" Block (from [routine_prompt.py](file:///c:/Users/subar/OneDrive/Documents/projects/concierge-1/app/agents/routine/lib/routine_prompt.py))
*   **Current State:** Builds a curly hair routine using goals and profile guardrails.
*   **Poysis Abstraction:** A "Plan Architect" block. It forces the LLM to follow a strict hierarchy: **Primary Objective** > **Environmental Constraints** > **Safety Rules**. This prevents "Hallucination drift" by anchoring the AI in user-defined facts.

---

## 3. Decoupling Strategy for Poysis MVP

To turn this repository into the first Poysis "Kernel", we should focus on **Parameterization**:
1.  **Prompts:** Extract all hardcoded strings into a `prompts.json` config.
2.  **Logic:** Move the hardcoded if/else classifiers into a database-backed "Rules Engine".
3.  **IO:** Standardize every block to accept `JSON` and return `JSON`, allowing them to be "pipeable" in a No-Code UI.

**Conclusion:** The most valuable "salvageable" piece is the **Socratic Diagnostic Flow**. Most AI platforms are "One-Shot" (Question -> Answer). This codebase provides the primitive for a "Multi-Turn Diagnostic" which is much harder to build from scratch.
