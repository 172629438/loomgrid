# -*- coding: utf-8 -*-
"""V4 手算核对：用 W1 第一天的 0-8 点，验证「充电段空间」。

背景：界面上 V4 每日表显示 2026-01-15 的充电空间 = 2,951.6 kWh。
这里独立算一遍，判据是差值 < 0.1%。

公式（mode A · 需量模式）：
    充电 = Σ max(容量线 − 负荷, 0) × dt

**梯形权重/中点法**（最容易算错的地方）：
    时段**含左含右**：0:00–8:00 取 0:00、0:15 … 8:00，共 **33** 点
     （core/spaces.py 用 np.searchsorted(..., side='right') 取右端点）
    首点 0.125 / 中间 0.25 / 末点 0.125，Σ 恒等于时段长度（8h → 8.0）
    ⚠️ 恒负荷测试无法区分端点约定：32 点与 33 点的 Σ 都是 8.0，必须用变负荷。

用法：
    python tests/verify_v4_handcalc.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LGM_ALLOW_ROOTS", str(ROOT / "baseline" / "_inputs"))

import numpy as np  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

C = TestClient(app)
W1 = ROOT / "baseline" / "_inputs" / "W1" / "W1_宽表96点.xlsx"

CAP_LINE = 800.0          # 容量线（界面默认 800）
CHARGE_HOURS = (0, 8)
DISCHARGE_HOURS = (18, 22)


def mid_dt(n_points: int, s_start: float, s_end: float, step: float = 0.25) -> np.ndarray:
    """中点法 + 两端补齐（照抄 core/spaces.py 的权重逻辑）。

    seg_dt[0]  = (sub[0] + sub[1]) / 2 - s_start     # 首点
    seg_dt[-1] = s_end - (sub[-1] + sub[-2]) / 2     # 末点
    seg_dt[i]  = (sub[i+1] - sub[i-1]) / 2.0         # 中间

    推论：Σ = s_end - s_start（恒等，不会少一格）。
    """
    sub = np.arange(n_points) * step
    w = np.full(n_points, step)
    if n_points >= 2:
        w[0] = (sub[0] + sub[1]) / 2 - s_start
        w[-1] = s_end - (sub[-1] + sub[-2]) / 2
        for i in range(1, n_points - 1):
            w[i] = (sub[i + 1] - sub[i - 1]) / 2.0
    return w


def main() -> int:
    r = C.post("/api/ingest/inspect", json={"path": str(W1)})
    sheet = r.json()["data"]["sheets"][0]
    r = C.post("/api/ingest/mapping", json={
        "path": str(W1), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    sid = r.json()["data"]["session_id"]

    pv = C.get(f"/api/preview?session_id={sid}&group=0&format=json").json()["data"]
    arr = np.array(pv["load_array"], dtype=float)
    tl = pv["time_labels"]
    dl = pv["date_labels"]
    n_pts = len(tl)
    print(f"数据：{len(dl)} 天 × {n_pts} 点，首日 {dl[0]}")

    # 索引：时刻 → 下标
    def idx_of(hhmm: str) -> int:
        return tl.index(hhmm)

    # 时段**含左含右**：0:00–8:00 取 0:00…8:00，共 33 点（与 side='right' 一致）
    i0, i1 = idx_of("00:00"), idx_of("08:00")
    j0, j1 = idx_of("18:00"), idx_of("22:00")

    day0 = arr[0]

    # ── 手算：充电段（mode A） ──
    seg = day0[i0:i1 + 1]
    dt = mid_dt(len(seg), 0.0, 8.0)
    print(f"\n[充电段 00:00–08:00] 点数={len(seg)}（含 08:00）"
          f"  Σdt={dt.sum():.4f} h（应等于 8.0000）")
    charge = float(np.sum(np.maximum(CAP_LINE - seg, 0.0) * dt))
    print(f"  充电空间 手算 = {charge:,.3f} kWh")
    print(f"  该段负荷范围 = {seg.min():.1f} … {seg.max():.1f}")

    # ── 手算：放电段 ──
    segd = day0[j0:j1 + 1]
    dtd = mid_dt(len(segd), 18.0, 22.0)
    print(f"\n[放电段 18:00–22:00] 点数={len(segd)}（含 22:00）"
          f"  Σdt={dtd.sum():.4f} h（应等于 4.0000）")
    discharge_limited = float(np.sum(segd * dtd))
    print(f"  放电（不限额时）上界 = {discharge_limited:,.3f} kWh")
    print(f"  该段负荷范围 = {segd.min():.1f} … {segd.max():.1f}")

    # ── 与后端 API 比对（同一口径） ──
    r = C.post("/api/calc/spaces", json={
        "session_id": sid, "group": 0,
        "segments": [
            {"start": 0, "end": 8, "type": "charge", "label": "充电段"},
            {"start": 18, "end": 22, "type": "discharge", "label": "放电段"},
        ],
        "billing": {"value": CAP_LINE},
        "mode": "A",
        "ignore_negative": True,
    })
    body = r.json()
    if not body.get("ok"):
        print("\nAPI 失败：", body.get("error"))
        return 1
    segs = body["data"]["segments"]
    # ⚠️ segments[0].charge 是**逐日数组**（5 天各一个值），不是单日值。
    # 拿 sum(...) 去比单日手算，必然差 5 倍——这就是第一次跑 FAIL 的原因，
    # 不是产品错，是比对对象错了。
    api_charge_day0 = float(segs[0]["charge"][0])
    api_charge = sum(segs[0]["charge"])
    api_discharge_day0 = float(segs[0]["discharge"][0])
    api_discharge = sum(segs[0]["discharge"])
    print(f"\n[后端 API 同口径]")
    print(f"  充电段 第 1 天 charge     = {api_charge_day0:,.3f} kWh")
    print(f"  充电段 全月 charge 合计   = {api_charge:,.3f} kWh（{len(segs[0]['charge'])} 天）")
    print(f"  放电段 第 1 天 discharge = {api_discharge_day0:,.3f} kWh")
    print(f"  放电段 全月 discharge    = {api_discharge:,.3f} kWh")

    diff = abs(charge - api_charge_day0) / api_charge_day0 if api_charge_day0 else 1.0
    print(f"\n判据：手算 vs API 第 1 天 相对差 = {diff:.6%}（要求 < 0.1%）")
    ok = diff < 0.001
    print("结果：" + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
