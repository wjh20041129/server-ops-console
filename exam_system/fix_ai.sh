#!/bin/bash
set -e

echo "=== 修复四级刷词模块AI功能 ==="

# 1. 配置正确的API密钥
cat > /root/exam_system/.env <<EOF
# 数据库配置
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=YOUR_DB_PASSWORD
DB_DATABASE=exam_system
DB_CHARSET=utf8mb4

# 阿里云百炼API配置
BAILIAN_API_KEY=your_actual_api_key_here
BAILIAN_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
BAILIAN_MODEL=qwen3.8-flash

# Flask配置
FLASK_APP=app.py
FLASK_ENV=production
SECRET_KEY=exam-system-secret-key-2026

# 上传配置
UPLOAD_FOLDER=uploads
MAX_CONTENT_LENGTH=52428800
EOF

# 2. 停止所有相关进程
echo "停止现有进程..."
pkill -f "python3.*app.py" 2>/dev/null || true
pkill -f "python3.*cet4_preload_daemon" 2>/dev/null || true
sleep 2

# 3. 启动服务
echo "启动服务..."
cd /root/exam_system
nohup python3 app.py > app.log 2>&1 &
nohup python3 cet4_preload_daemon.py > daemon.log 2>&1 &

# 4. 验证服务
echo "等待服务启动..."
sleep 5

if pgrep -f "app.py" >/dev/null; then
    echo "✅ 服务启动成功！"
    echo "📝 请将/root/exam_system/.env中的BAILIAN_API_KEY替换为你实际的API密钥"
    echo "🌐 访问地址：http://your-server-ip:5001/ 或 https://your-server/exam/"
else
    echo "❌ 服务启动失败，请查看日志：/root/exam_system/app.log"
fi
