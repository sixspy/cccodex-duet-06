import os
import threading
from pathlib import Path

_tmp = Path(__file__).resolve().parent / "_tmp"
_tmp.mkdir(exist_ok=True)
os.environ["数据目录"] = str(_tmp)
os.environ["夹具目录"] = str(Path(__file__).resolve().parents[2] / "fixtures")

from app import seed
from app.db import 连接
from app.services import 左装载, 右装载, 对齐, 结论, 目录, 冲正隔离


def setup_function():
    db = _tmp / "对拍.db"
    if db.exists():
        db.unlink()
    seed.初始化库()


def _右行(事件编号):
    conn = 连接()
    r = conn.execute("SELECT * FROM 右轨流水 WHERE 事件编号=?", (事件编号,)).fetchone()
    conn.close()
    return dict(r) if r else None


def _装左(键额):
    左装载.装一批({"事件": [
        {"事件编号": f"L-{k}", "业务键": k, "金额分": v, "源键": "渠甲", "事件时": "2026-11-24T09:00:00Z"}
        for k, v in 键额.items()
    ]})


def _装右(事件):
    return 右装载.装一批({"事件": 事件})


def test_旁路形态_同键右正后右负():
    _装左({"K1": 1000})
    r = _装右([
        {"事件编号": "RP1", "业务键": "K1", "金额分": 1000, "源键": "核乙", "事件时": "2026-11-24T09:00:01Z"},
        {"事件编号": "RN1", "业务键": "K1", "金额分": -1000, "源键": "核乙", "事件时": "2026-11-24T09:00:02Z", "冲正标记": True},
    ])
    assert r["入流"] == 1 and r["冲正隔离"] == 1
    # 负向在旁路凭证，不在现行右集
    负 = _右行("RN1")
    assert 负["命运"] == "冲正隔离" and 负["冲正标记"] == 1 and 负["隔离形态"] == "旁路凭证"
    凭证 = 目录.旁路凭证列表()
    assert len(凭证) == 1 and 凭证[0]["事件编号"] == "RN1" and 凭证[0]["金额分"] == -1000
    # 正向单独对齐：现行右汇总不含负
    对齐.跑对齐()
    差 = {d["业务键"]: d for d in 目录.差列表()}
    assert 差["K1"]["差型"] == "对齐" and 差["K1"]["右金额分"] == 1000
    assert "冲正隔离" in 差["K1"]["说明"] and "旁路凭证" in 差["K1"]["说明"]
    g = 结论.跑一次()
    assert g["是否通过"] is True
    assert g["右笔数"] == 1  # 结论读现行正集
    assert g["冲正隔离笔数"] == 1 and g["旁路凭证笔数"] == 1


def test_净值形态_同键右正后右负一行():
    目录.改轨规({"冲正隔离形态": "净值"})
    _装左({"K2": 600})
    _装右([
        {"事件编号": "RP2", "业务键": "K2", "金额分": 1000, "源键": "核乙", "事件时": "2026-11-24T09:00:01Z"},
        {"事件编号": "RN2", "业务键": "K2", "金额分": -400, "源键": "核乙", "事件时": "2026-11-24T09:00:02Z"},
    ])
    # 净值形态不写旁路凭证
    assert 目录.旁路凭证列表() == []
    隔离 = 目录.冲正隔离列表()
    assert len(隔离) == 1 and 隔离[0]["形态"] == "净值"
    assert 隔离[0]["读引用"] == "净值汇总:K2"  # 读引用与旁路形态不同
    对齐.跑对齐()
    差 = [d for d in 目录.差列表() if d["业务键"] == "K2"]
    assert len(差) == 1  # 净值形态一行
    assert 差[0]["右金额分"] == 600 and 差[0]["差型"] == "对齐"
    g = 结论.跑一次()
    assert g["是否通过"] is True


def test_仅负向_旁路不成普通右缺悬账():
    _装右([
        {"事件编号": "RN9", "业务键": "K9", "金额分": -700, "源键": "核乙", "事件时": "2026-11-24T09:00:03Z"},
    ])
    对齐.跑对齐()
    # 负向进旁路凭证，不落成普通右缺/左缺悬账
    assert all(d["业务键"] != "K9" for d in 目录.差列表())
    assert all(h["业务键"] != "K9" for h in 目录.悬列表())
    凭证 = 目录.旁路凭证列表()
    assert len(凭证) == 1 and 凭证[0]["业务键"] == "K9"
    g = 结论.跑一次()
    assert g["是否通过"] is True


def test_仅负向_净值形态一行():
    目录.改轨规({"冲正隔离形态": "净值"})
    _装右([
        {"事件编号": "RN8", "业务键": "K8", "金额分": -700, "源键": "核乙", "事件时": "2026-11-24T09:00:03Z"},
    ])
    对齐.跑对齐()
    差 = [d for d in 目录.差列表() if d["业务键"] == "K8"]
    assert len(差) == 1 and 差[0]["差型"] == "左缺" and 差[0]["右金额分"] == -700
    assert "净值" in 差[0]["说明"]


def test_冲正与对齐并发不负向双计():
    目录.改轨规({"冲正隔离形态": "净值"})
    _装左({"K3": 600})
    正批 = {"事件": [{"事件编号": "RP3", "业务键": "K3", "金额分": 1000, "源键": "核乙", "事件时": "2026-11-24T09:00:01Z"}]}
    负批 = {"事件": [{"事件编号": "RN3", "业务键": "K3", "金额分": -400, "源键": "核乙", "事件时": "2026-11-24T09:00:02Z"}]}
    错 = []

    def 跑(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            错.append(e)

    线程 = [
        threading.Thread(target=跑, args=(lambda: 右装载.装一批(正批),)),
        threading.Thread(target=跑, args=(lambda: 右装载.装一批(负批),)),
        threading.Thread(target=跑, args=(对齐.跑对齐,)),
        threading.Thread(target=跑, args=(lambda: 右装载.装一批(负批),)),  # 重复再装
    ]
    for t in 线程:
        t.start()
    for t in 线程:
        t.join()
    assert not 错
    对齐.跑对齐()  # 落定后重算
    差 = [d for d in 目录.差列表() if d["业务键"] == "K3"]
    assert len(差) == 1 and 差[0]["右金额分"] == 600  # 负向只计一次
    assert len(目录.冲正隔离列表()) == 1
    g = 结论.跑一次()
    assert g["是否通过"] is True


def test_正向再装并发不串键双计():
    _装左({"K4": 1000})
    批 = {"事件": [{"事件编号": "RP4", "业务键": "K4", "金额分": 1000, "源键": "核乙", "事件时": "2026-11-24T09:00:01Z"}]}
    错 = []

    def 装():
        try:
            右装载.装一批(批)
        except Exception as e:  # noqa: BLE001
            错.append(e)

    线程 = [threading.Thread(target=装) for _ in range(6)]
    for t in 线程:
        t.start()
    for t in 线程:
        t.join()
    assert not 错
    对齐.跑对齐()
    差 = [d for d in 目录.差列表() if d["业务键"] == "K4"]
    assert len(差) == 1 and 差[0]["右金额分"] == 1000 and 差[0]["差型"] == "对齐"
    conn = 连接()
    n = conn.execute("SELECT COUNT(*) AS c FROM 右轨流水 WHERE 事件编号='RP4'").fetchone()["c"]
    conn.close()
    assert n == 1


def test_同一冲正并发装载不双成功():
    批 = {"事件": [{"事件编号": "RN5", "业务键": "K5", "金额分": -500, "源键": "核乙", "事件时": "2026-11-24T09:00:05Z"}]}
    错 = []

    def 装():
        try:
            右装载.装一批(批)
        except Exception as e:  # noqa: BLE001
            错.append(e)

    线程 = [threading.Thread(target=装) for _ in range(6)]
    for t in 线程:
        t.start()
    for t in 线程:
        t.join()
    assert not 错
    assert len(目录.冲正隔离列表()) == 1
    assert len(目录.旁路凭证列表()) == 1
    conn = 连接()
    n = conn.execute("SELECT COUNT(*) AS c FROM 右轨流水 WHERE 事件编号='RN5'").fetchone()["c"]
    conn.close()
    assert n == 1


def test_冲正隔离只读接口字段():
    _装右([
        {"事件编号": "RN6", "业务键": "K6", "金额分": -300, "源键": "核乙", "事件时": "2026-11-24T09:00:06Z"},
    ])
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.get("/api/冲正隔离")
    assert r.status_code == 200
    行 = r.json()
    assert len(行) == 1
    一 = 行[0]
    assert 一["业务键"] == "K6"
    assert 一["冲正标记"] == 1
    assert 一["形态"] in ("旁路凭证", "净值")
    assert 一["有效"] == 1
    # 负向是否在旁路凭证可区分
    r2 = c.get("/api/旁路凭证")
    assert r2.status_code == 200 and r2.json()[0]["事件编号"] == "RN6"


def test_冲正识别口径():
    assert 冲正隔离.是冲正({"金额分": -1})
    assert 冲正隔离.是冲正({"金额分": 100, "冲正标记": True})
    assert 冲正隔离.是冲正({"金额分": 100, "冲正标记": "冲正"})
    assert not 冲正隔离.是冲正({"金额分": 100})
