#!/bin/bash

# 检查容器文件是否与宿主机一致，不一致则更新并重启

CONTAINER_NAME="report-app"
COMPOSE_FILE="docker-compose.yml"

# 检查容器是否运行
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "容器 ${CONTAINER_NAME} 未运行，直接启动..."
    docker compose -f ${COMPOSE_FILE} up -d --build
    exit 0
fi

# 需要检查的核心文件列表
FILES_TO_CHECK=(
    "app.py"
    "ai_generator.py"
    "latex_backend.py"
    "word_backend.py"
    "utils.py"
    "plot_utils.py"
    "email_utils.py"
    "crypto_utils.py"
    "constants.py"
    "requirements.txt"
    "gunicorn.conf.py"
)

# 检查 templates 目录下的所有 HTML 文件
if [ -d "templates" ]; then
    for f in templates/*.html; do
        if [ -f "$f" ]; then
            FILES_TO_CHECK+=("$f")
        fi
    done
fi

# 检查 static 目录下的所有文件
if [ -d "static" ]; then
    for f in $(find static -type f); do
        FILES_TO_CHECK+=("$f")
    done
fi

echo "正在检查 ${#FILES_TO_CHECK[@]} 个文件..."

NEED_UPDATE=false

for file in "${FILES_TO_CHECK[@]}"; do
    if [ ! -f "$file" ]; then
        continue
    fi

    # 计算宿主机文件 hash
    HOST_HASH=$(md5sum "$file" | awk '{print $1}')

    # 计算容器内文件 hash
    CONTAINER_HASH=$(docker exec ${CONTAINER_NAME} md5sum "/app/$file" 2>/dev/null | awk '{print $1}')

    if [ "$HOST_HASH" != "$CONTAINER_HASH" ]; then
        echo "文件不一致: $file"
        echo "  宿主机:  $HOST_HASH"
        echo "  容器内:  $CONTAINER_HASH"
        NEED_UPDATE=true
    fi
done

if [ "$NEED_UPDATE" = true ]; then
    echo ""
    echo "检测到文件变更，正在重新构建并重启容器..."
    docker compose -f ${COMPOSE_FILE} down
    docker compose -f ${COMPOSE_FILE} up -d --build
    echo ""
    echo "容器已更新并重启完成！"
else
    echo ""
    echo "所有文件已同步，无需更新。"
fi
