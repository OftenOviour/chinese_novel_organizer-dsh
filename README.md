# novel-dsh — 小说创作 Agent Preset for DeepSeek Harness

基于 **DeepSeek Harness (DSH)** 的小说创作 Agent 预设。以 L1–L4 逐级剧情树 + SQLite 数据层驱动长篇小说的规划、创作、一致性与发布，内置素材库、风格库、事实账本、文本替换（translation）、内容漂移检测与级联失效。

你可以直接将此文档内容交给DSH，让它自行安装。

## 特性

- **L1–L4 逐级剧情树**：全书梗概 → 分卷梗概 → 事件梗概 → 正文，确认门禁由数据层强制
- **单节点创作**：一次只写一个节点，衔接前后同级，避免长上下文失控
- **素材库**（`materials.db`）：类型自由的角色/地点/道具/概念/场景模型/伏笔条目，多行属性
- **风格库**（`style.db`）：从参考文本提取写作风格，多标签检索，正向参考
- **事实账本**（`nodes.db` facts）：已完成事项不重复写出，改动级联反向定位
- **级联失效**：改前置剧情 → 子树 + 引用网络自动标 `affected`，持续提醒核查
- **内容漂移检测**：确认后文件被外部修改 → `publish` 拒绝，杜绝绕过确认
- **冰山叙事**：正向写作原则——已知信息分散暗示，禁止"实验报告"式直白交代
- **去 AI 味清单**：每章写作后负向自查（删过度修饰/破四字格律/去模板感）
- **文本替换**：`{{key}}` 占位符 → 发布时替换为最终名（角色/地名改名只改一条记录）
- **中文标点约束**：中文小说强制全角标点
- **行内指令**：正文中 `<...>` 区域为用户写作指令，agent 执行后删除
- **纯文本日志**：`log/` 目录 + 按日期头轮转，不依赖文件 mtime

## 前置条件

- 已安装 [DeepSeek Harness](https://github.com/deepseek-ai)（含 Web/CLI 任一入口）
- Python 3.10+ 可用（`python` 在 PATH 中，供 novel-cli 执行）

## 安装

**任选一种获取方式**，然后运行一键安装脚本即可：

1. **获取仓库**：

   - 克隆：

     ```bash
     git clone https://github.com/OftenOviour/chinese_novel_organizer-dsh.git
     cd novel-dsh
     ```

   - 或下载 ZIP 并解压（GitHub 页面 → Code → Download ZIP），然后进入解压出的目录。

2. **选择你的系统，运行对应的安装脚本**（自动把 preset + 工具包安装到 DSH home）：

   | 你的系统 | 用哪个脚本 | 怎么运行 |
   |---|---|---|
   | **Windows** | `install.ps1` | 打开 **PowerShell**，在仓库根目录运行 `./install.ps1` |
   | **macOS / Linux** | `install.sh` | 打开终端，在仓库根目录运行 `./install.sh` |

   <details>
   <summary>Windows 详细步骤（PowerShell 怎么打开）</summary>

   1. 在仓库根目录的空白处按住 `Shift` 右键 → 选择 **"在此处打开 PowerShell 窗口"**（或"Open PowerShell window here"）
   2. 在窗口中输入：

      ```powershell
      ./install.ps1
      ```

   3. 若提示"禁止运行脚本"，先执行一次放开策略：

      ```powershell
      Set-ExecutionPolicy -Scope Process Bypass
      ./install.ps1
      ```

   > 注意：`.ps1` 脚本只能在 PowerShell 中运行，**cmd（命令提示符）跑不了**。
   </details>

   <details>
   <summary>macOS / Linux 详细步骤</summary>

   1. 打开终端，进入仓库根目录（clone 或解压 ZIP 后的目录）
   2. 运行：

      ```bash
      ./install.sh
      ```

   3. 若提示"Permission denied"（无执行权限），先加权限再运行：

      ```bash
      chmod +x install.sh
      ./install.sh
      ```

   </details>

   > - 脚本将 `agent-presets/novel` 复制到 `$DSH_HOME/.agent-presets/novel`，将 `novel-tools` 复制到 `$DSH_HOME/node_modules/@local/novel-tools`；`$DSH_HOME` 未设置时回退到 `~/.dsh`。
   > - 幂等：重复运行会覆盖已安装版本，可用于升级。
   > - 若你的 npm 全局前缀正好是 `~/.dsh` 或 `$DSH_HOME`，脚本与现有 node_modules 兼容，不会冲突。

3. **重启 DSH 进程**，新建会话时选择 **小说创作** 预设（或在 DSH 设置 → Agent Presets 中设为默认）。

### 为什么不在 npm 发布

`novel-tools` 工具包与 `novel` 预设**高度绑定**，因此选择随仓库一起分发，而不是发布到 npm registry：

- 工具包内 9 个工具（`novel_entry` / `novel_plot` / `novel_publish` / …）直接封装 `novel-cli` 的命令行参数、输出格式与错误消息，**必须与 preset 中的 `novel_cli.py` 同版本同发布**——两者一旦分离就会因参数或输出不一致而失效
- 包名 `@local/novel-tools` 使用 `@local` 本地 scope（npm 保留给本地安装的示例 scope，无法发布到 registry）
- 随仓库分发（clone / ZIP + 一键脚本）保证用户拿到的 preset 与工具包**永远是一套匹配的版本**，不存在"装了新 preset 却配了旧工具包"的版本漂移问题

## 使用

如果你只想使用此项目，直接对你的agent提出你要创作的主题即可，它会处理好所有准备工作。

1. **初始化项目**：在项目目录运行 `python novel-cli/cli.py init .`（首次会复制 CLI 到项目内）
2. 按 guides 顺序：初始化 → 素材库 → 剧情树 → 逐级创作 → 日志 → 风格 → 去 AI 味 → 写作原则
3. 写作流程：L1 梗概 → 确认 → L2 分卷 → 确认 → L3 事件 → 确认 → L4 正文 → 确认 → `publish` 导出到 `contents/`
4. 在L4正文层，可以对agent生成的md文件进行批注，采用“<...>”的形式。agent会读取其中内容作为提示词，并结合其附近的内容执行要求。

详细命令见 `agent-presets/novel/skills/novel-write/references/cli-cheatsheet.md`。

## 注意事项

1. 此项目只是提供了规范的外部工具，保证agent在小说创作过程中的连续性和一致性，并不能大幅提升agent具体的写作水平。想要提升写作水平，需要使用提取写作风格的技能，让agent浏览大量你指定的文本。或者你也可以将此项目当作辅助你进行小说创作的工具，与agent协商好大致的剧情框架后，可以让它帮助你细化剧情，或是提交你写好的正文让它帮你排除设定矛盾、逻辑冲突。

## 目录结构

```
novel-dsh/
├── install.ps1 / install.sh  ← 一键安装脚本（自动复制到 $DSH_HOME）
├── agent-presets/novel/     ← DSH agent preset（含 preset.yml / agent.cordis.yml / skills/）
│   └── skills/
│       ├── novel-write/         ← 主 skill：guides 01-08 + 参考 + novel_cli.py
│       ├── extract-writing-style/  ← 从参考文本提取风格
│       ├── output-style/        ← 按风格输出
│       └── update-style/        ← 追加/更新风格引例
└── novel-tools/             ← @local/novel-tools 工具包（novel_entry/plot/publish/log/style/status/fact/context/translation 9 个工具；与 preset 高度绑定，故不发布到 npm，见「为什么不在 npm 发布」）
```

## 工作原理

- **三个数据库（风格库可由特定skill整理后迁移）**：`nodes.db`（剧情树/引用/事实/文本替换）、`materials.db`（类型/条目/维度）、`style.db`（风格片段/标签/规则）
- **状态机**：`draft → confirmed → revised → affected → published`，祖先链确认门禁 + 引用网络闭包级联
- **正文即缓存**：`plots/L4/` 是 agent 工作区，`contents/` 是正式产出，编号/格式/替换全由 `publish` 自动处理

## License

MIT
