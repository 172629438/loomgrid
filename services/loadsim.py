# -*- coding: utf-8 -*-
"""负荷模拟：25 个行业模板 + 参数化曲线生成。

**来源**：从 Fusion 主程序（``参考程序.py``）原样搬入，逻辑一字未改。
搬入原因：WinUI 版要复用同一套生成算法；在 C# 里重写会变成第三份实现，
且预览曲线与真实数据的形状会分叉。

命名注意（重要）：
    本模块的 25 个模板 = ``INDUSTRY_TEMPLATES``（daily_load_factor / peak_valley_ratio /
    schedule / month_factors / 早晚峰 / 午谷）。
    ``algorithms/loadgen.py`` 里另有一套 10 个的 ``INDUSTRY_PARAMS``
    （par / lf / cv / base_load + 省份基线），**是不同引擎**，不要混用。

与原文的唯一差异：
    ``generate_load_profile`` 多一个可选参数 ``seed``。
    ``seed=None`` 时仍用全局 ``np.random``，行为与 Fusion 一致；
    传 int 时用独立 ``RandomState``，同一 seed 可复现同一条曲线。

返回值形状：``{'load_array': (n_days, n_points), 'time_labels': [...],
'date_labels': [...], 'config': {...}, 'month_str': '2025-01'}``。
注意 ``month_str`` 是原文里的固定值，不代表真实月份。
"""

INDUSTRY_TEMPLATES = {
    # ── 连续流程工业(三班制) ── 引用源:贵州电网(2020)·连续性生产行业
    '电解铝': {
        'name': '电解铝',
        'daily_load_factor': 0.96,      # 日负荷率——贵州电网实测
        'peak_valley_ratio': 0.05,       # 峰谷差率——几乎无波动,电解槽不能停
        'weekend_factor': 0.98,          # 周末系数
        'schedule': 'three_shift',       # 三班连续
        'month_factors': [0.92,0.93,0.95,0.97,0.98,1.00,1.00,1.00,0.98,0.95,0.93,0.92],  # 季不均衡0.92
        'morning_peak': (8, 0.05),       # (时,相对增幅)——连续型几乎无峰
        'evening_peak': (20, 0.05),
        'noon_valley': (13, 0.02),
        'source': '贵州电网(2020)·电解铝连续生产',
    },
    '铁合金': {
        'name': '铁合金', 'daily_load_factor': 0.89, 'peak_valley_ratio': 0.15,
        'weekend_factor': 0.95, 'schedule': 'three_shift',
        'month_factors': [0.88,0.90,0.92,0.95,0.97,1.00,0.98,0.97,0.95,0.92,0.89,0.88],
        'morning_peak': (9, 0.08), 'evening_peak': (19, 0.10), 'noon_valley': (13, 0.04),
        'source': '贵州电网(2020)·铁合金',
    },
    '钢铁(电炉)': {
        'name': '钢铁(电炉)', 'daily_load_factor': 0.82, 'peak_valley_ratio': 0.30,
        'weekend_factor': 0.88, 'schedule': 'three_shift',
        'month_factors': [0.85,0.87,0.90,0.93,0.97,1.00,0.98,0.97,0.95,0.92,0.88,0.85],
        'morning_peak': (10, 0.15), 'evening_peak': (18, 0.12), 'noon_valley': (14, 0.08),
        'source': '清华大学(2024)·钢铁电炉负荷台阶',
    },
    '化工(连续)': {
        'name': '化工(连续)', 'daily_load_factor': 0.90, 'peak_valley_ratio': 0.12,
        'weekend_factor': 0.95, 'schedule': 'three_shift',
        'month_factors': [0.92,0.93,0.95,0.97,0.98,1.00,1.00,0.98,0.97,0.95,0.93,0.92],
        'morning_peak': (9, 0.06), 'evening_peak': (19, 0.06), 'noon_valley': (13, 0.04),
        'source': '配四手册·连续流程化工',
    },
    '造纸': {
        'name': '造纸', 'daily_load_factor': 0.85, 'peak_valley_ratio': 0.20,
        'weekend_factor': 0.90, 'schedule': 'three_shift',
        'month_factors': [0.88,0.89,0.92,0.95,0.97,1.00,0.98,0.97,0.96,0.93,0.90,0.88],
        'morning_peak': (9, 0.10), 'evening_peak': (18, 0.10), 'noon_valley': (13, 0.05),
        'source': '清华大学(2024)·造纸负荷台阶',
    },
    '水泥(粉磨)': {
        'name': '水泥(粉磨)', 'daily_load_factor': 0.68, 'peak_valley_ratio': 0.45,
        'weekend_factor': 0.70, 'schedule': 'two_shift',
        'month_factors': [0.75,0.78,0.82,0.88,0.93,1.00,0.97,0.95,0.92,0.88,0.82,0.75],
        'morning_peak': (7, 0.35), 'evening_peak': (14, 0.20), 'noon_valley': (12, 0.30),
        'source': '贵州电网(2020)+清华大学(2024)·水泥',
    },
    '纺织/印染': {
        'name': '纺织/印染', 'daily_load_factor': 0.88, 'peak_valley_ratio': 0.18,
        'weekend_factor': 0.92, 'schedule': 'three_shift',
        'month_factors': [0.88,0.90,0.92,0.95,0.97,1.00,0.98,0.97,0.96,0.93,0.91,0.88],
        'morning_peak': (8, 0.08), 'evening_peak': (18, 0.10), 'noon_valley': (13, 0.05),
        'source': '配四手册·纺织工业',
    },
    '制药(原料药)': {
        'name': '制药(原料药)', 'daily_load_factor': 0.82, 'peak_valley_ratio': 0.25,
        'weekend_factor': 0.88, 'schedule': 'three_shift',
        'month_factors': [0.90,0.91,0.93,0.95,0.97,1.00,0.98,0.97,0.95,0.93,0.91,0.90],
        'morning_peak': (9, 0.12), 'evening_peak': (18, 0.12), 'noon_valley': (13, 0.06),
        'source': '配四手册·制药+GMP环境',
    },
    '塑料/注塑': {
        'name': '塑料/注塑', 'daily_load_factor': 0.75, 'peak_valley_ratio': 0.30,
        'weekend_factor': 0.78, 'schedule': 'two_shift',
        'month_factors': [0.85,0.87,0.90,0.93,0.96,1.00,0.98,0.97,0.95,0.92,0.88,0.85],
        'morning_peak': (8, 0.20), 'evening_peak': (16, 0.15), 'noon_valley': (12, 0.12),
        'source': '配四手册·注塑',
    },
    '半导体封装': {
        'name': '半导体封装', 'daily_load_factor': 0.92, 'peak_valley_ratio': 0.10,
        'weekend_factor': 0.97, 'schedule': 'three_shift',
        'month_factors': [0.93,0.94,0.95,0.96,0.98,1.00,1.00,0.99,0.97,0.96,0.94,0.93],
        'morning_peak': (9, 0.05), 'evening_peak': (19, 0.05), 'noon_valley': (13, 0.03),
        'source': '产业特性·7×24洁净间供电',
    },
    # ── 离散制造(单/双班制) ── 引用源:河南电网(2017)·不连续生产行业
    '机械加工(单班)': {
        'name': '机械加工(单班)', 'daily_load_factor': 0.55, 'peak_valley_ratio': 0.65,
        'weekend_factor': 0.40, 'schedule': 'one_shift',
        'month_factors': [0.80,0.82,0.86,0.90,0.94,1.00,0.97,0.95,0.92,0.88,0.84,0.80],
        'morning_peak': (9, 0.60), 'evening_peak': (15, 0.20), 'noon_valley': (12, 0.50),
        'source': '配四手册+河南电网(2017)·单班制机械',
    },
    '机械加工(双班)': {
        'name': '机械加工(双班)', 'daily_load_factor': 0.72, 'peak_valley_ratio': 0.35,
        'weekend_factor': 0.60, 'schedule': 'two_shift',
        'month_factors': [0.82,0.84,0.87,0.91,0.95,1.00,0.97,0.95,0.93,0.89,0.85,0.82],
        'morning_peak': (8, 0.25), 'evening_peak': (14, 0.20), 'noon_valley': (12, 0.15),
        'source': '配四手册·双班制机械',
    },
    '电子制造': {
        'name': '电子制造', 'daily_load_factor': 0.78, 'peak_valley_ratio': 0.22,
        'weekend_factor': 0.75, 'schedule': 'two_shift',
        'month_factors': [0.88,0.89,0.91,0.93,0.96,1.00,0.98,0.97,0.95,0.92,0.90,0.88],
        'morning_peak': (9, 0.15), 'evening_peak': (16, 0.12), 'noon_valley': (12, 0.08),
        'source': '河南电网(2017)·电气电子制造',
    },
    '食品加工(双班)': {
        'name': '食品加工(双班)', 'daily_load_factor': 0.70, 'peak_valley_ratio': 0.38,
        'weekend_factor': 0.65, 'schedule': 'two_shift',
        'month_factors': [0.82,0.83,0.86,0.90,0.94,1.00,0.97,0.96,0.94,0.91,0.87,0.83],
        'morning_peak': (7, 0.28), 'evening_peak': (15, 0.18), 'noon_valley': (12, 0.20),
        'source': '配四手册·食品工业',
    },
    '汽车制造': {
        'name': '汽车制造', 'daily_load_factor': 0.65, 'peak_valley_ratio': 0.42,
        'weekend_factor': 0.55, 'schedule': 'two_shift',
        'month_factors': [0.78,0.80,0.84,0.89,0.93,1.00,0.96,0.94,0.91,0.87,0.82,0.78],
        'morning_peak': (9, 0.30), 'evening_peak': (16, 0.20), 'noon_valley': (12, 0.20),
        'source': '产业特性·冲焊双班+总装单班',
    },
    '矿山/选矿': {
        'name': '矿山/选矿', 'daily_load_factor': 0.72, 'peak_valley_ratio': 0.35,
        'weekend_factor': 0.82, 'schedule': 'three_shift',
        'month_factors': [0.84,0.85,0.88,0.92,0.95,1.00,0.97,0.95,0.93,0.90,0.86,0.84],
        'morning_peak': (9, 0.18), 'evening_peak': (18, 0.15), 'noon_valley': (13, 0.08),
        'source': '配四手册·矿山开采',
    },
    # ── 商业/公共建筑 ── 引用源:发改委(2019)省级典型曲线+JGJ16
    '数据中心': {
        'name': '数据中心', 'daily_load_factor': 0.95, 'peak_valley_ratio': 0.06,
        'weekend_factor': 0.98, 'schedule': 'three_shift',
        'month_factors': [0.72,0.74,0.78,0.82,0.88,0.95,1.00,0.98,0.90,0.82,0.76,0.70],  # HVAC季节性
        'morning_peak': (10, 0.04), 'evening_peak': (18, 0.03), 'noon_valley': (14, 0.02),
        'source': '贵州电网(2020)·大数据中心',
    },
    '商业楼宇': {
        'name': '商业楼宇', 'daily_load_factor': 0.58, 'peak_valley_ratio': 0.55,
        'weekend_factor': 0.80, 'schedule': 'commercial',
        'month_factors': [0.60,0.62,0.68,0.75,0.85,0.95,1.00,0.98,0.88,0.75,0.65,0.62],
        'morning_peak': (10, 0.45), 'evening_peak': (18, 0.35), 'noon_valley': (14, 0.30),
        'source': '发改委(2019)+JGJ16·商业建筑',
    },
    '办公楼宇': {
        'name': '办公楼宇', 'daily_load_factor': 0.50, 'peak_valley_ratio': 0.60,
        'weekend_factor': 0.30, 'schedule': 'commercial',
        'month_factors': [0.60,0.62,0.68,0.75,0.85,0.95,1.00,0.98,0.88,0.75,0.65,0.60],
        'morning_peak': (9, 0.50), 'evening_peak': (15, 0.30), 'noon_valley': (12, 0.35),
        'source': '发改委(2019)+JGJ16·办公建筑',
    },
    '医院': {
        'name': '医院', 'daily_load_factor': 0.70, 'peak_valley_ratio': 0.35,
        'weekend_factor': 0.82, 'schedule': 'commercial',
        'month_factors': [0.88,0.86,0.82,0.80,0.85,0.92,1.00,0.98,0.90,0.82,0.85,0.88],
        'morning_peak': (9, 0.22), 'evening_peak': (18, 0.18), 'noon_valley': (13, 0.12),
        'source': 'JGJ16·医疗建筑',
    },
    '酒店': {
        'name': '酒店', 'daily_load_factor': 0.55, 'peak_valley_ratio': 0.50,
        'weekend_factor': 0.90, 'schedule': 'commercial',
        'month_factors': [0.65,0.67,0.72,0.78,0.85,0.95,1.00,0.97,0.85,0.78,0.68,0.65],
        'morning_peak': (8, 0.35), 'evening_peak': (19, 0.40), 'noon_valley': (14, 0.25),
        'source': '配四手册·酒店',
    },
    '居民小区': {
        'name': '居民小区', 'daily_load_factor': 0.45, 'peak_valley_ratio': 0.50,
        'weekend_factor': 1.05, 'schedule': 'commercial',
        'month_factors': [0.70,0.68,0.58,0.48,0.52,0.72,0.95,1.00,0.82,0.55,0.62,0.75],
        'morning_peak': (8, 0.40), 'evening_peak': (20, 0.50), 'noon_valley': (13, 0.25),
        'source': '发改委(2019)·居民+河南电网(2017)',
    },
    # ── 特殊行业 ──
    '农业排灌': {
        'name': '农业排灌', 'daily_load_factor': 0.35, 'peak_valley_ratio': 0.85,
        'weekend_factor': 1.0, 'schedule': 'one_shift',
        'month_factors': [0.10,0.12,0.18,0.25,0.50,0.90,1.00,0.95,0.60,0.30,0.15,0.10],
        'morning_peak': (10, 0.80), 'evening_peak': (15, 0.50), 'noon_valley': (13, 0.40),
        'source': '贵州电网(2020)·农业电灌站',
    },
    '污水处理': {
        'name': '污水处理', 'daily_load_factor': 0.78, 'peak_valley_ratio': 0.28,
        'weekend_factor': 0.95, 'schedule': 'three_shift',
        'month_factors': [0.88,0.88,0.90,0.92,0.94,0.97,1.00,0.98,0.95,0.92,0.90,0.88],
        'morning_peak': (8, 0.12), 'evening_peak': (18, 0.15), 'noon_valley': (13, 0.10),
        'source': '配四手册·污水处理',
    },
    '冷链物流': {
        'name': '冷链物流', 'daily_load_factor': 0.85, 'peak_valley_ratio': 0.20,
        'weekend_factor': 0.92, 'schedule': 'three_shift',
        'month_factors': [0.90,0.88,0.85,0.82,0.85,0.92,1.00,0.98,0.90,0.82,0.86,0.90],
        'morning_peak': (9, 0.08), 'evening_peak': (18, 0.12), 'noon_valley': (14, 0.06),
        'source': 'RMI(2023)·冷库+除霜脉冲',
    },
}

def generate_load_profile(config=None, seed=None):
    """v2.29: 参数化生成全年15分钟精度人工负荷曲线。

    Args:
        config: dict或None。None时使用电解铝默认模板。
            关键字段:
            - industry: 行业模板名(默认'电子制造'),覆盖以下参数
            - avg_power_kw: 平均功率(kW),默认500
            - daily_load_factor: 日负荷率(0~1)
            - peak_valley_ratio: 峰谷差率(0~1)
            - weekend_factor: 周末系数(0~1)
            - schedule: 'one_shift'/'two_shift'/'three_shift'/'commercial'
            - month_factors: [12个]月系数
            - morning_peak: (时, 增幅) 早高峰位置和强度
            - evening_peak: (时, 增幅) 晚高峰位置和强度
            - noon_valley: (时, 降幅) 午间谷深
            - noise_std: 随机噪声标准差比例,默认0.05
            - pv_kw: 叠加光伏容量(kW),默认0(不叠加)
            - n_days: 天数,默认365
            - n_points: 每日点数,默认96(15min)

    Returns:
        dict: {'load_array': (n_days,n_points), 'time_labels': [...], 'date_labels': [...], 'config': config}
    """
    import numpy as np
    from datetime import datetime, timedelta

    # seed 语义（与 Fusion 完全对齐）：
    #   seed=None -> 用全局 np.random，每次不同（原行为）
    #   seed=int  -> 固定序列，同一个 seed 得到同一条曲线
    rng = np.random.RandomState(seed) if seed is not None else np.random

    # ── 默认配置 ──
    if config is None:
        config = {}
    industry = config.get('industry', '电子制造')
    template = INDUSTRY_TEMPLATES.get(industry, INDUSTRY_TEMPLATES['电子制造'])

    # 合并配置:用户配置覆盖模板默认值
    avg_power = config.get('avg_power_kw', 500.0)
    daily_load_factor = config.get('daily_load_factor', template['daily_load_factor'])
    peak_valley_ratio = config.get('peak_valley_ratio', template['peak_valley_ratio'])
    weekend_factor = config.get('weekend_factor', template['weekend_factor'])
    schedule = config.get('schedule', template['schedule'])
    month_factors = config.get('month_factors', template['month_factors'])
    morning_peak = config.get('morning_peak', template['morning_peak'])
    evening_peak = config.get('evening_peak', template['evening_peak'])
    noon_valley = config.get('noon_valley', template['noon_valley'])
    noise_std = config.get('noise_std', 0.05)
    pv_kw = config.get('pv_kw', 0.0)
    n_days = config.get('n_days', 365)
    n_points = config.get('n_points', 96)

    # ── 计算基准值 ──
    # 日负荷率 = 平均/最大 → 最大 = 平均/日负荷率
    peak_power = avg_power / max(daily_load_factor, 0.01)
    # 峰谷差率 = (峰值-谷值)/峰值 → 谷值 = 峰值*(1-峰谷差率)
    valley_power = peak_power * (1.0 - peak_valley_ratio)
    # v2.3.39：删掉 base_power = valley_power。
    # 它只赋值不用，但注释「基荷=谷值」会让人以为它参与了计算——
    # 实际基荷是通过下面 day_profile 归一化后，由
    # L312 的 valley_power + (peak-valley)*profile 重新映射回来的。

    # ── 生成日典型曲线(0~1归一化) ──
    t = np.linspace(0, 24, n_points, endpoint=False)
    day_profile = np.ones(n_points)  # 基荷=1

    # 早高峰(高斯峰)
    mp_h, mp_amp = morning_peak
    morning_gauss = mp_amp * np.exp(-0.5 * ((t - mp_h) / 1.5) ** 2)
    day_profile += morning_gauss

    # 晚高峰(高斯峰)
    ep_h, ep_amp = evening_peak
    evening_gauss = ep_amp * np.exp(-0.5 * ((t - ep_h) / 1.5) ** 2)
    day_profile += evening_gauss

    # 午间低谷(下凹)
    nv_h, nv_depth = noon_valley
    noon_dip = nv_depth * np.exp(-0.5 * ((t - nv_h) / 2.0) ** 2)
    day_profile -= noon_dip

    # 班制修正
    if schedule == 'one_shift':
        # 单班:8-17点外大幅衰减
        mask = (t < 7) | (t > 18)
        day_profile[mask] *= 0.3
        mask2 = (t >= 7) & (t < 8)
        day_profile[mask2] *= 0.6
        mask2 = (t > 17) & (t <= 18)
        day_profile[mask2] *= 0.6
    elif schedule == 'two_shift':
        # 双班:6-22点,凌晨衰减
        mask = (t < 5) | (t > 23)
        day_profile[mask] *= 0.4
    elif schedule == 'commercial':
        # 商业:6-22点
        mask = (t < 6) | (t > 22)
        day_profile[mask] *= 0.35

    day_profile = np.maximum(day_profile, 0.05)  # 最低5%保底

    # ── 缩放至目标峰谷范围 ──
    day_profile = day_profile / day_profile.max()  # 归一化到0~1
    day_profile = valley_power + (peak_power - valley_power) * day_profile

    # ── 生成全年数据 ──
    result = np.zeros((n_days, n_points))
    date_labels = []

    for day_idx in range(n_days):
        # 日期
        d = datetime(2025, 1, 1) + timedelta(days=day_idx)
        date_labels.append(d.strftime('%Y-%m-%d'))

        # 月系数
        mf = month_factors[d.month - 1]

        # 日类型
        weekday = d.weekday()
        if weekday >= 5:  # 周六日
            day_factor = weekend_factor
        else:
            day_factor = 1.0

        # 日曲线 = 月系数 × 日类型 × 典型曲线
        daily = day_profile * mf * day_factor

        # 随机噪声
        if noise_std > 0:
            noise = rng.normal(0, noise_std * avg_power, n_points)
            daily += noise

        daily = np.maximum(daily, 0.0)
        result[day_idx] = daily

    # ── 光伏叠加(可选) ──
    if pv_kw > 0:
        result = _add_pv_to_load(result, date_labels, pv_kw, n_points)

    time_labels = [f'{i*24*60//n_points//60:02d}:{i*24*60//n_points%60:02d}' for i in range(n_points)]

    return {
        'load_array': result,
        'time_labels': time_labels,
        'date_labels': date_labels,
        'config': config,
        'month_str': '2025-01',  # 全年数据
    }


def _add_pv_to_load(load_array, date_labels, pv_kw, n_points):
    """v2.29: 叠加简化光伏出力到负荷曲线上。"""
    import numpy as np
    load_array = load_array.copy()  # v3.2: 防御性拷贝
    t_day = np.linspace(0, 24, n_points, endpoint=False)
    # 简化的日光伏曲线:高斯形,峰值在12:30,宽度约6小时
    pv_profile = np.exp(-0.5 * ((t_day - 12.5) / 2.5) ** 2)
    pv_profile = pv_profile / pv_profile.max() * pv_kw
    # 日出/日落外归零
    pv_profile[(t_day < 5) | (t_day > 20)] = 0.0

    # 月调幅:夏季长冬季短
    month_pv_factor = [0.55,0.65,0.80,0.90,0.95,1.00,0.98,0.92,0.82,0.72,0.60,0.52]

    labels = date_labels
    for i in range(len(load_array)):
        m = int(labels[i][5:7]) if len(labels[i]) >= 7 else 1
        result = load_array[i] - pv_profile * month_pv_factor[m-1]
        # v3.1fix: 已通过传入.copy()避免副作用,调用方负责拷贝
        load_array[i] = result
    return load_array
