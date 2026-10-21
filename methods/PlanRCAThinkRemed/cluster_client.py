"""Single declared Kubernetes client context for guarded repair execution.

Both read-side preconditions and the rendered mutation playbook import these
constants.  Keeping the context in one module prevents a precondition from
observing one cluster while the mutation is sent to another one.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Dict


@dataclass(frozen=True)
class ClusterClientContext:
    kubeconfig: str = "/etc/rancher/k3s/k3s.yaml"

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    def environment(self) -> Dict[str, str]:
        env = dict(os.environ)
        env["KUBECONFIG"] = self.kubeconfig
        return env


DEFAULT_CLUSTER_CONTEXT = ClusterClientContext()
KUBECONFIG = DEFAULT_CLUSTER_CONTEXT.kubeconfig
