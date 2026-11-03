# -*- coding: utf-8 -*-
"""负荷曲线绘制、拟合、降采样与单图任务。

本模块从 Fusion v2.3.9 单文件原样搬出，函数体一字未改。
搬移依据：`tools/analyze_deps.py --group plotting` 实测闭包 22 符号 / 472 行。

这一块是本层唯一允许依赖 matplotlib 的地方（plotting/ 的职责就是出图）。
_draw_curves_core 208 行，2026-10 审查确认过以下边界，搬移勿动：
  n_times<2 抛 ValueError · 降采样阈值 500 · X 轴标签两种策略 · 图例过滤 _nolegend_
"""
from __future__ import annotations

import gc
import os
import re
import threading

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from loguru import logger as log
from matplotlib import patches as mpatches

from core.models import (
    ColorConst, CurveLabel, PeriodType, PlotOptions,
    DEFAULT_WEEKDAY_COLOR, DEFAULT_WEEKEND_COLOR, DEFAULT_HOLIDAY_COLOR,
    DEFAULT_WEEKEND_LINE_WIDTH, DEFAULT_HOLIDAY_LINE_WIDTH,
)
from core.timegrid import (
    _clean_plot, _expand_overnight, _get_day_type, _hour_x, _is_holiday_available,
)
from plotting.fonts import _get_cached_font, _setup_font
from plotting.naming import (
    MAX_FILENAME_ATTEMPTS, _sanitize_name, generate_default_title,
    generate_filename, parse_title_template,
)

__all__ = ["_draw_curves_core", "_plot_curves", "_plot_single_worker",
           "_compute_fits", "_downsample"]


WEEKEND_BG_ALPHA = 0.08


def _downsample(x, y, target=500):
    if len(x)<=target:
        return x,y
    idx = np.linspace(0,len(x)-1,target,dtype=int)
    return x[idx], y[idx]


def _compute_fits(x, y, types):
    res = {}
    avg = np.nanmean(y,axis=0)
    n = y.shape[1]
    try:
        from scipy import interpolate, ndimage
        SCIPY = True
    except ImportError:
        SCIPY = False
        log.debug("scipy 未安装，部分拟合功能不可用。")
    if 'poly2' in types and n>=3:
        v = ~np.isnan(avg)
        if np.sum(v)>=3:
            try:
                c = np.polyfit(x[v],avg[v],2)
                res['poly2'] = (np.poly1d(c)(x),'二次拟合')
            except Exception:
                log.debug("二次拟合失败", exc_info=True)
    if 'poly3' in types and n>=4:
        v = ~np.isnan(avg)
        if np.sum(v)>=4:
            try:
                c = np.polyfit(x[v],avg[v],3)
                res['poly3'] = (np.poly1d(c)(x),'三次拟合')
            except Exception:
                log.debug("三次拟合失败", exc_info=True)
    if 'moving_avg' in types:
        w = min(5,n//4) if n>=5 else 2
        if w>=2:
            res['moving_avg'] = (np.convolve(avg, np.ones(w)/w, mode='same'), f'移动平均({w})')
    if 'gaussian' in types and SCIPY:
        try:
            res['gaussian'] = (ndimage.gaussian_filter1d(avg,1.0),'高斯平滑')
        except Exception:
            log.debug("高斯平滑失败", exc_info=True)
    if 'spline' in types and SCIPY and n>=4:
        v = ~np.isnan(avg)
        if np.sum(v)>=4:
            try:
                tck = interpolate.splrep(x[v],avg[v],s=len(x)*0.1)
                res['spline'] = (interpolate.splev(x,tck),'样条插值')
            except Exception:
                log.debug("样条插值失败", exc_info=True)
    return res


def _draw_curves_core(ax, dates, load_array, time_labels, title, opts=None, bill=None, periods=None):
    """
    在已有的 Axes 上绘制负荷曲线，不创建 Figure。
    支持暗色主题、异常值标记。
    """
    if opts is None:
        opts = {}
    show_daily = opts.get('show_daily', True)
    show_avg = opts.get('show_avg', True)
    show_max_demand = opts.get('show_max_demand', False)
    fit_types = opts.get('fit_types', [])
    highlight_mode = opts.get('highlight_mode', 'none')
    weekday_color = opts.get('weekday_color', DEFAULT_WEEKDAY_COLOR)
    weekend_color = opts.get('weekend_color', DEFAULT_WEEKEND_COLOR)
    holiday_color = opts.get('holiday_color', DEFAULT_HOLIDAY_COLOR)
    weekend_line_width = opts.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH)
    holiday_line_width = opts.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH)
    show_weekend_bg = opts.get('show_weekend_bg', False)
    legend_position = opts.get('legend_position', 'right')
    mark_anomalies = opts.get('mark_anomalies', False)
    dark_theme = opts.get('dark_theme', False)
    max_legend_dates = 5

    n_dates, n_times = load_array.shape
    if n_times < 2:
        raise ValueError("时间点数量小于2，无法绘制有意义曲线")
    x = np.arange(n_times)
    pp = n_times / 24.0
    thresh = 500

    # 暗色主题
    if dark_theme:
        ax.set_facecolor('#2e2e2e')
        ax.tick_params(colors='white')
        ax.xaxis.label.set_color('white')
        ax.yaxis.label.set_color('white')
        ax.title.set_color('white')
        ax.spines['bottom'].set_color('white')
        ax.spines['top'].set_color('white')
        ax.spines['left'].set_color('white')
        ax.spines['right'].set_color('white')
        ax.grid(True, alpha=0.2, color='white')

    # 充放策略背景
    if periods:
        for p in _expand_overnight(periods):
            xs = _hour_x(p["start"], pp)
            xe = min(_hour_x(p["end"], pp), n_times - 1)
            color = ColorConst.CHARGE_BG if p["type"] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL) else ColorConst.DISCHARGE_BG
            ax.axvspan(xs, xe, alpha=0.15, color=color, zorder=0)

    # 周末背景阴影
    if show_weekend_bg and highlight_mode != 'none':
        has_weekend = any(
            _get_day_type(d, enable_holiday=(highlight_mode in ('holiday','grouped'))) in ('weekend', 'holiday')
            for d in dates
        )
        if has_weekend:
            ax.axvspan(0, n_times - 1, alpha=WEEKEND_BG_ALPHA, color='gray', zorder=1)

    # 日负荷曲线
    if show_daily:
        too_many = n_dates > max_legend_dates and highlight_mode not in ('grouped', 'none')
        if highlight_mode == 'grouped':
            for i in range(n_dates):
                day_type = _get_day_type(dates[i], enable_holiday=_is_holiday_available())
                if day_type == 'holiday':
                    color = holiday_color
                elif day_type == 'weekend':
                    color = weekend_color
                else:
                    color = weekday_color
                lw = holiday_line_width if day_type == 'holiday' else (weekend_line_width if day_type == 'weekend' else 1.5)
                xp, yp = (_downsample(x, load_array[i], thresh) if n_times > thresh else (x, load_array[i]))
                ax.plot(xp, yp, color=color, alpha=0.6, lw=lw, zorder=10, label='_nolegend_')
        else:
            for i in range(n_dates):
                day_type = _get_day_type(dates[i], enable_holiday=(highlight_mode == 'holiday'))
                if highlight_mode != 'none':
                    if day_type == 'holiday':
                        color = holiday_color
                        lw = holiday_line_width
                    elif day_type == 'weekend':
                        color = weekend_color
                        lw = weekend_line_width
                    else:
                        color = weekday_color
                        lw = 1.5
                else:
                    color = plt.cm.tab10(i % 10)
                    lw = 1.5
                if too_many and highlight_mode != 'none':
                    label = '_nolegend_'
                else:
                    label = dates[i]
                xp, yp = (_downsample(x, load_array[i], thresh) if n_times > thresh else (x, load_array[i]))
                ax.plot(xp, yp, label=label, color=color, alpha=0.75, lw=lw, zorder=10)

    # 异常数据标记
    anomaly_handle = None
    if mark_anomalies:
        for i in range(n_dates):
            y = load_array[i]
            mask = (y <= 0)
            if np.any(mask):
                ax.scatter(x[mask], y[mask], color='red', marker='x', s=30, zorder=50, label='_nolegend_')
                if anomaly_handle is None:
                    anomaly_handle = True

    # 平均曲线
    avg = np.nanmean(load_array, axis=0)
    if show_avg:
        xp, yp = (_downsample(x, avg, thresh) if n_times > thresh else (x, avg))
        ax.plot(xp, yp, label=CurveLabel.AVERAGE, color='black', ls='--', lw=2.0, zorder=20)

    # 当月最大需量
    if show_max_demand:
        max_val = np.nanmax(load_array) if load_array.size > 0 else 0
        if max_val > 0:
            ax.axhline(y=max_val, color='blue', ls='--', lw=2.0, label=f'{CurveLabel.MAX_DEMAND}({max_val:.2f})', zorder=25)

    # 拟合曲线
    if fit_types:
        for nm, (curve, lab) in _compute_fits(x, load_array, fit_types).items():
            xp, yp = (_downsample(x, curve, thresh) if n_times > thresh else (x, curve))
            ax.plot(xp, yp, label=lab, ls='-.', lw=2.0, zorder=20)

    # 计费容量线
    if bill and bill.get('type') == 'capacity' and bill.get('value'):
        ax.axhline(y=bill['value'], color='red', ls=':', lw=2.0, label=f'{CurveLabel.CAPACITY_LINE}({bill["value"]:.2f})', zorder=30)

    # 坐标轴设置
    ax.set_title(_clean_plot(title), fontsize=16)
    ax.set_xlabel('时间', fontsize=12)
    ax.set_ylabel('负荷值', fontsize=12)

    if n_times >= 25:
        step = max(1, n_times // 24)
        idxs = list(range(0, n_times, step))
        lbls = [_clean_plot(f"{int(i // pp):02d}:00" if pp > 0 else f"{i}") for i in idxs]
        ax.set_xticks(idxs)
        ax.set_xticklabels(lbls, rotation=45, fontsize=9)
    else:
        ax.set_xticks(range(n_times))
        ax.set_xticklabels([_clean_plot(lbl) for lbl in time_labels], rotation=45, fontsize=9)

    ax.grid(True, alpha=0.3)

    # 图例整理
    handles, labels = ax.get_legend_handles_labels()
    filtered_handles, filtered_labels = [], []
    for h, l in zip(handles, labels):
        if l == '_nolegend_':
            continue
        filtered_handles.append(h)
        filtered_labels.append(l)

    if show_daily and too_many and highlight_mode != 'none':
        filtered_handles.append(mpatches.Patch(color=weekday_color, alpha=0.75, label=f'每日曲线({n_dates}条)'))
        filtered_labels.append(f'每日曲线({n_dates}条)')

    if periods:
        filtered_handles.extend([
            mpatches.Patch(color=ColorConst.CHARGE_BG, alpha=0.3, label=PeriodType.CHARGE_LABEL),
            mpatches.Patch(color=ColorConst.DISCHARGE_BG, alpha=0.3, label=PeriodType.DISCHARGE_LABEL)
        ])
        filtered_labels.extend([PeriodType.CHARGE_LABEL, PeriodType.DISCHARGE_LABEL])

    if highlight_mode == 'grouped':
        filtered_handles.extend([
            mpatches.Patch(color=weekday_color, alpha=0.6, label='工作日'),
            mpatches.Patch(color=weekend_color, alpha=0.6, label='周末'),
        ])
        filtered_labels.extend(['工作日', '周末'])
        if _is_holiday_available():
            filtered_handles.append(mpatches.Patch(color=holiday_color, alpha=0.6, label='节假日'))
            filtered_labels.append('节假日')
    elif highlight_mode == 'weekend':
        filtered_handles.extend([
            mpatches.Patch(color=weekday_color, alpha=0.6, label='工作日'),
            mpatches.Patch(color=weekend_color, alpha=0.6, label='周末')
        ])
        filtered_labels.extend(['工作日', '周末'])
    elif highlight_mode == 'holiday':
        filtered_handles.extend([
            mpatches.Patch(color=weekday_color, alpha=0.6, label='工作日'),
            mpatches.Patch(color=weekend_color, alpha=0.6, label='周末'),
            mpatches.Patch(color=holiday_color, alpha=0.6, label='节假日')
        ])
        filtered_labels.extend(['工作日', '周末', '节假日'])

    if show_weekend_bg and highlight_mode != 'none':
        filtered_handles.append(mpatches.Patch(color='gray', alpha=WEEKEND_BG_ALPHA, label='周末背景'))
        filtered_labels.append('周末背景')

    if mark_anomalies and anomaly_handle is not None:
        filtered_handles.append(
            mpatches.Patch(color='red', alpha=0.6, label='异常值(零/负)')
        )
        filtered_labels.append('异常值(零/负)')

    if filtered_handles or mark_anomalies:
        if legend_position == 'bottom':
            ax.legend(handles=filtered_handles, labels=filtered_labels, loc='upper center',
                      bbox_to_anchor=(0.5, -0.15), ncol=4, fontsize=9)
        else:
            ax.legend(handles=filtered_handles, labels=filtered_labels, bbox_to_anchor=(1.02, 1),
                      loc='upper left', fontsize=9)


def _plot_curves(dates, load, time_labels, title, outpath, opts=None, bill=None, periods=None, ctitle=None, cancel_event=None):
    """
    绘制负荷曲线图并保存到文件。返回 True 表示成功，False 表示取消，异常则抛出。
    支持 cancel_event 在耗时操作前检查。
    """
    with plt.rc_context({'font.sans-serif': [_get_cached_font()], 'axes.unicode_minus': False}):
        if opts is None:
            opts = {'show_daily': True, 'show_avg': True, 'fit_types': [], 'show_max_demand': False,
                    'highlight_mode': 'none', 'mark_anomalies': False, 'dark_theme': False}
        dark = opts.get('dark_theme', False)
        fig, ax = plt.subplots(figsize=(16, 8))
        if dark:
            fig.patch.set_facecolor('#1e1e1e')
            ax.set_facecolor('#2e2e2e')
        try:
            if cancel_event and cancel_event.is_set():
                plt.close(fig)
                return False
            _draw_curves_core(ax, dates, load, time_labels, ctitle or title, opts, bill, periods)
            fig.tight_layout()
            if cancel_event and cancel_event.is_set():
                plt.close(fig)
                return False
            os.makedirs(os.path.dirname(outpath), exist_ok=True)
            if cancel_event and cancel_event.is_set():
                plt.close(fig)
                return False
            fig.savefig(outpath, dpi=200, bbox_inches='tight', format='png')
            return True
        except np.linalg.LinAlgError:
            raise RuntimeError("数据矩阵运算异常，可能包含无效值")
        except ValueError as e:
            raise RuntimeError(f"数据形状错误: {e}")
        except MemoryError:
            raise RuntimeError("绘图时内存不足，请减少数据量或关闭其他程序")
        except Exception as e:
            raise RuntimeError(f"绘图失败: {e}")
        finally:
            plt.close(fig)
            gc.collect()


def _plot_single_worker(data, title_template, filename_template,
                        output_dir, classify_by_asset, plot_options,
                        actual_billing, price_periods, actual_multiplier, progress_queue, cancel_event):
    _setup_font()
    ctx = {
        'year_month': data.get('month_str', ''),
        'table_no': data.get('table_no', ''),
        'asset': data.get('asset', ''),
        'sheet': data.get('sheet', '')
    }
    title = parse_title_template(title_template, ctx) if title_template else generate_default_title(ctx)
    filename = generate_filename(filename_template, ctx)

    out_dir = output_dir
    if classify_by_asset:
        asset_name = _sanitize_name(data.get('asset', 'Other')) or 'Other'
        out_dir = os.path.join(out_dir, asset_name)
    os.makedirs(out_dir, exist_ok=True)
    outpath = os.path.join(out_dir, filename)

    base, ext = os.path.splitext(outpath)
    for counter in range(1, MAX_FILENAME_ATTEMPTS + 1):
        if not os.path.exists(outpath):
            break
        outpath = f"{base}_{counter}{ext}"
    else:
        return False, f"文件名冲突已达上限（{MAX_FILENAME_ATTEMPTS}次），无法生成: {outpath}"

    try:
        load_array = np.array(data['load_array']) * actual_multiplier
        if load_array.ndim != 2:
            return False, "负荷数据不是二维数组，可能日期与时间点结构异常"
        if load_array.size == 0:
            return False, "负荷数据为空，无有效记录"

        month_str = data.get('month_str', '1')
        periods = []
        if isinstance(price_periods, dict):
            m = month_str.split('-')[1] if '-' in month_str else month_str
            try:
                month_int = int(m)
            except Exception:
                month_int = 1
            periods = price_periods.get(str(month_int), [])

        # 标准化 plot_options 为 PlotOptions 对象，提取字典
        if isinstance(plot_options, PlotOptions):
            opts_dict = plot_options.to_dict()
        elif isinstance(plot_options, dict):
            opts_dict = PlotOptions.from_dict(plot_options).to_dict()
        else:
            opts_dict = PlotOptions().to_dict()

        tmp_path = outpath + '.tmp'
        try:
            # 传入取消事件，绘图函数内部会检查并返回 False
            success = _plot_curves(
                data.get('date_labels', []), load_array,
                data.get('time_labels', []), title, tmp_path,
                opts_dict, actual_billing, periods, cancel_event=cancel_event
            )
            if not success:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                return False, "已取消"
            os.replace(tmp_path, outpath)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise

        if progress_queue is not None:
            try:
                progress_queue.put(1)
            except (BrokenPipeError, EOFError, OSError):
                pass
        return True, outpath
    except RuntimeError as e:
        return False, str(e)
    except PermissionError:
        return False, f"写入权限被拒绝: {outpath}"
    except OSError as e:
        return False, f"磁盘写入错误: {str(e)}"
    except MemoryError:
        return False, "内存不足，请减少数据量或关闭其他程序"
    except Exception as e:
        log.error(f"绘图异常: {e}", exc_info=True)
        return False, f"绘图错误: {str(e)}"

