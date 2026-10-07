"""储能电池组业务规则：状态流转、字段校验、检测判定与筛选口径都收在这里。

判定口径全模块只有这一份：列表、详情、巡检清单读的都是落库后的结论，
老结论经 normalize_conclusion 归并到同一套档位，不再各算各的。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.services.maintenance import MaintenanceService
from app.store import store

MODULE = "energy_storage"
REQUIRED_FIELDS = ["电池组编号", "电池类型", "额定容量"]
STATUS_ORDER = ["充电中", "放电中", "待机", "故障停机"]
ACTION_RULES = {"启动充电": "充电中", "启动放电": "放电中", "切换到待机": "待机"}
NEGATIVE_ACTIONS = []

# —— 判定口径：唯一标准，改动只准动这里 ——
# 内阻变化率许可区间（%）：越出区间即判内阻异常
RESISTANCE_RATE_RANGE = (-10.0, 20.0)
# 结论档位，越靠后越严重；两条规则同时命中时取更严重的一档
CONCLUSION_ORDER = ["容量合格", "过充风险", "内阻异常"]
# 老结论别名：历史数据里的写法归并到现行档位，保证老结论兼容既有判定标准
LEGACY_CONCLUSION_ALIASES = {"合格": "容量合格", "正常": "容量合格", "容量正常": "容量合格"}
NO_CONCLUSION = "待检测"
# 上报检测时缺一不可的字段；SOC上限取自登记信息，缺了同样判不了
ASSESS_REQUIRED_FIELDS = ["电池组编号", "当前SOC", "内阻变化率"]

maintenance_service = MaintenanceService()


def parse_percent(value: Any) -> float | None:
    """把 "95"、"95%"、"95％" 这类写法统一成百分数；解析不了返回 None。"""
    text = str(value or "").strip().rstrip("%％").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_conclusion(raw: Any) -> str:
    """老结论兼容：历史结论归并到现行档位；没有结论的一律待检测，不再凭空给合格。"""
    text = str(raw or "").strip()
    if not text:
        return NO_CONCLUSION
    if text in CONCLUSION_ORDER:
        return text
    return LEGACY_CONCLUSION_ALIASES.get(text, NO_CONCLUSION)


def judge(*, current_soc: float, resistance_rate: float, soc_limit: float) -> tuple[str, list[str]]:
    """唯一判定函数：内阻越界算内阻异常，SOC 超上限算过充风险，同时命中取更严重档。"""
    low, high = RESISTANCE_RATE_RANGE
    hit: list[str] = []
    if not low <= resistance_rate <= high:
        hit.append("内阻异常")
    if current_soc > soc_limit:
        hit.append("过充风险")
    if not hit:
        return "容量合格", []
    return max(hit, key=CONCLUSION_ORDER.index), hit


class EnergyStorageService:
    def _present(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表、详情、巡检清单共用的出口：结论统一从落库值归一化，保证处处一致。"""
        item = dict(row)
        item["结论"] = normalize_conclusion(row.get("结论"))
        item.setdefault("命中规则", [])
        return item

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
            rows = [row for row in rows if keyword in str(row.get("电池组编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._present(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._present(row) if row is not None else None

    def inspection_list(self) -> tuple[list[dict[str, Any]], int]:
        """巡检清单：覆盖全部电池组（含故障停机等已停用组），条数与清单严格一致。"""
        items = [self._present(row) for row in store.rows(MODULE)]
        return items, len(items)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in ("SOC上限", "充放电循环", "电池温度"):
            if values.get(field) not in (None, ""):
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["运行状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["检测记录"] = []
        rows.append(entry)
        return self._present(entry), []

    def submit_assessment(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, bool]:
        """上报检测结论：缺数据退回、重复提交只生效一次、结论落库并联动检修待办。"""
        missing = [field for field in ASSESS_REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}", False
        code = str(values.get("电池组编号") or "").strip()
        group = next(
            (row for row in store.rows(MODULE) if str(row.get("电池组编号", "")).strip() == code),
            None,
        )
        if group is None:
            return None, f"电池组 {code} 不存在或已归档", False
        current_soc = parse_percent(values.get("当前SOC"))
        resistance_rate = parse_percent(values.get("内阻变化率"))
        invalid = [
            name
            for name, parsed in (("当前SOC", current_soc), ("内阻变化率", resistance_rate))
            if parsed is None
        ]
        if invalid:
            return None, f"字段数值无法解析：{'、'.join(invalid)}", False
        soc_limit = parse_percent(group.get("SOC上限"))
        if soc_limit is None:
            return None, f"电池组 {code} 缺少判定依据：SOC上限，请先完善登记信息", False
        check_date = str(values.get("检测日期") or "").strip() or date.today().isoformat()

        # 幂等：同一组电池、同一天、同一组数值的重复提交只生效一次
        history = group.setdefault("检测记录", [])
        for record in history:
            if (
                record.get("检测日期") == check_date
                and record.get("当前SOC") == current_soc
                and record.get("内阻变化率") == resistance_rate
            ):
                return (
                    self._present(group),
                    f"电池组 {code} 该次检测已上报，重复提交不再生效，沿用首次结论：{record.get('结论')}",
                    True,
                )

        conclusion, hit_rules = judge(
            current_soc=current_soc, resistance_rate=resistance_rate, soc_limit=soc_limit
        )
        previous = normalize_conclusion(group.get("结论"))
        history.append(
            {
                "检测日期": check_date,
                "当前SOC": current_soc,
                "内阻变化率": resistance_rate,
                "结论": conclusion,
                "命中规则": hit_rules,
            }
        )
        # 结论落库：组档与最新测量值一起回写，列表/详情/巡检清单都读这里
        group["结论"] = conclusion
        group["命中规则"] = hit_rules
        group["检测日期"] = check_date
        group["当前SOC"] = values.get("当前SOC")
        group["内阻变化率"] = values.get("内阻变化率")
        group["abnormal"] = conclusion != "容量合格"
        if conclusion != previous:
            maintenance_service.sync_battery_todo(
                battery_code=code,
                conclusion=conclusion,
                hit_rules=hit_rules,
                check_date=check_date,
            )
        return self._present(group), f"检测结论已落库：{conclusion}", True

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"储能电池组 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于储能电池组可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["运行状态"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        # abnormal 由检测结论拥有，状态流转不覆盖
        entry["abnormal"] = normalize_conclusion(entry.get("结论")) not in ("容量合格", NO_CONCLUSION)
        return self._present(entry), f"储能电池组已{action}"
