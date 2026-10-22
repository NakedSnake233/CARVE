#!/usr/bin/env python3
"""Evidence gates and honest denominators for the prospective context repair study."""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.intervention.data import validate_record
from scripts.e2r.build_context_repair import PHASES
from scripts.e2r.collect_method_costs import native_record, number, stats, TOKEN_KEYS
from scripts.e2r.summarize_fault_coverage import raw_evidence_issues, missing_slots


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def read_ledger(path):
    """An unfinished tail remains pending; malformed complete lines fail the gate."""
    path = Path(path)
    if not path.exists():
        return [], []
    rows, issues = [], []
    content = path.read_text()
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError('row_not_object')
            if value.get('status') != 'started':
                rows.append(value)
        except (ValueError, TypeError):
            issues.append('unfinished_ledger_tail' if index == len(lines) - 1 and not content.endswith('\n')
                          else 'invalid_ledger_line:' + str(index + 1))
    return rows, issues


def context_evidence_issues(episode, row, config):
    issues = []
    before = episode / 'action_context_before.json'
    candidates_path = episode / 'action_context_candidates.json'
    for path in (before, candidates_path):
        if not path.exists():
            issues.append('missing_raw:' + path.name)
    if issues:
        return issues
    try:
        snapshot, candidate_record = read(before), read(candidates_path)
        if (not isinstance(snapshot, dict) or snapshot.get('schema') != 'action_configuration_v1'
                or number(snapshot.get('captured_at')) is None
                or not isinstance(snapshot.get('objects'), dict)
                or not isinstance(snapshot.get('store_services'), dict)):
            issues.append('invalid_context_snapshot')
        action_started = number(row.get('action_started_at'))
        if (action_started is not None and number(snapshot.get('captured_at')) is not None
                and snapshot['captured_at'] > action_started):
            issues.append('post_action_context_snapshot')
        if candidate_record.get('schema') != 'action_context_candidates_v1':
            issues.append('invalid_context_candidates_schema')
        candidates = candidate_record.get('candidates', {})
        expected = {p['id'] for p in config['plans']}
        if not isinstance(candidates, dict) or set(candidates) != expected:
            issues.append('context_candidates_catalog_mismatch')
        else:
            for plan_id, witness in candidates.items():
                if (not isinstance(witness, dict) or witness.get('status') not in ('eligible', 'blocked', 'unknown')
                        or witness.get('schema') != 'action_context_v1'
                        or not isinstance(witness.get('features'), dict)):
                    issues.append('invalid_context_witness:' + plan_id)
                elif (action_started is not None and number(witness.get('evaluated_at')) is not None
                      and witness['evaluated_at'] > action_started):
                    issues.append('post_action_context_witness:' + plan_id)
    except (OSError, ValueError, TypeError, KeyError) as error:
        issues.append('invalid_context_evidence:' + str(error))
    return issues


def _record(folder, row, assignment, scheduled, stage):
    effects = row.get('outcome', {}).get('effects', {})
    slot = row.get('original_schedule_slot')
    joint_known = all(type(effects.get(k)) is bool for k in ('business_recovered', 'non_target_safe'))
    return {'attempt_id': str(folder / row.get('episode_id', 'missing_episode_id')),
            'slot': slot, 'stage': stage, 'fault': assignment['fault'],
            'state_condition': assignment['state_condition'], 'repeat': assignment['repeat'],
            'owner': assignment['owner'], 'rate': assignment['rate'],
            'method': assignment['method'] if stage == 'policy' else scheduled.get(slot, {}).get('arm'),
            'selected_plan': row.get('chosen_plan'), 'status': row.get('status', 'invalid'),
            'restored': row.get('restore', {}).get('status') == 'restored',
            'business_recovered': effects.get('business_recovered'), 'non_target_safe': effects.get('non_target_safe'),
            'joint_success': effects.get('business_recovered') is True and effects.get('non_target_safe') is True,
            'unknown': not joint_known}


def collect(root, stage, phase='final'):
    q = Path(root) / (stage + '_v1')
    registration = read(q / 'PREREGISTRATION.json')
    rows, costs, missing, issues = [], [], [], []
    if registration.get('stage') != stage:
        issues.append('registration_stage_mismatch')
    for assignment in registration['assignments']:
        if phase == 'sanity' and assignment['id'] not in registration['included_first_jobs']:
            continue
        prefix = assignment['id'] + ':'
        config, folder = read(assignment['config']), Path(assignment['out'])
        scheduled = {j['original_schedule_slot']: j for j in config['scheduled_jobs']}
        if config.get('stage') != stage or assignment.get('stage') != stage:
            issues.append(prefix + 'configuration_stage_mismatch')
        if config.get('action_context_schema') != 'action_context_v1':
            issues.append(prefix + 'missing_context_backend_selector')
        raw, ledger_issues = read_ledger(folder / 'episodes.jsonl')
        issues.extend(prefix + issue for issue in ledger_issues)
        if folder.exists():
            try:
                if read(folder / 'protocol.json') != config:
                    issues.append(prefix + 'protocol_mismatch')
            except (OSError, ValueError):
                issues.append(prefix + 'missing_or_invalid_protocol')
        if stage == 'policy' and raw:
            try:
                if read(folder / 'goal_model_snapshot.json') != read(config['goal_transition_model']):
                    issues.append(prefix + 'model_snapshot_mismatch')
            except (OSError, ValueError, KeyError):
                issues.append(prefix + 'missing_or_invalid_model_snapshot')
        seen = set()
        for index, row in enumerate(raw):
            slot = row.get('original_schedule_slot')
            record = _record(folder, row, assignment, scheduled, stage)
            rows.append(record)
            if slot not in scheduled or slot in seen:
                issues.append(prefix + 'unexpected_or_duplicate_slot:' + str(slot))
                continue
            seen.add(slot)
            episode = folder / row.get('episode_id', 'missing_episode_id')
            try:
                validate_record(row)
                if read(episode / 'episode.json') != row:
                    issues.append(prefix + 'episode_ledger_mismatch')
                selected = row.get('assigned_method') if stage == 'policy' else row.get('chosen_plan')
                if selected != scheduled[slot]['arm']:
                    issues.append(prefix + 'assigned_arm_mismatch')
                for field in ('group_id', 'condition_id', 'repeat'):
                    if row.get(field) != scheduled[slot][field]:
                        issues.append(prefix + 'assignment_mismatch:' + field)
                if row.get('source') != 'live':
                    issues.append(prefix + 'non_native_record')
                cost = native_record(folder, row, index, config, 'context_repair_' + stage)
                cost.update(stage=stage, fault=assignment['fault'], state_condition=assignment['state_condition'])
                costs.append(cost)
            except (OSError, ValueError, AssertionError, TypeError, KeyError, IndexError) as error:
                issues.append(prefix + 'invalid_record:' + str(error))
            if not record['restored']:
                issues.append(prefix + 'restore_failed')
            if row.get('status') in ('failed', 'ineligible'):
                issues.append(prefix + row['status'])
                continue
            if row.get('execution', {}).get('status') in ('execution_unknown', 'write_deviation'):
                issues.append(prefix + 'unverified_execution')
            budget = row.get('total_probe_budget', {})
            limits = config['total_probe_budget']
            if (budget.get('actual_runtime_exceeded') or budget.get('calls', 0) > limits['max_calls']
                    or budget.get('cost', 0) > limits['max_cost']):
                issues.append(prefix + 'budget_exceeded')
            issues.extend(prefix + issue for issue in raw_evidence_issues(episode, row, config))
            issues.extend(prefix + issue for issue in context_evidence_issues(episode, row, config))
            try:
                receipt = read(episode / 'acknowledged_state_before_fault.json')
                baseline = read(episode / 'baseline_business.json')['window']
                if not (receipt['status'] == 'acknowledged'
                        and receipt['required'] == config['state_continuity']['required_station']
                        and receipt['completed_at'] < baseline['start_time']):
                    issues.append(prefix + 'invalid_acknowledgment_receipt')
                if baseline['state_canary_ok'] is not True or baseline['p95_ms'] > config['baseline_latency_ceiling_ms']:
                    issues.append(prefix + 'baseline_health_failed')
                if read(episode / 'station_goal_cleanup.json')['status'] != 'restored':
                    issues.append(prefix + 'station_cleanup_failed')
            except (OSError, ValueError, KeyError, TypeError) as error:
                issues.append(prefix + 'invalid_station_receipt:' + str(error))
        missing.extend(missing_slots(folder, assignment['id'], config['scheduled_jobs'], seen))
        if (folder / 'summary.json').exists():
            try:
                summary = read(folder / 'summary.json')
                restoration = summary['campaign_baseline_restore']
                if summary['attempted'] != len(raw):
                    issues.append(prefix + 'source_count_mismatch')
                if (summary['status'] != 'complete' or restoration['status'] != 'restored'
                        or not restoration.get('native_canaries') or not all(restoration['native_canaries'].values())):
                    issues.append(prefix + 'source_not_clean_complete')
            except (OSError, ValueError, KeyError, TypeError) as error:
                issues.append(prefix + 'invalid_source_summary:' + str(error))
        elif folder.exists():
            # Every closed episode may be present while the final restore is still running.
            issues.append(prefix + 'source_not_terminal')
    return rows, costs, missing, issues


def cells(records):
    groups = defaultdict(list)
    for row in records:
        groups[(row['fault'], row['state_condition'], row['method'])].append(row)
    return [{'fault': key[0], 'state_condition': key[1], 'method': key[2], 'n': len(rs),
             'joint_success': sum(r['joint_success'] for r in rs),
             'business_recovered': sum(r['business_recovered'] is True for r in rs),
             'non_target_safe': sum(r['non_target_safe'] is True for r in rs),
             'unknown': sum(r.get('unknown', r['business_recovered'] is None or r['non_target_safe'] is None) for r in rs),
             'statuses': dict(Counter(r['status'] for r in rs)),
             'actions': dict(Counter(r['selected_plan'] for r in rs))}
            for key, rs in sorted(groups.items(), key=lambda item: tuple(str(k) for k in item[0]))]


def _cost_group(rows, **identity):
    return {**identity, 'n': len(rows), 'statuses': dict(Counter(r['status'] for r in rows)),
            'tokens': {key: stats([r['tokens'][key] for r in rows]) for key in TOKEN_KEYS},
            'time_seconds': {key: stats([r['time_seconds'].get(key) for r in rows])
                             for key in sorted({k for r in rows for k in r['time_seconds']})},
            'api_reads': stats([r.get('api_reads') for r in rows]),
            'probe_calls': stats([r.get('registered_probe_calls') for r in rows]),
            'probe_cost': stats([r.get('registered_probe_cost') for r in rows])}


def cost_report(root):
    root = Path(root)
    records, pending, issues, phase_jobs = [], [], [], {}
    for stage in PHASES:
        q = root / (stage + '_v1')
        if not (q / 'PREREGISTRATION.json').exists():
            continue
        assignments = read(q / 'PREREGISTRATION.json')['assignments']
        phase_jobs[stage] = [a['id'] for a in assignments]
        for assignment in assignments:
            folder, config = Path(assignment['out']), read(assignment['config'])
            raw, ledger_issues = read_ledger(folder / 'episodes.jsonl')
            issues.extend(assignment['id'] + ':' + issue for issue in ledger_issues)
            seen = set()
            for index, row in enumerate(raw):
                try:
                    record = native_record(folder, row, index, config, 'context_repair_' + stage)
                    record.update(stage=stage, fault=assignment['fault'], state_condition=assignment['state_condition'],
                                  owner=assignment['owner'], rate=assignment['rate'])
                    records.append(record)
                    seen.add(row.get('original_schedule_slot'))
                except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
                    issues.append(assignment['id'] + ':cost_record_invalid:' + str(error))
            pending.extend(dict(row, stage=stage, fault=assignment['fault'])
                           for row in missing_slots(folder, assignment['id'], config['scheduled_jobs'], seen))
    groups, configurations = defaultdict(list), defaultdict(list)
    for row in records:
        groups[(row['stage'], row['fault'], row['method'])].append(row)
        configurations[(row['stage'], row['fault'], row['state_condition'], row['method'])].append(row)
    report = {'schema': 'context_repair_token_time_cost_v1', 'checked_at': time.time(),
              'status': 'incremental_closed_attempts', 'closed_attempts': len(records),
              'scope': 'New context study only. Audited local numeric paths use token0; Codex session excluded.',
              'records': records, 'pending': pending, 'accounting_issues': issues,
              'methods': [_cost_group(rs, stage=k[0], fault=k[1], method=k[2]) for k, rs in sorted(groups.items())],
              'per_configuration': [_cost_group(rs, stage=k[0], fault=k[1], state_condition=k[2], method=k[3])
                                    for k, rs in sorted(configurations.items())],
              'accounting_rules': ['Unstarted and started-without-terminal slots are distinct pending denominators.',
                                   'Unavailable durations remain null; overlapping phase/API durations are not additive.',
                                   'Cumulative attempt duration differs from parallel elapsed campaign wall time.']}
    queue = root / 'master_v1/queue_state.json'
    if queue.exists():
        state = read(queue)
        now = time.time()
        start = number(state.get('started_at'))
        end = state.get('updated_at') if state.get('complete') else now
        report['pipeline_wallclock'] = {'started_at': start, 'reported_at': now,
                                       'elapsed_seconds': end - start if start is not None else None,
                                       'queue_status_at_report': state.get('status')}
        report['phase_wallclock'] = []
        for stage, ids in phase_jobs.items():
            jobs = [state.get('jobs', {}).get(i, {}) for i in ids]
            starts = [j['started_at'] for j in jobs if number(j.get('started_at')) is not None]
            ends = [j['completed_at'] for j in jobs if number(j.get('completed_at')) is not None]
            terminal = len(ends) == len(ids)
            report['phase_wallclock'].append({'stage': stage, 'started_jobs': len(starts),
                                              'terminal_jobs': len(ends), 'complete': terminal,
                                              'elapsed_seconds': (max(ends) if terminal else now) - min(starts)
                                              if starts else None})
    report['model_artifacts'] = {name: read(root / name / 'RESULTS.json')
                                for name in ('model_v1', 'evaluation_v1') if (root / name / 'RESULTS.json').exists()}
    save(root / 'METHOD_COSTS.json', report)
    return {'status': 'updated', 'closed_attempts': len(records), 'report': str(root / 'METHOD_COSTS.json')}


def run(root, stage=None, phase='final'):
    root = Path(root)
    cost_report(root)
    if stage:
        records, _, missing, issues = collect(root, stage, phase)
        complete = not missing and not issues
        result = {'status': ('passed' if phase == 'sanity' else 'complete') if complete else 'partial',
                  'stage': stage, 'checked_at': time.time(), 'attempts': len(records),
                  'uncovered': missing, 'integrity_issues': issues, 'records': records, 'cells': cells(records),
                  'scope': 'Refusals and unknown outcomes remain in denominators; adverse effects do not fail engineering gates.'}
        save(root / (stage + '_v1') / ('SANITY_GATE.json' if phase == 'sanity' else 'RESULTS.json'), result)
    else:
        stages = {}
        for name in PHASES:
            records, _, missing, issues = collect(root, name)
            stages[name] = {'status': 'complete' if not missing and not issues else 'partial',
                            'attempts': len(records), 'uncovered': missing, 'integrity_issues': issues,
                            'cells': cells(records), 'records': records}
        artifacts = {}
        for name in ('model_v1', 'evaluation_v1'):
            path = root / name / 'RESULTS.json'
            artifacts[name] = read(path) if path.exists() else {'status': 'unstarted'}
        complete = (all(r['status'] == 'complete' for r in stages.values())
                    and all(r.get('status') == 'complete' for r in artifacts.values()))
        result = {'status': 'complete' if complete else 'partial', 'checked_at': time.time(),
                  'positions': sum(r['attempts'] for r in stages.values()), 'planned_positions': 104,
                  'stages': stages, 'model_artifacts': artifacts,
                  'scope': 'Known-mechanism configuration/load study. No full-application, unseen-fault or production qualification claim.',
                  'limitations': ['Recovery and safety retain the registered windows and thresholds.',
                                  'Native canaries and PVC receipts cover the owned fixture and available observations only.',
                                  'Rejected/unknown actions have missing effect labels; they are never filled with synthetic zero.',
                                  'The test set is for frozen evaluation; it does not select model parameters.']}
        save(root / 'RESULTS.json', result)
        lines = ['# 状态条件动作效果修复结果', '',
                 '新原生实验；保留原恢复与安全判据。拒绝和 unknown 计入分母，负结果不阻断工程门槛。', '',
                 '| 阶段 | 故障 | 状态 | 方法 | n | 恢复且安全 | unknown |',
                 '|---|---|---|---|---:|---:|---:|']
        for name, report in stages.items():
            for cell in report['cells']:
                lines.append(f"| {name} | {cell['fault']} | {cell['state_condition']} | {cell['method']} | "
                             f"{cell['n']} | {cell['joint_success']} | {cell['unknown']} |")
        lines += ['', '范围：已知故障机制的新配置/负载评估，不是未曝光故障、全应用或生产资格确认。',
                  '原始 canary 与 PVC 凭证仅支持其实际观测范围；开发会话 token 不在本地数值算法成本内。']
        (root / 'RESULTS.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'status': result['status'], 'attempts': result.get('attempts', result.get('positions'))}))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--stage', choices=list(PHASES))
    parser.add_argument('--phase', choices=['sanity', 'final'], default='final')
    parser.add_argument('--cost-only', action='store_true')
    args = parser.parse_args()
    if args.cost_only:
        print(json.dumps(cost_report(args.root)))
    else:
        run(args.root, args.stage, args.phase)
