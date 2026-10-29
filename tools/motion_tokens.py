# -*- coding: utf-8 -*-
"""生成 CSS linear() 弹簧曲线。

阻尼谐振子：
    x(t) = 1 - e^(-ζωt) · [ cos(ωd·t) + (ζω/ωd)·sin(ωd·t) ]
    ωd = ω·sqrt(1-ζ²)

采样成 N 个等间距点，写成 linear(...)。
规范说 linear() 的点默认在时长上均匀分布，所以等间距采样即可。
"""
import math


def spring(zeta: float, omega: float, n: int = 48, settle: float = 1.0) -> str:
    """zeta 阻尼比（1=临界阻尼，>1 过阻尼，<1 有回弹）；omega 角频率。"""
    wd = omega * math.sqrt(max(1e-9, 1 - zeta * zeta)) if zeta < 1 else 0.0
    pts = []
    for i in range(n + 1):
        t = settle * i / n
        if zeta < 1:
            e = math.exp(-zeta * omega * t)
            x = 1 - e * (math.cos(wd * t) + (zeta * omega / wd) * math.sin(wd * t))
        else:
            e = math.exp(-omega * t)
            x = 1 - e * (1 + omega * t)
        pts.append(round(x, 4))
    # 末尾连续接近 1 的点裁掉，保留最后一个偏离点 +2
    vals = pts[:]
    last = len(vals) - 1
    while last > 2 and abs(vals[last - 1] - 1) < 0.0008:
        last -= 1
    vals = vals[:min(len(vals), last + 2)]
    vals[-1] = 1
    return "linear(" + ", ".join(f"{v:g}" for v in vals) + ")", len(vals)


def overshoot(zeta: float) -> float:
    if zeta >= 1:
        return 0.0
    return round(math.exp(-math.pi * zeta / math.sqrt(1 - zeta * zeta)) * 100, 1)


CANDS = [
    ("spring-tight", 0.72, 22.0, 48, 1.0),   # 小元素：探针、按钮、下划线
    ("spring-soft",  0.88, 13.0, 48, 1.2),   # 大块：幕布、织面带、条形
    ("spring-snap",  0.55, 30.0, 48, 0.85),  # 强调：锁定动作
]

for name, z, w, n, st in CANDS:
    s, k = spring(z, w, n, st)
    print(f"--- {name}  ζ={z} ω={w} 过冲 {overshoot(z)}%  {k} 点  {len(s)} 字符 ---")
    print(s)
    print()
