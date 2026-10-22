# E2R 审稿复核数据与公平性清单

- 当前 topology campaign：798 个案例；与历史 760 条七故障记录分开。
- 应用：{'boutique': 400, 'trainticket': 398}；scenario：{'T1': 199, 'T2': 150, 'T3': 199, 'T4': 250}。
- candidate 数：{'min': 13, 'median': 13.0, 'max': 65, 'mean': 38.93483709273183}。
- 无诊断期 telemetry 记录：398（当前均来自 TrainTicket）。
- 原始 798 campaign 的请求负载未写入 incident metadata，因此记为 unknown。
- 计划中的 isolated Boutique workload 固定 `USERS=10`、镜像 `v0.10.1`；真实运行仍需保存 warm-up、throughput、error 与 p95。

## 公平性契约

- **shared_across_graph_arms**: ["diagnostic cutoff", "observation snapshot", "candidate services", "base feature function", "edge weight=1", "split", "per-seed frozen scorer"]
- **intentional_differences**: {"reference": "pre-event graph", "runtime": "diagnosis-time observed graph", "rule_patch": "validated supported operations applied to reference"}
- **ground_truth_use**: ["pre-inference candidate-presence eligibility check", "recorded fault_end_time cutoff validity check", "split", "supervised loss", "evaluation"]
- **forbidden_model_inputs**: ["target/scenario/fault params", "original incident name", "ground truth", "post-recovery state", "old cached plan"]
- **external_baseline_adapter_requirements**: ["same snapshot facts", "same candidate list", "same held-out IDs", "same LLM alias/temperature/request budget when applicable", "same failure denominator", "same mutation namespace and safety policy for recovery"]
