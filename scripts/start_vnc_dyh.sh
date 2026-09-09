#!/bin/bash
export USER=dyh
export HOME=/home/dyh
export DISPLAY=:2
pgrep -f "Xvnc.*:2" >/dev/null && { echo "VNC :2 已在运行"; exit 0; }
mkdir -p /home/dyh/.vnc
vncserver -kill :2 2>/dev/null
sudo -u dyh env HOME=/home/dyh USER=dyh DISPLAY=:2 vncserver :2 -geometry 1366x800 -depth 24 -localhost yes -SecurityTypes None 2>&1
sleep 2
echo VNC_DYH_DONE
