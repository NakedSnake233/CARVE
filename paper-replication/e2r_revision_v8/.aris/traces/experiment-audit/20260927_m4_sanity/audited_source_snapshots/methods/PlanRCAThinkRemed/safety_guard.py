"""Safety checks for repair plans and generated Ansible playbooks.

This is the last line of defense before any mutation reaches the cluster. It is
deliberately conservative and pure-stdlib (regex + string scanning) so it can be
unit-tested with no cluster and no LLM.

Two entry points:

* :func:`check_repair_plan(plan)`  -> (ok, reasons)
* :func:`check_playbook_yaml(text)` -> (ok, reasons)

A ``False`` result means *do not execute*; ``reasons`` explains why so the
coordinator can feed the feedback back to the model and ask for a safer plan.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shlex
import subprocess
from typing import Any, Dict, List, Tuple

import yaml

from .schemas import RepairAction, RepairPlan, ACTION_TYPES
from .cluster_client import ClusterClientContext, DEFAULT_CLUSTER_CONTEXT

# ---------------------------------------------------------------------------
# Forbidden patterns. Each tuple is (compiled_regex, human_reason). Patterns are
# matched case-insensitively against a normalized (single-spaced) text.
# ---------------------------------------------------------------------------

_FORBIDDEN = [
    (r"\bdelete\b\s+(namespace|ns)\b", "namespace deletion is forbidden"),
    (r"\bdelete\b\s+namespaces?\b", "namespace deletion is forbidden"),
    (r"\bdelete\b[^\n]*\b(pvc|persistentvolumeclaim|pv|persistentvolume)\b",
     "persistent volume / PVC deletion is forbidden"),
    (r"\bdelete\b[^\n]*\bcrd\b", "CRD deletion is forbidden"),
    (r"\bdelete\b[^\n]*customresourcedefinition", "CRD deletion is forbidden"),
    (r"\bdelete\b[^\n]*\bnodes?\b", "node deletion is forbidden"),
    # mass / wildcard deletes
    (r"\bdelete\b[^\n]*--all\b", "mass deletion (--all) is forbidden"),
    (r"\bdelete\b\s+pods?\b[^\n]*(-l\b|--selector)", "selector-based mass pod deletion is forbidden"),
    (r"\bdelete\b\s+(deployment|deploy|statefulset|daemonset|rs|replicaset)\b[^\n]*--all",
     "mass workload deletion is forbidden"),
    # database wipe
    (r"\b(drop\s+database|drop\s+table|truncate\s+table)\b", "database wipe is forbidden"),
    (r"\bmongo[^\n]*\bdropDatabase\b", "database wipe is forbidden"),
    (r"\bredis-cli[^\n]*\bflushall\b", "database wipe is forbidden"),
    (r"\bflushall\b", "database wipe is forbidden"),
    # node / host level destruction
    (r"\b(reboot|shutdown|halt|poweroff)\b", "node reboot / shutdown is forbidden"),
    (r"\bmkfs\b", "filesystem format is forbidden"),
    (r"\brm\s+-rf\b", "recursive force delete is forbidden"),
    (r"\bdd\s+if=", "raw dd writes are forbidden"),
    (r"\biptables\b", "iptables manipulation is forbidden"),
    # cluster-wide operations
    (r"--all-namespaces\b", "cluster-wide (--all-namespaces) operations are forbidden"),
    (r"\bdelete\b[^\n]*-A\b", "cluster-wide (-A) deletion is forbidden"),
    (r"\brollout\s+restart\b[^\n]*(-n\s+)?(default|all)\b", "cluster-wide rollout restart is forbidden"),
    # kube-system / control plane
    (r"\bkube-system\b", "modifying kube-system resources is forbidden"),
    (r"\bkube-public\b", "modifying kube-public resources is forbidden"),
    # service mesh / arbitrary remote code
    (r"disable[-_ ]?(istio|linkerd|service[-_ ]?mesh)", "disabling the service mesh is forbidden"),
    (r"curl[^\n]*\|\s*(sh|bash)\b", "curl|sh remote code execution is forbidden"),
    (r"wget[^\n]*\|\s*(sh|bash)\b", "wget|sh remote code execution is forbidden"),
    (r"\bchmod\s+777\b", "chmod 777 is forbidden"),
    (r"\bchown\s+-R\b", "recursive chown is forbidden"),
    (r"\bsudo\b", "sudo escalation is forbidden"),
    # unbounded loops in a playbook shell task
    (r"while\s+true", "unbounded while loops are forbidden"),
    (r"for\s*\(\s*;\s*;\s*\)", "unbounded for loops are forbidden"),
]

_FORBIDDEN_COMPILED = [(re.compile(p, re.IGNORECASE), reason) for p, reason in _FORBIDDEN]

# Namespaces that must never be touched regardless of intent.
_PROTECTED_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease")
_CHAOS_RESOURCES = {
    'StressChaos': 'stresschaos',
    'NetworkChaos': 'networkchaos',
    'PodChaos': 'podchaos',
    'IOChaos': 'iochaos',
}


def _normalize(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    # collapse whitespace so "delete   namespace" and newlines are caught uniformly
    return re.sub(r"\s+", " ", text)


def _scan_text(text: str) -> List[str]:
    norm = _normalize(text)
    reasons: List[str] = []
    for rx, reason in _FORBIDDEN_COMPILED:
        if rx.search(norm):
            reasons.append(reason)
    # de-duplicate while preserving order
    seen = set()
    out = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _namespace_from_tokens(tokens: List[str]) -> str | None:
    values=[]
    for i,token in enumerate(tokens):
        if token in {'-n','--namespace'} and i+1<len(tokens):values.append(tokens[i+1])
        elif token.startswith('--namespace='):values.append(token.split('=',1)[1])
    return values[0] if len(set(values))==1 else None


def _validate_typed_command(action, allowed_namespace: str | None) -> List[str]:
    command=(action.command or '').strip();label=f"action ({action.action_type})";reasons=[]
    if not command:
        return [] if action.action_type=='WaitAndVerify' else [f'{label}: executable action lacks a command']
    if any(mark in command for mark in (';','&&','||','`','$(', '>', '<', '|')):
        return [f'{label}: shell composition/redirection is not allowed']
    if '\n' in command or '\r' in command:
        return [f'{label}: command must be one physical line']
    if not re.fullmatch(r'[A-Za-z0-9_./:=,@%+ -]+',command):
        return [f'{label}: command contains shell expansion/control characters']
    try:tokens=shlex.split(command)
    except ValueError:return [f'{label}: command cannot be parsed']
    if not tokens or tokens[0]!='kubectl':return [f'{label}: only kubectl commands are executable']
    namespace=_namespace_from_tokens(tokens)
    expected=(action.namespace or allowed_namespace or '').strip()
    name_re=re.compile(r'^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$')
    if not expected or not name_re.fullmatch(expected):reasons.append(f'{label}: invalid namespace identifier')
    if action.target_name and not name_re.fullmatch(action.target_name):reasons.append(f'{label}: invalid target identifier')
    if action.target_container and not name_re.fullmatch(action.target_container):
        reasons.append(f'{label}: invalid target container identifier')
    if not namespace or not expected or namespace!=expected:
        reasons.append(f"{label}: command namespace '{namespace or '(missing/ambiguous)'}' does not match '{expected or '(missing)'}'")
    if action.action_type in {'RemoveInjectedStress','RecoverNetworkFault','RecoverPodFailure'}:
        resource=_CHAOS_RESOURCES.get(action.target_kind)
        if len(tokens)<4 or tokens[1]!='delete' or not resource or tokens[2]!=resource or tokens[3]!=action.target_name:
            reasons.append(f'{label}: delete command does not match typed chaos kind/name')
        allowed=['kubectl','delete',resource or '',action.target_name,'-n',expected]
        allowed_with_ignore=allowed+['--ignore-not-found']
        if tokens not in (allowed,allowed_with_ignore):
            reasons.append(f'{label}: delete command contains unsupported arguments or ordering')
    elif action.action_type=='RecoverConfigError':
        target=f'deployment/{action.target_name}'
        if not action.target_container:
            reasons.append(f'{label}: target container is required')
        if len(tokens)<4 or tokens[1:3]!=['set','resources'] or tokens[3]!=target or action.target_kind.lower()!='deployment':
            reasons.append(f'{label}: resource command does not match typed deployment target')
        canonical=['kubectl','set','resources',target,'-n',expected,
                   f'--containers={action.target_container}',
                   '--limits=cpu=500m,memory=512Mi','--requests=cpu=100m,memory=128Mi']
        if tokens!=canonical:
            reasons.append(f'{label}: resource command differs from the bounded restoration template')
    elif action.action_type=='WaitAndVerify':
        if len(tokens)<2 or tokens[1] not in {'get','top','logs'}:
            reasons.append(f'{label}: WaitAndVerify must be read-only')
        allowed_flags={'-n','--namespace','-l','--selector','--containers','--all-containers','--prefix=false','-o'}
        for token in tokens[2:]:
            if token.startswith('-') and token not in allowed_flags and not token.startswith(('--tail=','--namespace=','--selector=')):
                reasons.append(f'{label}: read-only probe contains unsupported flag {token}')
    else:
        reasons.append(f'{label}: executable semantics for this action type are not implemented')
    return reasons


def expected_playbook_commands(plan: RepairPlan) -> List[str]:
    commands=[]
    for action in plan.actions:
        if action.command:commands.append(action.command.strip())
        if action.action_type=='RecoverConfigError' and action.target_name:
            commands.append(f'kubectl rollout status deployment/{action.target_name} -n {action.namespace} --timeout=120s')
    return commands


def expected_precondition(action:RepairAction)->str|None:
    if action.action_type in {'RemoveInjectedStress','RecoverNetworkFault','RecoverPodFailure'}:
        resource=_CHAOS_RESOURCES.get(action.target_kind)
        return f'kubectl get {resource} {action.target_name} -n {action.namespace} -o json' if resource else None
    if action.action_type=='RecoverConfigError':
        return f'kubectl get deployment {action.target_name} -n {action.namespace} -o json'
    return None


def _cpu_millicores(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        return float(text[:-1]) if text.endswith('m') else float(text) * 1000.0
    except (TypeError, ValueError):
        return None


def _memory_mib(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    units = {'Ki': 1 / 1024, 'Mi': 1, 'Gi': 1024,
             'K': 1000 / (1024 ** 2), 'M': 1000 ** 2 / (1024 ** 2),
             'G': 1000 ** 3 / (1024 ** 2)}
    try:
        for suffix, factor in units.items():
            if text.endswith(suffix):
                return float(text[:-len(suffix)]) * factor
        return float(text) / (1024 ** 2)
    except (TypeError, ValueError):
        return None


def _snapshot_hash(value: Dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest()


def _sanitized_chaos_object(obj: Dict[str, Any]) -> Dict[str, Any]:
    metadata = obj.get('metadata') or {}
    safe_metadata = {
        key: copy.deepcopy(metadata[key])
        for key in ('name', 'namespace', 'labels', 'annotations')
        if key in metadata
    }
    return {
        'apiVersion': obj.get('apiVersion'),
        'kind': obj.get('kind'),
        'metadata': safe_metadata,
        'spec': copy.deepcopy(obj.get('spec') or {}),
    }


def _evaluate_object_precondition(
    action: RepairAction, obj: Dict[str, Any]
) -> Tuple[str, str, Dict[str, Any] | None]:
    """Evaluate the declared current-state predicate and capture an inverse."""
    predicate = action.precondition_predicate or {}
    metadata = obj.get('metadata') if isinstance(obj, dict) else None
    if not isinstance(metadata, dict):
        return 'UNKNOWN', 'precondition output lacks object metadata', None
    if (metadata.get('name') != action.target_name or
            metadata.get('namespace') != action.namespace):
        return 'UNKNOWN', 'returned object identity does not match the action target', None

    if predicate.get('type') == 'active_chaos_target':
        if obj.get('kind') != action.target_kind:
            return 'UNKNOWN', 'returned chaos kind does not match the action target', None
        if metadata.get('deletionTimestamp'):
            return 'FAIL', 'chaos object is already terminating', None
        selector = ((obj.get('spec') or {}).get('selector') or {})
        labels = selector.get('labelSelectors') or {}
        expected_service = predicate.get('selector_service')
        if not expected_service or labels.get('app') != expected_service:
            return 'FAIL', 'active chaos selector does not match the diagnosed service', None
        snapshot = _sanitized_chaos_object(obj)
        if not snapshot.get('apiVersion') or not snapshot.get('spec'):
            return 'UNKNOWN', 'chaos object cannot be captured for rollback', None
        capture = {
            'target': {'kind': action.target_kind, 'name': action.target_name,
                       'namespace': action.namespace},
            'observed_object_sha256': _snapshot_hash(obj),
            'rollback': {
                'policy': 'reapply_captured_object',
                'object': snapshot,
            },
        }
        return 'PASS', 'active target-bound chaos object observed', capture

    if predicate.get('type') == 'deployment_underprovisioned':
        if obj.get('kind') != 'Deployment':
            return 'UNKNOWN', 'returned object is not a Deployment', None
        containers = (((obj.get('spec') or {}).get('template') or {})
                      .get('spec') or {}).get('containers')
        if not isinstance(containers, list):
            return 'UNKNOWN', 'deployment containers are unavailable', None
        container_name = predicate.get('container_name')
        matches = [(index, row) for index, row in enumerate(containers)
                   if isinstance(row, dict) and row.get('name') == container_name]
        if len(matches) != 1:
            return 'UNKNOWN', 'declared target container is missing or ambiguous', None
        container_index, container = matches[0]
        resources = container.get('resources') or {}
        limits = resources.get('limits') or {}
        cpu = _cpu_millicores(limits.get('cpu'))
        memory = _memory_mib(limits.get('memory'))
        if cpu is None or memory is None:
            return 'UNKNOWN', 'container resource limits are not measurable', None
        if (cpu > float(predicate.get('max_cpu_millicores', -1)) or
                memory > float(predicate.get('max_memory_mib', -1))):
            return 'FAIL', 'deployment is not in the declared underprovisioned state', None
        captured_resources = copy.deepcopy(resources)
        capture = {
            'target': {'kind': 'Deployment', 'name': action.target_name,
                       'namespace': action.namespace,
                       'container_name': container_name,
                       'container_index': container_index},
            'observed_object_sha256': _snapshot_hash(obj),
            'rollback': {
                'policy': 'restore_captured_container_resources',
                'resources': captured_resources,
                'resources_were_present': 'resources' in container,
            },
        }
        return 'PASS', 'target container is underprovisioned', capture

    return 'UNKNOWN', 'no supported current-state predicate is declared', None


def run_preconditions(
    plan: RepairPlan,
    timeout: int = 15,
    *,
    client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT,
):
    """Read, parse and validate every current-state predicate before mutation."""
    from .safe_probe_agent import run_probes

    action_commands = [
        (index, action, command)
        for index, action in enumerate(plan.actions)
        for command in action.preconditions
    ]
    commands = [row[2] for row in action_commands]
    if any(action.action_type != 'WaitAndVerify' and not action.preconditions
           for action in plan.actions):
        return {'status': 'UNKNOWN', 'passed': False,
                'reason': 'mutating action lacks preconditions', 'results': [],
                'captures': []}
    results = run_probes(
        commands, timeout=timeout, client_context=client_context
    )
    failed: List[str] = []
    unknown: List[str] = []
    captures: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    if len(results) != len(action_commands):
        return {'status': 'UNKNOWN', 'passed': False,
                'reason': 'precondition result coverage mismatch',
                'results': results, 'captures': []}

    for (index, action, command), row in zip(action_commands, results):
        status = 'PASS'
        reason = 'read-only precondition completed'
        capture = None
        if row.get('command') != command:
            status, reason = 'UNKNOWN', 'precondition command/result identity mismatch'
        elif row.get('client_context') != client_context.to_dict():
            status, reason = 'UNKNOWN', 'precondition used a different cluster client context'
        elif not row.get('allowed'):
            status, reason = 'FAIL', 'precondition command was rejected by the allowlist'
        elif row.get('timed_out'):
            status, reason = 'UNKNOWN', 'precondition command timed out'
        elif type(row.get('returncode')) is not int:
            status, reason = 'UNKNOWN', 'precondition process status is unavailable'
        elif row.get('returncode') != 0:
            stderr = str(row.get('stderr') or '').lower()
            status = 'FAIL' if 'notfound' in stderr or 'not found' in stderr else 'UNKNOWN'
            reason = 'precondition target does not exist' if status == 'FAIL' else 'precondition command failed'
        elif not str(row.get('stdout') or '').strip():
            status, reason = 'UNKNOWN', 'precondition returned no observation'
        elif action.action_type != 'WaitAndVerify':
            try:
                obj = json.loads(str(row.get('stdout')))
            except (TypeError, ValueError, json.JSONDecodeError):
                status, reason = 'UNKNOWN', 'precondition output is not valid JSON'
            else:
                if not isinstance(obj, dict):
                    status, reason = 'UNKNOWN', 'precondition JSON is not an object'
                else:
                    status, reason, capture = _evaluate_object_precondition(action, obj)
        if status == 'FAIL':
            failed.append(command)
        elif status == 'UNKNOWN':
            unknown.append(command)
        if capture is not None:
            capture['action_index'] = index
            capture['command'] = command
            captures.append(capture)
        diagnostics.append({'action_index': index, 'command': command,
                            'status': status, 'reason': reason})

    status = 'UNKNOWN' if unknown else 'FAIL' if failed else 'PASS'
    mutating_indices = {index for index, action in enumerate(plan.actions)
                        if action.action_type != 'WaitAndVerify'}
    captured_indices = {row['action_index'] for row in captures}
    if status == 'PASS' and captured_indices != mutating_indices:
        status = 'UNKNOWN'
        unknown.append('rollback capture coverage mismatch')
    return {
        'status': status,
        'passed': status == 'PASS',
        'failed': failed,
        'unknown': unknown,
        'results': results,
        'diagnostics': diagnostics,
        'captures': captures,
        'client_context': client_context.to_dict(),
    }


def execute_rollback(
    precondition_result: Dict[str, Any],
    timeout: int = 30,
    *,
    client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT,
) -> Dict[str, Any]:
    """Execute only inverses derived from captured, target-bound live state."""
    rows: List[Dict[str, Any]] = []
    captures = precondition_result.get('captures') or []
    if (precondition_result.get('client_context') != client_context.to_dict()
            or not captures):
        return {'status': 'UNKNOWN', 'success': False,
                'reason': 'validated rollback captures are unavailable', 'results': []}
    for capture in reversed(captures):
        target = capture.get('target') or {}
        rollback = capture.get('rollback') or {}
        policy = rollback.get('policy')
        stdin = None
        if policy == 'reapply_captured_object':
            obj = rollback.get('object')
            if (not isinstance(obj, dict) or
                    (obj.get('metadata') or {}).get('name') != target.get('name') or
                    (obj.get('metadata') or {}).get('namespace') != target.get('namespace')):
                return {'status': 'UNKNOWN', 'success': False,
                        'reason': 'captured chaos rollback identity is invalid',
                        'results': rows}
            argv = ['kubectl', 'apply', '-n', target['namespace'], '-f', '-']
            stdin = json.dumps(obj, sort_keys=True)
        elif policy == 'restore_captured_container_resources':
            if not isinstance(rollback.get('resources'), dict):
                return {'status': 'UNKNOWN', 'success': False,
                        'reason': 'captured resource rollback is invalid',
                        'results': rows}
            index = target.get('container_index')
            if type(index) is not int or index < 0 or not target.get('container_name'):
                return {'status': 'UNKNOWN', 'success': False,
                        'reason': 'captured container identity is invalid',
                        'results': rows}
            base = f'/spec/template/spec/containers/{index}'
            patch = [{'op': 'test', 'path': base + '/name',
                      'value': target['container_name']}]
            if rollback.get('resources_were_present'):
                patch.append({'op': 'replace', 'path': base + '/resources',
                              'value': rollback['resources']})
            else:
                patch.append({'op': 'remove', 'path': base + '/resources'})
            argv = [
                'kubectl', 'patch', 'deployment', target.get('name', ''),
                '-n', target.get('namespace', ''), '--type=json',
                '--patch', json.dumps(patch, sort_keys=True, separators=(',', ':')),
            ]
        else:
            return {'status': 'UNKNOWN', 'success': False,
                    'reason': 'unsupported captured rollback policy',
                    'results': rows}
        try:
            result = subprocess.run(
                argv, input=stdin, capture_output=True, text=True,
                timeout=timeout, shell=False, env=client_context.environment()
            )
            rows.append({'argv': argv, 'returncode': result.returncode,
                         'stdout': result.stdout.strip(),
                         'stderr': result.stderr.strip(),
                         'client_context': client_context.to_dict()})
        except subprocess.TimeoutExpired:
            rows.append({'argv': argv, 'returncode': None, 'timed_out': True,
                         'client_context': client_context.to_dict()})
    success = bool(rows) and all(row.get('returncode') == 0 for row in rows)
    return {'status': 'PASS' if success else 'FAIL', 'success': success,
            'results': rows}


def expected_precondition_predicate(
    action: RepairAction, plan: RepairPlan
) -> Dict[str, Any] | None:
    if action.action_type in {'RemoveInjectedStress', 'RecoverNetworkFault',
                              'RecoverPodFailure'}:
        return {
            'type': 'active_chaos_target',
            'kind': action.target_kind,
            'name': action.target_name,
            'namespace': action.namespace,
            'selector_service': plan.rca_report.root_cause_service,
        }
    if action.action_type == 'RecoverConfigError':
        return {
            'type': 'deployment_underprovisioned',
            'kind': 'Deployment',
            'name': action.target_name,
            'namespace': action.namespace,
            'container_name': action.target_container,
            'max_cpu_millicores': 2,
            'max_memory_mib': 200,
        }
    return None


def check_playbook_yaml(playbook: str, *, allowed_namespace: str | None = None,
                        expected_commands: List[str] | None = None,
                        client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT) -> Tuple[bool, List[str]]:
    """Parse and validate the executable Ansible task structure and commands."""
    if not playbook or not isinstance(playbook, str) or not playbook.strip():
        return False, ["empty playbook"]
    reasons=_scan_text(playbook)
    if any(marker in playbook for marker in ('{{','}}','{%','%}')):
        reasons.append('Jinja expressions are not allowed in executable playbooks')
    try:data=yaml.safe_load(playbook)
    except yaml.YAMLError as exc:return False,reasons+[f'invalid YAML: {type(exc).__name__}']
    if not isinstance(data,list) or len(data)!=1 or any(not isinstance(play,dict) for play in data):
        return False,reasons+['playbook must be a non-empty list of plays']
    shell_commands=[]
    allowed_modules={'ansible.builtin.shell','ansible.builtin.debug'}
    meta_keys={'name','register','ignore_errors'}
    registered=set()
    for play in data:
        allowed_play_keys={'name','hosts','gather_facts','environment','tasks'}
        extras=set(play)-allowed_play_keys
        if extras:reasons.append('play contains unsupported executable fields: '+','.join(sorted(extras)))
        if play.get('hosts')!='all' or play.get('gather_facts') is not False:
            reasons.append('play must target hosts=all with gather_facts=false')
        if play.get('environment')!={'KUBECONFIG':client_context.kubeconfig}:
            reasons.append('play environment must contain only the fixed KUBECONFIG')
        tasks=play.get('tasks')
        if not isinstance(tasks,list):reasons.append('play tasks must be a list');continue
        for task in tasks:
            if not isinstance(task,dict):reasons.append('task must be an object');continue
            modules=[key for key in task if key not in meta_keys]
            if len(modules)!=1 or modules[0] not in allowed_modules:
                reasons.append('task must use exactly one allowed Ansible module');continue
            if modules[0]=='ansible.builtin.shell':
                command=task[modules[0]]
                if not isinstance(command,str) or not command.strip():reasons.append('shell task has no command');continue
                trimmed=command.rstrip('\r\n')
                if '\n' in trimmed or '\r' in trimmed:
                    reasons.append('shell command must be one physical line')
                if not re.fullmatch(r'[A-Za-z0-9_./:=,@%+ -]+',trimmed):
                    reasons.append('shell command contains expansion/control characters')
                shell_commands.append(trimmed)
                register=task.get('register')
                if register is not None:
                    if not isinstance(register,str) or not re.fullmatch(r'planrca_step_\d+',register):
                        reasons.append('shell register name is outside the deterministic renderer schema')
                    else:registered.add(register)
                if task.get('ignore_errors') is not False:reasons.append('shell task must explicitly set ignore_errors=false')
                if allowed_namespace:
                    try:tokens=shlex.split(command)
                    except ValueError:reasons.append('shell command cannot be parsed');continue
                    if _namespace_from_tokens(tokens)!=allowed_namespace:
                        reasons.append(f"shell command namespace does not match '{allowed_namespace}'")
            else:
                payload=task[modules[0]]
                if not isinstance(payload,dict) or set(payload)!={'var'} or not isinstance(payload.get('var'),str):
                    reasons.append('debug task must contain exactly one renderer-generated var reference')
                else:
                    match=re.fullmatch(r'(planrca_step_\d+)\.stdout_lines',payload['var'])
                    if not match or match.group(1) not in registered:
                        reasons.append('debug task references an unknown shell result')
                if set(task)-{'name','ansible.builtin.debug'}:
                    reasons.append('debug task contains unsupported metadata')
    if expected_commands is not None:
        expected=[command.rstrip('\r\n') for command in expected_commands]
        if shell_commands!=expected:reasons.append('rendered shell commands do not exactly match the validated typed plan')
    return (len(reasons) == 0), reasons


def check_repair_plan(plan: RepairPlan, allowed_namespace: str | None = None) -> Tuple[bool, List[str]]:
    """Validate a typed repair plan before it is turned into a playbook.

    Checks: known action types, risk bounded to low/medium, no destructive
    command strings, and (if ``allowed_namespace`` is given) that no action
    targets a different / protected namespace.
    """
    reasons: List[str] = []
    if plan is None or not getattr(plan, "actions", None):
        return False, ["repair plan has no actions"]

    for idx, action in enumerate(plan.actions):
        label = f"action[{idx}] ({action.action_type})"

        if action.action_type not in ACTION_TYPES:
            reasons.append(f"{label}: unknown action_type")

        if action.risk == "high":
            reasons.append(f"{label}: high-risk actions are not allowed")

        # Namespace scoping
        ns = (action.namespace or "").strip()
        if allowed_namespace and not ns:
            reasons.append(f"{label}: action namespace is required")
        if ns in _PROTECTED_NAMESPACES:
            reasons.append(f"{label}: targets protected namespace '{ns}'")
        if allowed_namespace and ns and ns != allowed_namespace:
            reasons.append(
                f"{label}: targets namespace '{ns}' but incident namespace is '{allowed_namespace}'"
            )

        # Scan embedded command + patch for dangerous content.
        blob_parts = [action.command or "", action.rationale or "", action.expected_effect or ""]
        if action.patch:
            try:
                import json as _json
                blob_parts.append(_json.dumps(action.patch, ensure_ascii=False))
            except Exception:
                blob_parts.append(str(action.patch))
        for reason in _scan_text(" \n ".join(blob_parts)):
            reasons.append(f"{label}: {reason}")
        reasons.extend(_validate_typed_command(action,allowed_namespace))
        if action.action_type!='WaitAndVerify':
            if not action.evidence_refs:reasons.append(f'{label}: mutating action lacks evidence references')
            if not action.preconditions:reasons.append(f'{label}: mutating action lacks preconditions')
            if len(action.rollback_hint.strip())<12 or action.rollback_hint.strip().lower() in {'x','todo','unknown'}:reasons.append(f'{label}: mutating action lacks a rollback explanation')
            if len(action.expected_effect.strip())<12 or action.expected_effect.strip().lower() in {'x','todo','unknown'}:reasons.append(f'{label}: mutating action lacks concrete expected effect')
            expected_probe=expected_precondition(action)
            if action.preconditions!=([expected_probe] if expected_probe else []):
                reasons.append(f'{label}: precondition is not bound to the typed action target')
            expected_predicate=expected_precondition_predicate(action,plan)
            if action.precondition_predicate!=expected_predicate:
                reasons.append(f'{label}: current-state predicate is not bound to the typed action')
            expected_rollback={
                'RemoveInjectedStress':'reapply_captured_object',
                'RecoverNetworkFault':'reapply_captured_object',
                'RecoverPodFailure':'reapply_captured_object',
                'RecoverConfigError':'restore_captured_container_resources',
            }.get(action.action_type)
            if action.rollback_policy!=expected_rollback:
                reasons.append(f'{label}: rollback policy is not the executable inverse for this action')
            referenced=[];invalid_refs=[];observed_refs=[];prediction_refs=[]
            for ref in action.evidence_refs:
                if ref.startswith('evidence:'):
                    try:
                        index=int(ref.split(':',1)[1])
                        if index < 0 or index >= len(plan.rca_report.evidence):
                            raise IndexError(index)
                        item=plan.rca_report.evidence[index]
                        referenced.append(item);observed_refs.append(item)
                    except (ValueError,IndexError):invalid_refs.append(ref)
                elif ref.startswith('rca_prediction:'):
                    expected_ref=(f"rca_prediction:{plan.rca_report.root_cause_service or '-'}:"
                                  f"{plan.rca_report.failure_category or '-'}")
                    if ref!=expected_ref:invalid_refs.append(ref)
                    else:referenced.append(ref);prediction_refs.append(ref)
                else:invalid_refs.append(ref)
            if invalid_refs:reasons.append(f'{label}: evidence references do not resolve in the RCA report')
            inferred_basis=('observed' if observed_refs and not prediction_refs else
                            'prediction_only' if prediction_refs and not observed_refs else 'none')
            if action.evidence_basis!=inferred_basis:
                reasons.append(f'{label}: evidence basis does not distinguish observed from prediction-only support')
            report_service=(plan.rca_report.root_cause_service or '').lower();target=action.target_name.lower()
            bound=False
            for ref in referenced:
                if isinstance(ref,str):bound|=target in ref.lower() or (report_service and report_service in ref.lower())
                else:
                    text=f"{ref.service} {ref.finding}".lower();bound|=target in text or (report_service and report_service in text)
            if not bound:reasons.append(f'{label}: resolved evidence is not bound to the action target/report service')
        for pidx,command in enumerate(action.preconditions):
            probe=RepairAction(action_type='WaitAndVerify',namespace=allowed_namespace or action.namespace,command=command)
            for reason in _validate_typed_command(probe,allowed_namespace or action.namespace):
                reasons.append(f'{label} precondition[{pidx}]: {reason}')

    for idx,command in enumerate(plan.verification_plan):
        probe=RepairAction(action_type='WaitAndVerify',namespace=allowed_namespace or '',command=command)
        for reason in _validate_typed_command(probe,allowed_namespace):
            reasons.append(f'verification[{idx}]: {reason}')

    # de-duplicate
    seen = set()
    out = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return (len(out) == 0), out
