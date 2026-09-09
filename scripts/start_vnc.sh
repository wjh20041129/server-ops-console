#!/bin/bash
# 启动 VNC :1 会话 (XFCE)
export USER=root
export HOME=/root
export DISPLAY=:1

# 防止重复启动
pgrep -f "Xvnc.*:1" >/dev/null && { echo "VNC :1 已在运行"; exit 0; }

mkdir -p /root/.vnc

# 首次设置只允许本机访问，websockify 负责转发
if [ ! -f /root/.vnc/passwd ]; then
  echo "设置 VNC 密码 (默认：YOUR_VNC_PASSWORD)"
  mkdir -p /root/.vnc
  printf 'YOUR_VNC_PASSWORD\nYOUR_VNC_PASSWORD\n' | vncpasswd -f > /root/.vnc/passwd
  chmod 600 /root/.vnc/passwd
fi

# 以 root 启动 Xvnc + XFCE
vncserver -kill :1 2>/dev/null
vncserver :1 -geometry 1180x788 -depth 24 -localhost yes -SecurityTypes None 2>&1
sleep 2

echo "VNC_DONE"