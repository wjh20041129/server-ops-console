# server-ops-console

**自托管多服务运维架构** —— 用一个轻量控制台统一收编、监控和运维 5 个自研服务。

> 长期运维实践：覆盖 Nginx 反向代理、MySQL/Redis、认证鉴权、fail2ban 防护、systemd 生命周期管理、AI 能力集成。

## 架构总览

```
用户 → Nginx (80/443/8443)  HTTPS 反向代理
        │   (统一登录鉴权 auth_request)
        ├─ /         → svcctrl     轻量服务控制台 (8750)
        ├─ /exam/    → exam_system 随身笔记系统   (5000)
        ├─ /family/  → family_portal 家人门户     (8092)
        ├─ /menu/    → menu_app    点餐系统       (8090)
        ├─ /vnc/     → noVNC(6080) → VNC(5901/2)
        ├─ /ssh/     → ttyd  Web 终端 (7681)
        └─ /file/    → filebrowser 文件管理 (8088)

数据层：MySQL 8 (3306) · Redis (6379)
生命周期：全部 systemd 托管（开机自启 / 崩溃自动重启）
```

## 包含的服务

| 目录 | 服务 | 技术栈 | 说明 |
|------|------|--------|------|
| `svcctrl/` | 服务控制台 | Python Flask + systemd | 统一启停/监控上述所有服务，不依赖宝塔 |
| `exam_system/` | 随身笔记系统 | Flask + MySQL + 通义千问 | 公考/四级题库、AI 生成题目、仓库笔记、复习计划 |
| `family_portal/` | 家人门户 | Flask + MySQL | 注册审批、云盘、家庭入口 |
| `menu_app/` | 点餐系统 | Python + MySQL | 点餐管理端、图片/PPT 生成、SMTP 通知 |
| `scripts/` | 运维脚本 | Shell | VNC 启动、输入法监控等日常运维自动化 |

## 核心能力与亮点

- **高可用**：全部服务 systemd 托管，崩溃自动拉起；控制台独立进程，停 MySQL/Nginx 不掉线
- **安全**：登录鉴权 + `auth_request` 子路径鉴权 + fail2ban 暴力破解防护 + HTTPS
- **监控**：CPU/内存/磁盘环形仪表盘、LoadAvg/Uptime/网络流量、MySQL 连接数/慢查询/库表体积
- **AI 集成**：exam 用通义千问生成题目，多层质量校验 + 置信度评分，只基于权威白名单材料
- **资源友好**：纯轻量 Python，无重框架；控制台 `OOMScoreAdjust=-900` 优先保活

## 快速开始（本地展示）

```bash
# 控制台（需 systemd + nginx 环境）
pip install -r svcctrl/requirements.txt 2>/dev/null || true
python3 svcctrl/app.py changepass <你的密码>
python3 svcctrl/app.py

# exam 笔记系统
pip install -r exam_system/requirements.txt
python3 exam_system/app.py
```

> ⚠️ 本仓库为**代码归档/展示副本**，不含任何密钥与 `.env` 配置。完整运行需自行配置环境变量与数据库。

