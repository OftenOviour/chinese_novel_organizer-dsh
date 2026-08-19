# CLI 命令速查（novel-cli）

运行方式：`python novel-cli/cli.py <命令> [...]`（工作目录为项目根目录）。

## 素材库条目操作

```
entry add    <type> <name>                    创建条目（type 自由，新类型先 type add 登记）
entry get    <name> [--type TYPE]            查看条目全部属性（多行描述逐行显示）
entry set    <name> <dim> <key> <value>       设置/覆盖单个属性值
entry append <name> <dim> <key> <value>       ★ 同一 key 追加一条描述（多行；有效区间写进文本）
entry del    <name> <dim> [--key KEY]         删除维度或单个属性
entry list   [--type TYPE]                    列出条目
entry search <keyword>                        搜索条目名或属性值
```

## 类型字典

```
type add     <kind> <type> <description>      登记 entry/dimension 类型的含义
type list                                     列出全部已登记类型
```

## 剧情树操作

```
plot create  <L1-L4> <title> [--parent ID] [--volume N] [--sort N]
                                              ★ 创建需父节点已确认（数据层强制）
                                              L3/L4 按卷分目录存储（卷号自动继承父节点）
plot get     <id>                             查看节点信息+内容（含状态/affected_reason）
plot list    [--level L1-L4] [--parent ID]    列出节点（affected 有 ⚠ 标记）
plot status  <id> <draft|confirmed|revised>   ★ confirmed 需祖先链全确认；revised 触发
                                              树+引用网络闭包级联；affected 不可手动设置

# 引用关系（L4 主+次引用都存 references 表）
plot ref add <L4_id> <L3_id>                  添加次引用（被引用事件须已确认）
plot ref list <node_id>                       列出引用（→ 正向 / ← 反向）
plot ref del <L4_id> <L3_id>                  删除次引用（主引用与树绑定不可删）
```

状态枚举：`draft` 未确认 / `confirmed` 已确认 / `revised` 内容被改需重新确认 / `affected` 前置变化需核查（级联产生）/ `published` 已发布。

## 事实账本与上下文

```
fact add     <事实文本> [--category completed|revealed|state_changed] [--source 节点id]
                                              记录"已完成事项"（确认/发布后；含来源溯源）
fact list    [--category X] [--source id]     列出事实（写作前看"勿重复写出"）
context      <node_id>                        ★ 写作前组装上下文（父链/引用/事实/风格规则/前后章）
                                              并记录消费关系（改动级联反向定位依据）
```

## 名称映射（translation）

```
translation add <key> <display> [--note 说明] 登记替换（写作时用 {{key}}，发布时替换为 display）
translation set <key> <display>               ★ 改名：改后 translation apply 更新已发布正文
translation list                              查看全部映射
translation del <key>                         删除映射
translation apply [--id 节点]                 重跑已发布 L4 的替换输出
```

> 用户可能改名的实体（角色/地点/道具/专有名词等）一律用 `{{原始名-类型}}` 占位符写作（详见 guides/02）。

## 正文发布（★ 关键——这是 plots/L4/ → contents/ 的唯一路径）

```
publish      <plot_node_id>                   导出 md→txt 到 contents/{卷}/
                                              ★ 文件名：{全书章号}_{卷内章号}_{标题}.txt
                                              章号按 L4 同卷排序计算
                                              例：第三卷第五章 → contents/3/98_5_xxx.txt
```

## 日志操作

```
log append   <level> <op> <target> <note>     追加日志（自动轮转）
log show     [--today|--date YMD|--tail N]    查看日志
log rotate   [--force]                        强制轮转
```

## 维护

```
validate                                       完整性检查
export    [entries|plots|all]                  导出 Markdown 到 entries/ 和 plots/
                                               （entries/plots 仅为人工查看文档；
                                                数据以数据库为准，不做反向导入）
```

## 风格库检索（★ 写 L4 前必用）

```
style overview                                 各维度统计 + Top 标签
style search --tags "对话,省略号" [--any] [--limit N]
                                               按标签搜引例
style expand <fragment_id> [--limit N]         通过共享标签发现相关引例
style get    <fragment_id>                     查看引例详情+标签
style tags   [--dimension 2.X]                 列出标签及频次
style list   [--dimension 2.X] [--tag TAG] [--source]
                                               列出引例摘要
```

## 风格库维护

```
style add    --source <文件> --dimension <2.X> --tags "..." --fragment "..."
              --position "行号" [--note "分析"]
                                               追加自定义引例（source/position 必填）
style import <json_file>                       批量导入（extract 产出）
style init                                     创建空 style.db
```

## 风格库同步

```
运行 update-style skill 或 python novel-cli/update_style.py
  将根目录 style.db 的新增参考引例合并到工作区

运行 output-style skill 或 python novel-cli/output_style.py [--ids "6,7,9"] [--all]
  从工作区提炼引例 → 转为无源 → 覆盖根目录 style.db
```
