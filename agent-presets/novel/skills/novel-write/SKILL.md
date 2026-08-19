---
name: novel-write
description: "Use when creating or continuing a novel project with the staged L1–L4 tree workflow: initialize the project, plan level by level (L1 全书梗概 → L2 分卷梗概 → L3 事件梗概 → L4 正文), keep consistency via the material library, maintain long-arc coherence via the plot tree, and publish to contents/. Triggers: 写小说、小说创作、开始创作、大纲规划、继续写、novel."
---

# 小说创作管理

## 核心工具：novel-cli

所有**数据操作**（素材库条目、剧情树节点、日志、正文发布）通过 CLI 工具执行：

```
python novel-cli/cli.py <命令> [...]
```

CLI 底层使用 SQLite + 纯文本日志，保证格式一致性和操作原子性。**剧情树的确认门禁与级联失效由数据层强制，不要试图绕过。**

**要执行具体命令时，先读 [CLI 命令速查](references/cli-cheatsheet.md)，不要凭记忆拼命令。**

## 使用流程

按顺序读取 `guides/`，**用到哪步读哪步**：

1. [初始化流程](guides/01-初始化.md) — CLI 初始化项目骨架 + 登记内置类型
2. [素材库规范](guides/02-素材库.md) — ★ 类型自由 + types 表 + 多行属性；所有会被复用的内容入库
3. [剧情树规范](guides/03-剧情树.md) — 树状结构 + L1–L4 定义 + 状态/级联（affected）
4. [逐级创作规则](guides/04-逐级创作.md) — ★ 核心约束：树状确认、单节点创作、查表、批量协作
5. [日志系统](guides/05-日志系统.md) — CLI log 子命令
6. [风格参考](guides/06-风格参考.md) — style.db 多标签检索 + 追加自定义引例
7. [去 AI 味清单](guides/07-去AI味.md) — ★ 每章写完后必查：删过度修饰/破四字格律/去模板感/情绪落到细节
8. [写作原则](guides/08-写作原则.md) — ★ 正向引导：冰山叙事（设计时布局信息揭露、写作时用暗示替代直白交代）；与 07 负向约束相对

项目整体目录结构见 [项目结构](references/project-structure.md)。

## 关键规则

1. **plots/L4/ 是 agent 缓存工作区**，正文写在这里的 `.md` 文件中；用户确认后运行 `publish` 导出到 `contents/`
2. **contents/ 是正式产出**，由 CLI 自动管理编号和格式，agent 不要手动操作此目录
3. **所有数据操作走 CLI**（命令见 [速查表](references/cli-cheatsheet.md)），不要手动建文件夹、写 JSON、拼 Markdown 表格
4. **单节点创作**：一次只填一个节点；填完只关注与前后两个同级节点的衔接；内容超出父节点范围时去修改父节点，而不是在子节点里扩张
5. **修改节点必须置 revised**：改完任何节点立即 `plot status <id> revised`，触发级联；被级联标为 affected 的节点要持续核查衔接（`novel_status`/`plot list` 会一直列出，直到重新确认）
6. **素材库回填**：写作中产生的新设定、变化（写明区间）、场景建模、伏笔状态（open/resolved），写完立即 `entry set` / `entry append`
7. **每章写作后执行去 AI 味清单**（[guides/07](guides/07-去AI味.md)）：删过度修饰/破四字格律/去模板感/情绪落到动作细节，再做风格自查；修改后复读对话段与情绪段
8. **开工先读日志**：`python novel-cli/cli.py log show --tail 20`
9. **设定冲突先问再改**：用户提出新的剧情/设定时，若与已有且用户未一并提到的剧情/设定冲突，**不要盲目遵从**——先阐明冲突点与不合理之处，再向用户提问：**无视风险继续 / 撤回新设定 / 其它解决方案**（详见 [guides/04](guides/04-逐级创作.md)"设定冲突处理"）
10. **冰山叙事（正向约束）**：已知信息不得一次性交代——规划（L1–L3）时把每条关键信息拆成散布的暗示点，写作（L4）时写事实不写结论、一次一个暗示，禁止把小说写成实验报告（详见 [guides/08](guides/08-写作原则.md)）
11. **中文标点**：生成中文小说一律使用中文标点——逗号、句号、顿号、冒号、引号（“ ”）、问号、感叹号、省略号（……）、破折号（——）；禁止在中文正文混用英文半角标点（, . : ; " ' ? !）
12. **正文行内指令（`<>` 约定）**：用户可在正文任意位置用 `<` 与 `>` 包围的区域插入指令（例如 `<这里放慢节奏>`）；读到即按指令调整正文，**执行完毕后删除整个 `<>` 块（含尖括号）**，正式文本不残留任何指令痕迹（详见 [guides/04](guides/04-逐级创作.md)"行内指令"）
