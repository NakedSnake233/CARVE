# E2R scenario/application generalization

| Run | train/val/test | AC@1 | AC@3 | AC@5 | MRR |
|---|---|---:|---:|---:|---:|
| lofo_T1 | 425/87/29 | 0.3448 ± 0.0597 | 0.5862 ± 0.1243 | 0.6552 ± 0.1195 | 0.5094 ± 0.0726 |
| lofo_T2 | 460/94/22 | 0.4848 ± 0.0262 | 0.7879 ± 0.0694 | 0.8939 ± 0.1144 | 0.6598 ± 0.0380 |
| lofo_T3 | 425/87/29 | 0.1494 ± 0.0527 | 0.4713 ± 0.0527 | 0.7011 ± 0.0398 | 0.3631 ± 0.0404 |
| lofo_T4 | 388/80/36 | 0.1389 ± 0.0481 | 0.3611 ± 0.0735 | 0.6759 ± 0.1604 | 0.3463 ± 0.0238 |
| boutique_to_trainticket | 282/59/57 | 0.4152 ± 0.0442 | 0.5614 ± 0.0351 | 0.6140 ± 0.1228 | 0.5006 ± 0.0173 |
| trainticket_to_boutique | 284/57/59 | 0.3051 ± 0.0000 | 0.3898 ± 0.0000 | 0.6441 ± 0.0777 | 0.4446 ± 0.0062 |

## Limits

- LOFO tests only the held-out scenario portion of the original fixed test split, not all cases in that scenario.
- All test condition groups are controlled templates; this is not open-world or production evidence.
- Cross-application candidate/service identities are disjoint, while feature schema/model architecture are shared.
