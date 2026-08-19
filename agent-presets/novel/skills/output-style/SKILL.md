---
name: output-style
description: "Use when distilling the workspace style library: review custom exemplars for general reference value, export source-free fragments, and overwrite the root style.db. Triggers: 输出风格库、提炼风格、导出风格、output style."
---

# 输出风格库

从工作区 `novel-cli/style.db` 提炼核心引例，输出并覆盖根目录 `style.db`。

## 提炼规则

1. **所有无源引例自动保留**——无论是否有重复，从工作区原样搬运到根目录
2. **自定义引例（有来源的）需精选**——只提升那些具有通用参考价值的
3. 输出后所有引例的 `source_file` 和 `position` 被清空——转为无源参考数据

## 流程

### Step 1：审查自定义引例

```bash
python novel-cli/cli.py style list --source
```

逐一评估每条自定义引例是否具有通用参考价值：
- 具有普遍性的风格特征 → 入选
- 仅适用于特定章节的一次性用法 → 不入选

### Step 2：执行导出

```bash
# 仅输出无源引例（自定义的一条都不提升）
python novel-cli/output_style.py

# 输出无源引例 + 指定的自定义引例
python novel-cli/output_style.py --ids 6,7,9

# 输出全部引例
python novel-cli/output_style.py --all
```

### Step 3：确认结果

脚本会报告导出数量。根目录 `style.db` 被完全覆盖，可供其他项目复用或版本管理。
