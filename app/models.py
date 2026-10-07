"""API 输入/输出数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WindowIn(BaseModel):
    start: str = Field(..., description="ISO 8601 时间；不带偏移量时按时区字段解析")
    end: str
    timezone: str = Field("UTC", description="IANA 时区，如 Asia/Shanghai")


class ServiceIn(BaseModel):
    id: str
    name: str = ""
    timezone: str = Field("UTC", description="排程结果的本地展示时区")
    duration_minutes: int = Field(..., gt=0, description="部署占用时长")
    rollback_minutes: int = Field(0, ge=0, description="部署后必须可回滚的资源占用时长")
    resources: list[str] = Field(default_factory=list, description="占用的共享资源名")
    windows: list[WindowIn] = Field(..., min_length=1, description="候选发布时间窗")


class DependencyIn(BaseModel):
    upstream: str
    downstream: str
    gap_minutes: int = Field(0, ge=0, description="上游部署结束到下游部署开始的最小间隔")


class BlackoutIn(BaseModel):
    team: str = Field(..., description="团队名")
    start: str
    end: str
    timezone: str = "UTC"


class FreezeIn(BaseModel):
    label: str = Field(..., description="冻结名称，如“双十一封网”")
    start: str
    end: str
    timezone: str = "UTC"


class ServiceTeamIn(BaseModel):
    service: str
    team: str


class CapacityPeriodIn(BaseModel):
    resource: str = Field(..., description="资源名")
    start: str = Field(..., description="ISO 8601 时间；不带偏移量时按时区字段解析")
    end: str
    timezone: str = Field("UTC", description="IANA 时区，如 Asia/Shanghai")
    capacity: int = Field(..., description="该时段内的并行容量（非负整数，0 表示该时段不可占用）")


class PinnedStartIn(BaseModel):
    service: str = Field(..., description="已确认（锁定）服务的 ID；必须在指定时刻准时部署")
    start: str = Field(..., description="已确认的部署开始时刻（ISO 8601；不带偏移量时按时区字段解析，"
                                        "沿用现有 DST 重复/不存在校验）")
    timezone: str = Field("UTC", description="start 为不带偏移量的墙上时间时使用的 IANA 时区")


class ReferenceStartIn(BaseModel):
    service: str = Field(..., description="给出参考开始时刻的服务 ID（未确认服务）")
    start: str = Field(..., description="参考部署开始时刻（仅用于分层优化目标，不豁免候选窗、依赖、"
                                        "禁发、冻结、资源容量等任何约束）")
    timezone: str = Field("UTC", description="start 为不带偏移量的墙上时间时使用的 IANA 时区")


class ScheduleRequest(BaseModel):
    services: list[ServiceIn]
    dependencies: list[DependencyIn] = Field(default_factory=list)
    service_teams: list[ServiceTeamIn] = Field(default_factory=list)
    team_blackouts: list[BlackoutIn] = Field(default_factory=list)
    freezes: list[FreezeIn] = Field(default_factory=list)
    resource_capacities: dict[str, int] = Field(
        default_factory=dict,
        description="共享资源并行容量；未声明的资源容量为 1。同一时刻占用数不得超过容量",
    )
    resource_capacity_periods: list[CapacityPeriodIn] = Field(
        default_factory=list,
        description="共享资源的临时容量时段（左闭右开）；时段内在任意时刻占用数不得超过该时段容量"
        "（0 表示不可占用），时段外沿用 resource_capacities，未声明基准容量为 1",
    )
    pinned_starts: list[PinnedStartIn] = Field(
        default_factory=list,
        description="已确认服务的锁定部署开始时刻：这些服务必须在指定时刻准时部署；"
        "锁定不豁免候选窗、依赖、团队禁发、维护冻结与资源分时容量等任何约束，"
        "冲突时 blockers 会返回包含 lock 条件的阻塞组合，不返回部分排程",
    )
    reference_starts: list[ReferenceStartIn] = Field(
        default_factory=list,
        description="未确认服务的参考部署开始时刻：仅参与分层目标（先最小化偏离参考时刻的服务数，"
        "再最小化偏移总秒数，最后最小化部署开始时刻之和），不豁免任何约束；"
        "未给出参考时刻的服务只参与最后一层（尽早发布）目标",
    )
    alternative_count: int | None = Field(
        None, ge=1, le=5,
        description="发布评审备选方案总数（含首项，1~5）。省略时不启用备选方案，响应与旧版本一致；"
        "启用后按同一优化目标逐项求解，每项方案相对此前每项方案至少有一个未锁定服务的"
        "部署开始 UTC 时刻相差达到 min_gap_minutes",
    )
    min_gap_minutes: int | None = Field(
        None, ge=1,
        description="备选方案之间的最小差距（正整数分钟）：任意两项方案之间至少一个未锁定服务的"
        "部署开始 UTC 时刻之差不小于该值；仅在 alternative_count 给出时生效，省略按 1 分钟处理",
    )
    max_solve_seconds: float = Field(10.0, gt=0, le=60)


class ScheduleItem(BaseModel):
    service_id: str
    name: str
    deploy_start_utc: str
    deploy_end_utc: str
    occupied_until_utc: str
    local_time: str
    timezone: str
    window_used: str
    resources: list[str]


class BlockingConstraint(BaseModel):
    code: str
    message: str


# ---- 发布评审：备选方案 ----


class AlternativePlan(BaseModel):
    index: int = Field(..., description="方案序号（0 为首项，与顶层 schedule 内容一致）")
    schedule: list[ScheduleItem]


class AlternativesInfo(BaseModel):
    requested: int = Field(..., description="请求的方案总数（alternative_count）")
    min_gap_minutes: int = Field(..., description="实际生效的方案间最小差距（分钟）")
    status: str = Field(
        ..., description="complete=已凑够请求数量；exhausted=已证实无更多方案；"
        "unknown=求解超时，是否还有更多方案未能判定（已返回的方案均已验证）")
    plans: list[AlternativePlan] = Field(..., description="按同一优化目标逐项择优得到的完整方案")


class ScheduleResponse(BaseModel):
    feasible: bool
    schedule: list[ScheduleItem] | None = None
    blockers: list[BlockingConstraint] | None = None
    status: str  # "optimal" | "feasible" | "infeasible" | "unknown"
    alternatives: AlternativesInfo | None = Field(
        None, description="备选方案结果；仅在请求给出 alternative_count 时非空")


# ---- 发布评审：单项服务延迟容忍度（/api/schedule/tolerance） ----


class ToleranceRequest(BaseModel):
    request: ScheduleRequest = Field(..., description="现有排程请求（与 /api/schedule 请求体相同），"
                                                      "先按现有规则求基线排程")
    max_delay_minutes: int = Field(
        ..., gt=0,
        description="延迟上限（正整数分钟）：对每个未锁定服务，从 0 分钟起逐分钟推迟其部署开始时刻，"
                    "检查到该上限为止")


class ServiceTolerance(BaseModel):
    service_id: str
    name: str
    locked: bool = Field(..., description="True 表示已确认（锁定）服务：不可推迟，不计算容忍度")
    baseline_start_utc: str | None = Field(None, description="基线排程中的部署开始时刻（UTC）")
    max_delay_minutes: int | None = Field(
        None, description="从 0 分钟起连续仍满足候选窗、依赖、团队禁发、维护冻结及共享资源分时容量的"
                          "最大延迟分钟数（不超过上限）；锁定服务为 null")
    first_failure_minute: int | None = Field(
        None, description="上限内首个失效的延迟分钟数；上限内逐分钟均可行为 null；锁定服务为 null")
    first_failure_start_utc: str | None = Field(
        None, description="首个失效分钟对应的部署开始时刻（UTC）；无失效分钟为 null")
    triggered: list[BlockingConstraint] | None = Field(
        None, description="首个失效分钟触发的约束组合（阻塞核）；无失效分钟为 null")


class ToleranceResponse(BaseModel):
    feasible: bool
    status: str  # "optimal" | "feasible" | "infeasible" | "unknown"
    baseline: list[ScheduleItem] | None = Field(
        None, description="按现有规则求得的基线排程（与 /api/schedule 首项一致）")
    blockers: list[BlockingConstraint] | None = Field(
        None, description="基线无解/未定时返回原有的阻塞组合或未定说明，此时不输出容忍度")
    max_delay_minutes: int | None = Field(None, description="本次扫描使用的延迟上限（分钟）")
    tolerances: list[ServiceTolerance] | None = Field(
        None, description="各服务的延迟容忍度（按基线开始时刻排序）；基线无解/未定时为 null")


# ---- 变更影响预演（/api/schedule/diff） ----


class ScheduleDiffRequest(BaseModel):
    current: ScheduleRequest = Field(..., description="当前（变更前）排程请求")
    modified: ScheduleRequest = Field(..., description="修改后（变更后）的排程请求")


class TimeChange(BaseModel):
    before_utc: str
    after_utc: str
    delta_minutes: float = Field(..., description="after - before，正数表示推迟，负数表示提前")


class ServiceChange(BaseModel):
    service_id: str
    name: str
    deploy_start: TimeChange | None = Field(None, description="部署开始时刻的变化；未变化为 null")
    occupied_until: TimeChange | None = Field(
        None, description="占用结束时刻（部署结束 + 回滚窗口）的变化；未变化为 null")


class ServiceRef(BaseModel):
    service_id: str
    name: str


class ScheduleDiff(BaseModel):
    changed: list[ServiceChange] = Field(..., description="两侧都存在、开始或占用结束时刻发生变化的服务")
    added: list[ServiceRef] = Field(..., description="仅在 modified 中出现的服务")
    removed: list[ServiceRef] = Field(..., description="仅在 current 中出现的服务")


class ValidationError(BaseModel):
    code: str
    field: str | None = None
    message: str
    suggestions: list[str] | None = None


class RequestFailure(BaseModel):
    kind: str  # "validation" | "infeasible" | "unknown"
    status: str | None = None  # 求解器状态（infeasible/unknown）
    errors: list[ValidationError] | None = None  # kind=validation 时的完整输入错误
    blockers: list[BlockingConstraint] | None = None  # kind=infeasible/unknown 时的阻塞约束组合


class ScheduleDiffResponse(BaseModel):
    feasible: bool
    diff: ScheduleDiff | None = None
    failures: dict[str, RequestFailure] | None = Field(
        None, description="任一请求输入错误或无解时，按 current/modified 给出对应错误，不输出差异")
