# -*- coding: utf-8 -*-
"""配置服务：读写、迁移、校验、模板管理。

本模块从 Fusion v2.3.9 单文件原样搬出（Config 类 45 个业务方法），逻辑一字未改。
搬移依据：`tools/analyze_class.py Config` 显示基类为空，唯一 Qt 依赖是 4 个窗口几何
方法用到的 QByteArray；那 4 个方法已拆到 ui/window_state.py，本模块因此零 Qt。

配置里有 14 个版本的迁移链（_migrate 43 行），顺序不可调整——每一段都对应
一次历史数据结构变更，跳过或重排会让老配置文件读不出来。

约定：`Config.cfg` 是配置字典本体，window_state 模块直接读写它（见该模块说明）。
"""
from __future__ import annotations

import copy
import json
import logging
import multiprocessing as mp
import os
import shutil
import threading
from datetime import datetime
from difflib import SequenceMatcher

from core.models import (
    DEFAULT_WEEKDAY_COLOR, DEFAULT_WEEKEND_COLOR, DEFAULT_HOLIDAY_COLOR,
    DEFAULT_WEEKEND_LINE_WIDTH, DEFAULT_HOLIDAY_LINE_WIDTH,
)
from core.timegrid import (
    _is_holiday_available, _normalize_fullwidth_chars, _set_24h_belong,
)

log = logging.getLogger(__name__)

__all__ = ["Config"]


def _default_scan_workers():
    return min(4, max(1, mp.cpu_count() - 1))


def _default_plot_processes():
    return min(4, max(1, mp.cpu_count() - 1))


def _validate_config_schema(cfg):
    if not isinstance(cfg, dict):
        return False
    price_strategies = cfg.get('price_strategies', {})
    if not isinstance(price_strategies, dict):
        return False
    for prov, data in price_strategies.items():
        if not isinstance(data, dict):
            return False
        groups = data.get('groups', [])
        if not isinstance(groups, list):
            return False
        for g in groups:
            if not isinstance(g, dict):
                return False
            if 'name' not in g or 'months' not in g or 'periods' not in g:
                return False
    app_settings = cfg.get('app_settings', {})
    if not isinstance(app_settings, dict):
        return False
    return True


class Config:
    FILE = "column_mapping_config.json"
    BACKUP_DIR = "config_backups"
    MAX_BACKUPS = 5
    MAX_WEEKLY_BACKUPS = 3
    CONFIG_VERSION = 14

    _DEFAULT_SEASON_MAP = {
        "冬季": [12, 1, 2],
        "春季": [3, 4, 5],
        "夏季": [6, 7, 8],
        "秋季": [9, 10, 11]
    }

    DEFAULT_CONFIG = {
        'config_version': CONFIG_VERSION,
        'price_strategies': {},
        'active_strategy': None,
        'active_province': None,
        'preview_templates': {},
        'highlight_templates': {},
        'window_geometry': {},
        'app_settings': {
            'auto_open_dir': True,
            'last_output_dir': '',
            'last_multiplier': 1.0,
            'last_csv_encoding': 'auto',
            'last_title_template': '{table_no}_{year_month}_负荷曲线',
            'last_filename_template': '{table_no}_{year_month}_负荷曲线',
            'show_daily': True,
            'show_avg': True,
            'show_max_demand': False,
            'last_billing_enabled': False,
            'last_billing_value': '',
            'last_strategy_name': None,
            'classify_by_asset': False,
            'quick_mode': False,
            'scan_workers': None,
            'plot_processes': None,
            'highlight_mode': 'none',
            'weekday_color': DEFAULT_WEEKDAY_COLOR,
            'weekend_color': DEFAULT_WEEKEND_COLOR,
            'holiday_color': DEFAULT_HOLIDAY_COLOR,
            'weekend_line_width': DEFAULT_WEEKEND_LINE_WIDTH,
            'holiday_line_width': DEFAULT_HOLIDAY_LINE_WIDTH,
            'show_weekend_bg': False,
            'legend_position': 'right',
            'mark_anomalies': False,
            'highlight_enabled_default': True,
            'dark_theme': False,
            'auto_convert_accumulated': False,
            '24h_belongs_current_month': False,
        },
        'last_mapping': None,
        'column_fingerprints': {},
        'feedback_data': {}
    }

    def __init__(self):
        self._lock = threading.RLock()
        self.restored_from_backup = False
        # v2.3.9fix(P2-3): 备份目录必须先建。原顺序是先 _load() 再 makedirs，
        # 配置损坏且目录不存在时 _load → _restore_backup → os.listdir 直接
        # 抛 FileNotFoundError，程序启动即崩，错误信息还不指向配置文件。
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        self.cfg = self._load()
        with self._lock:
            for key, default_val in self.DEFAULT_CONFIG.items():
                # v2.3.9fix(P2-9): 深拷贝，避免实例与类属性共享同一个嵌套 dict
                self.cfg.setdefault(key, copy.deepcopy(default_val))
            self._migrate()
            self._validate_highlight_dependency()
            _set_24h_belong(self.cfg['app_settings'].get('24h_belongs_current_month', False))

    def get_season_map(self, province_name: str) -> dict:
        with self._lock:
            province = self.cfg.get('price_strategies', {}).get(province_name, {})
            seasons = province.get('seasons', None)
            if seasons and isinstance(seasons, dict) and len(seasons) > 0:
                return seasons
            return self._DEFAULT_SEASON_MAP.copy()

    def set_season_map(self, province_name: str, season_map: dict):
        with self._lock:
            if province_name not in self.cfg.get('price_strategies', {}):
                return False
            province = self.cfg['price_strategies'][province_name]
            province['seasons'] = season_map
            return self.save()
        
    def _migrate(self):
        with self._lock:
            old_version = self.cfg.get('config_version', 0)
            if old_version < 14:
                old_strategies = self.cfg.get('price_strategies', {})
                if 'charge_discharge_strategies' in self.cfg:
                    old_strategies.update(self.cfg.pop('charge_discharge_strategies'))
                new_strategies = {}
                for name, strat_data in old_strategies.items():
                    if isinstance(strat_data, dict) and 'type' not in strat_data:
                        groups = []
                        for month_key, periods in strat_data.items():
                            try:
                                m_int = int(month_key)
                            except Exception:
                                continue
                            groups.append({
                                "name": f"月份{m_int}",
                                "months": [m_int],
                                "periods": periods
                            })
                        if groups:
                            new_strategies[name] = {
                                "type": "province",
                                "groups": groups
                            }
                    else:
                        new_strategies[name] = strat_data
                self.cfg['price_strategies'] = new_strategies
                app = self.cfg.setdefault('app_settings', {})
                for key, val in self.DEFAULT_CONFIG['app_settings'].items():
                    app.setdefault(key, val)
                if not self.cfg.get('active_province') and self.cfg.get('active_strategy'):
                    if self.cfg['active_strategy'] in new_strategies:
                        self.cfg['active_province'] = self.cfg['active_strategy']
                self.cfg['config_version'] = self.CONFIG_VERSION
                log.info("配置已从旧版升级到v14（策略架构重构）")
            self.cfg['config_version'] = self.CONFIG_VERSION
            self.cfg.setdefault('app_settings', {})
            app = self.cfg['app_settings']
            for key, val in self.DEFAULT_CONFIG['app_settings'].items():
                app.setdefault(key, val)
            self.save()

    def _validate_highlight_dependency(self):
        with self._lock:
            mode = self.cfg.get('app_settings', {}).get('highlight_mode', 'none')
            if mode in ('holiday', 'grouped') and not _is_holiday_available():
                new_mode = 'weekend' if mode == 'holiday' else 'weekend'
                log.warning(f"高亮模式 '{mode}' 需要 chinesecalendar 库但未安装，自动降级为 '{new_mode}'")
                self.cfg['app_settings']['highlight_mode'] = new_mode

    def _load(self):
        if os.path.exists(self.FILE):
            try:
                with open(self.FILE,'r',encoding='utf-8') as f:
                    cfg = json.load(f)
                if not isinstance(cfg,dict):
                    raise ValueError("格式错误")
                if not _validate_config_schema(cfg):
                    log.warning("配置文件结构异常，将使用备份或默认配置。")
                    raise ValueError("Schema校验失败")
                cleaned = {}
                for k,v in cfg.items():
                    if k in ('price_strategies','active_strategy','preview_templates','highlight_templates',
                             'app_settings','last_mapping','window_geometry','config_version',
                             'charge_discharge_strategies','column_fingerprints','feedback_data','active_province'):
                        cleaned[k] = v
                        continue
                    if '|' not in k:
                        continue
                    if isinstance(v,dict) and 'date_col' in v and 'load_col' in v:
                        cleaned[k] = v
                if 'config_version' not in cleaned:
                    cleaned['config_version'] = self.CONFIG_VERSION
                cleaned.setdefault('app_settings', {})
                app = cleaned['app_settings']
                for key, val in self.DEFAULT_CONFIG['app_settings'].items():
                    app.setdefault(key, val)
                return cleaned
            except Exception as e:
                log.error(f"配置加载失败: {e}", exc_info=True)
                self.restored_from_backup = True
                return self._restore_backup()
        return {}

    def _restore_backup(self):
        # v2.3.9fix(P2-3): 目录不存在时 os.listdir 会抛 FileNotFoundError
        try:
            names = os.listdir(self.BACKUP_DIR)
        except OSError as e:
            log.warning(f"备份目录不可读，跳过备份恢复: {e}")
            names = []
        backups = sorted([f for f in names if f.endswith('.json')], reverse=True)
        for bk in backups:
            try:
                with open(os.path.join(self.BACKUP_DIR,bk),'r',encoding='utf-8') as f:
                    cfg = json.load(f)
                log.info(f"从备份 {bk} 恢复配置")
                self.restored_from_backup = True
                return cfg
            except Exception:
                log.debug(f"备份 {bk} 恢复失败", exc_info=True)
        log.critical("无法恢复任何配置，使用硬编码默认配置")
        self.restored_from_backup = True
        return copy.deepcopy(self.DEFAULT_CONFIG)

    def save(self):
        with self._lock:
            try:
                tmp = self.FILE+'.tmp'
                with open(tmp,'w',encoding='utf-8') as f:
                    json.dump(self.cfg,f,ensure_ascii=False,indent=2)
                if os.path.exists(self.FILE):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    bk = os.path.join(self.BACKUP_DIR,f"config_{timestamp}.json")
                    try:
                        shutil.copy2(self.FILE, bk)
                        self._maintain_backups()
                    except Exception as e:
                        log.error(f"备份配置文件失败: {e}")
                os.replace(tmp, self.FILE)
                return True
            except Exception as e:
                log.error(f"配置保存失败: {e}", exc_info=True)
                return False

    def _maintain_backups(self):
        backups = sorted([f for f in os.listdir(self.BACKUP_DIR) if f.startswith('config_')])
        daily = [f for f in backups if not f.startswith('config_weekly_')]
        weekly = [f for f in backups if f.startswith('config_weekly_')]
        while len(daily) > self.MAX_BACKUPS:
            os.remove(os.path.join(self.BACKUP_DIR,daily.pop(0)))
        while len(weekly) > self.MAX_WEEKLY_BACKUPS:
            os.remove(os.path.join(self.BACKUP_DIR,weekly.pop(0)))

    def add_map(self, pattern, sheet, mapping):
        if not pattern or not sheet or not mapping:
            return False
        fingerprint = self._generate_fingerprint(mapping)
        with self._lock:
            self.cfg[f"{pattern}|{sheet}"] = mapping.copy()
            if fingerprint:
                self.cfg.setdefault('column_fingerprints',{})[fingerprint] = mapping.copy()
            return self.save()

    def _generate_fingerprint(self, mapping):
        if not mapping or 'date_col' not in mapping:
            return None
        load_col = mapping.get('load_col','')
        if isinstance(load_col,list):
            load_col = '|'.join(sorted(load_col))
        return f"{mapping.get('date_col','')}|{mapping.get('time_col','')}|{mapping.get('asset_col','')}|{load_col}|{mapping.get('format_type','')}"

    def recommend(self, fpath, sheet, cols):
        fname = os.path.basename(fpath)
        with self._lock:
            for k,m in self.cfg.items():
                if k in ('price_strategies','active_strategy','app_settings','last_mapping','preview_templates',
                         'highlight_templates','window_geometry','config_version','charge_discharge_strategies',
                         'column_fingerprints','feedback_data','active_province'):
                    continue
                if '|' not in k:
                    continue
                fp,sp = k.split('|',1)
                if self._match(fname,fp) and self._match(sheet,sp) and self._valid_map(m,cols):
                    return m.copy()
            for fingerprint,m in self.cfg.get('column_fingerprints',{}).items():
                if self._valid_map(m,cols) and self._generate_fingerprint(m)==fingerprint:
                    return m.copy()
            best,best_m = 0,None
            for k,m in self.cfg.items():
                if k in ('price_strategies','active_strategy','app_settings','last_mapping','preview_templates',
                         'highlight_templates','window_geometry','config_version','charge_discharge_strategies',
                         'column_fingerprints','feedback_data','active_province'):
                    continue
                if '|' not in k:
                    continue
                # v2.3.15fix: 相似度兜底也必须校验列存在性。
                # 原实现只算 _sim，于是「采集时间」相同、负荷列名略异（A线/B线）时
                # 会返回一个 load_col 根本不在本文件里的映射——用户看到的是
                # 「映射套上了」，实际取数会报缺列或取错列。宁可让他重选。
                if not self._valid_map(m, cols):
                    continue
                try:
                    sc = self._sim(m,cols)
                    if sc>best:
                        best=sc
                        best_m=m
                except Exception:
                    continue
            if best>=0.7 and best_m:
                return best_m.copy()
        return None

    def _match(self,t,p):
        import fnmatch
        return fnmatch.fnmatch(t.lower(),p.lower())

    def _valid_map(self,m,col):
        # v2.3.9fix(P2-2): 旧版（v13 及更早）写下的 column_fingerprints 条目
        # 没有 format_type 键，硬取会 KeyError；recommend 的指纹循环没有 try。
        if m.get('date_col') not in col:
            return False
        if m.get('time_col') and m['time_col'] not in col:
            return False
        if m.get('asset_col') and m['asset_col'] not in col:
            return False
        if m.get('format_type', 'long') in ('wide', 'wide_transposed', 'accumulated'):
            load_cols = m.get('load_col', [])
            if isinstance(load_cols, str):
                load_cols = [load_cols]
            for c in load_cols:
                if c not in col:
                    return False
        else:
            if m.get('load_col') not in col:
                return False
        return True

    def _sim(self,m,col):
        from difflib import SequenceMatcher
        ds = self._bsim(m['date_col'],col)
        lc = m['load_col']
        if isinstance(lc, str):
            ls = self._bsim(lc, col)
        else:
            ls = max(self._bsim(c, col) for c in lc) if lc else 0.0
        ts = self._bsim(m.get('time_col',''),col) if m.get('time_col') else 1.0
        ass = self._bsim(m.get('asset_col',''),col) if m.get('asset_col') else 1.0
        return (ds + ls + ts + ass) / 4

    def _bsim(self,tar,cand):
        from difflib import SequenceMatcher
        norm_tar = _normalize_fullwidth_chars(tar)
        if norm_tar in cand:
            return 1.0
        return max(SequenceMatcher(None, norm_tar.lower(), _normalize_fullwidth_chars(str(c)).lower()).ratio() for c in cand)

    def provinces(self):
        with self._lock:
            return list(self.cfg.get('price_strategies', {}).keys())

    def get_province_data(self, name):
        with self._lock:
            return self.cfg.get('price_strategies', {}).get(name, {}).copy()

    def get_province_groups(self, name):
        province = self.get_province_data(name)
        return province.get('groups', [])

    def add_province(self, name):
        with self._lock:
            if name in self.cfg['price_strategies']:
                return False
            self.cfg['price_strategies'][name] = {"type": "province", "groups": []}
            return self.save()

    def rename_province(self, old_name, new_name):
        """v2.3fix: 重命名省份，同步更新active_province引用"""
        with self._lock:
            if old_name not in self.cfg['price_strategies']:
                return False
            if new_name in self.cfg['price_strategies']:
                return False
            if not new_name.strip():
                return False
            # 复制策略数据到新key
            self.cfg['price_strategies'][new_name] = self.cfg['price_strategies'][old_name]
            del self.cfg['price_strategies'][old_name]
            # 同步active_province引用
            if self.cfg.get('active_province') == old_name:
                self.cfg['active_province'] = new_name
            return self.save()

    def delete_province(self, name):
        with self._lock:
            if name not in self.cfg['price_strategies']:
                return False
            del self.cfg['price_strategies'][name]
            if self.cfg.get('active_province') == name:
                self.cfg['active_province'] = None
            return self.save()

    def add_group(self, province_name, group_name, months, periods):
        with self._lock:
            if province_name not in self.cfg['price_strategies']:
                return False
            province = self.cfg['price_strategies'][province_name]
            used_months = set()
            for g in province['groups']:
                used_months.update(g['months'])
            if set(months) & used_months:
                log.warning(f"策略组 {group_name} 的月份与已有策略组重叠，拒绝添加")
                return False
            province['groups'].append({
                "name": group_name,
                "months": months,
                "periods": periods
            })
            return self.save()

    def update_group(self, province_name, old_group_name, new_group_name, months, periods):
        with self._lock:
            if province_name not in self.cfg['price_strategies']:
                return False
            province = self.cfg['price_strategies'][province_name]
            target = None
            for g in province['groups']:
                if g['name'] == old_group_name:
                    target = g
                    break
            if not target:
                return False
            used = set()
            for g in province['groups']:
                if g is not target:
                    used.update(g['months'])
            if set(months) & used:
                return False
            target['name'] = new_group_name
            target['months'] = months
            target['periods'] = periods
            return self.save()

    def delete_group(self, province_name, group_name):
        with self._lock:
            if province_name not in self.cfg['price_strategies']:
                return False
            province = self.cfg['price_strategies'][province_name]
            province['groups'] = [g for g in province['groups'] if g['name'] != group_name]
            return self.save()

    def get_periods_for_month(self, province_name, month_int):
        if not province_name:
            return []
        groups = self.get_province_groups(province_name)
        for g in groups:
            if month_int in g['months']:
                return g['periods']
        return []

    def get_periods_for_all_months(self, province_name):
        result = {}
        groups = self.get_province_groups(province_name)
        for g in groups:
            for m in g['months']:
                result[str(m)] = g['periods']
        return result

    def active_province(self):
        with self._lock:
            return self.cfg.get('active_province')
    def set_active_province(self, name):
        with self._lock:
            self.cfg['active_province'] = name
            self.save()

    def strat_names(self):
        return self.provinces()

    def get_strat(self, name):
        with self._lock:
            if name not in self.cfg.get('price_strategies', {}):
                return None
            province = self.cfg['price_strategies'][name]
            if province.get('type') == 'province':
                result = {}
                for g in province['groups']:
                    for m in g['months']:
                        result[str(m)] = g['periods']
                return result
            else:
                return province

    def save_strat(self, name, periods_dict):
        with self._lock:
            groups = []
            for month_str, periods in periods_dict.items():
                try:
                    m_int = int(month_str)
                except Exception:
                    continue
                groups.append({
                    "name": f"月份{m_int}",
                    "months": [m_int],
                    "periods": periods
                })
            self.cfg['price_strategies'][name] = {"type": "province", "groups": groups}
            return self.save()

    def del_strat(self, name):
        with self._lock:
            if name in self.cfg.get('price_strategies', {}):
                del self.cfg['price_strategies'][name]
                if self.cfg.get('active_strategy') == name:
                    self.cfg['active_strategy'] = None
                if self.cfg.get('active_province') == name:
                    self.cfg['active_province'] = None
            return self.save()

    def active_strat(self):
        return self.cfg.get('active_strategy')
    def set_active(self, name):
        self.cfg['active_strategy'] = name
        self.save()

    def app_set(self,k,d=None):
        with self._lock:
            val = self.cfg.get('app_settings',{}).get(k,d)
            if isinstance(val, dict):
                return val.copy()
            if isinstance(val, list):
                return val.copy()
            return val
    def set_app(self,k,v):
        with self._lock:
            self.cfg.setdefault('app_settings',{})[k]=v
            self.save()
    def last_map(self):
        with self._lock:
            return self.cfg.get('last_mapping')
    def set_last_map(self,m):
        with self._lock:
            self.cfg['last_mapping']=m
            self.save()
    def get_template_names(self):
        with self._lock:
            return list(self.cfg.get('preview_templates',{}).keys())
    def get_template(self,name):
        with self._lock:
            return self.cfg.get('preview_templates',{}).get(name)
    def save_template(self,name,settings):
        with self._lock:
            self.cfg.setdefault('preview_templates',{})[name]=settings
            self.save()
    def delete_template(self,name):
        with self._lock:
            if name in self.cfg.get('preview_templates',{}):
                del self.cfg['preview_templates'][name]
            self.save()

    def get_scan_workers(self):
        user_val = self.app_set('scan_workers')
        if user_val is not None:
            return max(1, min(user_val, 8))
        return _default_scan_workers()

    def get_plot_processes(self):
        user_val = self.app_set('plot_processes')
        if user_val is not None:
            return max(1, min(user_val, 8))
        return _default_plot_processes()
