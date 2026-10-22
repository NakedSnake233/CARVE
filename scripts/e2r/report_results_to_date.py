#!/usr/bin/env python3
"""Read existing artifacts into a dated report; never fit, select or execute a policy."""
from collections import defaultdict
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / 'enhancement/revision-stage/v2'
OUT = ROOT / 'enhancement/reports/results_to_date_20261001_v1'


def read(path):
    return json.loads(Path(path).read_text())


def csv_rows(name, rows):
    with (OUT / name).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def stats(values):
    values = sorted(values)
    return {'n': len(values), 'mean_seconds': statistics.mean(values),
            'median_seconds': statistics.median(values), 'p90_seconds': values[math.ceil(.9 * len(values)) - 1],
            'maximum_seconds': max(values), 'total_worker_hours': sum(values) / 3600}


def queue_minutes(name):
    receipts = [read(p) for p in (ROOT / 'enhancement/queues' / name / 'jobs').glob('*/receipt.json')]
    assert all(r['status'] == 'completed' and r['exit_code'] == 0 and r['outputs_valid'] for r in receipts)
    return (max(r['completed_at'] for r in receipts) - min(r['started_at'] for r in receipts)) / 60


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    suite = V2 / 'exploratory_training_v1'
    new = read(suite / 'RESULTS.json')
    old = read(ROOT / 'enhancement/training-stage/native_suite_v1/results_v1/RESULTS.json')
    earlier = read(V2 / 'DEVELOPMENT_RESULTS.json')
    plan = read(suite / 'PLAN.json')
    rows = [json.loads(line) for line in (V2 / 'data/exploratory_v1/episodes.jsonl').read_text().splitlines()]
    assert new['models_completed'] == 18 and len(rows) == 120
    metrics = []
    for entry in plan['models']:
        if entry.get('profile'):
            continue
        p = entry['parameters']
        for evaluation in ('development_full', 'development_initial', 'transfer_full', 'transfer_initial'):
            r = read(Path(entry['out']) / (evaluation + '.json'))
            for effect, value in r['effects'].items():
                metrics.append({'model_id': entry['id'], 'variant': p['variant'], 'objective': p['objective'],
                    'seed': p['seeds'][0], 'evaluation': evaluation, 'effect': effect,
                    **{key: value[key] for key in ('attempts', 'known', 'unknown_or_unpredicted', 'brier', 'bce', 'ece_10_equal_width')},
                    'supported_scored_pairs': r['distinguishability']['scored_pairs'],
                    'pair_distance_mae': r['distinguishability']['distance_mae'],
                    'mean_inference_seconds': r['mean_inference_seconds']})
    assert len(metrics) == 360
    csv_rows('ALL_MODEL_METRICS.csv', metrics)
    cells = defaultdict(list)
    for row in rows:
        cells[row['application'], row['group_id'], row['condition_id'], row['chosen_plan']].append(row)
    def outcomes(selected):
        return {'attempts': len(selected),
                'recovered': sum(r['outcome']['effects']['business_recovered'] is True for r in selected),
                'safe': sum(r['outcome']['effects']['non_target_safe'] is True for r in selected),
                'recovered_and_safe': sum(r['outcome']['effects']['business_recovered'] is True and r['outcome']['effects']['non_target_safe'] is True for r in selected),
                'business_unknown': sum(r['outcome']['effects']['business_recovered'] is None for r in selected),
                'safety_unknown': sum(r['outcome']['effects']['non_target_safe'] is None for r in selected)}
    cell_rows = [{'application': app, 'group': group, 'condition': condition, 'arm': arm, **outcomes(selected)}
                 for (app, group, condition, arm), selected in sorted(cells.items())]
    csv_rows('RAW_COLLECTION_CELLS.csv', cell_rows)
    csv_rows('NATIVE_ATTEMPT_TIMES.csv', [{'attempt_id': r['episode_id'], 'application': r['application'],
             'arm': r['chosen_plan'], **r['timings_seconds']} for r in rows])
    csv_rows('PAIRED_COMPARISONS.csv', [{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in r.items()}
                                     for r in new['paired_comparisons']])
    times = stats([r['timings_seconds']['total_attempt'] for r in rows])
    phases = {k: stats([r['timings_seconds'][k] for r in rows if k in r['timings_seconds']])
              for k in rows[0]['timings_seconds']}
    inference = read(V2 / 'online_runtime_estimate_20261001/OFFLINE_INFERENCE_PROFILE.json')
    estimate = []
    for n in (32, 120, 480, 720):
        for concurrency in (2, 4, 8):
            estimate.append({'trials': n, 'concurrency': concurrency,
                'hours_low': n * (times['mean_seconds'] + 30) / concurrency / 3600 * 1.20,
                'hours_high': n * (times['mean_seconds'] + 60) / concurrency / 3600 * 1.35})
    audit = read(suite / 'EXPERIMENT_AUDIT.json') if (suite / 'EXPERIMENT_AUDIT.json').exists() else {'status': 'pending'}
    claims = read(suite / 'CLAIMS_FROM_RESULTS.json') if (suite / 'CLAIMS_FROM_RESULTS.json').exists() else {'status': 'pending'}
    report = {'generated_at': datetime.now().astimezone().isoformat(), 'scope': 'Completed native intervention stages through exploratory training v2; no newly launched online policy experiments.',
              'old_suite': {'training_jobs': old['training_jobs'], 'evaluation_jobs': old['evaluation_jobs'],
                            'refinement_cells': old['refinement_cells'], 'three_seed_summaries': old['three_seed_summaries']},
              'earlier_v2_development': earlier, 'new_offline': new,
              'native120_by_arm': {a: outcomes([r for r in rows if r['chosen_plan'] == a]) for a in sorted({r['chosen_plan'] for r in rows})},
              'native120_cells': cell_rows, 'timing': times, 'phase_timings': phases,
              'collection_queue_minutes': queue_minutes('revision_exploratory_v1'),
              'training_and_aggregate_queue_minutes': queue_minutes('revision_exploratory_training_v1'),
              'offline_inference_profile': inference,
              'prospective_online_estimates': estimate,
              'estimate_assumptions': 'Not a preregistered or launched matrix. Per attempt use measured194s mean +30..60s unmeasured online query/decision margin, then20..35% scheduling/retry reserve. No failures are discarded. Implementation/review/sanity time excluded.',
              'fresh_audit': audit, 'fresh_claim_review': claims}
    (OUT / 'RESULTS.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = ['# 截至当前的完整实验结果与在线运行时间估计', '', '生成时间：' + report['generated_at'], '',
             '本报告覆盖 `enhancement/improvement.md` 对应的原生干预实验各阶段；原始工件保留，模型测试集、开发集与已知配置的新测量分别报告。**18 个新增模型、72 份离线评估及汇总均已完成；在线学习策略实验尚未启动，整体创新性主张仍需证据。**', '',
             '## 1. 已完成的范围', '',
             '| 阶段 | 实际完成 | 主要结果与范围 |', '|---|---|---|',
             '| v1 原生正式采集 | 66 来源、1812 次尝试、1786 位置 | 1681 observed / 105 rejected / 26 failed；observed 不等于恢复成功 |',
             '| v1 冻结离线套件 | 42 模型、62 评估、30 细化回放、16 离线扩展性格 | 审计 WARN；C1/C2=no；学习策略准入覆盖0 |',
             '| v2 首轮开发模型 | graph/set/local ×3种子，9模型 | 仅历史train/validation；无独立新测试 |',
             '| v2 原生开发pilot | 48/48 | 已恢复原环境；固定切备用14/16恢复，回滚8/16 |',
             '| 对称依赖机制试采 | 16/16 | 安全恢复差异不稳定，按原计划结束此分支 |',
             '| 无故障稳定性诊断 | 8/8条轨迹 | 全部观测/恢复完成；存在相对基线不稳定性 |',
             '| 固定参考有效性诊断 | 1/4条实际运行 | 首条失败后停止，其余3条从未运行；后续用户取消其作为实验启动条件 |',
             '| 新探索性原生采集 | 120/120尝试、40来源 | 全部observed并恢复；业务55真/65假、安全115真/5假 |',
             '| 本轮三种子训练与对照 | 18/18模型 + 72评估 + 1汇总 | 19/19队列作业成功，无重试；另有6个工程profile，均不计为研究模型 |', '',
             '三批研究训练合计69个模型（42+9+18），不包括工程profile。48/16/120为不同目的/协议的开发采集，不能合并为一个总体恢复率；诊断轨迹也不是修复试验。前置sanity保留在原队列目录，不能算作新增独立测试。', '',
             '## 2. v1 历史基础测试结果', '', 'Brier越低越好；均值±样本标准差来自种子42/43/44。基础测试10配置组、384尝试，358业务目标已知、26未知/无预测。', '',
             '| 方法 | 业务Brier | 自动执行准入覆盖 |', '|---|---:|---:|']
    def fmt(d):
        if d['mean'] is None:
            return 'NA'
        return f"{d['mean']:.5f}" if d['sample_std'] is None else f"{d['mean']:.5f} ± {d['sample_std']:.5f}"
    for r in old['three_seed_summaries']:
        if r['view'] == 'base' and r['name'] in {'main', 'no_edges', 'no_distinguish', 'diagnosis', 'state', 'full_graph'}:
            lines.append(f"| {r['name']} | {fmt(r['metrics']['business_brier'])} | {r['metrics']['coverage']['mean']:.1%} |")
    lines += ['', '主模型与无边对照三种子的配对组差异区间均跨0；30组保存观测回放的自动覆盖和候选计划改变均为0。未执行条件下的成功率/风险为未定义，不能写为安全。主模型策略恢复3.75%–8.75%来自不操作臂及unknown界限，不能归因于模型干预。', '',
              'B4已执行留一应用、F3/拓扑及未见操作组合的冻结评估；图收益不稳定。部分泛化规则控制风险上界48.26%–60.57%。B5保留777业务真/904假/131未知及全部失败；1602个已记录写入步骤中前置刷新违反/缺证据均0。旧episode的2次恢复失败保留；历史独立等待/补偿时钟缺失仍标为未测。离线扩展性不是在线规模验证；角色重命名最大概率变化8.94e-8。', '',
              '原生范围仅Cart/Redis与Station/MySQL依赖切片。历史测试配置Boutique F2-config-04参与过基础设施sanity；旧测试不再用于v2调参。没有新增完整外部LLM/E2E-REME/ThinkRemed方法比较。详见历史[最终报告](../../training-stage/FINAL_REPORT_20261001.md)、[逐运行表](../../training-stage/native_suite_v1/results_v1/PER_RUN.csv)、[主张审查](../../CLAIMS_FROM_RESULTS.md)。', '',
              '## 3. v2 首轮开发及原生补充结果', '', '| 模型 | 首轮历史validation业务Brier均值 |', '|---|---:|']
    for k, v in earlier['business_brier'].items():
        lines.append(f"| {k} | {v['mean']:.5f} |")
    lines += ['', '首轮graph对set三种子均较低、对local两种子较低；这是开发比较，不能与v1基础测试Brier直接计算提升。', '',
              '| 原生开发阶段/动作 | 业务恢复 | 非目标安全 | 同时恢复且安全 |', '|---|---:|---:|---:|',
              '| 48次pilot：不操作 | 0/16 | 16/16 | 0/16 |',
              '| 48次pilot：切备用 | 14/16 | 16/16 | 14/16 |',
              '| 48次pilot：回滚 | 8/16 | 16/16 | 8/16 |',
              '| 16次对称试采：不操作 | 0/4 | 0/4 | 0/4 |',
              '| 16次对称试采：旧备用 | 1/4 | 3/4 | 1/4 |',
              '| 16次对称试采：替代备用 | 4/4 | 1/4 | 1/4 |',
              '| 16次对称试采：回滚 | 4/4 | 3/4 | 3/4 |']
    for arm, values in report['native120_by_arm'].items():
        n = values['attempts']
        lines.append(f"| 新120次：{arm} | {values['recovered']}/{n} | {values['safe']}/{n} | {values['recovered_and_safe']}/{n} |")
    lines += ['', '以上均为预先分配动作的真实采集，不能写成学习模型自动选择的恢复率。新120次没有业务/安全unknown；切备用恢复90%，安全92.5%，同时满足82.5%，尚不证明≤5%总体风险。24个应用/负载/条件/动作单元见[原始计数CSV](RAW_COLLECTION_CELLS.csv)。', '',
              '8条无故障诊断均完成观测和恢复，末端窗口通过不意味着整个轨迹稳定。固定参考诊断首条peer参考两半p95为112.93/12.12ms，参考与后段为107.39/13.49ms，科学有效性失败；没有改写原标签或续跑至阳性。用户允许后续探索不依赖这些外部基准/科学门槛，但这些阴性结果保留。', '',
              '## 4. 本轮18模型完整离线结果', '',
              '固定hidden64、3层、120epoch、lr0.003、种子42/43/44。train27组1296尝试（原train+新low）；checkpoint只使用历史validation5组192尝试，业务179已知/13未知或无预测。新stressed60尝试全部有标签，用于已知配置的探索迁移评分；仅2个应用/配置组，不是独立新配置确认。原test未评分。', '',
              '### 完整观测输入：均值±样本标准差', '',
              '| 模型/目标 | validation业务 | validation安全 | 迁移业务 | 迁移安全 |', '|---|---:|---:|---:|---:|']
    by = {(r['variant'], r['objective'], r['evaluation']): r for r in new['summaries']}
    combos = [('graph', 'effect_distinguish'), ('set', 'effect_distinguish'), ('local', 'effect_distinguish'),
              ('graph', 'effect'), ('graph', 'diagnosis'), ('graph', 'state')]
    for view in ('full', 'initial'):
        if view == 'initial':
            lines += ['', '### 仅初始观测输入：同一完整观测训练模型的推理输入消融', '',
                      '| 模型/目标 | validation业务 | validation安全 | 迁移业务 | 迁移安全 |', '|---|---:|---:|---:|---:|']
        for variant, objective in combos:
            dev, transfer = (by[variant, objective, part + '_' + view] for part in ('development', 'transfer'))
            values = [dev['business_brier'], dev['safety_brier'], transfer['business_brier'], transfer['safety_brier']]
            lines.append('| ' + variant + '/' + objective + ' | ' + ' | '.join(fmt(v) for v in values) + ' |')
    lines += ['', 'full/initial只改变推理时可见的已保存输入；所有模型均使用full训练，标签仍来自实际完整采集协议。不能把initial预测称为无查询在线策略效果。全部5种效果维度、每个种子、4个评估视图的360行Brier/BCE/ECE及分母见[完整指标CSV](ALL_MODEL_METRICS.csv)，所有配对比较见[配对CSV](PAIRED_COMPARISONS.csv)。', '',
              '### 关键发现', '',
              '1. 历史validation中graph业务Brier相对set低3.70%、相对local低2.68%，三种子均低。该分区参与checkpoint选择，属于开发表现。',
              '2. 新stressed完整输入下graph业务Brier反而比set高3.61%、比local高3.33%；只赢set的1/3种子，对local为0/3。没有稳定的跨条件图结构收益。仅2组的bootstrap区间必须作描述性解释，不能用种子替代独立配置数。',
              '3. graph完整输入的迁移安全预测均值比set/local低，但diagnosis/state目标的安全Brier又更低；不能从某一指标推断整体最优，更不能将概率误差直接当作动作安全率。',
              '4. 三个分区统计支持的同诊断效果配对均为0，pair loss全程0；graph/effect_distinguish与graph/effect逐种子评分相同，区分损失的贡献没有被检验出来。',
              '5. 效果监督的业务Brier低于本轮指定diagnosis/state训练流程，但后者先辅助预训练、再冻结表示训练效果头；存在优化预算分配混杂，不能声称信息论必要性。',
              '6. v1严格准入覆盖为0；本轮v2完成的是离线预测，没有新的合格准入profile或真实学习策略覆盖测量。修复工程拒绝逻辑、允许实验执行，均不能自动认定已解决覆盖问题。', '',
              '本轮60个完整输入迁移预测全部带有训练数值范围外标记（动作签名仍可已见）。这是输入覆盖诊断，不能当成已测在线覆盖率；对高负载行为的预测与准入需要分开解释。', '',
              '## 5. 本轮成本与失败', '',
              f"120次原生采集8路队列实际 {report['collection_queue_minutes']:.2f} 分钟；18模型及汇总2路队列实际 {report['training_and_aggregate_queue_minutes']:.2f} 分钟。18模型累计进程时间 {new['compute']['total_worker_wall_seconds']/3600:.2f} 小时；峰值单进程RSS {new['compute']['maximum_worker_peak_rss_mib']/1024:.2f} GiB。以上不含方案、实现与复核。", '',
              '| 原生每次尝试阶段 | 均值秒 | P90秒 |', '|---|---:|---:|']
    for key, value in phases.items():
        lines.append(f"| {key} | {value['mean_seconds']:.2f} | {value['p90_seconds']:.2f} |")
    lines += ['', '总尝试耗时独立报告，不与其子阶段相加。新采集0 failed/0 unknown、18训练0失败；历史失败仍保留。尝试级时钟见[NATIVE_ATTEMPT_TIMES.csv](NATIVE_ATTEMPT_TIMES.csv)。', '',
              '## 6. 在线策略实验预期时间', '',
              '**在线矩阵尚未冻结，下列是预算情景，不是已部署任务或保证完成时间。** 同权限、同探针目录、相同调用/成本/总时限、相同动作与执行器的四种策略为：指导细化、随机、固定、不细化。', '',
              '- 工程试运行：32次，例为2应用×2条件×4策略×2重复，仅固定一个负载/种子；不作为三种子充分确认。',
              '- 核心C2矩阵：480次 = 2应用×2负载×2条件×4策略×3种子×5重复。',
              '- 加表示对照：720次 = 上述基础的策略/模型组合扩到6种（再加set/local指导细化）；不包括新增未见配置、更多预算档或额外强规则对照。每增加一整档预算约再增加相应矩阵运行时间。', '',
              f"测量依据：真实完整尝试均值 {times['mean_seconds']:.1f} 秒、中位数 {times['median_seconds']:.1f} 秒、P90 {times['p90_seconds']:.1f} 秒；所有3候选的一次离线排序均值 {inference['rank_seconds_mean']*1000:.2f} ms，峰值RSS {inference['peak_rss_mib']:.1f} MiB。该排序profile没有运行完整在线选择器、原生负载父进程或8路竞争，不能替代上线资源sanity。", '',
              '估算公式：`总时长 = 次数 × (实测194.36秒 + 预留30–60秒) ÷ 并发 × 1.20–1.35`。额外秒数与20%–35%余量为规划假设，覆盖策略查询、调度、恢复尾部及有限重试，不是测量结果或统计置信区间。', '',
              '| 情景 | 2路预计纯运行小时 | 4路预计纯运行小时 | 8路预计纯运行小时 |', '|---|---:|---:|---:|']
    for count in (32, 120, 480, 720):
        parts = []
        for c in (2, 4, 8):
            e = next(r for r in estimate if r['trials'] == count and r['concurrency'] == c)
            parts.append(f"{e['hours_low']:.2f}–{e['hours_high']:.2f}")
        lines.append('| ' + str(count) + '次 | ' + ' | '.join(parts) + ' |')
    lines += ['', '实验模式入口、选择/查询路径测试与fresh复核尚待完成：工程规划约2–4小时；原生sanity及最终资源检查约0.5–1小时。这些额外时间可能因发现缺陷而延长。按核心480次估算，从当前状态到完成：8路约7–11小时，4路约12–17小时；不包含进一步设计独立新配置以支持C1/C2的追加实验。', '',
              '当前可用内存约17.8GiB；历史8路原生采集已跑通，单模型离线排序峰值约0.34GiB。8路具有可行性，但完整在线父进程/负载、选择器和实际共享节点竞争尚未测量，不能承诺8路上线。合理执行顺序是完成入口复核、少量4路sanity后按现场内存/调度/恢复证据决定是否到8路。外部SLO或新benchmark不再作为启动前提。', '',
              '## 7. 审查与结论', '',
              '历史审查：完整性WARN，C1=no、C2=no。新一轮审查文件与机器结果如下（fresh same-family、provisional，不称跨模型家族独立确认）：', '',
              '新一轮审查也为 **WARN；C1=no，C2=no（本轮未测试在线策略）**。1,548条纳入记录、19作业回执、72评估、360个效果指标集及120个配对比较通过独立复算。全部2,160训练轮的pair loss为0；effect与effect_distinguish三种子的checkpoint张量完全一致。WARN来自科学范围和决定性证据限制，未发现目标伪造或分数归一化欺骗。', '',
              '- [新完整性审计](../../revision-stage/v2/exploratory_training_v1/EXPERIMENT_AUDIT.md)',
              '- [新结果到主张判断](../../revision-stage/v2/exploratory_training_v1/CLAIMS_FROM_RESULTS.md)',
              '- [完整JSON汇编](RESULTS.json)', '',
              '当前可报告的是：采集与离线执行完整、标签与失败可追溯、效果训练在指定业务预测比较上有收益；稳定关系结构收益、区分监督收益、学习策略自动修复收益及C2观测效率仍未证实。在线运行本身也不保证这些主张成立；应先冻结对照与分析，再记录真实正负结果。', '',
              '已按本次要求先提供结果与时间预算，没有启动在线策略任务。']
    (OUT / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'report': str(OUT / 'REPORT.md'), 'metrics_rows': len(metrics), 'collection_cells': len(cell_rows), 'estimate_cells': len(estimate)}))


if __name__ == '__main__':
    main()
