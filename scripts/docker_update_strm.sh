#!/bin/bash
# Docker容器内STRM文件批量更新脚本
# 将绝对URL转换为相对路径，适配SenPlayer等外部客户端

set -e

CONTAINER_NAME="bili_curator_v7"
STRM_PATH="/app/strm"
DRY_RUN=false
VERBOSE=false

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --container)
            CONTAINER_NAME="$2"
            shift 2
            ;;
        --strm-path)
            STRM_PATH="$2"
            shift 2
            ;;
        *)
            echo "未知参数: $1"
            exit 1
            ;;
    esac
done

echo "🔧 Docker STRM文件更新工具"
echo "📦 容器名称: $CONTAINER_NAME"
echo "📁 STRM路径: $STRM_PATH"

# 检查容器是否运行
if ! docker ps --format "table {{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ 容器 $CONTAINER_NAME 未运行"
    exit 1
fi

# 构建Python命令参数
PYTHON_ARGS="--strm-path $STRM_PATH"
if [ "$DRY_RUN" = true ]; then
    PYTHON_ARGS="$PYTHON_ARGS --dry-run"
fi
if [ "$VERBOSE" = true ]; then
    PYTHON_ARGS="$PYTHON_ARGS --verbose"
fi

# 在容器内执行更新脚本
echo "🚀 开始更新STRM文件..."
docker exec -it $CONTAINER_NAME python3 -c "
import os
import re
import shutil
from pathlib import Path
from datetime import datetime

class STRMUpdater:
    def __init__(self, strm_path):
        self.strm_path = Path(strm_path)
    
    def find_strm_files(self):
        if not self.strm_path.exists():
            return []
        return list(self.strm_path.rglob('*.strm'))
    
    def update_file(self, file_path, dry_run=False):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # 匹配绝对URL
            pattern = r'https?://[^/]+(/api/strm/stream/[A-Za-z0-9]+)'
            match = re.search(pattern, content)
            
            if match:
                relative_url = match.group(1)
                if not dry_run:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(relative_url)
                return True, content, relative_url
            elif content.startswith('/api/strm/stream/'):
                return False, content, '已是相对路径'
            else:
                return False, content, '未识别格式'
        except Exception as e:
            return False, '', f'错误: {e}'

updater = STRMUpdater('$STRM_PATH')
files = updater.find_strm_files()

print(f'📁 找到 {len(files)} 个STRM文件')

updated = 0
skipped = 0
errors = 0

dry_run = $DRY_RUN
verbose = $VERBOSE

for file_path in files:
    success, original, new_content = updater.update_file(file_path, dry_run)
    
    if success:
        updated += 1
        status = '[DRY-RUN]' if dry_run else '[UPDATED]'
        print(f'✅ {status} {file_path}')
        if verbose:
            print(f'    原: {original}')
            print(f'    新: {new_content}')
    elif '已是相对路径' in new_content:
        skipped += 1
        if verbose:
            print(f'✓ [SKIP] {file_path}: {new_content}')
    else:
        errors += 1
        print(f'❌ [ERROR] {file_path}: {new_content}')

print(f'\\n📊 处理结果:')
print(f'   更新: {updated}')
print(f'   跳过: {skipped}')
print(f'   错误: {errors}')

if dry_run and updated > 0:
    print('\\n💡 预览模式完成，实际更新请去掉 --dry-run 参数')
"

echo "✅ STRM文件更新完成"
