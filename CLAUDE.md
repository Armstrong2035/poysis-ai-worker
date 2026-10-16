# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Working Rules

These rules apply to every task in this project unless explicitly overridden. Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

1. **Think before coding.** State assumptions explicitly. If uncertain, ask rather than guess. Present multiple interpretations when ambiguity exists. Push back when a simpler approach exists. Stop when confused — name what's unclear.
2. **Simplicity first.** Minimum code that solves the problem. Nothing speculative, no abstractions for single-use code. Test: would a senior engineer call this overcomplicated? If yes, simplify.
3. **Surgical changes.** Touch only what you must. Don't "improve" adjacent code, comments, or formatting. Don't refactor what isn't broken. Match existing style.
4. **Goal-driven execution.** Define success criteria and loop until verified, rather than just following steps.
5. **Use the model only for judgment calls.** Use it for classification, drafting, summarization, extraction — not for routing, retries, or deterministic transforms. If code can answer, code answers.
6. **Token budgets are not advisory.** Per-task: 4,000 tokens. Per-session: 30,000 tokens. Summarize and start fresh if approaching budget. Surface the breach, don't silently overrun.
7. **Surface conflicts, don't average them.** If two patterns contradict, pick one (more recent / more tested), explain why, and flag the other for cleanup.
8. **Read before you write.** Before adding code, read exports, immediate callers, and shared utilities. "Looks orthogonal" is dangerous — if unsure why code is structured a way, ask.
9. **Tests verify intent, not just behavior.** Tests must encode WHY behavior matters, not just WHAT it does. A test that can't fail when business logic changes is wrong.
10. **Checkpoint after every significant step.** Summarize what was done, what's verified, what's left. Don't continue from a state you can't describe back.
11. **Match the codebase's conventions, even if you disagree.** Conformance beats taste inside the codebase. If a convention is genuinely harmful, surface it — don't fork silently.
12. **Fail loud.** "Completed" is wrong if anything was skipped silently. "Tests pass" is wrong if any were skipped. Default to surfacing uncertainty, not hiding it.
13. **Write in Simplified Technical English (ASD-STE100).** Applies to all prose: chat replies, docs, commit messages, code comments. One word, one meaning. Use the active voice. Keep sentences short — max 20 words for instructions, 25 for description. One instruction per sentence. Use the article ("the", "a") — do not drop it. No jargon, slang, or metaphor when a plain word works. Do not use `-ing` forms as nouns or adjectives when a simpler form exists. Technical names (`FargateService`, `pgvector`, `workspace_id`) stay as they are.

See `README.md` for what this project is and how it's built.
