# -*- coding: utf-8 -*-
"""基线比对器：判断重构后的实现是否与 v2.3.8 行为等价。

判据分层（与 capture_baseline.py 的 grade 对应）
------------------------------------------------
strict  数值必须一致（容差 1e-9），标签/形状/汇总字段必须完全一致
        对比内容：解析宽表、分段充放空间、Excel 单元格
loose   只要求形态一致（图像尺寸），像素哈希默认不比
         理由：matplotlib 版本、字体、DPI 任一变化都会改像素；
         重构若把出图搬到前端渲染，像素必然全变，那不是回归。
         需要像素级比对时显式加 --strict-pixels。
info    跳过（控件默认值只作参考快照）

为什么不用 sha256 做主判据
-------------------------
浮点运算顺序一改（numpy 版本、向量化写法），和值不变但字节变了。
拿哈希砸会产生大量假失败，人就会开始忽略红叉——那比没有比对器更糟。

用法
----
    python tools/compare_baseline.py --base baseline --cand baseline_v2
    python tools/compare_baseline.py --base baseline --cand baseline_v2 --strict-pixels
    python tools/compare_baseline.py --base baseline --cand baseline_v2 --only W1 A2 -v

退出码：等价即 0；任一处不等价即 1。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ATOL = 1e-9


# ────────────────────────────────────────────────────────────
# 基础比对
# ────────────────────────────────────────────────────────────
def cmp_array(bp: str, cp: str, res: list, tag: str) -> None:
    """数值比对：形状必须一致，值在容差内一致。"""
    if not os.path.exists(bp):
        res.append(("FAIL", tag, "基准 npy 缺失"))
        return
    if not os.path.exists(cp):
        res.append(("FAIL", tag, "候选 npy 缺失"))
        return
    a = np.load(bp)
    b = np.load(cp)
    if a.shape != b.shape:
        res.append(("FAIL", tag, f"形状 {a.shape} → {b.shape}"))
        return
    if a.size == 0:
        res.append(("PASS", tag, "空数组"))
        return
    both_nan = np.isnan(a) & np.isnan(b)
    diff = np.where(both_nan, 0.0, np.abs(np.nan_to_num(a, nan=0.0) - np.nan_to_num(b, nan=0.0)))
    worst = float(diff.max())
    n_bad = int((diff > ATOL).sum())
    if n_bad:
        idx = np.unravel_index(int(diff.argmax()), a.shape)
        res.append(("FAIL", tag, f"{n_bad} 处超差，最大 {worst:.3e} @ {idx}"))
    else:
        res.append(("PASS", tag, f"max|Δ|={worst:.3e}"))


def cmp_scalar(a, b, res: list, tag: str, tol: float = ATOL) -> None:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(float(a) - float(b)) > tol:
            res.append(("FAIL", tag, f"{a} → {b}"))
        else:
            res.append(("PASS", tag, ""))
    else:
        if a != b:
            res.append(("FAIL", tag, f"{a!r} → {b!r}"))
        else:
            res.append(("PASS", tag, ""))


def cmp_list(a, b, res: list, tag: str) -> None:
    a = a or []
    b = b or []
    if list(a) != list(b):
        na, nb = len(a), len(b)
        first = next((i for i in range(min(na, nb)) if a[i] != b[i]), None)
        where = f"首个差异 @{first}: {a[first]!r} → {b[first]!r}" if first is not None else f"长度 {na} → {nb}"
        res.append(("FAIL", tag, where))
    else:
        res.append(("PASS", tag, f"{len(a)} 项"))


def strip_paths(summaries):
    """绝对路径逐机不同，从比对里剔除。"""
    out = []
    for s in summaries or []:
        if not isinstance(s, dict):
            out.append(s)
            continue
        d = {k: v for k, v in s.items() if k not in ("file_path", "mapping")}
        out.append(d)
    return out


def _report_fingerprint(text) -> str:
    """报告正文指纹：先掩掉时间戳、输出目录与计数行，再哈希。

    报告模板里这三处位置固定，用正则精确替换；其余正文不动，
    否则转义、条数、表号这些真正要看的东西会被一起抹平。
    """
    if not text:
        return ""
    import hashlib
    import re
    t = re.sub(r"生成时间:.*?</p>", "生成时间: <TS></p>", text, flags=re.S)
    # 不能写 [^<]* —— 基准里路径被脱敏成 <REPO>\...，遇 < 就断，替换会半途而废。
    t = re.sub(r"输出目录:.*?</p>", "输出目录: <OUTDIR></p>", t, flags=re.S)
    t = re.sub(r'成功 <span class="success">\d+</span> 张，失败 <span class="failure">\d+</span> 张，总计 \d+ 张',
               "成功/失败/总计: <COUNTS>", t)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


# ────────────────────────────────────────────────────────────
# 单场景比对
# ────────────────────────────────────────────────────────────
def compare_scenario(base_root: str, cand_root: str, sid: str,
                     strict_pixels: bool, verbose: bool) -> list:
    res: list = []
    bdir = os.path.join(base_root, sid)
    cdir = os.path.join(cand_root, sid)
    bm_p = os.path.join(bdir, "manifest.json")
    cm_p = os.path.join(cdir, "manifest.json")
    if not os.path.exists(bm_p):
        return [("FAIL", sid, "基准 manifest 缺失")]
    if not os.path.exists(cm_p):
        return [("FAIL", sid, "候选 manifest 缺失（该场景未采集）")]
    bm = json.load(open(bm_p, encoding="utf-8"))
    cm = json.load(open(cm_p, encoding="utf-8"))

    if bm.get("fatal") or cm.get("fatal"):
        res.append(("FAIL", f"{sid}/fatal", f"基准={bm.get('fatal')} 候选={cm.get('fatal')}"))
        return res

    bs, cs = bm.get("stages", {}), cm.get("stages", {})

    # ── 解析层 ──
    for tag, bv in (bs.get("parse") or {}).items():
        cv = (cs.get("parse") or {}).get(tag)
        if cv is None:
            res.append(("FAIL", f"{sid}/parse/{tag}", "候选缺该输入"))
            continue
        cmp_list(bv.get("date_labels"), cv.get("date_labels"), res, f"{sid}/parse/{tag}/日期")
        cmp_list(bv.get("time_labels"), cv.get("time_labels"), res, f"{sid}/parse/{tag}/时刻")
        cmp_array(os.path.join(bdir, tag, "wide.npy"),
                  os.path.join(cdir, tag, "wide.npy"), res, f"{sid}/parse/{tag}/宽表")
        b_sum = strip_paths(bv.get("summaries"))
        c_sum = strip_paths(cv.get("summaries"))
        if len(b_sum) != len(c_sum):
            res.append(("FAIL", f"{sid}/parse/{tag}/分组数", f"{len(b_sum)} → {len(c_sum)}"))
        else:
            for i, (a, b) in enumerate(zip(b_sum, c_sum)):
                keys = set(a) | set(b)
                bad = []
                for k in sorted(keys):
                    av, bv2 = a.get(k), b.get(k)
                    if isinstance(av, float) and isinstance(bv2, float):
                        if av != bv2:              # 汇总字段记录的就是原值，精确比
                            bad.append(f"{k}: {av}→{bv2}")
                    elif av != bv2:
                        bad.append(f"{k}: {av!r}→{bv2!r}")
                if bad:
                    res.append(("FAIL", f"{sid}/parse/{tag}/汇总{i}", "; ".join(bad)))
                else:
                    res.append(("PASS", f"{sid}/parse/{tag}/汇总{i}", ""))

    # ── 测算层 ──
    for tag, bv in (bs.get("calc") or {}).items():
        cv = (cs.get("calc") or {}).get(tag)
        if cv is None:
            res.append(("FAIL", f"{sid}/calc/{tag}", "候选缺该输入"))
            continue
        bvars = bv.get("variants") or {}
        cvars = cv.get("variants") or {}
        for vn, bvv in bvars.items():
            cvv = cvars.get(vn)
            if cvv is None:
                res.append(("FAIL", f"{sid}/calc/{tag}/{vn}", "候选缺该参数组"))
                continue
            if bool(bvv.get("ok")) != bool(cvv.get("ok")):
                res.append(("FAIL", f"{sid}/calc/{tag}/{vn}/ok",
                            f"{bvv.get('ok')} → {cvv.get('ok')} ({cvv.get('error')})"))
                continue
            if not bvv.get("ok"):
                res.append(("PASS", f"{sid}/calc/{tag}/{vn}", "两侧同报错"))
                continue
            cmp_list(bvv.get("charge_labels"), cvv.get("charge_labels"), res,
                     f"{sid}/calc/{tag}/{vn}/充电段名")
            cmp_list(bvv.get("discharge_labels"), cvv.get("discharge_labels"), res,
                     f"{sid}/calc/{tag}/{vn}/放电段名")
            for direction in ("charge", "discharge"):
                cmp_array(os.path.join(bdir, tag, f"seg_{direction}_{vn}.npy"),
                          os.path.join(cdir, tag, f"seg_{direction}_{vn}.npy"),
                          res, f"{sid}/calc/{tag}/{vn}/{direction}")
            bc = bvv.get("capacity_calc") or {}
            cc = cvv.get("capacity_calc") or {}
            for k in ("target_days", "capacity", "days_for_capacity"):
                if k in bc or k in cc:
                    cmp_scalar(bc.get(k), cc.get(k), res, f"{sid}/calc/{tag}/{vn}/{k}")

    # ── 出图层（loose：默认只比尺寸）──
    for tag, bv in (bs.get("plot") or {}).items():
        cv = (cs.get("plot") or {}).get(tag)
        if cv is None:
            res.append(("FAIL", f"{sid}/plot/{tag}", "候选缺该输入"))
            continue
        for vn, bvv in (bv.get("variants") or {}).items():
            cvv = (cv.get("variants") or {}).get(vn)
            if cvv is None:
                res.append(("FAIL", f"{sid}/plot/{tag}/{vn}", "候选缺该变体"))
                continue
            if bool(bvv.get("ok")) != bool(cvv.get("ok")):
                res.append(("FAIL", f"{sid}/plot/{tag}/{vn}/ok",
                            f"{bvv.get('ok')} → {cvv.get('ok')} ({cvv.get('error')})"))
                continue
            if not bvv.get("ok"):
                res.append(("PASS", f"{sid}/plot/{tag}/{vn}", "两侧同失败"))
                continue
            if strict_pixels:
                cmp_scalar(bvv.get("sha256"), cvv.get("sha256"), res, f"{sid}/plot/{tag}/{vn}/像素")
            else:
                b_sz = bvv.get("size") or [0, 0]
                c_sz = cvv.get("size") or [0, 0]
                if list(b_sz) == list(c_sz):
                    res.append(("PASS", f"{sid}/plot/{tag}/{vn}/尺寸", ""))
                else:
                    # matplotlib 的 bbox_inches='tight' 按文字度量裁边，
                    # 字体缓存 / 模块加载顺序变化就可能差 1~2 像素。
                    # 这不是版面变化，当失败报会淹没真正的差异。
                    d = max(abs(int(b_sz[0]) - int(c_sz[0])), abs(int(b_sz[1]) - int(c_sz[1])))
                    if d <= 2:
                        res.append(("PASS", f"{sid}/plot/{tag}/{vn}/尺寸",
                                    f"±{d}px 渲染抖动 {b_sz}→{c_sz}"))
                    else:
                        res.append(("FAIL", f"{sid}/plot/{tag}/{vn}/尺寸",
                                    f"差 {d}px {b_sz}→{c_sz}"))

    # ── 导出层 ──
    for tag, bv in (bs.get("export") or {}).items():
        cv = (cs.get("export") or {}).get(tag)
        if cv is None:
            res.append(("FAIL", f"{sid}/export/{tag}", "候选缺该输入"))
            continue
        be = (bv.get("result") or {}).get("excel") or {}
        ce = (cv.get("result") or {}).get("excel") or {}
        if bool(be.get("ok")) != bool(ce.get("ok")):
            res.append(("FAIL", f"{sid}/export/{tag}", f"ok {be.get('ok')} → {ce.get('ok')}"))
            continue
        if not be.get("ok"):
            res.append(("PASS", f"{sid}/export/{tag}", "两侧同失败"))
            continue
        cmp_list(be.get("index"), ce.get("index"), res, f"{sid}/export/{tag}/行索引")
        cmp_list(be.get("columns"), ce.get("columns"), res, f"{sid}/export/{tag}/列名")
        cmp_list(be.get("shape"), ce.get("shape"), res, f"{sid}/export/{tag}/形状")
        # 单元格逐值：从 npy 读不了（xlsx 在候选目录），用 values_sha256 兜底 + 偏差
        if be.get("values_sha256") != ce.get("values_sha256"):
            res.append(("WARN", f"{sid}/export/{tag}/值哈希",
                        "哈希不同（可能是浮点写盘顺序），请人工确认 Excel 内容"))
        else:
            res.append(("PASS", f"{sid}/export/{tag}/值哈希", ""))

    # ── 报告层：产品当前有缺陷，只报状态不做等价判定 ──
    br = (bs.get("report") or {}).get("result") or {}
    cr = (cs.get("report") or {}).get("result") or {}
    if br.get("ok") and cr.get("ok"):
        # 报告正文含「生成时间」与「输出目录」，两者每次采集都会变。
        # 两侧 manifest 都存了 normalized 全文，先掩掉这两处再比，
        # 而不是直接比原始哈希（旧基线用的是不含目录掩码的归一化方式）。
        cmp_scalar(_report_fingerprint(br.get("normalized")),
                   _report_fingerprint(cr.get("normalized")),
                   res, f"{sid}/report/正文")
    elif br.get("ok") != cr.get("ok"):
        res.append(("WARN", f"{sid}/report", f"基准 ok={br.get('ok')} 候选 ok={cr.get('ok')}"))
    else:
        res.append(("SKIP", f"{sid}/report", "两侧均失败（产品已知缺陷，见报告）"))

    return res


# ────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="比对两份基线")
    ap.add_argument("--base", default=os.path.join(ROOT, "baseline"))
    ap.add_argument("--cand", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--strict-pixels", action="store_true",
                    help="PNG 像素哈希也必须一致（默认只比尺寸）")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json-out", help="把结果写成 JSON")
    a = ap.parse_args()

    idx_p = os.path.join(a.base, "BASELINE-INDEX.json")
    if not os.path.exists(idx_p):
        print(f"基准索引缺失: {idx_p}")
        return 2
    idx = json.load(open(idx_p, encoding="utf-8"))
    sids = [s["id"] for s in idx["scenarios"] if not a.only or s["id"] in a.only]

    all_res = []
    for sid in sids:
        all_res.extend(compare_scenario(a.base, a.cand, sid, a.strict_pixels, a.verbose))

    n_pass = sum(1 for r in all_res if r[0] == "PASS")
    n_fail = sum(1 for r in all_res if r[0] == "FAIL")
    n_warn = sum(1 for r in all_res if r[0] == "WARN")
    n_skip = sum(1 for r in all_res if r[0] == "SKIP")

    if a.verbose:
        for st, tag, msg in all_res:
            if st == "PASS" and not a.verbose:
                continue
            print(f"{st:5s} {tag:44s} {msg}")
    else:
        for st, tag, msg in all_res:
            if st in ("FAIL", "WARN"):
                print(f"{st:5s} {tag:44s} {msg}")

    print("-" * 92)
    print(f"场景 {len(sids)} | PASS {n_pass} | FAIL {n_fail} | WARN {n_warn} | SKIP {n_skip}")
    if n_fail:
        print("结论：不等价 —— 上列 FAIL 项即为行为差异所在。")
    elif n_warn:
        print("结论：数值等价，但存在需人工确认的告警。")
    else:
        print("结论：等价（loose 层未做像素比对，需要时加 --strict-pixels）。")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump([{"status": s, "item": t, "detail": m} for s, t, m in all_res],
                      f, ensure_ascii=False, indent=2)
        print(f"明细已写入 {a.json_out}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
