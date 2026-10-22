"""PlanRCAThinkRemed: a unified RCA + auto-remediation SRE method for MicroRemed.

This package is the *source of truth*. It is wired into the MicroRemed benchmark
either by a symlink at ``MicroRemed/methods/PlanRCAThinkRemed`` or by adding the
rca2Remed root to ``sys.path``; MicroRemed's ``methods/remediate.py`` then calls
``methods.PlanRCAThinkRemed.coordinator.remediate_failure(...)``.

IMPORTANT: keep this ``__init__`` import-light. The coordinator and LLM/cluster
agents are imported lazily (by MicroRemed importing the submodule directly), so
that the pure modules -- schemas, safety_guard, cloudops_retriever, prompts --
can be imported in an offline test environment without MicroRemed, kubernetes,
httpx, or ansible being installed.
"""

__all__ = ["coordinator"]
__version__ = "0.1.0"
