# Namespace package marker for the standalone rca2Remed source tree.
#
# When PlanRCAThinkRemed is installed into MicroRemed (via symlink under
# MicroRemed/methods/PlanRCAThinkRemed), this file is NOT used -- MicroRemed's
# own methods/__init__.py is. It exists only so the package is importable as
# `methods.PlanRCAThinkRemed.*` when running the offline tests from the
# rca2Remed/ root. Keep it empty so importing it never pulls in MicroRemed deps.
