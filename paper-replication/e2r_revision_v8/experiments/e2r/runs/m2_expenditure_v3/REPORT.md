# E2R M2 request and token accounting

| Run | Model | Attempts | Transport | Strict valid | Usage rows | Normalized tokens | Cache-inclusive volume | Request seconds | Provider cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| m2_qwen38_pilot_formal_v1 | qwen3.8-flash | 3 | 3 | 0 | 3 | 20456 | 42984 | 119.13 | not reported |
| m2_qwen38_pilot_formal_v2 | qwen3.8-flash | 48 | 48 | 35 | 48 | 158935 | 710359 | 337.47 | not reported |
| m2_qwen38_formal_v1 | qwen3.8-flash | 648 | 648 | 476 | 648 | 2106292 | 9568180 | 4509.65 | not reported |
| m2_deepseek41_compat_v1 | deepseek-v4.1-flash | 3 | 0 | 0 | 0 | unknown | unknown | 2.15 | not reported |
| m2_gpt56luna_compat_v1 | gpt-5.6-luna | 3 | 3 | 3 | 3 | 29558 | 29558 | 19.91 | not reported |

Total attempts: 705; normalized input+output tokens on reported rows: 2315241; cache-inclusive reported volume: 10351081; provider cost: not reported.

- Provider cost is null when the gateway did not report a cost; no alias-based price is fabricated.
- Cache-inclusive token volume adds cache creation/read fields to normalized total_tokens and is not a monetary bill.
- A failed attempt with missing usage is unknown usage, not zero; aggregate token volumes sum reported rows only.
- Failed transports and the stopped Qwen v1 compatibility pilot remain in attempt totals.
