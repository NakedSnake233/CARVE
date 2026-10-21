#!/usr/bin/env python3
"""Pack an audited, completed collection without discarding stopped ancestors."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.intervention.data import validate_record, validate_split, group_key
from methods.E2R.intervention.model import plan_signature
from methods.E2R.intervention.protocols import canonical, protocol_descriptor, stratum_id
from methods.E2R.intervention.schema import Entity, EvidenceGraph, Plan


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def derived_views(split, rows):
    """All view choices use assignments/action catalogs, not measured outcomes."""
    result = {'base': {'data': 'episodes.jsonl', 'split': split, 'scope': 'registered grouped split'}}
    applications = sorted({r['application'] for r in rows})
    flat = [g for values in split.values() for g in values]
    for app in applications:
        target = [g for g in flat if g.startswith(app + '/')]
        view_split = {'train': [g for g in split['train'] if g not in target],
                      'validation': [g for g in split['validation'] if g not in target], 'test': target}
        name = 'leave_' + app
        result[name] = {'data': name + '.jsonl', 'split': view_split,
                        'include_groups': sum(view_split.values(), []),
                        'scope': 'leave application; source original-test groups excluded, target groups reused across independent experiments'}
    target = [g for g in flat if '/F3-' in g]
    view_split = {'train': [g for g in split['train'] if g not in target],
                  'validation': [g for g in split['validation'] if g not in target], 'test': target}
    result['unseen_family_topology'] = {'data': 'unseen_family_topology.jsonl', 'split': view_split,
        'include_groups': sum(view_split.values(), []), 'scope': 'F3 family plus its topology held out; not topology-only causality'}
    result['unseen_composition'] = {'data': 'unseen_composition.jsonl', 'split': split,
        'exclude_train_validation_multistep': True,
        'scope': 'multistep plans withheld from training and validation; full test attempts retained; report multistep subset separately'}
    return result


def pack(archive, out):
    archive, out = Path(archive).resolve(), Path(out).resolve()
    manifest = read(archive / 'manifest.json')
    for name, item in manifest['files'].items():
        if digest(archive / name) != item['sha256']:
            raise ValueError('frozen inventory differs: ' + name)
    check, inventory = read(archive/'completion_check.json'), read(archive/'attempt_index.json')
    if not check['all_requested_checks_satisfied'] or check['expected_slots'] != check['covered_unique_slots']:
        raise ValueError('collection completion not established')
    split = {'train': [], 'validation': [], 'test': []}
    for name in ('boutique_preregistration.json', 'trainticket_preregistration.json'):
        registration = read(archive/name)
        # Split is the final registration's original whole-group allocation.
        for part in split:
            split[part] += registration['split'][part]
    meta_by_path = {str(Path(r['episode_path']).resolve()):r for r in inventory['attempts']}
    if len(meta_by_path) != len(inventory['attempts']):
        raise ValueError('duplicate indexed attempt path')
    out.mkdir(parents=True, exist_ok=False)
    (out/'protocols').mkdir()
    rows, source_inventory, seen_paths = [], [], set()
    strata, covered = {}, defaultdict(list)
    raw_statuses = Counter()
    for source_number, source in enumerate(sorted(inventory['sources'], key=lambda r:r['source'])):
        directory = Path(source['source']).resolve()
        source_id = f'source-{source_number:03d}'
        protocol_path = directory/'protocol.json'
        if digest(protocol_path) != source['protocol_sha256']:
            raise ValueError('source protocol changed: ' + str(directory))
        config = read(protocol_path)
        if config.get('stage') != 'formal':
            raise ValueError('non-formal data in packed collection')
        source_files = {str(p.relative_to(directory/'source_snapshot')):digest(p)
                        for p in sorted((directory/'source_snapshot').rglob('*.py'))}
        source_identity = hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest()
        if source_files != source['source_snapshot_files'] or source_identity != source['source_snapshot_sha256']:
            raise ValueError('source snapshot changed: ' + str(directory))
        for name, value in source_files.items():
            package = 'rca_bench' if name in ('recovery_oracle.py', 'workload_oracle.py') else 'methods/E2R/intervention'
            frozen = Path(source['runtime_path'])/package/name
            if not frozen.exists() or digest(frozen) != value:
                raise ValueError('recorded runtime differs from source snapshot: ' + str(frozen))
        descriptor = protocol_descriptor(config, source)
        protocol_id = stratum_id(descriptor)
        strata[protocol_id] = descriptor
        write(out/'protocols'/(source_id+'.json'), config)
        reference_graph = EvidenceGraph(0., [Entity(**e) for e in config['inventory']['entities']], [], [], config['inventory']['goals'])
        catalog = {p['id']: Plan.from_dict(p) for p in config['plans']}
        summary = read(directory/'summary.json')
        cleanup = summary['campaign_baseline_restore']
        if summary['status'] != source['summary_status'] or cleanup != source['final_restore']:
            raise ValueError('source summary differs from frozen inventory')
        if (summary['status'] not in ('complete','stopped') or cleanup['status'] != 'restored'
                or not cleanup.get('native_canaries') or not all(cleanup['native_canaries'].values())):
            raise ValueError('source final cleanup unverified: ' + str(directory))
        source_rows = 0
        with (directory/'episodes.jsonl').open() as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                raw_id = row['episode_id']
                episode_path = (directory/raw_id/'episode.json').resolve()
                metadata = meta_by_path.get(str(episode_path))
                if metadata is None or str(episode_path) in seen_paths or str(episode_path.parent.parent) != str(directory):
                    raise ValueError('unindexed, duplicate, or misplaced source episode')
                if read(episode_path) != row:
                    raise ValueError('episode and ledger disagree: ' + str(episode_path))
                for key in ('application','group_id','condition_id','repeat','chosen_plan','status','completed_at'):
                    if row.get(key) != metadata[key]:
                        raise ValueError('attempt metadata differs: ' + key)
                if row.get('restore',{}).get('status') != metadata['restore']:
                    raise ValueError('attempt restore record differs')
                is_covered = row['status'] not in ('failed', 'started') and metadata['restore'] == 'restored'
                if is_covered != metadata['covered']:
                    raise ValueError('frozen coverage flag differs from terminal record')
                if row.get('source') != 'live':
                    raise ValueError('synthetic data cannot enter native corpus')
                graph, plan = validate_record(row)
                registered_plan = catalog[row['chosen_plan']]
                signature = plan_signature(reference_graph, registered_plan)
                if plan is not None and plan_signature(graph, plan) != signature:
                    raise ValueError('recorded plan differs from registered action catalog')
                slot = metadata['original_schedule_slot']
                if row.get('original_schedule_slot', slot) != slot:
                    raise ValueError('original slot differs')
                row['episode_id'] = source_id + '/' + raw_id
                row['original_schedule_slot'] = slot
                row['artifact_directory'] = str(episode_path.parent)
                row.setdefault('operation_policy', copy.deepcopy(config['policy']))
                row['protocol_stratum'] = protocol_id
                row['source_provenance'] = {'source_id': source_id, 'source_directory': str(directory),
                    'original_campaign': source['original'], 'original_episode_id': raw_id,
                    'original_schedule_slot': slot, 'namespace': config.get('experimental_namespace'),
                    'raw_episode_sha256': digest(episode_path), 'protocol_file': str(out/'protocols'/(source_id+'.json')),
                    'protocol_sha256': source['protocol_sha256'], 'runtime_path': source['runtime_path'],
                    'source_snapshot_sha256': source_identity, 'plan_signature': signature,
                    'action_count': len(registered_plan.actions)}
                validate_record(row)
                rows.append(row)
                seen_paths.add(str(episode_path))
                source_rows += 1
                raw_statuses[row['status']] += 1
                if metadata['covered']:
                    covered[(row['application'],row['group_id'],slot)].append(row['episode_id'])
        if source_rows != source['attempts'] or source_rows != summary['attempted']:
            raise ValueError('source attempt denominator differs')
        source_inventory.append({**source, 'source_id':source_id, 'protocol_stratum':protocol_id,
                                 'ledger_sha256':digest(directory/'episodes.jsonl'), 'summary_sha256':digest(directory/'summary.json')})
    if (seen_paths != set(meta_by_path) or len(rows) != check['raw_attempts']
            or raw_statuses != Counter(check['statuses']) or len(covered) != check['expected_slots']
            or any(len(ids) != 1 for ids in covered.values())):
        raise ValueError('packed collection denominator or unique coverage differs')
    validate_split(rows, split)
    row_ids = {row['episode_id'] for row in rows}
    if len(row_ids) != len(rows):
        raise ValueError('duplicate packed attempt ID')
    views = derived_views(split, rows)
    outputs = {}
    for name, view in views.items():
        chosen, exclusions = [], []
        for row in rows:
            group = group_key(row)
            if 'include_groups' in view and group not in view['include_groups']:
                exclusions.append({'attempt_id':row['episode_id'], 'reason':'outside_declared_view_groups'})
                continue
            if (view.get('exclude_train_validation_multistep') and group not in view['split']['test']
                    and row['source_provenance']['action_count'] > 1):
                exclusions.append({'attempt_id':row['episode_id'], 'reason':'registered_multistep_fit_holdout'})
                continue
            chosen.append(row)
        validate_split(chosen, view['split'])
        data_path = out/view['data']
        with data_path.open('x') as stream:
            for row in chosen:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        write(out/(name+'_split.json'), view['split'])
        write(out/(name+'_exclusions.json'), exclusions)
        view.update(split_file=name+'_split.json', exclusions_file=name+'_exclusions.json', attempts=len(chosen),
                    data_sha256=digest(data_path), split_sha256=digest(out/(name+'_split.json')))
        outputs[view['data']] = view['data_sha256']
    write(out/'protocol_strata.json', strata)
    write(out/'source_inventory.json', source_inventory)
    write(out/'views.json', views)
    write(out/'manifest.json', {'schema':'native_lineage_corpus_manifest_v1', 'created_at':time.time(),
        'frozen_input_manifest':str(archive/'manifest.json'), 'frozen_input_manifest_sha256':digest(archive/'manifest.json'),
        'files':outputs, 'sources':len(source_inventory), 'protocol_strata':len(strata),
        'unique_attempt_ids':len(row_ids), 'unique_covered_slots':len(covered),
        'source_inventory_sha256':digest(out/'source_inventory.json'), 'views_sha256':digest(out/'views.json')})
    write(out/'summary.json', {'status':'complete', 'source':'live', 'attempts':len(rows),
        'statuses':dict(raw_statuses), 'original_slots':len(covered), 'launched_sources':len(source_inventory),
        'independent_groups':len({group_key(r) for r in rows}), 'split_groups':{k:len(v) for k,v in split.items()},
        'protocol_strata':len(strata), 'input_episodes':sum('input' in r for r in rows),
        'scope':'All original and retry attempts preserved; protocol metadata outside model input; no sanity/profile data.'})
    print(json.dumps(read(out/'summary.json')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    pack(args.archive, args.out)
