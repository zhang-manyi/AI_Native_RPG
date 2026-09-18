# Development decision before held-out evaluation

The dataset and gold labels were authored before retrieval was run. Development
results use 24 queries: 20 answerable, 4 unknown/cross-NPC, with 100 memories per NPC.

| Condition | Macro Recall@3 | MRR, full pool | Query p50 |
|---|---:|---:|---:|
| Hashing, 64 dimensions | 0.300 | 0.287 | 1.04 ms |
| Qwen3-Embedding-0.6B, 256 dimensions | 1.000 | 0.950 | 826.78 ms |

Decision: carry the existing Qwen encoder forward as a retrieval candidate with
no tuning. Keep K=3, importance weight 0.5 and all labels fixed for the held-out
run. No threshold, reranker, prompt instruction or runtime generation change is
selected from these results. Neither backend rejects unknown queries: the
no-answer nonempty rate is 4/4 for both. No runtime promotion follows from this
development result.

The Qwen condition uses the downloaded model's registered `query` prompt:
`Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:`.
This is the sentence-transformers path already used by `Qwen3Embedder`; the GGUF
path's separately defined NPC-specific instruction is not the condition tested.

Environment-only attempts `qwen-dev` and `qwen-dev-local` failed at import, before
retrieval, and are retained as unavailable reports. `qwen-dev-stable` is the
completed condition after the scientific-library compatibility fix. These are
not repeated behaviour trials from which a best score was selected.

Next: evaluate both fixed encoders on the held-out event groups, then perform
three repetitions of the two supplemental multi-turn events. The latter diagnose
write/retrieve/context/answer and disk restoration; they are not a new population
benchmark. Do not select a generation change on these probes and claim it was
validated by the held-out retrieval test.
