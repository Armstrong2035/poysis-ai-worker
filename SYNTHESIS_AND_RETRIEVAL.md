# Synthesis and Retrieval: From Transcript to Answer

*An engineering decisions record. Written in ASD-STE100 Simplified Technical English.*

This article traces the path from a raw transcript to a grounded answer. It follows the
decisions in the order we made them. Each decision has three parts: the problem, the
options, and the choice. Clustering feeds this pipeline but has its own article, because
we have not solved it yet.

The code is in four files:
[transcript.py](app/primitives/consolidation/processors/transcript.py),
[knowledge/engine.py](app/primitives/knowledge/engine.py),
[vector_store.py](app/primitives/knowledge/vector_store.py), and
[chat.py](app/api/chat.py).

---

## 1. The problem

A creator has a large body of work. One channel can hold more than 500 videos. A person
cannot search this work by hand.

We want a bot that does two jobs:

- It points the user to the exact source and the exact moment.
- It gives one answer that draws from the whole body of work.

The first job is **retrieval**. The second job is **synthesis**. This article is how we
built each one.

---

## 2. Keep the time, not only the words

The first decision was about what to store.

A transcript is a list of timed segments. Each segment has a start time, a duration, and
a text. Most systems keep only the text. We decided to keep the time too.

The reason is direct. The user must jump to the exact moment in the video. So each chunk
holds a start time and an end time in milliseconds. Each chunk also holds a deep-link with
a `?t=` value. The user clicks the link and the video starts at that second. You can see
this in `_make_chunk` in [transcript.py](app/primitives/consolidation/processors/transcript.py).

---

## 3. One transcript processor for all sources

YouTube is not the only source with timed segments. Drive recordings and meeting exports
have them too.

We had two options:

- Write a transcript reader inside the YouTube code.
- Write one processor that any source can use.

We chose the second option. The `TranscriptProcessor` takes a list of segments in the
shape `{start, text, duration}`. It does not know or care about the source. Any connector
that gives this shape can use it. This keeps the timestamp logic in one place.

---

## 4. Do not use the sentence splitter for transcripts

For normal text documents, the system uses the LlamaIndex `SentenceSplitter`. It cuts text
into blocks of 512 tokens with an overlap of 50. This works well for documents.

It does not work for transcripts. The sentence splitter cuts at sentence limits. It does
not keep the timestamps. For a transcript, the timestamp is necessary. So the transcript
path skips the sentence splitter. It keeps the time boundaries of each segment instead.
See `embed_and_store` in [knowledge/engine.py](app/primitives/knowledge/engine.py).

---

## 5. Cut chunks at topic boundaries

This is the core decision. It is what we call semantic chunking.

**The naive method.** Cut the transcript into fixed windows of 60 seconds. This is simple.
But a topic rarely changes at a clean minute. So a 60-second chunk often holds the end of
one idea and the start of the next. This makes retrieval less exact.

**The better method.** Cut the transcript where the topic changes, not where the clock
changes. The system does this in two passes:

1. **Pass 1 — find the boundaries.** The system embeds the 60-second pre-chunks. Then it
   measures the cosine similarity between each pair of adjacent pre-chunks. A high value
   means the same topic. A low value means a topic change. The system smooths the values
   to remove noise. Where the value falls below the threshold of 0.75, the system cuts.
2. **Pass 2 — build and store the chunks.** The system merges the pre-chunks of one topic
   into one topic chunk. It embeds each topic chunk again. Then it stores the topic chunk.

The 60-second window did not go away. It became the raw material for the topic search, not
the final unit. The merged topic chunk keeps the start time of the first pre-chunk and the
end time of the last pre-chunk. So the deep-link still points to the start of the topic.
The functions are `_find_topic_groups` and `_merge_transcript_chunks` in
[knowledge/engine.py](app/primitives/knowledge/engine.py).

One note on the math. OpenAI embeddings are unit vectors. For unit vectors, the dot product
is the cosine similarity. So the similarity check is one multiplication, and it is fast.

---

## 6. Store the text with the vector, and fix the index filter

The system embeds each chunk with the `text-embedding-3-small` model. It stores each vector
in Supabase with pgvector. It uses an HNSW index for the search.

**Store the text.** The system stores the chunk text in the vector metadata, in the `_text`
field. So retrieval gets the vector and the text in one query. It does not need a second
store.

**Fix the index filter.** We found a fault in the search. The HNSW index sorts only by the
vector distance. It does not know the namespace. Postgres applies the namespace filter
after the index search. With the default search width, the index returns about 40 near rows.
Only the rows in the correct namespace stay. A small workspace shares a large table. So the
search returned only about 5 rows for any request, and the number fell as more workspaces
came.

The fix is one command: `SET LOCAL hnsw.iterative_scan = relaxed_order`. The index now
walks the graph until enough rows pass the namespace filter. The result changed from 5 rows
to 48 rows, and the query is a little faster. The system sorts the rows again by score,
because the relaxed order is not exact. See `query_vectors` in
[vector_store.py](app/primitives/knowledge/vector_store.py).

---

## 7. Over-fetch, then cut at the score gap

Retrieval is the shared core of both modes. It has three steps.

**Step 1 — over-fetch.** The system fetches six times the requested `top_k`. This gives the
later steps many candidates from many sources.

**Step 2 — remove weak matches.** A minimum score of 0.4 removes matches that are not
relevant. This guards the case where the knowledge base has no answer.

**Step 3 — cut at the natural limit.** We had two options here. Keep a fixed number of
results, or find the natural limit. A fixed number keeps too many results for a narrow
question and too few for a broad one. So we chose the score-gap detector. It finds the
largest drop in the sorted scores. It cuts the list at this drop. This point is the edge
between the relevant chunks and the rest. See `detect_score_gap` in
[vector_store.py](app/primitives/knowledge/vector_store.py).

---

## 8. Spread the results across sources

One video can hold many strong chunks. Without a control, that one video fills the whole
answer. The answer then speaks from one talk, not the whole body of work.

So the system diversifies the results. It puts the chunks into one bucket for each source.
It keeps the score order in each bucket. Then it takes one chunk from each bucket in turn.
This spreads the answer across many sources. See `_diversify` in
[chat.py](app/api/chat.py).

---

## 9. Let the question choose the mode

At first, chat had one mode: synthesis. It gave an essay for every question. But not every
question wants an essay.

Look at two questions:

- "Find the sermon about fasting." The user wants a pointer to a source.
- "What does he teach about faith?" The user wants an answer across many sources.

The first is retrieval. The second is synthesis. The difference is in the intent of the
question, not in the topic. Embeddings measure the topic. So embeddings are the wrong tool
for this choice.

We use a small, low-cost model to read the question and select the mode. Three decisions
shape this:

- **The model runs at the same time as the retrieval.** The retrieval waits for the
  network. So the model adds no extra time to the response.
- **The model is a judgment call, not a route.** The choice depends on intent, which code
  cannot read. So the model is the correct tool here.
- **The default is synthesis.** If the model fails, the system uses synthesis. So the
  classifier can never make a question worse than before.

The client can also send the mode. Then the system skips the classifier. See
`_classify_intent` in [chat.py](app/api/chat.py).

The mode also matters for external clients. A smart client, such as Claude or ChatGPT, can
synthesize by itself and wants only retrieval. A simple client, such as the Poysis
dashboard, needs the server to synthesize. So the system exposes both modes as tools.

---

## 10. Give the model a synthesis contract

The synthesis prompt caused the hardest problem, and its history explains the current design.

**The failure.** The first prompt told the model to answer only from the exact text of the
excerpts. But the chunks are excerpts from longer talks. The exact answer is rarely present
in one chunk. So the model refused many real questions. It returned "I couldn't find
relevant information" for good questions such as "how do I improve my prayer life."

**The fix.** The new prompt is a contract, not a limit. It tells the model to do three
things:

- Combine the excerpts and build one coherent view.
- Speak as the interpreter of one creator's work, in a warm and direct voice.
- Stay grounded in the excerpts, and refuse only when the excerpts are truly unrelated.

The contract permits inference across the excerpts. It does not permit invention. So the
model can answer a real question without a verbatim source, but it cannot add outside facts.
See `_synthesis_contract` in [chat.py](app/api/chat.py).

**Layer the persona on top.** Each bot has its own voice instructions. Before, these
instructions replaced the whole prompt. This removed the grounding rules, and each bot lost
them in silence. Now the system adds the persona on top of the contract. It states the
contract last, where the contract has the most force. See `_build_system_prompt` in
[chat.py](app/api/chat.py).

---

## 11. Keep grounding in code, not only in the prompt

A prompt cannot stop every invention. So the system also checks the output in code.

The response can include one key quote. The quote must come from a real excerpt. The
function `_quote_grounded` compares the words of the quote to the words of each excerpt. It
finds the excerpt with the most shared words. If the overlap is below 60 percent, the system
drops the quote. So a quote that no source contains never reaches the user. See
`_quote_grounded` in [chat.py](app/api/chat.py).

This is the pattern for the whole pipeline. Code enforces the rules that code can enforce.
The prompt does only the work that needs judgment.

---

## 12. The response contract

The endpoint returns one streamed response. It serves both modes on the same channel.

- **Synthesis mode.** The system streams the answer text first. Then it sends the sources
  as `__SOURCES__`. Then it sends the meta block as `__META__`. The meta block holds the
  scale, the themes, and one grounded key quote.
- **Retrieval mode.** The system sends `__MODE__` first, so the client shows a source list
  and not a typing sign. Then it sends the sources and a smaller meta block. Retrieval mode
  makes no model call for prose. So it is faster and cheaper.

Each meta part degrades on its own. If one part is slow or fails, the other parts still
arrive. The system also logs each turn as a topic event, for the knowledge graph and for
training data.

---

## 13. The throughline

One rule shaped every decision: **code decides where it can, and the model decides only the
judgment calls.**

Code does the deterministic work:

- Where to cut a transcript — the topic boundary and the score gap.
- Which sources to show — the diversify step.
- Whether a quote is real — the overlap check.

The model does only the two true judgment calls:

- What the question intends — retrieval or synthesis.
- How to write the answer — the synthesized prose.

This split keeps the system fast, cheap, and honest. It also makes each part easy to test
on its own.
