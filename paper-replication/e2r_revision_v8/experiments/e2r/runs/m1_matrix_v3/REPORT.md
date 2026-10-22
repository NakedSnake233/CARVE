# M1 matched-training graph matrix

| Train \ Eval | Reference | Runtime | Patch | Node-only |
|---|---:|---:|---:|---:|
| reference | 0.7184 ± 0.0179 | 0.6580 ± 0.0498 | 0.6868 ± 0.0277 | 0.3621 ± 0.0994 |
| runtime | 0.7098 ± 0.0217 | 0.7730 ± 0.0326 | 0.7385 ± 0.0199 | 0.2414 ± 0.0311 |
| patch | 0.3649 ± 0.0132 | 0.4167 ± 0.0249 | 0.8218 ± 0.0132 | -- |
| node_only | 0.7241 ± 0.0376 | 0.7241 ± 0.0376 | 0.7241 ± 0.0376 | 0.7241 ± 0.0376 |

## Primary paired AC@1 contrasts

- patch->patch-minus-runtime->runtime: incident-weighted +0.0489, incident CI [0.0028735632183908063, 0.09770114942528736]; condition-macro +0.0527, condition CI [-0.012471655328798185, 0.1275510204081633]; incident sign-flip p=0.05339, Holm=0.05339
- patch->patch-minus-reference->reference: incident-weighted +0.1034, incident CI [0.04597701149425288, 0.16666666666666666]; condition-macro +0.1237, condition CI [0.03684807256235828, 0.21939058956916094]; incident sign-flip p=0.00150
- patch->patch-minus-node_only->node_only: incident-weighted +0.0977, incident CI [0.04885057471264368, 0.14942528735632185]; condition-macro +0.1276, condition CI [0.04761904761904762, 0.21543367346938758]; incident sign-flip p=0.00020, Holm=0.00040

## Application and changed-input strata

| Group | n | Ref | Runtime | Patch | Node-only |
|---|---:|---:|---:|---:|---:|
| all | 116 | 0.7184 | 0.7730 | 0.8218 | 0.7241 |
| boutique | 59 | 0.7006 | 0.8249 | 0.8927 | 0.7175 |
| boutique:T1 | 15 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| patch_vs_runtime_unchanged | 39 | 0.7521 | 0.7350 | 0.7009 | 0.7009 |
| boutique:T2 | 11 | 0.5758 | 0.6667 | 1.0000 | 0.5758 |
| patch_vs_runtime_changed | 77 | 0.7013 | 0.7922 | 0.8831 | 0.7359 |
| boutique:T3 | 15 | 0.4000 | 0.6889 | 0.5778 | 0.3111 |
| boutique:T4 | 18 | 0.7778 | 0.8889 | 1.0000 | 0.9074 |
| trainticket | 57 | 0.7368 | 0.7193 | 0.7485 | 0.7310 |
| trainticket:T1 | 14 | 0.8810 | 0.8333 | 0.8571 | 0.8095 |
| trainticket:T2 | 11 | 0.8788 | 0.8485 | 0.8182 | 0.8788 |
| trainticket:T3 | 14 | 0.1667 | 0.1667 | 0.2619 | 0.1905 |
| trainticket:T4 | 18 | 0.9815 | 0.9815 | 1.0000 | 1.0000 |

## Limits

- Same architecture/hyperparameters and fixed split; validation selection occurs within each training condition.
- node_only retains patch nodes/features/candidates and removes message-passing edges.
- Incident-weighted and condition-macro contrasts are distinct estimands; recurring templates limit unseen-condition significance claims.
