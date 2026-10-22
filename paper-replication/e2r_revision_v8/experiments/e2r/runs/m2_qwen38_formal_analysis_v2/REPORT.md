# E2R M2 matched-information LLM analysis

Model: `qwen3.8-flash`; incidents=108; generations=2.

| Group | Arm | Coverage | AC@1 | AC@3 | AC@5 | MRR |
|---|---|---:|---:|---:|---:|---:|
| all | reference | 1.000 | 0.3735 | 0.6358 | 0.7932 | 0.5362 |
| all | runtime | 1.000 | 0.4198 | 0.6914 | 0.8056 | 0.5730 |
| all | rule_patch | 1.000 | 0.8179 | 0.9290 | 0.9444 | 0.8768 |
| all | llm_patch_no_fallback | 0.880 | 0.6574 | 0.7932 | 0.8194 | 0.7284 |
| all | llm_patch_with_fallback | 1.000 | 0.7052 | 0.8688 | 0.9028 | 0.7924 |
| all | proposal_rank_no_gnn | 0.880 | 0.7685 | 0.8426 | 0.8472 | 0.8071 |
| all | raw_direct_rank | 0.477 | 0.3657 | 0.4074 | 0.4352 | 0.3935 |
| all | catalog_direct_rank | 0.847 | 0.7870 | 0.8102 | 0.8241 | 0.8019 |
| boutique | reference | 1.000 | 0.3939 | 0.6970 | 0.8970 | 0.5869 |
| boutique | runtime | 1.000 | 0.4727 | 0.7758 | 0.9091 | 0.6458 |
| boutique | rule_patch | 1.000 | 0.8848 | 0.9515 | 0.9818 | 0.9208 |
| boutique | llm_patch_no_fallback | 0.936 | 0.8000 | 0.8818 | 0.9061 | 0.8464 |
| boutique | llm_patch_with_fallback | 1.000 | 0.8121 | 0.9212 | 0.9606 | 0.8736 |
| boutique | proposal_rank_no_gnn | 0.936 | 0.8818 | 0.9091 | 0.9182 | 0.8986 |
| boutique | raw_direct_rank | 0.682 | 0.5182 | 0.5727 | 0.6273 | 0.5597 |
| boutique | catalog_direct_rank | 0.873 | 0.8273 | 0.8455 | 0.8727 | 0.8418 |
| trainticket | reference | 1.000 | 0.3522 | 0.5723 | 0.6855 | 0.4834 |
| trainticket | runtime | 1.000 | 0.3648 | 0.6038 | 0.6981 | 0.4974 |
| trainticket | rule_patch | 1.000 | 0.7484 | 0.9057 | 0.9057 | 0.8312 |
| trainticket | llm_patch_no_fallback | 0.821 | 0.5094 | 0.7013 | 0.7296 | 0.6060 |
| trainticket | llm_patch_with_fallback | 1.000 | 0.5943 | 0.8145 | 0.8428 | 0.7080 |
| trainticket | proposal_rank_no_gnn | 0.821 | 0.6509 | 0.7736 | 0.7736 | 0.7122 |
| trainticket | raw_direct_rank | 0.264 | 0.2075 | 0.2358 | 0.2358 | 0.2210 |
| trainticket | catalog_direct_rank | 0.821 | 0.7453 | 0.7736 | 0.7736 | 0.7605 |

## Request accounting

- Attempts: 648/648
- Usage reported: 648/648
- Normalized input+output tokens: 2106292
- Cache-inclusive token volume: 9568180
- Provider cost total: None

## Limits

- Failed/invalid/abstained outputs remain in unconditional denominators as zero.
- GNN arms use frozen patch-trained M1 scorers.
- proposal_rank_no_gnn is the proposal response ranking, not an implemented evidence reranker.
- Cache-inclusive token volume is usage volume, not a monetary bill; provider cost remains missing unless explicitly returned.
- No recovery or production claim.
