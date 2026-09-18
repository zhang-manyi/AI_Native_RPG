# Memory retrieval comparison

Synthetic fixtures; one deterministic pass. Answer quality is evaluated separately.

| Split | Backend | Hits / gold | Macro Recall@3 | Full-pool MRR | All relevant@3 | No-answer nonempty | Query p50 ms |
|---|---|---:|---:|---:|---:|---:|---:|
| dev | hashing | 7/22 | 0.300 | 0.287 | 0.300 | 1.000 | 1.04 |
| dev | qwen | 22/22 | 1.000 | 0.950 | 1.000 | 1.000 | 826.78 |
| test | hashing | 7/24 | 0.325 | 0.371 | 0.300 | 1.000 | 1.06 |
| test | qwen | 22/24 | 0.925 | 0.854 | 0.900 | 1.000 | 805.92 |

No-answer nonempty is not false acceptance: this store always returns K entries and has no abstention threshold. Corpus encoding and model loading are excluded from query latency and reported separately in JSON.
