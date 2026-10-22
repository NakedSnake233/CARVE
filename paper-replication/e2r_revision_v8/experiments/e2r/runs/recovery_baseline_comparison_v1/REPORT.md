# Isolated no-fault calibration comparison

| Baseline | Samples | Throughput rps | Error rate | p95 ms | Canary | Runtime identity stable |
|---|---:|---:|---:|---:|---:|---:|
| baseline-calibration-v1 | 900 | 4.9972 | 0.0000 | 17.336 | True | None |
| baseline-calibration-v2 | 900 | 4.9972 | 0.0000 | 18.263 | True | True |

V2/V1 p95 ratio: 1.0535; throughput ratio: 1.0000.

- The two calibrations occurred at different wall-clock times and are not randomized treatments.
- V2 adds pre/post pod-identity and restart stability checks.
