"""右轨（核心记账）装载：入库清洗识别冲正（负向或冲正标记），按轨规形态隔离落账。"""
from __future__ import annotations
import json, sqlite3, uuid
from app.db import 连接
from app.seed import 此刻
from app.services import 校验, 冲正隔离

def 装一批(体: dict) -> dict:
    批次 = 体.get("批次编号") or f"右批-{uuid.uuid4().hex[:8]}"
    事件 = 体.get("事件") or []
    报告 = {"批次编号": 批次, "轨别": "右", "入流": 0, "冲正隔离": 0, "跳过": 0, "驳回": 0, "明细": []}
    for e in 校验.校验批次(体, 允许负=True):
        报告["明细"].append({"命运": "批次预检", "说明": e})
    conn = 连接()
    try:
        # 写锁前置：装载与对齐互斥，同一冲正/正向事件并发再装不得双成功
        conn.execute("BEGIN IMMEDIATE")
        形态 = 冲正隔离.当前形态(conn)
        for ev in 事件:
            行错 = 校验.校验事件(ev, 允许负=True)
            if 行错:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-校验", "说明": ";".join(行错)})
                continue
            编号 = ev.get("事件编号")
            键 = ev.get("业务键")
            if not 编号 or not 键:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-缺字段", "事件": ev})
                continue
            if conn.execute("SELECT 1 FROM 右轨流水 WHERE 事件编号=?", (编号,)).fetchone():
                报告["跳过"] += 1
                报告["明细"].append({"事件编号": 编号, "命运": "幂等跳过"})
                continue
            源 = ev.get("源键") or "核乙"
            if not conn.execute("SELECT 1 FROM 源登记 WHERE 源键=? AND 轨别='右'", (源,)).fetchone():
                报告["驳回"] += 1
                报告["明细"].append({"事件编号": 编号, "命运": "驳回-未知右源"})
                continue
            金额 = int(ev.get("金额分", 0))
            事件时 = ev.get("事件时") or 此刻()
            处理时 = ev.get("处理时") or 此刻()
            try:
                if 冲正隔离.是冲正(ev):
                    读引用 = 冲正隔离.登记(
                        conn, 事件编号=编号, 业务键=键, 金额分=金额, 源键=源,
                        事件时=事件时, 处理时=处理时, 批次=批次, 形态=形态,
                    )
                    报告["冲正隔离"] += 1
                    报告["明细"].append({
                        "事件编号": 编号, "业务键": 键,
                        "命运": f"冲正隔离-{形态}", "读引用": 读引用,
                    })
                else:
                    conn.execute(
                        """INSERT INTO 右轨流水(事件编号,业务键,金额分,源键,事件时,处理时,命运,批次编号)
                           VALUES (?,?,?,?,?,?,'已入右',?)""",
                        (编号, 键, 金额, 源, 事件时, 处理时, 批次),
                    )
                    报告["入流"] += 1
                    报告["明细"].append({"事件编号": 编号, "业务键": 键, "命运": "已入右"})
            except sqlite3.IntegrityError:
                # 并发下同键事件已被另一事务写入：按幂等跳过，不双计
                报告["跳过"] += 1
                报告["明细"].append({"事件编号": 编号, "命运": "幂等跳过"})
                continue
        conn.execute(
            "INSERT INTO 装载批次(编号,轨别,时刻,报告) VALUES (?,?,?,?)",
            (批次, "右", 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
        return 报告
    finally:
        conn.close()
