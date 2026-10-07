"""检修计划业务规则：状态流转、字段校验、筛选口径与待办接入都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "maintenance"
REQUIRED_FIELDS = ["计划编号", "检修设备", "检修类别"]
# 巡检结论变化生成的待办排在最前一档；老数据仍从「待审批」流转，兼容不动
STATUS_ORDER = ["待办", "待审批", "已批复", "执行中", "已完工"]
ACTION_RULES = {"提交审批": "已批复", "开始执行": "执行中", "确认完工": "已完工"}
NEGATIVE_ACTIONS = []


class MaintenanceService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("计划编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"检修计划 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于检修计划可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检修计划已{action}"

    def add_todo(
        self,
        *,
        device_code: str,
        device_name: str,
        category: str,
        reason: str,
        source: str = "巡检判定",
        urgent: bool = False,
    ) -> dict[str, Any]:
        """结论变化时往检修计划的待办列表追加一条，状态固定为「待办」。"""
        rows = store.rows(MODULE)
        next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        today = datetime.now().strftime("%Y-%m-%d")
        todo = {
            "id": next_id,
            "status": "待办",
            "pending": True,
            "abnormal": urgent,
            "计划编号": f"MAIN-TODO-{next_id:04d}",
            "检修设备": f"{device_name} {device_code}".strip(),
            "检修类别": category,
            "计划开始": today,
            "计划结束": "",
            "责任人": "待分派",
            "安全措施": reason,
            "计划状态": "待办",
            "待办来源": source,
        }
        rows.append(todo)
        return todo
