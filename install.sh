#!/usr/bin/env bash
# novel-dsh 一键安装脚本（macOS / Linux）
# 用法：在仓库根目录运行  ./install.sh
# 作用：把 agent-presets/novel 复制到 $DSH_HOME/.agent-presets/novel
#       把 novel-tools 复制到 $DSH_HOME/node_modules/@local/novel-tools
# 幂等：已存在则覆盖；$DSH_HOME 未设置时回退到 ~/.dsh

set -euo pipefail

# 解析 DSH home：$DSH_HOME 优先，回退 $HOME/.dsh
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
echo "DSH home: $DSH_HOME"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRESET_SRC="$SCRIPT_DIR/agent-presets/novel"
TOOLS_SRC="$SCRIPT_DIR/novel-tools"
PRESET_DST="$DSH_HOME/.agent-presets/novel"
TOOLS_DST="$DSH_HOME/node_modules/@local/novel-tools"

# 校验仓库内源目录存在
[ -d "$PRESET_SRC" ] || { echo "找不到 $PRESET_SRC（请在仓库根目录运行本脚本）" >&2; exit 1; }
[ -d "$TOOLS_SRC" ]  || { echo "找不到 $TOOLS_SRC（请在仓库根目录运行本脚本）" >&2; exit 1; }

# 复制 preset
echo "安装 preset → $PRESET_DST"
rm -rf "$PRESET_DST"
mkdir -p "$(dirname "$PRESET_DST")"
cp -r "$PRESET_SRC" "$PRESET_DST"
# 清理 Python 缓存（避免把本机编译产物带进安装）
find "$PRESET_DST" -type d -name "__pycache__" -prune -exec rm -rf {} +

# 复制工具包
echo "安装 novel-tools → $TOOLS_DST"
rm -rf "$TOOLS_DST"
mkdir -p "$(dirname "$TOOLS_DST")"
cp -r "$TOOLS_SRC" "$TOOLS_DST"

echo ""
echo "安装完成！请重启 DSH 进程，新建会话时选择『小说创作』预设。"
