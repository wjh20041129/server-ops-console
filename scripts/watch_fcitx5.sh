#!/bin/bash
# fcitx5 守护进程：检测 fcitx5 是否运行，挂了自动拉起
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root
export DISPLAY=:1
export XDG_RUNTIME_DIR=/run/user/0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx

while true; do
  if ! pgrep -x fcitx5 >/dev/null; then
    echo "$(date '+%F %T') fcitx5 未运行，正在重启..." >> /var/log/fcitx5-watchdog.log
    # 确保 dbus 名字释放（等待1秒）
    sleep 1
    setsid /usr/bin/fcitx5 -d >/tmp/fcitx_watchdog.log 2>&1 < /dev/null &
    echo "$(date '+%F %T') fcitx5 已重启 (PID $!)" >> /var/log/fcitx5-watchdog.log
  fi
  sleep 5
done