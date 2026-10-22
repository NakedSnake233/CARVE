# E2R 补充实验阶段结果

日期：2026-09-26。执行计划：`refine-logs/EXPERIMENT_PLAN.md`。以下均为新补充实验；原论文历史结果未覆盖。

## M0：范围与完整性

- 当前 observation-only 数据为 798 个 T1–T4 事件，固定 split 566/116/116；与论文历史 760 个七故障事件分开。
- Boutique/TrainTicket 候选数分别为 13/65；TrainTicket 当前 398 个观察均无诊断期遥测。
- 原表 mABC TrainTicket 三行存在 `AC@5 < AC@3`，保持 unresolved，不据此修正历史数值。

## M1：匹配训练与组件控制

三种子匹配训练的 AC@1 为：reference 0.7184、runtime 0.7730、rule patch 0.8218、无消息传递 node-only 0.7241。Patch−runtime 为 +0.0489，但 Holm 校正符号置换 `p=0.05339`，仅作描述性增益；patch−node-only 为 +0.0977，校正 `p=0.0004`。完整性审计为 artifact PASS / scope WARN。

冻结 patch scorer 的 Boutique 特征删除 AC@1：full 0.8927、no-log 0.8983、no-trace-features 0.8079、no-metric-features 0.5480、topology-only 0.6045。除 topology-only 外，删除仍保留混合来源 call edges，因此它们是敏感性分析，不是完整模态移除。操作组删除 AC@1：full 0.8218、去 ALIGN 0.7443、去 blackhole/mismatch 0.7615、去 version split 0.5546；均为冻结 scorer，不代表重训结果。

## M2：同事实、同模型的 GraphPatch 对照

Qwen3.8 Flash 的 8 事件 pilot 与其余 108 事件 formal shard 配置一致且互斥，合并后覆盖 116 个事件、每事件 2 generations、三路线共 696 请求。所有请求有确定结果且传输成功；proposal/catalog/raw 严格有效率分别为 204/232、197/232、110/232。失败或无效输出均留在无条件分母。

| 方法臂 | Coverage | AC@1 | AC@3 | AC@5 | MRR |
|---|---:|---:|---:|---:|---:|
| reference | 1.000 | 0.3649 | 0.6236 | 0.7816 | 0.5266 |
| runtime | 1.000 | 0.4167 | 0.6839 | 0.7960 | 0.5681 |
| rule patch | 1.000 | 0.8218 | 0.9339 | 0.9483 | 0.8805 |
| LLM patch，无回退 | 0.879 | 0.6422 | 0.7773 | 0.8032 | 0.7139 |
| LLM patch，规则回退 | 1.000 | 0.6954 | 0.8563 | 0.8894 | 0.7820 |
| proposal 原始 ranking | 0.879 | 0.7716 | 0.8448 | 0.8491 | 0.8097 |
| raw direct | 0.474 | 0.3664 | 0.4052 | 0.4310 | 0.3923 |
| catalog direct | 0.849 | 0.7931 | 0.8147 | 0.8276 | 0.8069 |

无回退 LLM patch 相对 catalog direct 的 AC@1 配对差为 −0.1509，95% incident bootstrap CI `[−0.2356, −0.0689]`；相对 rule patch 为 −0.1796，CI `[−0.2457, −0.1207]`。catalog direct 相对 raw direct 为 +0.4267，CI `[0.3362, 0.5172]`。因此数据支持 catalog 预处理有用，但不支持“LLM GraphPatch+GNN 优于同信息直接排序”。`proposal_rank_no_gnn` 只是模型返回的 ranking，不称为 evidence reranker。

Qwen 合并 shard 的 normalized input+output 为 2,265,227 tokens，另有 8,013,312 cache-read tokens；cache-inclusive volume 为 10,278,539，仅是用量而非账单。五个当前 OpenCode Go campaigns 共 705 请求；702 条返回 usage，reported cache-inclusive volume 10,351,081。该 705 不含早先保留在其他目录的 8 次 Gemini/GPT-5.4/Claude 网关失败。网关未返回 provider cost，成本保持 unknown。DeepSeek V4.1 Flash 3/3 路线均 HTTP 400，按停止规则不重试且 usage 未知；GPT-5.6 Luna 3/3 传输和严格 schema 均通过。完整跨模型重复不再是必需，因为论文不保留跨模型稳健性主张。

## M3：适用边界与效率

- LOFO patch AC@1：T1 0.3448、T2 0.4848、T3 0.1494、T4 0.1389。它们只测试原固定 test split 中相应场景的 29/22/29/36 个事件。
- Cross-app patch AC@1：Boutique→TrainTicket 0.4152，TrainTicket→Boutique 0.3051。该结果显示明显迁移边界，不能作生产泛化主张。
- 合成双根 proxy：57 个不重叠事件对、两种方向、三种子；Recall@2 0.7895–0.8246、ExactSet@2 0.5789–0.6491、MAP 0.8728–0.8909。构造使用 GT 选择两项服务状态，且不同源记录没有共享物理时间；只作 simulation-only 敏感性结果。
- 合成规模实验：13→520 candidates 时，CPU inference 中位数 0.340→2.576 ms，记录的 GPU 环境为 0.617→0.866 ms，GPU 峰值分配 12.98 MiB。包含 serialization/catalog/execution/tensor 的整体流水线在这些小图上 CPU 更快。复制图和额外、未被特征器使用的 telemetry keys 只测实现开销，不测生产准确率；CPU/GPU PyTorch 版本不同，历史输出未直接记录 GPU 型号。

B2/B3 fresh experiment audit 总体为 WARN、无 integrity BLOCK：Qwen/generalization/scaling 因范围、历史 provenance 与数值近同分限制为 WARN；合成双根在明确 simulation-only/GT-conditioned 范围内 PASS。审计重放 29,075 项核心检查并保存 5,977 个输入哈希。

## M4：恢复准备与当前门禁

本地恢复合同经独立反例复审 provisional PASS：命令/namespace/target、真实进程状态、当前状态 predicate、observed/prediction-only evidence、精确回滚、三值技术+业务 oracle、时间窗新鲜性均有失败闭合测试；全量 126 tests 通过。

隔离命名空间 `e2r-supplement-20260925` 已部署 12 个 Online Boutique workload，全部 2/2 Ready；没有修改用户 `online-boutique`。无故障基线为 30 秒 warm-up + 6×30 秒、5 offered rps：900 请求、0 错误、aggregate p95 17.34 ms，六个 state canary 全通过。专用 `e2r-executor` kubeconfig 为 0600，能访问/修复隔离 namespace，但不能读取 `online-boutique` pods 或删除 namespace。

第一次 `frontend` CPU Stress sanity 使 p95 从 baseline 17.34 ms 升至 804.54 ms，且 evidence、plan、YAML、precondition 全部通过；但系统 `ansible-playbook` 启动器缺少 Python 模块，修复命令未执行，因此该事件是 infrastructure failure，不是恢复失败/成功证据。失败路径的回滚和最终清理均 PASS，Chaos 后续 GET 为 NotFound，12 个 workload 全部恢复 2/2 Ready。项目 venv 随后补装 ansible-core，并以相同 scoped playbook 完成无故障只读预检。获准的 v2 在注入前被健康门拒绝：0 error/4.997 rps/canary PASS，但 p95 26.15 ms 超过基线容差 20.80 ms，故 `fault_injected=false`。该漂移与 12 个 `sidecar-busybox` 的 `sleep 3600` 同步第二次重启相吻合。

审查随后只批准隔离稳定化：12 个 sidecar 改为长时 sleep 并完成 rollout。新的 baseline v2 在前后使用相同 12 个 Pod UID、全部容器 restartCount=0；六个 30 秒窗口共 900 请求、0 error、4.9970–4.9977 rps、p95 17.43–18.60 ms、canary 全 PASS，aggregate p95 18.26 ms。两个旧 sanity attempt 和旧 baseline 均保留；当时 v3 与扩展 no-action/typed/direct/factorial 仍需独立复审。

在固定 baseline/manifest 哈希和本地 mismatch tests 通过后，获准的 v3 单案例完成：注入前 p95 16.97 ms；`frontend` CPU Stress 故障窗口 p95 1399.84 ms，形成可测退化；observed evidence、typed plan、YAML 和 scoped precondition 均 PASS。scoped Ansible 在故障年龄 63.92 秒时用 1.30 秒删除目标 Chaos。随后三个 30 秒窗口均为 150/150 成功、0 error、约 4.997 rps，p95 16.94/17.15/16.89 ms，canary 全 PASS；`fault_absent/resource_restored/ready_endpoints` 均 True，三窗口新鲜性 oracle 为 PASS。最终 cleanup 和独立 GET 均确认 Chaos NotFound，12 个 Pod Ready、总重启数 0。该结果仍只是一个 known-target contract sanity，不能外推为完整恢复率或生产安全性。

M4 fresh audit 为 WARN、无 integrity BLOCK：20 个窗口、3,000 个请求和 247 项检查重算通过。精确分母是 3 次 sanity attempt、2 次实际注入、2 次 repair process invocation、1 次完成的 typed mutation、1 个 recorded oracle PASS。v3 缺少 trace 内原始技术 probe 与同时刻 Pod runtime snapshot，因此只支持“一次 known-target、pre-expiry、cleanup-verified contract sanity”，不支持比较性或因果恢复优势。下一步只准备同故障的 no-action/typed 两事件匹配区块，并在运行前另行代码复审。

获审通过的两事件匹配区块按固定顺序启动，但被会话中断，因而 **0 个完整匹配对，比较无效**。no-action 事件完整：pre p95 16.79 ms，故障确认 1359.61 ms，三个观察窗口 p95 1500.68/1501.26/1399.67 ms；同一 Chaos UID 在全部 liveness/最终技术探测中保持 active，`fault_absent=False`、joint oracle FAIL，最终 cleanup NotFound 且 Pod identity/restart 未变。typed 事件的 pre/fault p95 为 19.50/1399.26 ms，scoped 删除在故障年龄 63.82 秒返回 rc0；随后父会话中断，0 个完整 evaluation window 落盘，technical/oracle/同时刻 cleanup 均缺失，故 typed oracle 为 null，不能用晚到 NotFound 补齐。终止 sidecar 保留原件哈希并记录：planned/attempted/injected=2/2/2，repair invocation/mutation=1/1，oracles=`[FAIL,null]`，complete pairs=0；禁止重跑或替换。

该中断区块的 fresh audit 为 WARN，328 项离线检查全部通过，未发现伪造或保存数值不一致。它只认可一条独立 no-action 持续退化轨迹，以及一次 exact scoped typed deletion 的过程事实；typed 恢复结果仍为 null，不能计算 paired delta、恢复率或因果优势。晚到两项 Chaos NotFound 距 repair 约 34,223 秒，且 scoped token 已过期导致 late runtime 为 unknown，均未被当作同时刻恢复证据。

## M5：匿名复现包

匿名包候选仅包含 E2R 源码、观察快照、冻结 checkpoint/请求/预测、分析、审稿门与 fresh audits；外部基线实现、API/SSH 凭据和 scoped kubeconfig 均排除。v6 虽通过文件哈希与 12 个离线测试，但独立 release audit 发现 `VALIDATION.json` 重新暴露原始 redaction marker，tar header 也保留本机 owner/group，因此 v6 明确 BLOCK/rejected。v7 修复了 payload/tar metadata，补齐三套 forensic traces、formal-only v2、generalization 定义与原始→匿名字节映射；独立审计确认其 7,312 项 manifest、归一化 tar、30 checkpoints/480 ZIP entries、798 observations 和 12 tests 均通过，但分发用 verifier 的拼接字面量仍可被 AST 常量折叠还原敏感值，因此 v7 继续 BLOCK。v8 candidate 已移除所有 source-specific verifier 字面量，只保留通用结构规则，仍需 final release audit。

## 当前可用结论

在当前受控、重复模板数据中，规则生成的观察补丁和匹配训练 scorer 有较高定位准确率；LLM 获得 operation catalog 后的直接排序显著优于 raw direct。新实验同时否定了更强的 LLM GraphPatch 必要性主张，并显示场景/应用迁移明显退化。论文应把贡献收缩为安全、可审计的有限操作接口与受控条件证据，不能声称开放世界泛化、GraphPatch 普遍优越或生产级自动恢复。
