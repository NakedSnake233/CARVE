# Interrupted matched recovery block

**Comparison status: invalid (0 complete matched pairs).**

## Preserved accounting

- Planned / attempted / confirmed injections: 2 / 2 / 2
- Repair process / confirmed mutation: 1 / 1
- Oracle statuses: ['FAIL', None]

## No-action complete trajectory

| Window | p95 ms | rps | error |
|---|---:|---:|---:|
| no_action-pre | 16.787 | 4.9974 | 0.000 |
| no_action-fault-validation | 1359.610 | 4.8765 | 0.000 |
| no_action-evaluation-0 | 1500.685 | 4.8399 | 0.000 |
| no_action-evaluation-1 | 1501.262 | 4.8754 | 0.000 |
| no_action-evaluation-2 | 1399.665 | 4.7816 | 0.000 |

No-action retained the exact injected UID through all recorded liveness probes; joint oracle FAIL and final cleanup NotFound were recorded.

## Typed partial trajectory

Pre/fault p95: 19.502 / 1399.259 ms.
Scoped deletion returned 0 at fault age 63.824 s.
The session was interrupted before any complete typed evaluation window, raw technical package, oracle, or contemporaneous cleanup record was saved.

## Limits

- The typed episode was interrupted before a complete evaluation window was saved.
- No typed/no-action business delta, effect size, recovery rate or causal comparison is defined.
- Late absence checks cannot replace contemporaneous typed technical/oracle/cleanup evidence.
- The complete no-action trajectory remains a valid standalone persistent-fault observation.
