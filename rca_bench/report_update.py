"""Render the live results section and splice it into the technical report.

The report contains a marked region::

    <!-- RESULTS:AUTO:START -->
    ...auto-generated tables...
    <!-- RESULTS:AUTO:END -->

The full-lifecycle runner calls :func:`update_report_file` every N incidents so
the technical report stays current during a long run.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .metrics import IncidentEval, evaluate

START = "<!-- RESULTS:AUTO:START -->"
END = "<!-- RESULTS:AUTO:END -->"

_DIFF_ORDER = ("easy", "medium", "hard")


def render_results(rca_by_diff: Dict[str, List[IncidentEval]],
                   rem_by_diff: Dict[str, List[Tuple[str, bool]]], *,
                   n_done: int, n_total: int, rca_method: str,
                   settle: int, hide_artifacts: bool) -> str:
    all_evals = [e for evs in rca_by_diff.values() for e in evs]
    overall = evaluate(all_evals, ks=(1, 3, 5), per_category=True) if all_evals else None

    L: List[str] = []
    mode = "telemetry-only，隐藏注入伪影" if hide_artifacts else "暴露注入伪影（上界）"
    L.append(f"> **自动更新**：已完成 **{n_done}/{n_total}** incidents。RCA 方法 `{rca_method}`，"
             f"{mode}，settle={settle}s。修复为 report-driven（输入仅 RCA 报告，无 chaos selector，oracle 仅验证）。")

    # --- RCA per difficulty ---
    L.append("\n### RCA 定位（AC@k / MRR，oracle 仅打分）\n")
    L.append("| 难度 | n | AC@1 | AC@3 | AC@5 | MRR | cat-acc |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for diff in _DIFF_ORDER:
        evs = rca_by_diff.get(diff, [])
        if not evs:
            continue
        m = evaluate(evs, ks=(1, 3, 5), per_category=False)
        ca = f"{m.category_accuracy:.3f}" if m.category_accuracy is not None else "n/a"
        L.append(f"| {diff} | {m.count} | {m.ac_at_k[1]:.3f} | {m.ac_at_k[3]:.3f} | "
                 f"{m.ac_at_k[5]:.3f} | {m.mrr:.3f} | {ca} |")
    if overall:
        ca = f"{overall.category_accuracy:.3f}" if overall.category_accuracy is not None else "n/a"
        L.append(f"| **OVERALL** | {overall.count} | **{overall.ac_at_k[1]:.3f}** | "
                 f"{overall.ac_at_k[3]:.3f} | {overall.ac_at_k[5]:.3f} | **{overall.mrr:.3f}** | {ca} |")

    # --- RCA per fault type ---
    if overall and overall.by_category:
        L.append("\n### RCA 分故障类型（AC@k，整体）\n")
        L.append("| 故障类型 | n | AC@1 | AC@3 | AC@5 |")
        L.append("|---|---:|---:|---:|---:|")
        for c, m in overall.by_category.items():
            L.append(f"| {c} | {m.count} | {m.ac_at_k[1]:.3f} | {m.ac_at_k[3]:.3f} | {m.ac_at_k[5]:.3f} |")

    # --- Remediation (conditioned on predicted report, not bounded by top-1) ---
    all_rem = [r for rs in rem_by_diff.values() for r in rs]
    if all_rem:
        L.append("\n### 修复成功率（report-driven，故障状态检查）\n")
        L.append("| 故障类型 | 成功/注入 | 成功率 |")
        L.append("|---|---:|---:|")
        bytype: Dict[str, List[int]] = {}
        for ft, ok in all_rem:
            d = bytype.setdefault(ft, [0, 0])
            d[1] += 1
            d[0] += 1 if ok else 0
        for ft in sorted(bytype):
            s, n = bytype[ft]
            L.append(f"| {ft} | {s}/{n} | {100 * s / n:.1f}% |")
        s = sum(1 for _, ok in all_rem if ok)
        n = len(all_rem)
        L.append(f"| **OVERALL** | {s}/{n} | **{100 * s / n:.1f}%** |")
        L.append("\n> 修复以 RCA 预测的(服务, 类别)为输入。当前 Chaos Mesh 清理动作按类别定位注入资源，"
                 "不依赖预测服务；配置修改使用预测服务。因此恢复率不受服务 AC@1 严格上界约束。"
                 "验证成功是故障状态检查结果，不等同于动作执行成功、因果归因或业务 SLO 恢复。")
    return "\n".join(L)


def update_report_file(path: str, section_md: str) -> bool:
    """Replace the region between the markers with ``section_md``. Never raises."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            txt = fh.read()
        if START not in txt or END not in txt:
            return False
        pre = txt.split(START)[0]
        post = txt.split(END, 1)[1]
        new = pre + START + "\n\n" + section_md + "\n\n" + END + post
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        return True
    except Exception as exc:  # pragma: no cover
        print(f"[report_update] failed: {exc}")
        return False
