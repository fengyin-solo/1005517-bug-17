"""检修计划业务规则：状态流转、字段校验、待办口径与筛选都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "maintenance"
REQUIRED_FIELDS = ["计划编号", "检修设备", "检修类别"]
STATUS_ORDER = ["待审批", "已批复", "执行中", "已完工"]
ACTION_RULES = {"提交审批": "已批复", "开始执行": "执行中", "确认完工": "已完工"}
NEGATIVE_ACTIONS = []

# 储能电池组检测结论联动出来的待办，检修类别统一打这个标记，方便按组去重
BATTERY_TODO_CATEGORY = "储能电池组检测整改"


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

    def todo_list(self) -> tuple[list[dict[str, Any]], int]:
        """检修待办列表：所有还没完工的计划，条数与清单一致。"""
        items = [row for row in store.rows(MODULE) if row.get("pending")]
        return items, len(items)

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

    def sync_battery_todo(
        self,
        *,
        battery_code: str,
        conclusion: str,
        hit_rules: list[str],
        check_date: str,
    ) -> None:
        """储能结论改动后的联动：异常结论开立/更新待办，恢复合格则完结未完工待办。"""
        rows = store.rows(MODULE)
        open_todos = [
            row
            for row in rows
            if row.get("检修设备") == battery_code
            and row.get("检修类别") == BATTERY_TODO_CATEGORY
            and row.get("status") != "已完工"
        ]
        if conclusion == "容量合格":
            for todo in open_todos:
                todo["status"] = "已完工"
                todo["pending"] = False
                todo["abnormal"] = False
                todo["计划状态"] = "已完工"
            return
        detail = f"{check_date} 判定{conclusion}"
        if hit_rules:
            detail += f"（命中：{'、'.join(hit_rules)}）"
        if open_todos:
            todo = open_todos[0]
            todo["安全措施"] = detail
            todo["pending"] = True
            todo["abnormal"] = True
            return
        plan_code = f"MAIN-{battery_code}"
        if any(row.get("计划编号") == plan_code for row in rows):
            plan_code = f"{plan_code}-{len(rows) + 1}"
        rows.append(
            {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "计划编号": plan_code,
                "检修设备": battery_code,
                "检修类别": BATTERY_TODO_CATEGORY,
                "计划开始": check_date,
                "计划结束": "",
                "责任人": "待分派",
                "安全措施": detail,
                "计划状态": "待审批",
                "status": "待审批",
                "pending": True,
                "abnormal": True,
            }
        )

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
        entry["计划状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"检修计划已{action}"
