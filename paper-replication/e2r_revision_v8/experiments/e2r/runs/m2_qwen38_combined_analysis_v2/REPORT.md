# E2R M2 matched-information LLM analysis

Model: `qwen3.8-flash`; incidents=116; generations=2.

| Group | Arm | Coverage | AC@1 | AC@3 | AC@5 | MRR |
|---|---|---:|---:|---:|---:|---:|
| all | reference | 1.000 | 0.3649 | 0.6236 | 0.7816 | 0.5266 |
| all | runtime | 1.000 | 0.4167 | 0.6839 | 0.7960 | 0.5681 |
| all | rule_patch | 1.000 | 0.8218 | 0.9339 | 0.9483 | 0.8805 |
| all | llm_patch_no_fallback | 0.879 | 0.6422 | 0.7773 | 0.8032 | 0.7139 |
| all | llm_patch_with_fallback | 1.000 | 0.6954 | 0.8563 | 0.8894 | 0.7820 |
| all | proposal_rank_no_gnn | 0.879 | 0.7716 | 0.8448 | 0.8491 | 0.8097 |
| all | raw_direct_rank | 0.474 | 0.3664 | 0.4052 | 0.4310 | 0.3923 |
| all | catalog_direct_rank | 0.849 | 0.7931 | 0.8147 | 0.8276 | 0.8069 |
| boutique | reference | 1.000 | 0.3842 | 0.6780 | 0.8814 | 0.5747 |
| boutique | runtime | 1.000 | 0.4746 | 0.7684 | 0.8983 | 0.6438 |
| boutique | rule_patch | 1.000 | 0.8927 | 0.9548 | 0.9831 | 0.9262 |
| boutique | llm_patch_no_fallback | 0.941 | 0.7966 | 0.8729 | 0.8955 | 0.8420 |
| boutique | llm_patch_with_fallback | 1.000 | 0.8079 | 0.9096 | 0.9463 | 0.8674 |
| boutique | proposal_rank_no_gnn | 0.941 | 0.8898 | 0.9153 | 0.9237 | 0.9054 |
| boutique | raw_direct_rank | 0.686 | 0.5339 | 0.5847 | 0.6356 | 0.5726 |
| boutique | catalog_direct_rank | 0.864 | 0.8220 | 0.8390 | 0.8644 | 0.8356 |
| trainticket | reference | 1.000 | 0.3450 | 0.5673 | 0.6784 | 0.4769 |
| trainticket | runtime | 1.000 | 0.3567 | 0.5965 | 0.6901 | 0.4898 |
| trainticket | rule_patch | 1.000 | 0.7485 | 0.9123 | 0.9123 | 0.8333 |
| trainticket | llm_patch_no_fallback | 0.816 | 0.4825 | 0.6784 | 0.7076 | 0.5812 |
| trainticket | llm_patch_with_fallback | 1.000 | 0.5789 | 0.8012 | 0.8304 | 0.6936 |
| trainticket | proposal_rank_no_gnn | 0.816 | 0.6491 | 0.7719 | 0.7719 | 0.7105 |
| trainticket | raw_direct_rank | 0.254 | 0.1930 | 0.2193 | 0.2193 | 0.2057 |
| trainticket | catalog_direct_rank | 0.833 | 0.7632 | 0.7895 | 0.7895 | 0.7773 |

## Request accounting

- Attempts: 696/696
- Usage reported: 696/696
- Normalized input+output tokens: 2265227
- Cache-inclusive token volume: 10278539
- Provider cost total: None

## Limits

- Failed/invalid/abstained outputs remain in unconditional denominators as zero.
- GNN arms use frozen patch-trained M1 scorers.
- proposal_rank_no_gnn is the proposal response ranking, not an implemented evidence reranker.
- Cache-inclusive token volume is usage volume, not a monetary bill; provider cost remains missing unless explicitly returned.
- No recovery or production claim.
