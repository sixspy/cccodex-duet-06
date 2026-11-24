"""右轨冲正隔离：负向/冲正标记事件的形态择定、隔离落账与只读视图。

形态二选一（装载清洗时落戳，对齐按戳执行）：
- 旁路凭证：负向进旁路凭证，不参与现行汇总；正向单独对齐；结论读现行正集。
- 净值：正负加总后进现行汇总，不写旁路凭证；读引用指向净值汇总而非凭证。
"""
from __future__ import annotations
import sqlite3
from app.db import 连接
from app.seed import 此刻

形态集 = ("旁路凭证", "净值")
默认形态 = "旁路凭证"


def 是冲正(ev: dict) -> bool:
    """金额为负或带冲正标记即认定为冲正事件。"""
    try:
        if int(ev.get("金额分", 0)) < 0:
            return True
    except (TypeError, ValueError):
        pass
    标 = ev.get("冲正标记")
    return 标 in (True, 1, "1", "true", "True", "是", "冲正")


def 当前形态(conn: sqlite3.Connection) -> str:
    """读轨规里的冲正隔离形态；未知值回落默认形态。"""
    r = conn.execute("SELECT 值 FROM 轨规 WHERE 键='冲正隔离形态'").fetchone()
    v = (r["值"] if r else "") or 默认形态
    return v if v in 形态集 else 默认形态


def 登记(conn: sqlite3.Connection, *, 事件编号: str, 业务键: str, 金额分: int,
         源键: str, 事件时: str, 处理时: str, 批次: str, 形态: str) -> str:
    """冲正隔离落账：右轨流水隔离行 + 冲正隔离登记（+ 旁路凭证）同事务写入。

    调用方须已持有写事务（BEGIN IMMEDIATE），与本键对齐同成败。
    返回读引用。
    """
    时刻 = 此刻()
    conn.execute(
        """INSERT INTO 右轨流水(事件编号,业务键,金额分,源键,事件时,处理时,命运,批次编号,冲正标记,隔离形态)
           VALUES (?,?,?,?,?,?,'冲正隔离',?,1,?)""",
        (事件编号, 业务键, 金额分, 源键, 事件时, 处理时, 批次, 形态),
    )
    if 形态 == "旁路凭证":
        凭证编号 = f"旁路-{事件编号}"
        conn.execute(
            "INSERT INTO 旁路凭证(凭证编号,事件编号,业务键,金额分,有效,时刻) VALUES (?,?,?,?,1,?)",
            (凭证编号, 事件编号, 业务键, 金额分, 时刻),
        )
        读引用 = f"旁路凭证:{凭证编号}"
    else:
        读引用 = f"净值汇总:{业务键}"
    conn.execute(
        """INSERT INTO 冲正隔离(事件编号,业务键,金额分,冲正标记,形态,读引用,有效,时刻)
           VALUES (?,?,?,1,?,?,1,?)""",
        (事件编号, 业务键, 金额分, 形态, 读引用, 时刻),
    )
    return 读引用


def 隔离列表() -> list[dict]:
    """只读接口数据源：业务键、冲正标记、旁路或净值、valid（有效）。"""
    conn = 连接()
    rows = conn.execute("SELECT * FROM 冲正隔离 ORDER BY 编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def 凭证列表() -> list[dict]:
    conn = 连接()
    rows = conn.execute("SELECT * FROM 旁路凭证 ORDER BY 时刻 DESC, 凭证编号 DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]
