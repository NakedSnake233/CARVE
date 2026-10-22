Wrote [recompute_noaction.py](${PROJECT_ROOT}/experiments/e2r/runs/recovery_matched_frontend_cpu_v1/audit_v1/recompute_noaction.py) and [recompute_noaction.json](${PROJECT_ROOT}/experiments/e2r/runs/recovery_matched_frontend_cpu_v1/audit_v1/recompute_noaction.json): **212 comparisons, 0 mismatches**.

All six baseline and five no-action windows were recomputed. Baseline: 900 samples, mean **18.11247389090972 ms**, pooled p95 **18.263094127178192 ms**, median throughput **4.997163960150042 rps**.

| No-action window | Mean ms | p95 ms | Throughput rps |
|---|---:|---:|---:|
| Pre | 15.676435303563872 | 16.786579973995686 | 4.99743826517097 |
| Fault validation | 878.7293362586448 | 1359.6097487024963 | 4.876480284977113 |
| Evaluation 0 | 946.3970578213533 | 1500.6846310570836 | 4.83986119409073 |
| Evaluation 1 | 904.3455117785683 | 1501.2616850435734 | 4.875425808027974 |
| Evaluation 2 | 964.091991915678 | 1399.6652089990675 | 4.781646495579526 |

Each window has 150 attempts, 150 recorded successes and zero errors. Evaluation windows fail the p95 threshold **21.91571295261383 ms**; other recorded business predicates pass. Independent oracle reconstruction exactly matches **FAIL**, with zero consecutive passing windows.

Raw technical probes yield `fault_absent=False`, `resource_restored=True`, `ready_endpoints=True`. UID `7d2f31e8-4d0f-4632-9ff8-f1e2d66c6045` remains active in all six recorded observations. Recorded ages satisfy technical **158.11121950577945 < 170 s**, final **158.11700530489907 < 165 s**, and cleanup **158.68644268298522 < 175 s**. Cleanup absence is confirmed; 12 pod UIDs, 24 ready containers and zero restarts match block/baseline snapshots.

Evidence: metric definitions [workload_oracle.py:153](${PROJECT_ROOT}/rca_bench/workload_oracle.py:153), baseline aggregation [run_recovery_baseline.py:118](${PROJECT_ROOT}/scripts/e2r/run_recovery_baseline.py:118), evaluation [00_no_action.json:547](${PROJECT_ROOT}/experiments/e2r/runs/recovery_matched_frontend_cpu_v1/episodes/00_no_action.json:547), technical probes [00_no_action.json:1141](${PROJECT_ROOT}/experiments/e2r/runs/recovery_matched_frontend_cpu_v1/episodes/00_no_action.json:1141), cleanup/runtime [00_no_action.json:1342](${PROJECT_ROOT}/experiments/e2r/runs/recovery_matched_frontend_cpu_v1/episodes/00_no_action.json:1342).

Limits explicitly recorded: latency arrays are sorted observations with aggregate successes; per-request statuses/timestamps and raw canary evidence are absent. Clock readings remain recorded inputs. Deadline inequalities were independently checked, not clock measurements reconstructed.
