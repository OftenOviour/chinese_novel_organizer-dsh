---
name: update-style
description: "Use when syncing new reference exemplars from the root style.db into the workspace novel-cli/style.db. Triggers: 更新风格库、同步风格、update style."
---

# 更新风格库

将根目录 `style.db`（由 extract-writing-style 生成）中的新增参考引例合并到工作区 `novel-cli/style.db`。

## 执行

```bash
python novel-cli/update_style.py
```

## 合并规则

- 只处理 `source_file IS NULL` 的引例（extract 产出的参考数据）
- 比较引例文本：工作区已存在相同文本 → 跳过
- 新的引例 → 插入，同时复制其所有标签
- 工作区自定义引例（`source_file IS NOT NULL`）→ 原样保留

## 典型场景

1. 用户用新的参考文本运行 extract-writing-style → 根目录 `style.db` 更新
2. 运行 update-style skill → 新增引例合并到工作区
3. 原有自定义引例不受影响
