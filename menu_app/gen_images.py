#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量生成菜品占位图 (SVG)：统一风格 - 柔和渐变底 + 大 emoji"""
import os, re

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "img")
os.makedirs(OUT, exist_ok=True)

# (id, emoji, 背景渐变起色, 背景渐变止色)
DISHS = [
    (1,  "🍖", "#ff9a8b", "#ff6a88"),   # 红烧排骨
    (2,  "🍗", "#ffc371", "#ff5f6d"),   # 可乐鸡翅
    (3,  "🍅", "#f6d365", "#fda085"),   # 西红柿炒蛋
    (4,  "🫑", "#a8e063", "#56ab2f"),   # 青椒肉丝
    (5,  "🥩", "#f2994a", "#f2c94c"),   # 糖醋里脊
    (6,  "🍥", "#89f7fe", "#66a6ff"),   # 紫菜蛋花汤
    (7,  "🌽", "#fdfcfb", "#e2d1c3"),   # 玉米排骨汤
    (8,  "🥘", "#f093fb", "#f5576c"),   # 番茄牛腩汤
    (9,  "🍧", "#e0c3fc", "#8ec5fc"),   # 银耳莲子羹
    (10, "🍚", "#ffffff", "#e6dada"),   # 白米饭
    (11, "🍜", "#fbc2eb", "#a6c1ee"),   # 葱油拌面
    (12, "🍳", "#ffecd2", "#fcb69f"),   # 鸡蛋炒饭
    (13, "🍝", "#d4fc79", "#96e6a1"),   # 肉丝炒面
    (14, "🍋", "#f7ff00", "#db36a4"),   # 柠檬水
    (15, "🥛", "#a1c4fd", "#c2e9fb"),   # 水果酸奶杯
    (16, "🍮", "#ffafbd", "#ffc3a0"),   # 红豆双皮奶
]

def esc(s):
    return re.sub(r'[^\u0000-\uFFFF]', '', s)

for did, emoji, c1, c2 in DISHS:
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="320" viewBox="0 0 400 320">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
  </defs>
  <rect width="400" height="320" fill="url(#g)"/>
  <rect width="400" height="320" fill="rgba(255,255,255,0.08)"/>
  <text x="200" y="180" font-size="150" text-anchor="middle" dominant-baseline="middle">{emoji}</text>
  <text x="200" y="285" font-size="20" text-anchor="middle" fill="rgba(255,255,255,0.85)" font-family="sans-serif">待上传真实图片</text>
</svg>'''
    path = os.path.join(OUT, f"dish_{did}.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"已生成 dish_{did}.svg  ({emoji})")

print(f"\n共生成 {len(DISHS)} 张占位图 → {OUT}")
