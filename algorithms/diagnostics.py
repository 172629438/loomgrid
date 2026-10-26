# -*- coding: utf-8 -*-
"""
Load Diagnostics
================

负荷指纹分析与行业匹配（诊断引擎的轻量入口）。

本文件由 Load Curve Studio (LoomGrid) 主程序剥离而来，保留原始逻辑，仅去除 GUI 依赖。
原始出处：负荷曲线批量生成Ultra.py v3.2

依赖：numpy

"""

import math
import numpy as np


def analyze_load_fingerprint(load_array, date_labels, time_labels):
    """v2.28: 分析负荷曲线的运行模式指纹.
    
    Args:
        load_array: shape (n_days, n_points) 的负荷矩阵
        date_labels: 日期标签列表 (长度=n_days)
        time_labels: 时间标签列表 (长度=n_points)
    
    Returns:
        dict: {primary_tag, secondary_tags[], confidence, weekday_avg, weekend_avg, ...}
    """
    arr = np.asarray(load_array)
    if arr.ndim != 2 or arr.shape[0] < 3:
        return {'primary_tag': '📊 数据不足', 'secondary_tags': [], 'confidence': 0.0}
    n_days, n_points = arr.shape
    # ── 周日/周末分离 ──
    weekday_mask = np.zeros(n_days, dtype=bool)
    weekend_mask = np.zeros(n_days, dtype=bool)
    for i, dl in enumerate(date_labels):
        dt = _parse_date_flexible(str(dl))
        if dt is not None:
            wd = dt.weekday()
            if wd < 5:
                weekday_mask[i] = True
            else:
                weekend_mask[i] = True
    has_weekend = weekend_mask.sum() >= 2
    # ── 日均负荷 ──
    daily_avg = np.nanmean(arr, axis=1)
    peak_all = np.nanmax(arr)
    daily_peak = np.nanmax(arr, axis=1)
    # ── 时间索引(用于凌晨/早晚检测) ──
    hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
    night_mask = (hours >= 2) & (hours < 5)  # 凌晨2-5点
    morning_mask = (hours >= 8) & (hours < 9)  # 早8-9点
    evening_mask = (hours >= 17) & (hours < 18)  # 晚17-18点
    prev_mask = (hours >= 7) & (hours < 8)  # 7-8点(对比基线)
    post_mask = (hours >= 18) & (hours < 19)  # 18-19点(对比基线)
    # ── 检测1: 光伏主导 ──
    zero_rate = np.sum(arr < peak_all * 0.005) / arr.size
    if zero_rate > 0.40:
        return {'primary_tag': '☀️ 光伏主导', 'secondary_tags': [], 'confidence': min(zero_rate * 100, 95),
                'zero_rate': zero_rate * 100}
    # ── 检测2: 连续生产 ──
    if night_mask.sum() >= 2:
        night_load = np.nanmean(arr[:, night_mask])
        if peak_all > 0 and night_load / peak_all > 0.60:
            return {'primary_tag': '🏭 连续生产', 'secondary_tags': [],
                    'confidence': min(night_load / peak_all * 100, 95)}
    # ── 检测3: 白班制 ──
    if morning_mask.sum() >= 1 and prev_mask.sum() >= 1:
        morning_avg = np.nanmean(arr[:, morning_mask])
        prev_avg = np.nanmean(arr[:, prev_mask])
        if prev_avg > 0 and (morning_avg - prev_avg) / prev_avg > 0.50:
            evening_jump = 0.0
            if evening_mask.sum() >= 1 and post_mask.sum() >= 1:
                eve_avg = np.nanmean(arr[:, evening_mask])
                post_avg = np.nanmean(arr[:, post_mask])
                if eve_avg > 0:
                    evening_jump = (eve_avg - post_avg) / eve_avg if post_avg >= 0 else 0
            if evening_jump < 0 or (evening_mask.sum() == 0):
                return {'primary_tag': '🌅 白班制', 'secondary_tags': [],
                        'confidence': 80, 'morning_jump_pct': (morning_avg - prev_avg) / prev_avg * 100}
    # ── 检测4: 周末模式 ──
    tags = []
    conf = 0
    if has_weekend:
        wd_avg = np.nanmean(daily_avg[weekday_mask]) if weekday_mask.sum() > 0 else 0
        we_avg = np.nanmean(daily_avg[weekend_mask]) if weekend_mask.sum() > 0 else 0
        if wd_avg > 0:
            we_ratio = we_avg / wd_avg
            if we_ratio < 0.30:
                tags.append('🗓 双休')
                conf = max(conf, (1 - we_ratio) * 100)
            elif we_ratio < 0.60:
                tags.append('📅 单休')
                conf = max(conf, (1 - we_ratio) * 120 if we_ratio < 0.45 else 70)
            else:
                tags.append('🔄 无休')
                conf = 60
    # ── 检测5: 季节性 ──
    if n_days >= 30:
        monthly_avgs = []
        for i, dl in enumerate(date_labels):
            dt = _parse_date_flexible(str(dl))
            if dt is not None:
                monthly_avgs.append((dt.month, daily_avg[i]))
        if monthly_avgs:
            by_month = defaultdict(list)
            for m, v in monthly_avgs:
                by_month[m].append(v)
            month_means = [np.mean(v) for v in by_month.values() if len(v) >= 5]
            if len(month_means) >= 3:
                cv = np.std(month_means) / max(np.mean(month_means), 1e-9)
                if cv > 0.30:
                    tags.append('🍂 季节性波动')
                    conf = max(conf, min(cv * 150, 85))
    if not tags:
        tags.append('⚙️ 常规模式')
        conf = 50
    return {'primary_tag': tags[0], 'secondary_tags': tags[1:], 'confidence': round(conf, 1)}


def match_fingerprint_to_industry(fp_result):
    """v2.32: 从指纹标签匹配最可能的行业模板。返回(industry_name, confidence)。"""
    if not fp_result:
        return '电子制造', 50
    # v3.2fix: 指纹→行业映射表
    _FINGERPRINT_TO_INDUSTRY = {
        'continuous_process': ['电解铝', '铁合金', '钢铁(电炉)', '化工(连续)', '造纸'],
        'three_shift': ['制药(原料药)', '矿山/选矿', '数据中心', '污水处理', '冷链物流', '纺织/印染', '半导体封装'],
        'two_shift': ['水泥(粉磨)', '塑料/注塑', '电子制造', '机械加工(双班)', '食品加工(双班)', '汽车制造'],
        'one_shift': ['机械加工(单班)'],
        'commercial': ['商业楼宇', '办公楼宇', '医院', '酒店', '居民小区'],
        'seasonal': ['农业排灌'],
    }
    best = None; best_conf = 0
    for tag, industries in _FINGERPRINT_TO_INDUSTRY.items():
        if tag == fp_result.get('primary_tag', ''):
            for ind in industries:
                if best is None or fp_result.get('confidence', 0) > best_conf:
                    best = ind; best_conf = fp_result.get('confidence', 0)
    return best or '电子制造', best_conf or 50


def _estimate_degradation(equiv_full_cycles_per_year, chemistry='LFP',
                           ambient_temp_c=25, project_life_years=15):
    """电池退化预估模型(日历老化 + 循环老化混合模型).

    模型: Q_loss = k_cal(T) x t + k_cyc(T) x N
    其中温度加速因子为 Arrhenius 形式: k(T) = k_25 x exp(Ea/R x (1/298.15 - 1/T))

    标定基准(25degC,365 循环/年):
      LFP: 第5年 SOH~=88%,第10年 SOH~=79%,第13年触发更换
      NMC: 第5年 SOH~=83%,第8年 SOH~=72%,第8年触发更换

    Args:
        equiv_full_cycles_per_year: 年均等效全循环次数
        chemistry: 'LFP' 或 'NMC'
        ambient_temp_c: 环境温度 (degC)
        project_life_years: 项目寿命 (年)

    Returns:
        dict: {yearly_soh_pct, replacement_year, note, ...}
    """
    # ── 输入验证 ──
    if equiv_full_cycles_per_year is None or equiv_full_cycles_per_year < 0:
        equiv_full_cycles_per_year = 0
    if chemistry not in ('LFP', 'NMC'):
        chemistry = 'LFP'
        log.warning("_estimate_degradation: 未知电池化学类型,回退为LFP")
    if ambient_temp_c < -50 or ambient_temp_c > 80:
        log.warning(f"_estimate_degradation: 环境温度{ambient_temp_c}degC超出合理范围,已钳位")
        ambient_temp_c = max(-50, min(80, ambient_temp_c))
    if project_life_years < 1 or project_life_years > 50:
        log.warning(f"_estimate_degradation: 项目寿命{project_life_years}年超出合理范围")
        project_life_years = max(1, min(50, project_life_years))

    # ── 25degC 基准衰减系数 ──
    if chemistry == 'LFP':
        k_cal_25 = 0.012     # 日历衰减系数 (1/年)
        k_cyc_25 = 0.000025  # 循环衰减系数 (1/次)
        Ea_cal = 24000       # 日历老化活化能 J/mol
        Ea_cyc = 15000       # 循环老化活化能 J/mol
        label = 'LFP (磷酸铁锂)'
    else:  # NMC
        k_cal_25 = 0.018
        k_cyc_25 = 0.000040
        Ea_cal = 24000
        Ea_cyc = 15000
        label = 'NMC (三元锂)'

    R = 8.314
    T_ref = 298.15          # 25degC 参考温度 (K)
    T_actual = ambient_temp_c + 273.15

    # 温度加速因子
    accel_cal = math.exp((Ea_cal / R) * (1.0 / T_ref - 1.0 / T_actual))
    accel_cyc = math.exp((Ea_cyc / R) * (1.0 / T_ref - 1.0 / T_actual))
    k_cal_T = k_cal_25 * accel_cal
    k_cyc_T = k_cyc_25 * accel_cyc

    yearly_soh = []
    cumulative_cycles = 0
    replacement_year = None

    for y in range(1, project_life_years + 1):
        cumulative_cycles += equiv_full_cycles_per_year
        q_loss_cal = k_cal_T * y
        q_loss_cyc = k_cyc_T * cumulative_cycles
        q_loss_total = q_loss_cal + q_loss_cyc
        soh = max(0.0, 1.0 - q_loss_total)
        yearly_soh.append(round(soh * 100, 1))
        if replacement_year is None and soh < 0.70:
            replacement_year = y

    return {
        'chemistry': chemistry,
        'chemistry_label': label,
        'ambient_temp_c': ambient_temp_c,
        'cycles_per_year': equiv_full_cycles_per_year,
        'yearly_soh_pct': yearly_soh,
        'year5_soh_pct': yearly_soh[4] if len(yearly_soh) >= 5 else yearly_soh[-1],
        'year10_soh_pct': yearly_soh[9] if len(yearly_soh) >= 10 else yearly_soh[-1],
        'replacement_year': replacement_year,
        'cal_loss_rate': round(k_cal_T * 100, 2),
        'cyc_loss_rate': round(k_cyc_T * 100, 5),
        'note': (f'{label},{ambient_temp_c}degC环境下(日历衰减{k_cal_T*100:.1f}%/年 + 循环衰减{k_cyc_T*100:.4f}%/次),'
                 f'第5年SOH~={yearly_soh[4] if len(yearly_soh)>=5 else yearly_soh[-1]}%,'
                 + (f'预计第{replacement_year}年需更换' if replacement_year
                    else f'{project_life_years}年内未触发更换阈值(SOH>{70}%)'))
    }
