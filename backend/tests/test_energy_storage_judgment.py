"""储能电池组判定口径服务层测试：python3 tests/test_energy_storage_judgment.py

不依赖 FastAPI，直接跑 service 层，覆盖统一后的 9 条口径。
每轮重建 store，避免内存数据互相污染。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import energy_storage as es_module  # noqa: E402
from app.services import maintenance as maintenance_module  # noqa: E402
from app.services.energy_storage import (  # noqa: E402
    EnergyStorageService,
    VERDICT_NORMAL,
    VERDICT_OVERCHARGE,
    VERDICT_PENDING,
    VERDICT_RESISTANCE,
    evaluate_verdict,
    normalize_verdict,
)
from app.services.maintenance import MaintenanceService  # noqa: E402
from app.store import Store  # noqa: E402


def fresh_service() -> tuple[EnergyStorageService, MaintenanceService]:
    store = Store()
    # service 用的是 from app.store import store 绑定的模块级对象，测试里两处都得换
    es_module.store = store
    maintenance_module.store = store
    es_module.maintenance_service = MaintenanceService()
    return EnergyStorageService(), MaintenanceService()


def make_row(
    store: Store,
    *,
    code: str = "ENER-TEST",
    capacity: str = "500",
    limit: str = "90",
    verdict: str | None = None,
    fingerprint: str | None = None,
    status: str = "待机",
) -> int:
    rows = store.rows("energy_storage")
    next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
    row = {
        "id": next_id,
        "status": status,
        "pending": True,
        "abnormal": False,
        "电池组编号": code,
        "电池类型": "磷酸铁锂",
        "额定容量": capacity,
        "SOC上限": limit,
    }
    if verdict is not None:
        row["判定结论"] = verdict
    if fingerprint is not None:
        row["判定指纹"] = fingerprint
    rows.append(row)
    return next_id


passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


print("1) 判定四档：正常 / 内阻异常 / 过充风险 / 同时命中取更严重一档")
check("区间内且不越限=正常", evaluate_verdict(resistance_rate=4.2, soc=62, soc_limit=90) == VERDICT_NORMAL)
check("内阻 25 越上限=内阻异常", evaluate_verdict(resistance_rate=25, soc=62, soc_limit=90) == VERDICT_RESISTANCE)
check("内阻 -15 越下限=内阻异常", evaluate_verdict(resistance_rate=-15, soc=62, soc_limit=90) == VERDICT_RESISTANCE)
check("临界值 20 仍在许可区间", evaluate_verdict(resistance_rate=20, soc=62, soc_limit=90) == VERDICT_NORMAL)
check("临界值 -10 仍在许可区间", evaluate_verdict(resistance_rate=-10, soc=62, soc_limit=90) == VERDICT_NORMAL)
check("SOC 92 超上限 90=过充风险", evaluate_verdict(resistance_rate=5, soc=92, soc_limit=90) == VERDICT_OVERCHARGE)
check("SOC 等于上限不算过充", evaluate_verdict(resistance_rate=5, soc=90, soc_limit=90) == VERDICT_NORMAL)
check("两条同时命中取过充风险", evaluate_verdict(resistance_rate=30, soc=95, soc_limit=90) == VERDICT_OVERCHARGE)

print("2) 缺数据不保存，逐字段退回")
svc, maint = fresh_service()
store = es_module.store
eid = make_row(store, code="ENER-X")
entry, message, applied = svc.submit_judgment(eid, {"当前SOC": "", "内阻变化率": ""})
check("缺数据返回失败", entry is None and applied is False)
check("说明列明缺失字段", "当前SOC缺失" in message and "内阻变化率缺失" in message, message)
row_after = es_module.store.find("energy_storage", eid)
check("记录未落库任何判定数据", "判定结论" not in row_after and "判定时间" not in row_after)

entry, message, applied = svc.submit_judgment(eid, {"当前SOC": "abc", "内阻变化率": "4"})
check("非数字 SOC 被退回", entry is None and "当前SOC" in message and "有效数字" in message, message)

entry, message, applied = svc.submit_judgment(eid, {"当前SOC": "60", "内阻变化率": "300"})
check("内阻变化率越合理范围被退回", entry is None and "-100~100" in message, message)

print("3) 同一份数据重复提交只生效一次")
svc, maint = fresh_service()
store = es_module.store
eid = make_row(store, code="ENER-DUP")
payload = {"当前SOC": "95", "内阻变化率": "25"}
first, msg1, applied1 = svc.submit_judgment(eid, payload)
maintenance_rows_before = len(store.rows("maintenance"))
second, msg2, applied2 = svc.submit_judgment(eid, dict(payload))
check("首次生效", applied1 is True and first["判定结论"] == VERDICT_OVERCHARGE)
check("重复提交不生效", applied2 is False, msg2)
check("重复提交不重复写待办", len(store.rows("maintenance")) == maintenance_rows_before)
check("结论保持不变", second["判定结论"] == VERDICT_OVERCHARGE)

print("4) 结论落库 + 结论改动写检修待办")
svc, maint = fresh_service()
store = es_module.store
store.rows("energy_storage").clear()
eid = make_row(store, code="ENER-TODO")
_, _, applied1 = svc.submit_judgment(eid, {"当前SOC": "95", "内阻变化率": "4"})
todos = [row for row in store.rows("maintenance") if row.get("status") == "待办"]
check("首次过充判定生成待办", applied1 and len(todos) == 1)
check("待办标明设备与原因", "ENER-TODO" in todos[0]["检修设备"] and "过充风险" in todos[0]["安全措施"] and "未判定" in todos[0]["安全措施"])
check("过充待办为紧急", todos[0]["abnormal"] is True)
check("结论已落库", store.find("energy_storage", eid)["判定结论"] == VERDICT_OVERCHARGE and bool(store.find("energy_storage", eid).get("判定时间")))

_, _, _ = svc.submit_judgment(eid, {"当前SOC": "60", "内阻变化率": "4"})
todos = [row for row in store.rows("maintenance") if row.get("status") == "待办"]
check("档位变化（过充->正常）再写一条", len(todos) == 2 and "正常" in todos[-1]["安全措施"])

# 再次提交同一正常数据：幂等，无第三条
_, _, applied3 = svc.submit_judgment(eid, {"当前SOC": "60", "内阻变化率": "4"})
check("结论不变且数据不变时无新待办", applied3 is False and len([row for row in store.rows("maintenance") if row.get("status") == "待办"]) == 2)

print("5) 老结论兼容既有判定标准")
check("容量合格映射为正常", normalize_verdict("容量合格") == VERDICT_NORMAL)
svc, _ = fresh_service()
store = es_module.store
eid = make_row(store, code="ENER-LEGACY", verdict="容量合格")
detail = svc.get_entry(eid)
check("详情页老结论显示为正常", detail["判定结论"] == VERDICT_NORMAL)
listed, total = svc.list_entries(page=1, size=20)
listed_row = next(row for row in listed if row["id"] == eid)
check("列表页老结论同样映射为正常（口径一致）", listed_row["判定结论"] == VERDICT_NORMAL)
check("底层存储保持老结论不强制改写", store.find("energy_storage", eid)["判定结论"] == "容量合格")

print("6) 列表与详情同一出口（SOC 上限 / 容量不再各算各的）")
svc, _ = fresh_service()
store = es_module.store
eid = make_row(store, code="ENER-ONE", limit="88")
svc.submit_judgment(eid, {"当前SOC": "80", "内阻变化率": "3"})
detail = svc.get_entry(eid)
listed, _ = svc.list_entries(page=1, size=20)
listed_row = next(row for row in listed if row["id"] == eid)
check("SOC上限列表与详情一致", detail["SOC上限"] == listed_row["SOC上限"] == "88")
check("判定结论列表与详情一致", detail["判定结论"] == listed_row["判定结论"] == VERDICT_NORMAL)

print("7) 巡检清单含已停用组、序号连续、条数对齐")
svc, _ = fresh_service()
store = es_module.store
store.rows("energy_storage").clear()
make_row(store, code="ENER-A", status="充电中")
make_row(store, code="ENER-B", status="已停用")
make_row(store, code="ENER-C", status="故障停机")
items, total = svc.patrol_checklist()
check("清单条数=3（含已停用）", total == 3 and len(items) == 3)
check("序号 1..3 连续不错位", [item["序号"] for item in items] == [1, 2, 3])
check("已停用组在册且有标记", any(item["电池组编号"] == "ENER-B" and item["已停用"] == "是" for item in items))
check("未判定组结论占位为未判定", all(item["判定结论"] == VERDICT_PENDING for item in items))

print("8) 停用/启用动作")
svc, _ = fresh_service()
store = es_module.store
store.rows("energy_storage").clear()
eid = make_row(store, code="ENER-ACT")
entry, _ = svc.run_action(eid, "停用")
check("停用后状态=已停用", entry["status"] == "已停用" and entry["abnormal"] is True)
check("清单仍包含停用组", len(svc.patrol_checklist()[0]) == 1)
entry, _ = svc.run_action(eid, "启用")
check("启用后回待机、异常按结论复位（无结论=不异常）", entry["status"] == "待机" and entry["abnormal"] is False)

print(f"\n结果：{passed} 通过，{failed} 失败")
sys.exit(1 if failed else 0)
