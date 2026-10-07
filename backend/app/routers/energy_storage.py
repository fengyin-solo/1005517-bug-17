"""储能电池组接口：维护储能电池组，覆盖检测结论上报、巡检清单与状态流转。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.energy_storage import EnergyStorageService

router = APIRouter(prefix="/api/energy_storage", tags=["储能电池组"])

service = EnergyStorageService()

LIST_FIELDS = ["电池组编号", "电池类型", "额定容量", "SOC上限", "当前SOC", "内阻变化率", "结论", "运行状态"]
STATUSES = ["充电中", "放电中", "待机", "故障停机"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按电池组编号检索"),
    status: str | None = Query(default=None, description="充电中、放电中、待机、故障停机"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按电池组编号与状态过滤储能电池组列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/inspection", response_model=PageResult[dict])
def inspection_entries() -> PageResult[dict]:
    """巡检清单：全部电池组（含故障停机等已停用组）逐组带结论，条数与清单一致。"""
    items, total = service.inspection_list()
    return PageResult(items=items, total=total, page=1, size=total or 1)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出储能电池组清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "energy_storage", "total": total, "items": items}


@router.post("/assessments", response_model=ActionResult)
def submit_assessment(payload: EntryPayload) -> ActionResult:
    """上报一组电池的检测数据：缺数据退回并写清缺什么，重复提交只生效一次，结论落库。"""
    entry, message, ok = service.submit_assessment(payload.values)
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条储能电池组明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"储能电池组 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条储能电池组，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="储能电池组已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条储能电池组执行启动充电、启动放电、切换到待机；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
