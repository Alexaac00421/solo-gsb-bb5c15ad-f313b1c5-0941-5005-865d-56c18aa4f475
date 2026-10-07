"""FastAPI 入口：HTTP API + 操作页面静态托管。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    AlternativePlan,
    AlternativesInfo,
    BlockingConstraint,
    ScheduleDiffRequest,
    ScheduleDiffResponse,
    ScheduleItem,
    ScheduleRequest,
    ScheduleResponse,
    ServiceTolerance,
    ToleranceRequest,
    ToleranceResponse,
)
from .diff import build_diff, run_request
from .times import to_local_display
from .tolerance import scan_tolerances, solve_baseline
from .validator import validate_and_normalize

app = FastAPI(
    title="跨时区发布窗口编排",
    description="在时区/DST、依赖先后、禁发、冻结与资源约束下求可执行发布排程",
    version="1.0.0",
)

WEB_DIR = Path(__file__).parent / "web"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/schedule", response_model=ScheduleResponse)
def schedule(req: ScheduleRequest) -> ScheduleResponse:
    run = run_request(req)

    if run.empty:
        return ScheduleResponse(
            feasible=False, status="infeasible",
            blockers=[BlockingConstraint(code="empty_services",
                                        message="至少需要一个服务")],
        )

    if run.validation_errors is not None:
        # 422：输入本身有问题（DST 重复/不存在、坏时区、依赖环、区间非法等）。
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,
                            content={"errors": run.validation_errors})

    assert run.norm is not None and run.result is not None
    norm, result = run.norm, run.result

    if not result.feasible:
        return ScheduleResponse(
            feasible=False,
            status=result.status,
            blockers=[BlockingConstraint(**b) for b in (result.blockers or [])],
        )

    def build_plan(starts: dict[str, int]) -> list[ScheduleItem]:
        return [_build_item(norm, sid, st)
                for sid, st in sorted(starts.items(), key=lambda kv: kv[1])]

    # 备选方案（仅在请求给出 alternative_count 时非空）：plans[0] 即首项，
    # 与顶层 schedule 内容一致，未启用备选时 alternatives 为 null，响应保持兼容。
    alternatives = None
    if result.plans is not None:
        alternatives = AlternativesInfo(
            requested=norm.alternative_count,
            min_gap_minutes=norm.min_gap_seconds // 60,
            status=result.alternatives_status,
            plans=[AlternativePlan(index=i, schedule=build_plan(p))
                   for i, p in enumerate(result.plans)],
        )

    return ScheduleResponse(
        feasible=True, status=result.status,
        schedule=build_plan(result.starts),
        alternatives=alternatives,
    )


def _build_item(norm, sid: str, st: int) -> ScheduleItem:
    s = norm.services[sid]
    end = st + s.duration
    occ = st + s.duration + s.rollback
    used = next(
        (w for w in s.windows if w.start <= st and occ <= w.end),
        None,
    )
    window_text = (
        f"{_iso(used.start)} ~ {_iso(used.end)} UTC（{used.label}）"
        if used else "（窗口信息缺失）"
    )
    return ScheduleItem(
        service_id=sid,
        name=s.name,
        deploy_start_utc=_iso(st),
        deploy_end_utc=_iso(end),
        occupied_until_utc=_iso(occ),
        local_time=to_local_display(datetime.fromtimestamp(st, tz=timezone.utc), s.timezone),
        timezone=s.timezone,
        window_used=window_text,
        resources=s.resources,
    )


@app.post("/api/schedule/tolerance", response_model=ToleranceResponse)
def schedule_tolerance(body: ToleranceRequest):
    """发布评审：单项服务延迟容忍度。

    先按现有规则求基线排程；再对每个未锁定服务，保持其他服务的基线开始时刻不变，
    逐分钟推迟其部署开始时刻（0..max_delay_minutes），给出从 0 分钟起连续仍满足
    全部约束的最大延迟、上限内首个失效分钟及触发的约束组合。锁定服务标为不可推迟。
    基线无解/未定时返回原有阻塞组合/未定状态，不输出容忍度。
    """
    req = body.request

    if not req.services:
        return ToleranceResponse(
            feasible=False, status="infeasible",
            blockers=[BlockingConstraint(code="empty_services",
                                        message="至少需要一个服务")],
        )

    norm, errors = validate_and_normalize(req)
    if errors:
        # 422：输入本身有问题，沿用现有校验错误格式。
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,
                            content={"errors": errors})

    # 基线：与 /api/schedule 首项完全一致；无解/未定时沿用原有阻塞/未定响应，
    # 不输出容忍度。
    baseline = solve_baseline(norm)
    if not baseline.feasible:
        return ToleranceResponse(
            feasible=False,
            status=baseline.status,
            blockers=[BlockingConstraint(**b) for b in (baseline.blockers or [])],
        )

    assert baseline.starts is not None
    starts = baseline.starts

    # 逐服务逐分钟扫描；扫描自身使用一份独立的 max_solve_seconds 预算。
    deadline = time.monotonic() + norm.max_solve_seconds
    tolerances = scan_tolerances(norm, starts, body.max_delay_minutes, deadline)
    if tolerances is None:
        return ToleranceResponse(
            feasible=False, status="unknown",
            blockers=[BlockingConstraint(
                code="solver_unknown",
                message="延迟容忍度扫描在 max_solve_seconds 时限内未能完成，"
                        "按约定不输出容忍度；可增大 max_solve_seconds 或减小 "
                        "max_delay_minutes 后重试")],
        )

    return ToleranceResponse(
        feasible=True,
        status=baseline.status,
        baseline=[_build_item(norm, sid, st)
                  for sid, st in sorted(starts.items(), key=lambda kv: kv[1])],
        max_delay_minutes=body.max_delay_minutes,
        tolerances=[ServiceTolerance(**t) for t in tolerances],
    )


@app.post("/api/schedule/diff", response_model=ScheduleDiffResponse)
def schedule_diff(body: ScheduleDiffRequest):
    """变更影响预演：分别求解 current / modified，按服务 ID 对齐后返回时刻差异。

    任一请求输入错误或无解时，只返回对应一侧的完整错误/阻塞约束组合，不输出差异；
    两份请求都无变化时返回空差异（changed/added/removed 均为空列表）。
    """
    current = run_request(body.current)
    modified = run_request(body.modified)

    resp = build_diff(current, modified)
    if not resp.feasible and resp.failures:
        # 只要存在输入错误（422 类）即以 422 返回；纯无解/超时仍为 200。
        if any(f.kind == "validation" for f in resp.failures.values()):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=422,
                content=resp.model_dump(mode="json"),
            )
    return resp


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
