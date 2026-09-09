# svcctrl — 轻量服务控制台

自托管、不依赖宝塔的服务监控与启停面板。独立进程(端口8750)，与 exam 系统(5000)完全分离，
因此在面板里停 exam / MySQL / nginx 都不会让控制台自身掉线。

## 访问
- 内网/https 反代：`https://<服务器>:8443/`   (需阿里云安全组放行 TCP 8443)
- 本地直连：`http://127.0.0.1:8750/`（仅服务器本机）

## 首次登录
- 密码在 `auth.json`(sha256)。首次生成随机密码见 `journalctl -u svcctrl`。
- 或用 `python3 /root/svcctrl/app.py changepass <新密码>` 修改。
- 登录后每会话有效，可"退出登录"。

## 服务
- **systemd 服务**(10个)：可 启动/停止/重启 + 设/关 开机自启
  随身笔记(exam-system)、MySQL、Nginx、CET4预加载、code-server、VNC、noVNC、
  filebrowser、Docker、宝塔(BT-Panel)
- **只读进程监控**(3个)：RustDesk(hbbs/hbbr)、OpenClaw网关(18789)、dsh web(3080)
  —— 这些不在面板启停（避免切断自身会话等），仅显示状态/内存。

## 数据
- 系统表盘：CPU/内存/磁盘环形 gauge + LoadAvg / Uptime / 进程数 / 网络流量，1.5s 刷新
- MySQL：版本 / 运行时长 / 连接数 / 慢查询 / 各库体积表数，(5s 刷新用 pymysql 查询)
  - DB 密码默认读 root/YOUR_DB_PASSWORD(与 exam 一致)；如需不同覆盖 `/root/svcctrl/db.json`

## 运维
- systemd 单元：`/etc/systemd/system/svcctrl.service`（自动启动/崩溃重启）
- 日志：`journalctl -u svcctrl -f`
- 安全：整个面板需登录；未登录 API 一律 401；停止/重启前浏览器二次 confirm。
  - ⚠️ 请勿把 8750 直接对公网开放，务必走 nginx 并保持登录保护。

## 文件
```
/root/svcctrl/app.py           后端(Flask)
/root/svcctrl/templates/*.html 登录页 + 主控制台
/root/svcctrl/auth.json         密码哈希
/root/svcctrl/db.json           (可选)DB凭据覆盖
/root/svcctrl/svcctrl.service   systemd 单元
```

## 更新(2026-09-06)
- 服务列表改为**动态探测**：某服务若被外部卸载(unit文件消失)，5秒内从面板**自动消失**
- 服务卡片新增按钮：
  - **日志**：右侧滑出抽屉，实时(每2秒增量)查看 journalctl 日志
  - **卸载**：stop+disable+删除unit文件+daemon-reload（仅非核心；mysql/exam/nginx 受保护不可卸载）
