"""Safe, read-only probe execution for the RCA phase.

Wraps MicroRemed's existing ``methods.ThinkRemed.probe_agent.get_probe_response``
(falling back to a local subprocess runner if unavailable) but enforces a strict
allowlist/denylist FIRST. The RCA probe phase must never mutate cluster state;
all mutations happen later through the safety-checked repair playbook only.

Pure to import (the ThinkRemed runner is imported lazily inside ``_raw_run``), so
this module can be imported without MicroRemed installed.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any, Dict, List, Union

from .cluster_client import ClusterClientContext, DEFAULT_CLUSTER_CONTEXT

# Command prefixes that are permitted (read-only diagnostics).
_ALLOWED_PREFIXES = (
    "kubectl get",
    "kubectl describe",
    "kubectl logs",
    "kubectl top",
    "kubectl rollout status",
    "kubectl api-resources",
    "kubectl version",
    "kubectl cluster-info",
    "helm list",
    "helm status",
)

# kubectl exec is only allowed when the remote command is one of these read-only probes.
_ALLOWED_EXEC_TOOLS = (
    "curl", "wget", "nslookup", "dig", "cat /etc/resolv.conf", "ping", "getent",
    "env", "printenv", "ls", "nc -zv",
)

# Local file reads (cat/grep) restricted to known manifest locations.
_ALLOWED_LOCAL_FILE_RE = re.compile(
    r"^(cat|grep|head|tail)\b[^|;&]*\b("
    r"chaos/templates/[^\s]+|envs/[^\s]+|inventory\.ini|"
    r"[^\s]*-config\.ya?ml|[^\s]*manifests?[^\s]*\.ya?ml)"
)

# Substrings that, if present anywhere, immediately reject the command.
_DENY_SUBSTRINGS = (
    "delete", "rm -rf", "shutdown", "reboot", "mkfs", " dd ", "dd if=", "iptables",
    "kubectl apply", "kubectl patch", "kubectl scale", "kubectl rollout restart",
    "kubectl delete", "kubectl edit", "kubectl replace", "kubectl create",
    "chmod 777", "chown -r", "sudo", "| sh", "|sh", "| bash", "|bash",
    "kubectl cp", "kubectl drain", "kubectl cordon", "kubectl taint",
    ">/", "> /", "tee ", "truncate",
)


def is_allowed(cmd: str) -> bool:
    """Return True iff ``cmd`` is a permitted read-only probe."""
    if not cmd or not isinstance(cmd, str):
        return False
    c = " ".join(cmd.strip().split())
    low = c.lower()

    # Hard denials win over everything.
    for bad in _DENY_SUBSTRINGS:
        if bad in low:
            return False
    # No shell chaining / piping into mutators or arbitrary code.
    if "&&" in c or ";" in c or "`" in c or "$(" in c:
        return False

    # kubectl exec ... -- <read-only tool>
    if low.startswith("kubectl exec"):
        if " -- " not in c:
            return False
        remote = c.split(" -- ", 1)[1].strip().lower()
        return any(remote.startswith(tool) for tool in _ALLOWED_EXEC_TOOLS)

    # Local manifest reads.
    if low.startswith(("cat", "grep", "head", "tail")):
        return bool(_ALLOWED_LOCAL_FILE_RE.match(c))

    # General allowlist.
    return any(low.startswith(prefix) for prefix in _ALLOWED_PREFIXES)


def _structured_run(
    cmd: str,
    timeout: int,
    *,
    client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT,
) -> Dict[str, Any]:
    """Execute a vetted probe without a shell and preserve process evidence."""
    base: Dict[str, Any] = {
        "command": cmd,
        "allowed": True,
        "returncode": None,
        "timed_out": False,
        "stdout": "",
        "stderr": "",
        "client_context": client_context.to_dict(),
    }
    try:
        argv = shlex.split(cmd)
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=client_context.environment(),
        )
        base.update(
            returncode=int(result.returncode),
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
    except subprocess.TimeoutExpired as exc:
        base.update(
            timed_out=True,
            stdout=(exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
        )
    except Exception as exc:  # pragma: no cover - defensive process failure
        base["stderr"] = f"{type(exc).__name__}: {exc}"
    body = base["stdout"] if base["returncode"] == 0 else (
        f"STDOUT:\n{base['stdout']}\nSTDERR:\n{base['stderr']}"
    )
    if base["timed_out"]:
        body = "time out"
    base["output"] = f"command:{cmd}\nresponse:{body}\n"
    return base


def run_probe(cmd: str, timeout: int = 15, *,
              client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT) -> str:
    """Run a single probe command if (and only if) it is read-only-safe."""
    if not is_allowed(cmd):
        return (
            f"command:{cmd}\nresponse: REJECTED by safe_probe_agent "
            f"(probe must be read-only; mutations are not allowed during RCA)\n"
        )
    return _structured_run(cmd, timeout, client_context=client_context)["output"]


def run_probes(cmds: Union[str, List[str]], timeout: int = 15, *,
               client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT) -> List[Dict[str, Any]]:
    """Run a batch of probe commands, returning structured per-command results."""
    if isinstance(cmds, str):
        # Split on ';' the way MicroRemed's probe agent does, but we re-vet each.
        cmd_list = [c.strip() for c in cmds.split(";") if c.strip()]
    else:
        cmd_list = [str(c).strip() for c in (cmds or []) if str(c).strip()]

    results: List[Dict[str, Any]] = []
    for cmd in cmd_list:
        allowed = is_allowed(cmd)
        if allowed:
            results.append(_structured_run(
                cmd, timeout, client_context=client_context
            ))
        else:
            results.append({
                "command": cmd,
                "allowed": False,
                "returncode": None,
                "timed_out": False,
                "stdout": "",
                "stderr": "probe rejected by allowlist",
                "client_context": client_context.to_dict(),
                "output": f"command:{cmd}\nresponse: REJECTED (not read-only safe)\n",
            })
    return results
