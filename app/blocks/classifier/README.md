# Block: Categorization (The Intent Sorter)

The Categorization block is a specialized logic node that converts messy natural language into structured "Condition Flags" for the AI Orchestrator.

## Architectural Spec

### 1. Inputs (Standard JSON)
```json
{
  "text": "The input string to categorize",
  "schema": {
    "labels": {
      "billing": "Queries about invoices, payments, or credit cards.",
      "technical_support": "Issues with the software, bugs, or crashes.",
      "sales": "Interest in buying, pricing, or demos."
    },
    "threshold": 0.7
  }
}
```

### 2. The Mechanic (Semantic Proximity)
This block does **not** use if/else keyword matching. It uses the **Embedder Primitive**:
1. It generates a vector for the `text`.
2. It generates vectors for each of the `labels`' descriptions.
3. It performs a **Cosine Similarity** check.
4. If the top match is above the `threshold`, it returns that label.

### 3. Outputs (Standard JSON)
```json
{
  "label": "billing",
  "confidence": 0.89,
  "met_threshold": true,
  "flags": ["is_billing"]
}
```

## Reusability
Because the `labels` are provided as a JSON schema, this block is domain-agnostic. 
- **Customer Support**: Use support categories.
- **Legal**: Use document types.
- **Medical**: Use symptom types.
