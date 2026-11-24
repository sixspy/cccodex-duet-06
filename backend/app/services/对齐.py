"""同业务键对齐：双边金额比对，落对齐差 + 悬账单。右轨冲正隔离行按落戳形态参与编排。"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻
from app.services import 目录

def _隔离注记(k: str, 隔离按键: dict) -> str:
    """对齐差/悬账说明里的冲正去向注记：旁路不计现行，净值已并现行。"""
    行集 = 隔离按键.get(k)
    if not 行集:
        return ""
    段 = []
    旁 = [r for r in 行集 if r["隔离形态"] == "旁路凭证"]
    净 = [r for r in 行集 if r["隔离形态"] != "旁路凭证"]
    if 旁:
        段.append(f"旁路凭证{len(旁)}笔共{sum(int(r['金额分']) for r in 旁)}分不进现行汇总")
    if 净:
        段.append(f"净值{len(净)}笔共{sum(int(r['金额分']) for r in 净)}分已并入现行右汇总")
    return "；冲正隔离:" + "，".join(段)

def 跑对齐() -> dict:
    规 = 目录.轨规()
    容差 = int(规.get("金额容差分", "0"))
    conn = 连接()
    try:
        # 写锁前置：与右装载互斥，隔离写与原键对齐同成败，冲正并发不得负向双计
        conn.execute("BEGIN IMMEDIATE")
        # 清空上次对齐结果（基线默认：每次重算）
        conn.execute("DELETE FROM 对齐差")
        conn.execute("DELETE FROM 悬账单")

        # 同键多笔：按业务键汇总金额（基线口径）
        左汇总: dict[str, int] = {}
        左代表: dict[str, str] = {}
        for r in conn.execute("SELECT * FROM 左轨流水 WHERE 命运='已入左'").fetchall():
            k = r["业务键"]
            左汇总[k] = 左汇总.get(k, 0) + int(r["金额分"])
            左代表[k] = r["事件编号"]

        右汇总: dict[str, int] = {}
        右代表: dict[str, str] = {}
        隔离按键: dict[str, list] = {}
        for r in conn.execute(
            "SELECT * FROM 右轨流水 WHERE 命运 IN ('已入右','冲正隔离')"
        ).fetchall():
            k = r["业务键"]
            if r["命运"] == "冲正隔离":
                隔离按键.setdefault(k, []).append(r)
                if r["隔离形态"] == "净值":
                    # 净值形态：正负加总进现行汇总
                    右汇总[k] = 右汇总.get(k, 0) + int(r["金额分"])
                    右代表.setdefault(k, r["事件编号"])
                # 旁路凭证形态：负向在旁路凭证，不进现行汇总
                continue
            右汇总[k] = 右汇总.get(k, 0) + int(r["金额分"])
            右代表[k] = r["事件编号"]

        所有键 = set(左汇总) | set(右汇总)
        对齐 = 0
        差 = 0
        悬 = 0
        时刻 = 此刻()

        for k in sorted(所有键):
            注 = _隔离注记(k, 隔离按键)
            有左 = k in 左汇总
            有右 = k in 右汇总
            if 有左 and 有右:
                la, ra = 左汇总[k], 右汇总[k]
                if abs(la - ra) <= 容差:
                    conn.execute(
                        "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                        (k, la, ra, "对齐", "双边金额在容差内" + 注, 时刻),
                    )
                    对齐 += 1
                else:
                    conn.execute(
                        "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                        (k, la, ra, "金额不符", f"左{la}-右{ra}" + 注, 时刻),
                    )
                    差 += 1
            elif 有左:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                    (k, 左汇总[k], None, "右缺", "仅左轨有键" + 注, 时刻),
                )
                conn.execute(
                    "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) VALUES (?,?,?,?,?,?)",
                    (k, "左", 左汇总[k], 左代表.get(k, ""), "右轨缺键" + 注, 时刻),
                )
                悬 += 1
                差 += 1
            else:
                conn.execute(
                    "INSERT INTO 对齐差(业务键,左金额分,右金额分,差型,说明,时刻) VALUES (?,?,?,?,?,?)",
                    (k, None, 右汇总[k], "左缺", "仅右轨有键" + 注, 时刻),
                )
                conn.execute(
                    "INSERT INTO 悬账单(业务键,悬边,金额分,事件编号,原因,时刻) VALUES (?,?,?,?,?,?)",
                    (k, "右", 右汇总[k], 右代表.get(k, ""), "左轨缺键" + 注, 时刻),
                )
                悬 += 1
                差 += 1

        conn.commit()
        return {
            "键总数": len(所有键),
            "对齐": 对齐,
            "差与悬": 差,
            "悬账": 悬,
            "冲正隔离键数": len(隔离按键),
            "容差分": 容差,
            "时刻": 时刻,
        }
    finally:
        conn.close()
