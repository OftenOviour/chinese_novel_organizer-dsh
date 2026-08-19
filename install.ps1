# novel-dsh 一键安装脚本（Windows PowerShell）
# 用法：在仓库根目录运行  ./install.ps1
# 作用：把 agent-presets/novel 复制到 $DSH_HOME/.agent-presets/novel
#       把 novel-tools 复制到 $DSH_HOME/node_modules/@local/novel-tools
# 幂等：已存在则覆盖；$DSH_HOME 未设置时回退到 ~/.dsh

$ErrorActionPreference = "Stop"

# 解析 DSH home：$DSH_HOME 优先，回退 $HOME\.dsh
$dshHome = $env:DSH_HOME
if (-not $dshHome) {
    $dshHome = Join-Path $HOME ".dsh"
}
Write-Host "DSH home: $dshHome"

$presetSrc = Join-Path $PSScriptRoot "agent-presets\novel"
$toolsSrc  = Join-Path $PSScriptRoot "novel-tools"
$presetDst = Join-Path $dshHome ".agent-presets\novel"
$toolsDst  = Join-Path $dshHome "node_modules\@local\novel-tools"

# 校验仓库内源目录存在
if (-not (Test-Path $presetSrc)) { Write-Error "找不到 $presetSrc（请在仓库根目录运行本脚本）" }
if (-not (Test-Path $toolsSrc))  { Write-Error "找不到 $toolsSrc（请在仓库根目录运行本脚本）" }

# 复制 preset
Write-Host "安装 preset → $presetDst"
Copy-Item $presetSrc $presetDst -Recurse -Force
# 清理 Python 缓存（避免把本机编译产物带进安装）
Get-ChildItem $presetDst -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 复制工具包
Write-Host "安装 novel-tools → $toolsDst"
New-Item -ItemType Directory -Path (Split-Path $toolsDst -Parent) -Force | Out-Null
Copy-Item $toolsSrc $toolsDst -Recurse -Force

Write-Host ""
Write-Host "安装完成！请重启 DSH 进程，新建会话时选择『小说创作』预设。"
