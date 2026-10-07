"""发布评审：单项服务延迟容忍度。

先按现有规则求基线排程（与 /api/schedule 首项完全一致）；再对每个未锁定服务，
保持其他服务的基线开始时刻不变，把该服务的部署开始时刻逐分钟推迟（0..上限），
每一分钟都用与排程完全相同的建模检查全部既有约束——候选时间窗、依赖先后、
团队禁发、维护冻结、共享资源分时容量（部署与回滚占用均按原规则判断）：

- 最大连续可行延迟：从 0 分钟起连续仍满足全部约束的最大分钟数（不超过上限）；
- 首个失效分钟：上限内第一个不可行的延迟分钟，及其触发的约束组合
  （不可行假设核，与 /api/schedule 无解时的 blockers 同源）；
  上限内逐分钟均可行时为空；
- 一旦某分钟失效即停止扫描：即使更晚的时段重新可行，也不越过该失效分钟，
  最大延迟只统计从 0 分钟起的连续可行段。

锁定（已确认）服务不参与推迟，标记为不可推迟。基线无解或未定时不输出容忍度，
沿用原有的阻塞组合 / 未定状态。
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone

from ortools.sat.python import cp_model

from .scheduler import SolveResult, _build_model, solve
from .validator import Normalized


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def solve_baseline(norm: Normalized) -> SolveResult:
    """按现有规则求基线排程。

    备选方案不影响首项结果，这里不启用备选，只求与 /api/schedule 首项一致的基线。
    """
    return solve(replace(norm, alternative_count=None))


def _check_delayed(norm: Normalized, baseline_starts: dict[str, int],
                   sid: str, delayed_start: int, deadline: float
                   ) -> tuple[str, list[dict] | None]:
    """把 sid 的部署开始固定在 delayed_start、其他服务固定在基线时刻，做纯可行性检查。

    返回 (status, blockers)：status ∈ feasible / infeasible / unknown；
    infeasible 时 blockers 为该分钟触发的约束组合（sufficient assumption core，
    与排程无解时的阻塞核同一来源）；unknown 表示时限内未能判定。
    """
    ctx = _build_model(norm)
    for other, st in baseline_starts.items():
        # 其他服务（含锁定服务）固定在基线开始时刻；被测服务固定在推迟后的时刻。
        ctx.m.add(ctx.starts[other] == (delayed_start if other == sid else st))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, deadline - time.monotonic())
    solver.parameters.num_search_workers = 8
    status = solver.solve(ctx.m)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return "feasible", None
    if status == cp_model.INFEASIBLE:
        core = solver.sufficient_assumptions_for_infeasibility()
        if core:
            blockers = [{"code": code, "message": message}
                        for code, message in
                        (ctx.assumption_map[lit] for lit in core)]
        else:
            blockers = [{"code": code, "message": message}
                        for code, message in ctx.assumption_map.values()]
        return "infeasible", blockers
    return "unknown", None


def scan_tolerances(norm: Normalized, baseline_starts: dict[str, int],
                    max_delay_minutes: int, deadline: float) -> list[dict] | None:
    """逐服务扫描延迟容忍度；返回 None 表示扫描在时限内未能完成（不输出容忍度）。

    每个未锁定服务从延迟 1 分钟起逐分钟检查（延迟 0 即基线本身，必然可行），
    首次失效即停止该服务的扫描；锁定服务直接标记为不可推迟。
    """
    results: list[dict] = []
    ordered = sorted(norm.services, key=lambda s: (baseline_starts[s], s))
    for sid in ordered:
        svc = norm.services[sid]
        base = baseline_starts[sid]
        if sid in norm.pinned_starts:
            # 已确认锁定服务：必须准时部署，不可推迟，不计算容忍度。
            results.append({
                "service_id": sid,
                "name": svc.name,
                "locked": True,
                "baseline_start_utc": _iso(base),
                "max_delay_minutes": None,
                "first_failure_minute": None,
                "first_failure_start_utc": None,
                "triggered": None,
            })
            continue

        occ = svc.duration + svc.rollback
        last_window_end = max(w.end for w in svc.windows)
        # 首个使占用终点越过该服务最后候选窗终点的延迟分钟数：从该分钟起部署+回滚
        # 占用必然越出全部候选窗（建模时间轴的硬上界），直接触发候选窗约束，
        # 无需求解即可判定。
        d_overflow = (last_window_end - occ - base) // 60 + 1

        max_ok = 0
        first_fail: int | None = None
        triggered: list[dict] | None = None
        for d in range(1, max_delay_minutes + 1):
            if d >= d_overflow:
                first_fail = d
                triggered = [{
                    "code": f"window:{sid}",
                    "message": (
                        f"服务 {svc.name}（{sid}）推迟 {d} 分钟后，部署+回滚占用将结束于 "
                        f"{_iso(base + 60 * d + occ)} UTC，超出其最后候选窗终点 "
                        f"{_iso(last_window_end)} UTC，不再完整落在任何候选时间窗内"
                    ),
                }]
                break
            if time.monotonic() >= deadline:
                return None
            st, blockers = _check_delayed(norm, baseline_starts, sid,
                                          base + 60 * d, deadline)
            if st == "unknown":
                return None
            if st == "infeasible":
                first_fail = d
                triggered = blockers
                break
            max_ok = d

        results.append({
            "service_id": sid,
            "name": svc.name,
            "locked": False,
            "baseline_start_utc": _iso(base),
            "max_delay_minutes": max_ok,
            "first_failure_minute": first_fail,
            "first_failure_start_utc": (
                _iso(base + 60 * first_fail) if first_fail is not None else None),
            "triggered": triggered,
        })
    return results
