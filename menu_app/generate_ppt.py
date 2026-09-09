#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""点菜系统讲解 PPT 生成器"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

# ---------- 配色 ----------
ORANGE   = RGBColor(0xE8, 0x5D, 0x2A)   # 主色(温暖食欲橙)
DARK     = RGBColor(0x2B, 0x2B, 0x2B)   # 深色文字
WHITE    = RGBColor(0xFF, 0xFF, 0xFF)
CREAM    = RGBColor(0xFF, 0xF7, 0xF0)   # 浅底
GREY     = RGBColor(0x6B, 0x6B, 0x6B)
GREEN    = RGBColor(0x2E, 0x8B, 0x57)
BLUE     = RGBColor(0x2F, 0x5B, 0x9E)

prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]

def bg(slide, color):
    from pptx.oxml.ns import qn
    r = slide.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
    r.fill.solid(); r.fill.fore_color.rgb = color
    r.line.fill.background()
    r.shadow.inherit = False
    r._element.set('id', '0')
    return slide

def add_rect(slide, x, y, w, h, color, line=None):
    from pptx.oxml.ns import qn
    r = slide.shapes.add_shape(1, x, y, w, h)
    r.fill.solid(); r.fill.fore_color.rgb = color
    if line: r.line.color.rgb = line; r.line.width = Pt(1)
    else: r.line.fill.background()
    r.shadow.inherit = False
    return r

def txt(slide, x, y, w, h, text, size=18, color=DARK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font="微软雅黑", line_spacing=1.0):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        r = p.add_run(); r.text = ln
        f = r.font; f.size = Pt(size); f.bold = bold; f.color.rgb = color; f.name = font
    return tb

def title_slide():
    slide = prs.slides.add_slide(BLANK)
    bg(slide, ORANGE)
    # 顶部装饰条
    add_rect(slide, 0, 0, prs.slide_width, Inches(0.18), RGBColor(0xC7, 0x45, 0x1E))
    txt(slide, Inches(1), Inches(1.5), Inches(11.3), Inches(1), "🍽️ 宝宝点菜系统", 60, WHITE, True, PP_ALIGN.CENTER)
    txt(slide, Inches(1), Inches(2.8), Inches(11.3), Inches(0.8), "把想吃的菜，亲手交给厨房", 26, WHITE, False, PP_ALIGN.CENTER)
    add_rect(slide, Inches(5.17), Inches(3.9), Inches(3), Inches(0.06), WHITE)
    txt(slide, Inches(2), Inches(4.3), Inches(9.3), Inches(2.4),
        "—— 项目介绍与技术讲解 ——\n\n后端：Python  http.server  +  MySQL   （端口 8090）", 20, CREAM, False, PP_ALIGN.CENTER)
    txt(slide, Inches(8), Inches(6.8), Inches(4.3), Inches(0.5), "制作：个人作品", 13, CREAM, False, PP_ALIGN.RIGHT)

def section_slide(num, en, title, sub):
    slide = prs.slides.add_slide(BLANK)
    bg(slide, CREAM)
    add_rect(slide, 0, 0, Inches(0.35), prs.slide_height, ORANGE)
    txt(slide, Inches(0.8), Inches(0.6), Inches(2), Inches(1.2), num, 54, ORANGE, True)
    txt(slide, Inches(2.0), Inches(0.75), Inches(10), Inches(0.6), en, 14, GREY, False)
    txt(slide, Inches(2.0), Inches(1.35), Inches(10), Inches(1.0), title, 40, DARK, True)
    txt(slide, Inches(2.0), Inches(2.5), Inches(10), Inches(0.9), sub, 17, GREY)
    return slide

def content_slide(title, bullets, footer=None):
    """bullets: list of (level, text, color?)"""
    slide = prs.slides.add_slide(BLANK)
    bg(slide, WHITE)
    add_rect(slide, 0, 0, prs.slide_width, Inches(1.0), ORANGE)
    txt(slide, Inches(0.6), Inches(0.12), Inches(12), Inches(0.8), title, 30, WHITE, True)
    y = Inches(1.35)
    for lv, text in bullets:
        lv = 0 if lv == "" else int(lv)
        color = DARK
        if text.startswith("•"):
            txt(slide, Inches(0.8 + lv*0.5), y, Inches(11.5 - lv*0.5), Inches(0.5), text, 19, color, lv==0)
            y += Inches(0.55)
        else:
            # 关键强调块
            add_rect(slide, Inches(0.7), y-Emu(10000), Inches(11.9), Inches(0.6), CREAM)
            tb = txt(slide, Inches(0.9+lv*0.4), y, Inches(11.4), Inches(0.6), text, 18, DARK, True)
            y += Inches(0.75)
    if footer:
        txt(slide, Inches(0.6), Inches(7.0), Inches(12), Inches(0.4), footer, 12, GREY, False, align=PP_ALIGN.RIGHT)
    return slide

# ================= 1 封面 =================
title_slide()

# ================= 2 项目概览 =================
content_slide("📌 项目是什么？", [
    ("", "•  一个“宝宝点菜 · 妈妈做”的家庭点餐小工具"),
    ("", "    宝贝在前台把想吃的菜加进购物车、下单；  厨房（家长手机/后台）即时收到并处理。"),
    ("", "•  场景：孩子想吃啥，直接告诉厨房，不用喊话。"),
    ("", "•  双端页面："),
    ("", "      ① 点菜前台（首页）—— 浏览菜单、下单"),
    ("", "      ② 厨房后台（/admin）—— 管理菜单、查订单、改状态"),
    ("", "•  16 道菜 · 4 个分类（热菜/汤类/主食/饮品甜品）"),
], footer="宝宝点菜 = 前台 + 后台 一套搞定")

# ================= 3 技术架构 =================
slide = content_slide("🧱 技术架构一览", [
    ("", "•  后端：Python 标准库  http.server（零第三方 Web 框架）"),
    ("", "•  数据库：MySQL（表  categories / dishes / orders / order_items）"),
    ("", "•  前端：原生 HTML + JavaScript（Ajax 调 REST 接口）"),
    ("", "•  图片：新增菜品自动生成 SVG 占位图（Emoji + 渐变底色）"),
    ("", "•  通知：可选 QQ 邮箱 SMTP 推送订单邮件"),
    ("", "•  部署：0.0.0.0:8090，浏览器直接访问"),
])
# 叠一个简版架构示意条
add_rect(slide, Inches(0.7), Inches(5.3), Inches(0.2), Inches(1.1), ORANGE)
txt(slide, Inches(1.0), Inches(5.15), Inches(11.5), Inches(1.4),
    "浏览器(前台/后台)  ──REST/JSON──▶  Python HTTP Server  ──pymysql──▶  MySQL\n        ▲                                            │\n        └────────────  可选：QQ邮箱 SMTP 订单邮件  ◀──┘",
    16, BLUE, False)

# ================= 4 架构数据流(单独更清晰) =================
slide = bg(prs.slides.add_slide(BLANK), WHITE)
add_rect(slide, 0, 0, prs.slide_width, Inches(1.0), ORANGE)
txt(slide, Inches(0.6), Inches(0.12), Inches(12), Inches(0.8), "🔁 一次点单，数据怎么走？", 30, WHITE, True)
# 流程块
def step(x, w, top, title, body, c=ORANGE):
    add_rect(slide, x, top, w, Inches(1.5), c)
    txt(slide, x+Inches(0.25), top+Inches(0.15), w-Inches(0.5), Inches(1.2), title+"\n"+body, 15, WHITE, False)
def arrow(x, y):
    txt(slide, x, y, Inches(0.6), Inches(1.5), "▶", 20, ORANGE, True, PP_ALIGN.CENTER)
add_rect(slide, 0.05, Inches(4.4), Inches(0.14), Inches(2.4), ORANGE)
txt(slide, Inches(0.4), Inches(4.25), Inches(12.5), Inches(2.9),
    "① 宝贝加菜下单 → ② 后端落库(orders + order_items)\n"
    "③ 计算总价、生成订单号 → ④ 触发邮件通知厨房\n"
    "⑤ 厨房在 /admin 查看新订单 → ⑥ 一键改状态(加餐中/完成)",
    18, DARK, False)
txt(slide, Inches(0.4), Inches(6.9), Inches(12.5), Inches(0.4),
    "核心接口：GET /api/menu ｜ POST /api/order ｜ GET /api/orders ｜ POST /api/orders/<id>/status", 13, GREY)

# ================= 5 数据库设计 =================
content_slide("🗄️ 数据库设计（MySQL）", [
    ("", "•  categories        菜品分类（热菜 / 汤类 / 主食 / 饮品甜品）"),
    ("", "        id · name · icon · sort_order"),
    ("", "•  dishes            菜品（16 道，含上下架状态）"),
    ("", "        category_id · name · emoji · image · description · tags · price · is_available"),
    ("", "•  orders            订单主表"),
    ("", "        status(pending) · note(备注) · total_price(总价) · created_at"),
    ("", "•  order_items       订单明细（一单可含多个菜）"),
    ("", "        order_id · dish_id · dish_name · quantity · price"),
], footer="一对多：orders 1—N order_items；分类 1—N dishes")

# ================= 6 前台/后台/接口总表 =================
content_slide("🖥️ 两个页面 + 一套接口", [
    ("", "•  前台  index.html —— 展示菜单分类与菜品、加购物车、下单、下单成功提醒"),
    ("", "     接口：GET /api/menu、POST /api/order"),
    ("", "•  后台  admin.html —— 登录、看实时订单、改订单状态、增删改菜品/分类"),
    ("", "     接口：POST /api/admin/login、GET /api/orders、菜品/分类增删改、订单状态"),
    ("", "•  权限：下单免鉴权；后台操作需登录拿到 Bearer Token"),
    ("", "•  后台登录密码存于 admin_config.json，邮箱通知可后台配置"),
], footer="后台默认密码字段保存在 admin_config.json（当前项目内）")

# ================= 7 亮点与实现细节 =================
content_slide("✨ 亮点与实现细节", [
    ("", "•  零框架后端：仅用 Python 标准库 http.server + 多线程(ThreadingHTTPServer)"),
    ("", "•  兼容全端：CORS 全开、UTF-8、手机浏览器即用"),
    ("", "•  自动配图：新增菜品无图时，按菜品 id 选配色自动生成 SVG 占位图，菜价字段价格"),
    ("", "•  Token 鉴权：登录后发随机 token，存内存集合，后台接口统一校验"),
    ("", "•  非阻塞通知：邮件在独立线程后台发送，不卡下单"),
    ("", "•  数据一致性：下单用事务化 insert，自动算总价、过滤无效菜品"),
])

# ================= 8 目录结构 =================
content_slide("📂 项目目录结构", [
    ("", "   /root/menu_app/"),
    ("", "   ├── server.py        后端主程序（HTTP 服务 + 业务逻辑，约 430 行）"),
    ("", "   ├── index.html       点菜前台页面"),
    ("", "   ├── admin.html        厨房管理后台页面"),
    ("", "   ├── db_config.json    MySQL 连接配置"),
    ("", "   ├── admin_config.json  后台密码 & 邮件 SMTP 配置"),
    ("", "   ├── gen_images.py     批量生成菜单图片的脚本"),
    ("", "   └── img/              菜品 SVG 图片目录"),
])

# ================= 9 运行 =================
slide = bg(prs.slides.add_slide(BLANK), CREAM)
add_rect(slide, 0, 0, Inches(0.35), prs.slide_height, ORANGE)
txt(slide, Inches(0.8), Inches(0.7), Inches(11), Inches(1), "🚀 如何运行", 40, DARK, True)
txt(slide, Inches(0.8), Inches(1.8), Inches(11.5), Inches(0.6), "启动服务（需先准备 MySQL 与 db_config.json）", 18, GREY)
add_rect(slide, Inches(0.8), Inches(2.5), Inches(11.7), Inches(0.7), WHITE)
txt(slide, Inches(1.0), Inches(2.58), Inches(11.3), Inches(0.6), "python3 server.py        # 默认监听 0.0.0.0:8090", 20, RGBColor(0x0B,0x52,0x8C), True)
txt(slide, Inches(0.8), Inches(3.5), Inches(11.5), Inches(0.6), "访问入口", 18, GREY)
add_rect(slide, Inches(0.8), Inches(4.1), Inches(11.7), Inches(1.1), WHITE)
txt(slide, Inches(1.0), Inches(4.25), Inches(11.3), Inches(1.0),
    "点菜前台： http://<服务器IP>:8090/\n厨房后台： http://<服务器IP>:8090/admin",
    20, DARK, False)
txt(slide, Inches(0.8), Inches(5.5), Inches(11.5), Inches(0.6), "端口可通过环境变量覆盖（MENU_PORT）", 15, GREY)
RED = RGBColor(0xC0,0x39,0x2B)
txt(slide, Inches(0.8), Inches(6.1), Inches(11.5), Inches(0.8),
    "⚠ 重启机器后需重新启动服务；可配置 systemd 实现开机自启。", 16, RED, False)

# ================= 10 结尾 =================
slide = prs.slides.add_slide(BLANK)
bg(slide, ORANGE)
add_rect(slide, 0, prs.slide_height-Inches(0.18), prs.slide_width, Inches(0.18), RGBColor(0xC7,0x45,0x1E))
txt(slide, Inches(1), Inches(2.5), Inches(11.3), Inches(1), "谢谢观看 🙌", 56, WHITE, True, PP_ALIGN.CENTER)
txt(slide, Inches(1), Inches(4.0), Inches(11.3), Inches(1), "宝宝点菜 · 让爱下厨更简单", 22, CREAM, False, PP_ALIGN.CENTER)

out = "/root/Desktop/点菜系统讲解.pptx"
prs.save(out)
print("已生成:", out)
