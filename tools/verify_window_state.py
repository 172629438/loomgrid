# -*- coding: utf-8 -*-
"""窗口几何真机验证：真实平台下尺寸能否完整恢复。

为什么不能用 offscreen
----------------------
`restoreGeometry` 会按当前屏幕可用区做 clamp。离屏平台的虚拟屏幕很小，
1234x777 会被压成 798x774 —— 于是「恢复对不对」这个判断永远失真。
必须用系统默认平台（Windows）跑，脚本会先打印平台名与屏幕可用区备查。

为什么不启动完整程序
--------------------
`main()` 里欢迎框是模态、且先于 MainWindow 创建。脚本驱动要先关欢迎框才能拿到
主窗口，容易抓错对象（合作方第一次验时抓到 261x193 的欢迎框，以为主窗口就那么大）。
这里直接用 QWidget 复现「存 → 销毁 → 重建 → 恢复」这条链，
它正是 ui/window_state 四个函数被调用的方式，且 QWidget 不 show() 就不会弹窗。

什么时候要跑
------------
1. 改动 ui/window_state.py 之后
2. 改动 Config 的配置读写之后
3. 阶段 5 打包出 exe 之后（冻结环境下的路径与序列化最容易出问题）

用法
----
    python tools/verify_window_state.py
    python tools/verify_window_state.py --sizes 1280x800 900x600

退出码：全部尺寸恢复成功（误差 ≤1 像素）为 0。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FUSION = os.path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")


def main() -> int:
    ap = argparse.ArgumentParser(description="窗口几何真机验证")
    ap.add_argument("--sizes", nargs="*", default=["1180x760", "1050x680"],
                    help="要验证的尺寸，形如 1180x760")
    ap.add_argument("--fusion", default=FUSION, help="主程序路径")
    a = ap.parse_args()

    # 关键：用系统默认平台，不设 offscreen
    os.environ.pop("QT_QPA_PLATFORM", None)
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)

    from PySide6.QtWidgets import QApplication, QWidget

    spec = importlib.util.spec_from_file_location("lg_fusion", a.fusion)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from ui.window_state import get_window_geometry, set_window_geometry

    app = QApplication.instance() or QApplication([])
    print(f"平台 = {app.platformName()}")
    scr = app.primaryScreen().availableGeometry()
    print(f"屏幕可用区域 = {scr.width()}x{scr.height()}\n")
    if app.platformName() != "windows":
        print("⚠ 当前不是 windows 平台，尺寸 clamp 规则可能不同，结论仅供参考\n")

    cfg = mod.Config()
    key = "__geom_verify__"
    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"[PASS] {name}  {detail}")
        else:
            failed += 1
            print(f"[FAIL] {name}  {detail}")

    try:
        for token in a.sizes:
            try:
                w_, h_ = (int(x) for x in token.lower().split("x"))
            except ValueError:
                print(f"跳过无法解析的尺寸：{token}")
                continue
            w1 = QWidget()
            w1.resize(w_, h_)
            actual = (w1.width(), w1.height())
            set_window_geometry(cfg, key, w1.saveGeometry())
            w1.close()

            w2 = QWidget()
            ok = w2.restoreGeometry(get_window_geometry(cfg, key))
            got = (w2.width(), w2.height())
            dx, dy = abs(got[0] - actual[0]), abs(got[1] - actual[1])
            check(f"尺寸 {token} 恢复", bool(ok) and dx <= 1 and dy <= 1,
                  f"设 {actual[0]}x{actual[1]} → 恢复 {got[0]}x{got[1]}"
                  f"（差 {dx}x{dy}，≤1 像素属系统边框取整）")
            w2.close()
    finally:
        cfg.cfg.get("window_geometry", {}).pop(key, None)
        cfg.save()
        print("\n测试键已清理")

    print(f"\n结果：PASS {passed}  FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
