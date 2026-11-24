"""对拍结论：核对对齐差与悬账是否自洽；冲正隔离下结论读现行正集。"""
from __future__ import annotations
from app.db import 连接
from app.seed import 此刻

def 跑一次() -> dict:
    conn = 连接()
    try:
        左 = conn.execute("SELECT COUNT(*) AS c FROM 左轨流水").fetchone()["c"]
        # 结论读现行正集：冲正隔离行不计入右笔数
        右 = conn.execute(
            "SELECT COUNT(*) AS c FROM 右轨流水 WHERE 命运='已入右'"
        ).fetchone()["c"]
        隔离行 = conn.execute("SELECT * FROM 冲正隔离").fetchall()
        旁数 = sum(1 for r in 隔离行 if r["形态"] == "旁路凭证")
        净数 = sum(1 for r in 隔离行 if r["形态"] != "旁路凭证")
        对齐 = conn.execute(
            "SELECT COUNT(*) AS c FROM 对齐差 WHERE 差型='对齐'"
        ).fetchone()["c"]
        悬 = conn.execute("SELECT COUNT(*) AS c FROM 悬账单").fetchone()["c"]
        差总 = conn.execute("SELECT COUNT(*) AS c FROM 对齐差").fetchone()["c"]

        问题 = []
        # 悬账单业务键应能在对齐差找到左缺/右缺
        for r in conn.execute("SELECT * FROM 悬账单").fetchall():
            行 = conn.execute(
                "SELECT 差型 FROM 对齐差 WHERE 业务键=?", (r["业务键"],)
            ).fetchone()
            if not 行:
                问题.append(f"悬账{r['业务键']}无对齐差行")
            elif r["悬边"] == "左" and 行["差型"] not in ("右缺", "金额不符"):
                问题.append(f"左悬{r['业务键']}差型={行['差型']}")
            elif r["悬边"] == "右" and 行["差型"] not in ("左缺", "金额不符"):
                问题.append(f"右悬{r['业务键']}差型={行['差型']}")

        # 冲正隔离自洽：负向是否在旁路凭证、现行右汇总是否含负，必须可区分
        for r in 隔离行:
            凭证 = conn.execute(
                "SELECT 1 FROM 旁路凭证 WHERE 事件编号=? AND 有效=1", (r["事件编号"],)
            ).fetchone()
            if r["形态"] == "旁路凭证" and not 凭证:
                问题.append(f"冲正{r['事件编号']}负向不在旁路凭证")
            if r["形态"] != "旁路凭证" and 凭证:
                问题.append(f"冲正{r['事件编号']}净值形态误写旁路凭证")
            if not conn.execute(
                "SELECT 1 FROM 右轨流水 WHERE 事件编号=? AND 命运='冲正隔离'",
                (r["事件编号"],),
            ).fetchone():
                问题.append(f"冲正{r['事件编号']}右轨流水缺隔离行")
        负现 = conn.execute(
            "SELECT COUNT(*) AS c FROM 右轨流水 WHERE 命运='已入右' AND 金额分<0"
        ).fetchone()["c"]
        if 负现:
            问题.append(f"现行右集含负向{负现}笔")

        通过 = len(问题) == 0 and (左 + 右 == 0 or 差总 > 0 or (左 > 0 and 右 > 0 and 对齐 >= 0))
        if 左 == 0 and 右 == 0 and not 隔离行:
            通过 = True
            说明 = "双轨皆空，结论空跑通过"
        elif 问题:
            通过 = False
            说明 = "问题:" + ";".join(问题[:6])
        else:
            说明 = (
                f"左{左}/右现行{右}/对齐{对齐}/悬{悬}/差行{差总} 自洽"
                f"；冲正隔离{len(隔离行)}笔(旁路{旁数}/净值{净数})，结论读现行正集"
            )

        conn.execute(
            """INSERT INTO 对拍结论(时刻,左笔数,右笔数,对齐笔数,悬账笔数,是否通过,说明)
               VALUES (?,?,?,?,?,?,?)""",
            (此刻(), 左, 右, 对齐, 悬, 1 if 通过 else 0, 说明),
        )
        conn.commit()
        return {
            "是否通过": 通过,
            "左笔数": 左,
            "右笔数": 右,
            "对齐笔数": 对齐,
            "悬账笔数": 悬,
            "冲正隔离笔数": len(隔离行),
            "旁路凭证笔数": 旁数,
            "说明": 说明,
            "问题": 问题,
        }
    finally:
        conn.close()
