# -*- coding: utf-8 -*-
"""pytest 配置：把仓库根目录放进 sys.path，保证不安装也能跑测试。"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for extra in ("experiments", "baseline"):
    p = ROOT / extra
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
