# E2R 审稿意见驱动的完整实验计划

**Problem**: 现有证据混合了观测增益、图更新、训练条件、LLM 提案、回退和恢复动作，无法回答各组件是否必要，也不足以支持域外或生产声明。
**Method Thesis**: 在严格相同的诊断时刻观测、候选集和预算下，E2R 的可验证图更新应带来可归因的 RCA 增益；只有带证据、受限且可独立验证的 typed action 才能获得恢复贡献。
**Date**: 2026-09-26
**Scope**: 只实现和评估 E2R；外部 baseline 由用户工作流提供，本计划只输出公平接口与核验门。

## Claim Map

| Claim | Why It Matters | Minimum Convincing Evidence | Linked Blocks |
|---|---|---|---|
| C1：GraphPatch 表示在匹配观测与训练条件下改善根因排序 | 回应“GraphPatchPlans 是否有用”及组件贡献不清 | 3×3 train/eval 图条件矩阵；node/relation 控制；同事实+同 LLM 的 raw-direct、catalog-direct、proposal+graph 对照；真实 held-out GT、三 seed、失败不删 | B0–B2 |
| C2：证据绑定 typed recovery 在明确可恢复域内提高独立验证的恢复率 | 回应动作、安全、post-repair oracle 与因果归因 | 隔离 namespace；typed/direct-template/no-action；服务×类别干预只在动作实际使用该因子时做；业务状态+资源状态+故障消失三重 oracle；失败、拒绝、回滚分别报告 | B4 |

**Anti-claims to rule out**:

- 增益只来自更多/更丰富的输入，而非图表示。
- 增益只来自 patch-trained scorer 对自身输入分布的偏好。
- 无效 LLM 输出被 deterministic fallback 隐藏。
- 删除 Chaos 对象后的自然恢复被计作 service-aware remediation。
- 学术 benchmark 结果被外推为 production safety、任意未知故障或开放世界恢复。

## Reviewer-comment coverage matrix

| 审稿意见 | 已有证据 | 必须补充的实验/产物 |
|---|---|---|
| 主要组件贡献不清；同 telemetry+同 LLM、无 GraphPatch | M1 固定 patch-trained scorer；M2 双路线实现 | B1 训练条件矩阵；B2 新增 `catalog-direct`，与 proposal 使用完全相同事实和 catalog；GraphPatch/GNN 与 LLM 直接排序分开 |
| 输入、候选、probe、拓扑、安全策略不公平 | observation-only snapshot、统一候选 | B0 每臂输入字段/候选/权限/预算清单；逐请求保存 route 和模型；baseline 只在相同接口后比较 |
| 图增益可能来自 added nodes 而非 relations | M1 仅 reference/runtime/full patch | B1 node-only、existing-node relation-only、full patch 控制 |
| 未见/复合/开放故障 | 当前 798 例只有 T1–T4 topology scenarios | B3 leave-one-scenario-out、跨应用迁移、明确标注的 synthetic two-root；未知操作必须 abstain，不能宣称真实 open-world |
| 仅两个学术应用、缺生产规模 | 已知 Boutique13 / TrainTicket65 候选 | B3 13→65→130→260 service synthetic scaling；只做 overhead/容量结论，不声称生产效果 |
| 成本和效率不足 | M1 GPU 时延、M2 请求 usage 框架 | B2/B3 分解序列化、API、validation、graph execution、GNN、repair planning/render/execute/verify；calls、失败、usage、provider cost、固定等待分别报告 |
| workload 未说明 | isolated Boutique `USERS=10`；raw metadata 有注入参数 | B0 生成 workload/injection inventory；live 固定 warm-up、用户数和 observation horizon；TrainTicket 历史 workload 缺失则明确 unknown |
| recovery agent、安全、证据、不可测 predicate 不清 | typed schemas、verification bridge | B4 修复 namespace/target 绑定和真实 YAML/action 验证；定义 evidence reference、measurable predicate 和独立 oracle；给出逐步案例 |
| 动作空间过于宽泛 | 当前 action enum/模板 | B4 输出 fault→allowed action→precondition→rollback→oracle 映射；未映射类别只能 WaitAndVerify/abstain |
| 多根因、并发、stateful | 无真实证据 | B3 synthetic multi-root 仅 proxy；B4 在 isolated benchmark 做少量双故障，Redis 仅无持久数据 benchmark，严禁 production 外推 |
| latency 混入 40s wait/restore | run_fulllifecycle 已分阶段 | B4 汇总 computation 与 fixed wait/cleanup 分栏，禁止嵌套重复相加 |
| mABC TrainTicket AC@5<AC@3 | 自动审计已确认3行无效 | B0 发布前 gate；逐事件预测到位前这些 cell 不参与胜负结论，不交换/夹紧数值 |
| replication package 为空 | 本地代码/数据/结果已存在 | B0/B5 生成匿名可运行 artifact：源码、快照或生成器、locked env、命令、原始输出、校验报告；不含密钥/主机身份 |
| 缺近期 graph/agent baseline | 用户正在实现 | 输出 matched-input adapter contract 和 evidence schema；不伪造/替代用户 baseline 结果 |

## Paper Storyline

- **Main paper must prove**: B1 匹配训练的图机制；B2 同事实同模型的 LLM/GraphPatch 必要性；B4 明确可恢复域内的 typed/no-action 因果对照；成本和失败分母。
- **Appendix can support**: LOFO/cross-app、synthetic multi-root、规模曲线、详细 prompts/plans、逐事件失败分析。
- **Experiments intentionally cut**: production deployment、未经独立标签的生产事故、任意开放世界修复、对用户 baseline 的代跑或替代。

## Experiment Blocks

### B0：完整性、公平性与可复现入口（MUST）

- **Claim tested**: 后续比较的输入、标签、候选、预算和结果可审计。
- **Dataset/task**: 798 observation-only snapshots；历史760记录单独保留。
- **Outputs**:
  1. 每应用/scenario/category/service、候选/节点/边/telemetry volume、注入参数 inventory；workload 已知/unknown 字段。
  2. 每个 arm 的信息/候选/模型/权限/安全策略矩阵。
  3. AC 单调性、rank 重算、split/source-dir 去重 gate。
  4. 匿名 artifact staging（无 API/SSH/kubeconfig）。
- **Decision gate**: 任一 label/future leak、分母变化、AC 单调性或 arm 输入不匹配即阻止结果写入论文。

### B1：图表示与训练条件隔离（MUST）

- **Dataset/split**: 固定 566/116/116；Boutique/TrainTicket 分层报告。
- **Training matrix**: train ∈ {reference, runtime, rule-patch} × eval ∈ {reference, runtime, rule-patch}；seeds 42/43/44；相同 GraphSAGE 128×2、优化器、patience 和 split。
- **Structural controls**: `node_only`（保留 full-patch 节点、节点特征与候选，但删除全部 message-passing edges）、operation-group deletion（按依赖一致地删除 identity/blackhole-mismatch/version groups）、`full_patch`；共享 base features/candidates/unit weights。`node_only` 回答传播是否必要，operation deletion 回答哪类更新起作用。
- **Observation controls**（Boutique）: full、no-log、no-trace/call-edge、no-metric、topology-only；TrainTicket 作为自然 topology-only stratum 单独报告。
- **Metrics**: AC@1/3/5、MRR、per-incident paired deltas、有效输入相等性、train/val curves、materialization/train/inference time。
- **Success criterion**: matched-train patch 对 matched-train runtime/reference 有稳定增益；或 full patch 相对 node/relation controls 显示不可由单一表示变化解释。否则 C1 收缩/否定。
- **Failure interpretation**: 仅 patch-trained substitution positive 不再算充分机制证据。

### B2：同事实、同 LLM 的完整 GraphPatch 对照（MUST）

- **Primary model**: OpenCode Go `qwen3.8-flash`；先 8 个 app×scenario 平衡事件×2 generations×3 routes（48 requests）冻结 prompt 与有效性，再在剩余 108 个 test incidents 上跑 2 generations×3 routes（648 requests）。`deepseek-v4.1-flash` 与 `gpt-5.6-luna` 先做三路线 compatibility sanity，完整重复为 NICE，除非论文保留跨模型稳健性 claim。
- **Routes per incident**:
  1. `raw_direct`: cutoff facts，禁止 catalog/GraphPatch。
  2. `catalog_direct`: 与 proposer 相同 facts+supported catalog，只输出完整 ranking，不产生 plan。
  3. `proposal`: 相同 facts+catalog，输出完整 ranking+operation IDs；统一 acceptance gate 后得到 no-fallback graph score；无效时另报 rule fallback。
  4. `rule_patch` 和 M1 reference/runtime 为非 LLM controls，不消耗请求。
- **Fairness**: 相同候选、事实字段、模型、temperature、max output、会话政策；catalog 差异显式成为被测变量。proposal 的 ranking 是 no-GNN readout，不称为 evidence reranker。
- **Metrics**: 无条件 AC/MRR（失败=0）、valid plan/ranking/abstain/fallback、paired incident delta、generation agreement、calls/tokens/provider cost/API time/validation/graph/GNN time。
- **Budget/stop**: Qwen pilot 首事件三请求 sanity；任一路由不满足严格 schema 即停并保留失败。pilot 48 与 formal 648 分成两个 immutable campaign；旧网关 8 次失败单列。DeepSeek/GPT sanity 各 3 请求，未经新的显式预算不扩展。禁止隐藏 retry/模型切换。
- **Success criterion**: proposal+graph 相对 catalog-direct 的 paired improvement 且 no-fallback coverage 足够；否则不能说 GraphPatch 必要。raw-direct 只衡量 catalog preprocessing 的总效应。

### B3：泛化、复合与规模边界（MUST 的离线部分；live 双故障为 NICE）

- **LOFO**: 每次 hold out T1/T2/T3/T4，其他 scenario train/val，三 seeds；真实 GT。
- **Cross-app**: Boutique→TrainTicket、TrainTicket→Boutique，三 seeds；服务名不重叠条件显式报告。
- **Synthetic multi-root**: 同应用、不同服务的两份观察合成；set Recall@2/5、MAP；标记 `synthetic_proxy`，不进入主性能表。
- **Scale**: 从真实模板复制为 13/65/130/260 services，telemetry 量 1×/4×/16×；测 serialization、catalog、execution、GNN memory/latency；不测“生产准确率”。
- **Unknown handling**: unsupported operation/category、缺证据和不可测 predicate 的 acceptance/abstention test；拒绝率单独报告。
- **Success criterion**: 用于限定适用边界，不设置必须为正的结果。

### B4：恢复归因、安全与独立 oracle（MUST，但受安全 gate 约束）

- **Precondition before cluster mutation**:
  1. structured namespace/target 与渲染命令强绑定；禁止命令内其他 namespace/target；
  2. playbook 必须真实 YAML parse，action/module allowlist；
  3. isolated manifest 加运行身份、权限/网络边界；不得触碰 `online-boutique`；
  4. workload baseline、清理和 snapshot restore dry-run/单案例通过；
  5. fresh code review PASS。
- **Workload**: isolated Online Boutique，`USERS=10`，固定 warm-up/settle/observation window；每次 fresh restore；记录实际 request throughput/error/p95。
- **Typed action map**: fault category→允许 action→服务是否实际参与→precondition→rollback→可测 predicate。未映射项只能 WaitAndVerify。
- **Main arms**: evidence-bound typed E2R、same diagnosis direct-template、no-action；相同 mutation namespace、安全 policy 和 verify horizon。
- **Diagnostic factorial**: correct/wrong service×correct/wrong category 仅在 action 同时实际使用 service/category 的 fault 上运行；Chaos-object deletion 不用于证明 service attribution。
- **Independent post-repair oracle**: injected fault object absent/expired；目标资源恢复预期；pods ready/endpoints healthy；固定 workload 成功率/error/p95 回到 pre-fault tolerance。缺任何必要测量为 `unmeasurable`，不记成功。
- **Scope**: 先 1 fault×1 arm sanity，再每个可恢复 fault 3 repetitions；no-action 每 fault ≥3。少量双故障作为 benchmark stress，独立表；无 production safety 声明。
- **Timing**: injection、fixed settle、collection、RCA、LLM、plan validation、render、execution、verify wait、verify、restore、cleanup 分栏；嵌套时间不相加。

## Run Order and Milestones

| Milestone | Goal | Runs | Decision Gate | Cost | Risk |
|---|---|---|---|---|---|
| M0 | B0 inventory/monotonicity/fairness/artifact staging | deterministic local | 全部 integrity gates PASS；历史 mABC invalid cells保持 unresolved | <1 CPU h | 历史 workload/逐事件 baseline 缺失 |
| M1a | B1 training-condition smoke | 40 cases×3 train variants×seed42×2 epochs | 无 NaN、split/row coverage一致 | <5 GPU min | runner 参数错误 |
| M1b | B1 full 3×3 matrix | 2新增train variants×3 seeds；复用已审计 patch models | 所有 checkpoints/predictions齐全 | 10–20 GPU min | matched training may remove gain |
| M1c | structural/modality controls | fixed checkpoints×all116 | common features/candidates verified | <10 GPU min | equal-input cells dilute effect |
| M2a | Qwen pilot + 其他模型 compatibility sanity | Qwen48；DeepSeek3；GPT3 | 三 route strict valid；否则对应模型停 | provider-dependent | protocol/schema/quota |
| M2b | B2 formal LLM | Qwen remaining108×2×3=648 requests | 无 unknown attempts；失败留分母；formal预算独立记录 | 以真实 usage/cost为准 | quota/long prompts |
| M2c | frozen GPU scoring/analysis | accepted plans×3 seeds | exact row keys、same checkpoints、metrics重算 | <10 GPU min | near ties |
| M3 | B3 LOFO/cross-app/scale/proxy multi-root | 18+ GPU trainings + deterministic benches | 每类结果按 claim ceiling标注 | 30–60 GPU min | domain shift severe |
| M4a | B4 safety + isolated deployment | unit/mock/dry-run/one live no-action | 全部安全 gate PASS | 1–2 CPU h | namespace/command escape |
| M4b | live recovery | mapped faults×3 arms×3 reps；有意义 factorial | initial fault与 workload degradation被验证；cleanup通过 | 4–8 h cluster | contamination/checker ambiguity |
| M5 | artifact freeze + experiment audit | deterministic + fresh reviewer | claims only from real files | <1 h | secret or stale artifact |

## Compute and Data Budget

- **GPU**: RTX4090D，通过既有 SSH worker；只运行 B1/B2 frozen scoring/B3 training。GPU 空闲门仍为连续两次 `<500MiB` 且 `<5%`。
- **API**: OpenCode Go 三模型；先 sanity；完整额度由 campaign manifest 固定，实际 calls/usage/cost 决定是否继续稳定性重复。
- **Cluster**: 本机 k3s，live 仅 isolated namespace；不得运行在用户 baseline 的 `online-boutique`。
- **Data**: 798 当前 topology incidents（400/398），不是历史 760 七故障 campaign；两者不得合并叙述。
- **Biggest bottleneck**: B4 安全/独立 oracle，而非 GPU。

## Risks and Mitigations

- **Patch-only training bias**: 3×3 train/eval matrix。
- **Catalog 给 proposer 额外语义**: 加 `catalog_direct`；raw-direct 只作总效应。
- **Fallback 隐藏失败**: no-fallback 为主；fallback 单独 coverage/性能。
- **Future/label leak**: allowlist 与 prompt recursive-key test；GT 只在训练/评分。
- **TrainTicket 无 telemetry**: 独立 topology-only stratum；不混称 multimodal。
- **重复模板泄漏**: LOFO/cross-app；固定 split 结果只称 in-distribution。
- **Live 自然恢复**: no-action + fresh injection + initial degradation gate。
- **不安全命令**: structured binding + YAML/module parse；未通过前禁止 apply/execute。
- **API/cluster 中断**: append-only request/run journal，unknown outcome 停止，不自动重发。

## Final Checklist

- [x] 两个核心 claim 与 anti-claims 已冻结
- [x] 审稿意见逐项映射到实验/产物
- [ ] B0 完整性清单与匿名 artifact staging
- [ ] B1 matched-training、node/relation、modality controls
- [ ] B2 三模型同输入 full comparison 与 cost
- [ ] B3 LOFO/cross-app/scale/proxy multi-root
- [ ] B4 safety gate 与 independent post-repair oracle
- [ ] live 恢复只在 safety gate PASS 后启动
- [ ] 新结果经过 experiment-audit / result-to-claim 后再进入论文
