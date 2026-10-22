#!/usr/bin/env python3
"""Host-side read-only collection checks; request repairs in the existing Codex thread."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import time
import uuid

from check_native_collection import collect


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def process_alive(pid, start_ticks):
    if not pid or start_ticks is None:
        return False
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(') ', 1)[1].split()
        return fields[0] != 'Z' and fields[19] == str(start_ticks)
    except (FileNotFoundError, ProcessLookupError):
        return False


def inspect_collection(index, now, heartbeat_seconds=180, stall_seconds=1200,
                       alive=process_alive):
    """Ignore superseded namespace assignments and tolerate receipt/heartbeat races."""
    issues, lanes = [], []
    unfinished = 0
    for declaration in index['active_queues']:
        directory = Path(declaration['directory'])
        amendment = read(directory / 'AMENDMENT.json')
        selected = set(declaration['namespaces'])
        assignments = [a for a in amendment['assignments'] if a['namespace'] in selected]
        missing = selected - {a['namespace'] for a in assignments}
        for namespace in sorted(missing):
            issues.append(f'{directory.name}/{namespace}:missing_assignment')
        state_path = directory / 'queue_state.json'
        if not state_path.exists():
            issues.append(f'{directory.name}:queue_not_started')
            unfinished += len(assignments)
            continue
        queue = read(state_path)
        queue_unfinished = 0
        for assignment in assignments:
            identity = assignment['id']
            folder = directory / 'jobs' / identity
            row = dict(queue['jobs'].get(identity, {'status': 'missing'}))
            receipt = folder / 'receipt.json'
            if receipt.exists():
                row.update(read(receipt))
            status = row['status']
            if status == 'completed':
                continue
            unfinished += 1
            queue_unfinished += 1
            key = f'{directory.name}/{identity}'
            lane = {'queue': str(directory), 'job': identity,
                    'namespace': assignment['namespace'], 'status': status}
            lanes.append(lane)
            if status not in ('pending', 'running', 'launching'):
                issues.append(f'{key}:{status}')
                continue
            if status == 'pending':
                continue
            started = folder / 'started.json'
            if started.exists():
                info = read(started)
                live = alive(info.get('pid'), info.get('start_ticks'))
                lane.update(worker_pid=info.get('pid'), worker_alive=live)
                if not live:
                    # Completion can be committed between the first receipt read and /proc.
                    if receipt.exists():
                        continue
                    issues.append(f'{key}:worker_lost')
                last = info.get('started_at', row.get('launch_at', now))
                terminal_count = 0
                for path in Path(assignment['path']).glob('episode-*/episode.json'):
                    try:
                        episode = read(path)
                        if episode.get('status') != 'started':
                            last = max(last, path.stat().st_mtime)
                            terminal_count += 1
                    except (json.JSONDecodeError, FileNotFoundError):
                        continue
                lane.update(last_episode_age_seconds=round(now - last, 1),
                            terminal_episodes=terminal_count)
                if live and now - last > stall_seconds:
                    issues.append(f'{key}:no_terminal_episode_for_{stall_seconds}s')
            elif now - row.get('launch_at', now) > 30:
                issues.append(f'{key}:worker_never_registered')
        # A historical queue may be stopped after all its currently selected jobs finished.
        if queue_unfinished and now - queue.get('updated_at', 0) > heartbeat_seconds:
            issues.append(f'{directory.name}:scheduler_heartbeat_stale')
    return {'issues': sorted(set(issues)), 'unfinished_jobs': unfinished, 'lanes': lanes}


class CodexRPC:
    """Native app-server WebSocket over the user's Unix control socket."""
    def __init__(self, path):
        import websocket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect(path)
            self.ws = websocket.create_connection('ws://localhost/', socket=sock,
                                                   timeout=10, suppress_origin=True)
        except BaseException:
            sock.close()
            raise
        self.sequence = 0
        self.call('initialize', {'clientInfo': {'name': 'rca_experiment_watch', 'version': '1'},
                                 'capabilities': {'experimentalApi': True}})
        self.ws.send(json.dumps({'method': 'initialized'}))

    def call(self, method, params):
        self.sequence += 1
        identity = self.sequence
        self.ws.send(json.dumps({'id': identity, 'method': method, 'params': params}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = json.loads(self.ws.recv())
            if result.get('id') == identity:
                if 'error' in result:
                    raise RuntimeError(f'{method}: {result["error"]}')
                return result['result']
        raise TimeoutError(method)

    def close(self):
        self.ws.close()


def dispatch(rpc, config, state, persist, now):
    """Single durable request; never start a second turn or jump a user's queue."""
    request = state['request']
    thread_id = config['thread_id']
    thread = rpc.call('thread/read', {'threadId': thread_id, 'includeTurns': False})['thread']
    if thread['status']['type'] == 'notLoaded':
        # Explicitly retain the authorized protections when loading a persisted conversation.
        thread = rpc.call('thread/resume', {'threadId': thread_id, 'cwd': config['project'],
                         'sandbox': 'workspace-write', 'approvalPolicy': 'on-request',
                         'approvalsReviewer': 'auto_review'})['thread']
    if thread.get('cwd') != config['project']:
        raise RuntimeError('thread working directory differs from configured project')
    if thread['status']['type'] != 'idle':
        return 'deferred_thread_' + thread['status']['type']
    queued = rpc.call('thread/queue/list', {'threadId': thread_id, 'limit': 100})['data']
    own = next((q for q in queued if q['clientUserMessageId'] == request['id']), None)
    if own is None and request['phase'] != 'prepared':
        # An add/start response may have been lost. Do not blindly enqueue it again.
        return 'awaiting_ack_after_delivery'
    if own is None:
        if queued:
            return 'deferred_user_queue'
        request['phase'] = 'adding'
        request['sent_at'] = now
        persist()
        own = rpc.call('thread/queue/add', {'threadId': thread_id,
                      'clientUserMessageId': request['id'],
                      'input': [{'type': 'text', 'text': request['prompt']}]})['queuedSubmission']
        request.update(phase='queued', queued_submission_id=own['id'])
        persist()
    # Re-read after enqueue: the UI may have started it, or a user may have intervened.
    thread = rpc.call('thread/read', {'threadId': thread_id, 'includeTurns': False})['thread']
    if thread['status']['type'] != 'idle':
        return 'queued_thread_active'
    queued = rpc.call('thread/queue/list', {'threadId': thread_id, 'limit': 100})['data']
    if not queued or queued[0]['id'] != own['id']:
        return 'queued_waiting_turn'
    response = rpc.call('thread/queue/start', {'threadId': thread_id, 'queuedSubmissionId': own['id']})
    request.update(phase='started', turn_id=response['turn']['id'], sent_at=now)
    persist()
    return 'started'


def request_prompt(config, request, snapshot):
    home = Path(config['directory'])
    return (f'【用户已授权的实验定时巡检：{request["kind"]}】\n'
            '用户要求：检查进度，定时检查；如果实验停止就修复并继续。\n'
            f'项目：{config["project"]}\n请求：{request["id"]}\n'
            f'请先阅读 {home / "SUPERVISION.md"}。本轮快照：{snapshot}。\n'
            f'最新报告：{home / "LATEST.json"}；须重新核实现场状态。\n'
            '沿用当前会话权限与自动审批机制，诊断并修复当前停止的实验，'
            '仅续跑未覆盖位置；保留历史结果和健康作业。不要因为已有授权而再次询问常规续跑许可。\n'
            'validation 类型需验证定时器和报告；如发现故障仍应修复。'
            'collection_complete 类型需核验采集完整性，报告剩余训练/评估工作，不等同全项目完成。\n'
            f'完成后原子写入 {home / "acks" / (request["id"] + ".json")}，'
            '内容包含 request_id、status（completed 或 needs_attention）、summary、checked_at。'
            '只有确实需要用户输入且不能继续时才用 needs_attention。最后简要汇报。')


def tick(config, validation=False):
    home = Path(config['directory'])
    state_path = home / 'state.json'
    state = read(state_path) if state_path.exists() else {}
    persist = lambda: save(state_path, state)
    now = time.time()
    if not config.get('enabled', True):
        print('supervision disabled')
        return
    snapshot = {'checked_at': now}
    try:
        index = read(config['index'])
        progress = collect(config['index'])
        snapshot.update(inspect_collection(index, now, config['heartbeat_seconds'], config['stall_seconds']))
        snapshot['progress'] = progress
        snapshot['complete'] = (progress['covered_slots'] == progress['planned_slots']
                                and snapshot['unfinished_jobs'] == 0 and not snapshot['issues'])
        if not snapshot['unfinished_jobs'] and progress['covered_slots'] < progress['planned_slots']:
            snapshot['issues'].append('coverage_gap_without_unfinished_jobs')
        if progress['covered_slots'] > progress['planned_slots']:
            snapshot['issues'].append('coverage_exceeds_plan')
        save(config['progress_report'], progress)
    except Exception as error:
        snapshot.update(issues=['inspection_error:' + str(error)], complete=False)
    snapshot['load_average'] = list(os.getloadavg())
    snapshot['memory'] = {line.split(':')[0]: line.split(':')[1].strip()
                          for line in Path('/proc/meminfo').read_text().splitlines()
                          if line.startswith(('MemTotal:', 'MemAvailable:'))}
    snapshot_path = home / 'snapshots' / f'{int(now)}.json'
    save(snapshot_path, snapshot)
    save(home / 'LATEST.json', snapshot)
    state['last_check_at'] = now
    request = state.get('request')
    if request:
        ack_path = home / 'acks' / (request['id'] + '.json')
        if ack_path.exists():
            ack = read(ack_path)
            if ack.get('request_id') != request['id'] or ack.get('status') not in ('completed', 'needs_attention'):
                raise ValueError('invalid supervision acknowledgement')
            state['last_ack'] = ack
            if ack['status'] == 'needs_attention':
                state['attention_issues'] = request['issues']
            elif request['kind'] == 'collection_complete':
                state['completion_reported'] = True
            state['request'] = None
            request = None
    if not snapshot['issues']:
        state.pop('attention_issues', None)
    kind = ('validation' if validation else 'repair' if snapshot['issues']
            else 'collection_complete' if snapshot['complete'] and not state.get('completion_reported') else None)
    if (not request and kind and
            not (kind == 'repair' and snapshot['issues'] == state.get('attention_issues'))):
        request = {'id': str(uuid.uuid4()), 'created_at': now, 'phase': 'prepared',
                   'kind': kind, 'issues': snapshot['issues']}
        request['prompt'] = request_prompt(config, request, snapshot_path)
        state['request'] = request
        save(home / 'requests' / (request['id'] + '.json'), request)
    persist()
    outcome = 'healthy' if not snapshot['issues'] else 'needs_repair'
    if request:
        rpc = None
        try:
            rpc = CodexRPC(config['codex_socket'])
            outcome = dispatch(rpc, config, state, persist, now)
            if outcome == 'awaiting_ack_after_delivery' and now - request.get('sent_at', now) > 600:
                # The turn ended/cancelled without an acknowledgement. Re-evaluate next tick.
                state['last_unacknowledged_request'] = request
                state['request'] = None
                outcome = 'unacknowledged_delivery_retired'
        except Exception as error:
            outcome = 'dispatch_error:' + str(error)
        finally:
            if rpc is not None:
                rpc.close()
    state['last_outcome'] = outcome
    persist()
    save(home / 'LAST_CHECK.json', {'checked_at': now, 'outcome': outcome,
                                   'issues': snapshot['issues'], 'request_id': request['id'] if request else None})
    print(json.dumps({'checked_at': now, 'outcome': outcome, 'issues': snapshot['issues'],
                      'covered_slots': snapshot.get('progress', {}).get('covered_slots')}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--validation', action='store_true')
    args = parser.parse_args()
    config = read(args.config)
    home = Path(config['directory'])
    home.mkdir(parents=True, exist_ok=True)
    with (home / 'watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('previous check still running')
            return
        tick(config, args.validation)


if __name__ == '__main__':
    main()
