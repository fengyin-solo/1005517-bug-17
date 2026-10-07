"""储能电池组业务规则：巡检判定口径、结论落库、状态流转与筛选都收在这里。

判定口径全系统只此一份，列表、详情、巡检清单都从这里取结论，避免各算各的：

1. 内阻变化率越出许可区间（RESISTANCE_RATE_MIN ~ RESISTANCE_RATE_MAX）判「内阻异常」；
2. 实测 SOC 超过本组 SOC 上限判「过充风险」；
3. 两条同时命中取更严重一档：过充风险 > 内阻异常；都不命中为「正常」。

其它硬口径：

- 判定记录缺数据（SOC 上限、实测 SOC、内阻变化率等）一律不保存，逐字段退回；
- 同一组电池用同一份巡检数据重复提交只生效一次（按数据指纹幂等）；
- 判定结论落库；结论相对老结论发生档位变化时，往检修计划写一条待办；
- 老结论（如「容量合格」）按既定语义映射后继续兼容，不强制重判。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.maintenance import MaintenanceService
from app.store import store

MODULE = "energy_storage"

# 建档必填的台账字段
REQUIRED_FIELDS = ["电池组编号", "电池类型", "额定容量"]
# 可随登记一并写入的其它台账字段
OPTIONAL_FIELDS = ["SOC上限", "充放电循环", "电池温度", "内阻变化率", "当前SOC"]
# 判定时必须齐备且可解析的字段，缺一个都不能保存
JUDGMENT_REQUIRED_FIELDS = ["额定容量", "SOC上限", "当前SOC", "内阻变化率"]

STATUS_ORDER = ["充电中", "放电中", "待机", "故障停机", "已停用"]
ACTION_RULES = {
    "启动充电": "充电中",
    "启动放电": "放电中",
    "切换到待机": "待机",
    "停用": "已停用",
    "启用": "待机",
}
NEGATIVE_ACTIONS = ["停用"]

# 内阻变化率许可区间（百分比，相对出厂基线）
RESISTANCE_RATE_MIN = -10.0
RESISTANCE_RATE_MAX = 20.0

VERDICT_NORMAL = "正常"
VERDICT_RESISTANCE = "内阻异常"
VERDICT_OVERCHARGE = "过充风险"
# 尚未判定过的组在清单里展示的占位结论
VERDICT_PENDING = "未判定"

# 老结论兼容映射：老口径的「容量合格」按「正常」继续使用
LEGACY_VERDICT_MAP = {"容量合格": VERDICT_NORMAL, "合格": VERDICT_NORMAL}

DISABLED_STATUS = "已停用"

maintenance_service = MaintenanceService()


def normalize_verdict(value: Any) -> str | None:
    """把库里存着的结论（含老口径结论）归一到现行三档之一：正常/内阻异常/过充风险。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return LEGACY_VERDICT_MAP.get(text, text)


def parse_percent(raw: Any, field: str) -> tuple[float | None, str | None]:
    """把「5.2」「5.2%」这类输入解析成百分数；空值、非数字、越界都给出可读原因。"""
    if raw is None or not str(raw).strip():
        return None, f"{field}缺失"
    text = str(raw).strip().rstrip("%").strip()
    try:
        value = float(text)
    except ValueError:
        return None, f"{field}的值「{raw}」不是有效数字"
    if not -100.0 <= value <= 100.0:
        return None, f"{field}应在 -100~100 之间，当前为 {value:g}"
    return value, None


def evaluate_verdict(*, resistance_rate: float, soc: float, soc_limit: float) -> str:
    """统一判定函数：内阻、SOC 两条规则在这里合并，严重度只取一档。"""
    resistance_abnormal = not (RESISTANCE_RATE_MIN <= resistance_rate <= RESISTANCE_RATE_MAX)
    overcharge_risk = soc > soc_limit
    if overcharge_risk:
        # 单命中过充或两条同时命中，都取更严重的「过充风险」
        return VERDICT_OVERCHARGE
    if resistance_abnormal:
        return VERDICT_RESISTANCE
    return VERDICT_NORMAL


class EnergyStorageService:
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
        entry = store.find(MODULE, entry_id)
        return self._present(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in OPTIONAL_FIELDS:
            if str(values.get(field) or "").strip():
                entry[field] = values.get(field)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self._present(entry), []

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
        entry["pending"] = target != STATUS_ORDER[-1]
        if action in NEGATIVE_ACTIONS:
            entry["abnormal"] = True
        elif action == "启用":
            # 异常标记交给判定结论决定，启用时按现有结论复位
            entry["abnormal"] = normalize_verdict(entry.get("判定结论")) not in (
                None,
                VERDICT_NORMAL,
            )
        return self._present(entry), f"储能电池组已{action}"

    # ------------------------------------------------------------------ 判定

    def submit_judgment(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """提交一组电池的巡检数据并判定。

        返回 (记录, 说明, 是否生效)。缺数据 / 数据非法时不保存；
        数据指纹与上一次一致时幂等返回，不重复落库、不重复写待办。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"储能电池组 {entry_id} 不存在或已归档", False

        # 实测数据允许随本次提交覆盖；SOC 上限、额定容量缺省取台账值
        merged = {
            "额定容量": entry.get("额定容量"),
            "SOC上限": entry.get("SOC上限"),
            "当前SOC": values.get("当前SOC"),
            "内阻变化率": values.get("内阻变化率"),
        }
        for field in ("SOC上限", "额定容量"):
            if str(values.get(field) or "").strip():
                merged[field] = values.get(field)

        errors = self._validate_judgment(merged)
        if errors:
            return None, "判定记录未保存：" + "；".join(errors), False

        soc = float(str(merged["当前SOC"]).strip().rstrip("%").strip())
        rate = float(str(merged["内阻变化率"]).strip().rstrip("%").strip())
        soc_limit = float(str(merged["SOC上限"]).strip().rstrip("%").strip())

        fingerprint = "|".join(
            str(merged[field]).strip() for field in JUDGMENT_REQUIRED_FIELDS
        )
        if entry.get("判定指纹") == fingerprint:
            return (
                self._present(entry),
                "巡检数据与上一次判定完全一致，结论未重复生效",
                False,
            )

        verdict = evaluate_verdict(resistance_rate=rate, soc=soc, soc_limit=soc_limit)
        old_verdict = normalize_verdict(entry.get("判定结论"))
        # 首次判定（库里没结论）按「正常」基线比较：变差或变好都算结论变化，要写待办
        baseline = old_verdict or VERDICT_NORMAL

        # 落库：实测值与结论都记在电池组台账上
        entry["SOC上限"] = merged["SOC上限"]
        entry["当前SOC"] = merged["当前SOC"]
        entry["内阻变化率"] = merged["内阻变化率"]
        entry["判定结论"] = verdict
        entry["判定时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry["判定指纹"] = fingerprint
        entry["abnormal"] = verdict != VERDICT_NORMAL

        message = f"判定完成：{verdict}"
        if baseline != verdict:
            todo = self._create_maintenance_todo(entry, old_verdict, verdict)
            message += f"，已生成检修待办「{todo['计划编号']}」"

        return self._present(entry), message, True

    def _validate_judgment(self, values: dict[str, Any]) -> list[str]:
        """缺数据、格式非法逐字段列清楚，调用方原样退回给前端。"""
        errors: list[str] = [
            f"{field}缺失"
            for field in JUDGMENT_REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if errors:
            # 有缺项就不做后续解析，避免一条记录刷一堆次生报错
            return errors
        for field in ("SOC上限", "当前SOC", "内阻变化率"):
            _, parse_error = parse_percent(values.get(field), field)
            if parse_error:
                errors.append(parse_error)
        try:
            float(str(values.get("额定容量")))
        except (TypeError, ValueError):
            errors.append(f"额定容量的值「{values.get('额定容量')}」不是有效数字")
        return errors

    def _create_maintenance_todo(
        self, entry: dict[str, Any], old_verdict: str | None, verdict: str
    ) -> dict[str, Any]:
        category_map = {
            VERDICT_OVERCHARGE: "过充风险待处理",
            VERDICT_RESISTANCE: "内阻异常待核查",
            VERDICT_NORMAL: "判定恢复正常待复查",
        }
        old_text = old_verdict or "未判定"
        reason = f"巡检判定由「{old_text}」变为「{verdict}」，请安排检修"
        return maintenance_service.add_todo(
            device_code=str(entry.get("电池组编号", "")),
            device_name="储能电池组",
            category=category_map.get(verdict, "巡检异常待处理"),
            reason=reason,
            source="储能电池组巡检判定",
            urgent=verdict == VERDICT_OVERCHARGE,
        )

    # ------------------------------------------------------------- 巡检清单

    def patrol_checklist(self) -> tuple[list[dict[str, Any]], int]:
        """储能电池组巡检清单：已停用的组也要在册，序号连续、条数与总数一致。"""
        rows = store.rows(MODULE)
        items: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            item = self._present(row)
            item["序号"] = index
            item["已停用"] = "是" if row.get("status") == DISABLED_STATUS else "否"
            items.append(item)
        return items, len(items)

    # ----------------------------------------------------------------- 呈现

    def _present(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表与详情共用同一份出口：老结论在这里归一，保证两处口径一致。"""
        data = dict(row)
        verdict = normalize_verdict(data.get("判定结论"))
        data["判定结论"] = verdict if verdict is not None else VERDICT_PENDING
        return data
