# 项目目录结构

```
[项目根目录]/
├── requirements.md              ← 创作需求
├── style.db                     ← 不可变参考风格库（extract-writing-style 产出）
├── novel-cli/                   ← 结构化数据 + CLI 工具
│   ├── cli.py                   ← CLI 入口（从 skill 目录复制）
│   ├── update_style.py          ← 风格库合并脚本
│   ├── nodes.db                 ← ★ 节点库：剧情树/引用/事实（高频状态变更）
│   ├── materials.db             ← ★ 素材库：条目/维度/类型字典（低频设定维护）
│   ├── style.db                 ← ★ 工作区风格库（引例 + 特征规则）
│   ├── entries/                 ← 条目导出（human-readable）
│   ├── plots/                   ← 剧情文本
│   │   ├── L1/                  ← 全书梗概（唯一根节点）
│   │   ├── L2/                  ← 分卷梗概（每卷一个节点）
│   │   ├── L3/                  ← ★ 事件梗概，按卷分目录
│   │   │   ├── 1/               ← 卷排序 1 的事件（文件名：{排序}_{标题}.md）
│   │   │   └── 2/
│   │   └── L4/                  ← ★ agent 缓存工作区，按卷分目录
│   │       ├── 1/               ← 文件名：{排序}_{章节名}.md
│   │       └── 2/
│   └── log/
│       └── latest.log           ← 当日日志（纯文本，agent 用 shell 工具读）
├── contents/                    ← ★ 正式正文产出（.txt），按卷分目录
│   ├── 1/                       ← 文件名：{全书章号}_{卷内章号}_{标题}.txt
│   └── 2/
└── skills/                       ← skill 定义（随 agent 预设提供，历史来源 .claude/skills/）
    ├── novel-write/             ← 小说创作 skill
    ├── extract-writing-style/   ← 风格提取 skill
    ├── output-style/            ← 风格库输出 skill
    └── update-style/            ← 风格库同步 skill
```
