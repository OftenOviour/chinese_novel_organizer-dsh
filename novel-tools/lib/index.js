import { defineTool } from "@deepseek-ai/dsh-tools"

/**
 * @local/novel-tools — structured tools wrapping novel-cli for the novel agent preset.
 *
 * Every tool executes `python novel-cli/cli.py <args>` in the calling agent's
 * session workspace (novel-cli must live at `<cwd>/novel-cli/cli.py`). All
 * workflow gates (tree confirmation order, cascade invalidation, publish-only-
 * when-confirmed) are enforced by the CLI data layer; these tools map
 * structured arguments to exact argv and pass the CLI's detailed rejections
 * through unchanged.
 */
const name = "novel-tools"
const inject = ["subprocess", "tools"]

function apply(ctx) {
  /** Build a CLI runner bound to one tool execution's session workspace. */
  const runner = (exec) => {
    const root = exec?.agent?.session?.header?.cwd
    return {
      async run(args) {
        const sub = ctx.get("subprocess")
        if (sub === undefined) return "subprocess service unavailable in this runtime"
        if (!root) return "无法确定会话工作目录（session cwd 缺失）"
        const script = root + "/novel-cli/cli.py"
        let python
        try {
          python = await sub.resolveExecutable("python")
        } catch (error) {
          return `python not resolvable: ${error.message}`
        }
        let proc
        try {
          proc = sub.spawn({
            argv: [python, script, ...args],
            cwd: root,
            stdio: {
              stdin: "ignore",
              stdout: { maxBytes: 262144, spill: { maxBytes: 1048576 } },
              stderr: { maxBytes: 65536, spill: { maxBytes: 262144 } },
            },
            graceMs: 5000,
            ...(exec?.signal ? { signal: exec.signal } : {}),
          })
        } catch (error) {
          return `spawn failed: ${error.message}`
        }
        try {
          const outcome = await proc.done
          const read = (r) => (r ? r.readFrom(0).text : "")
          const out = read(proc.collected.stdout)
          const err = read(proc.collected.stderr)
          const text = [out, err].filter(Boolean).join("\n[stderr]\n").trim() || "(no output)"
          return outcome.exitCode === 0 ? text : `[exit code: ${outcome.exitCode}]\n${text}`
        } catch (error) {
          return `run failed: ${error.message}`
        }
      },
    }
  }

  const need = (args, key) => {
    if (args[key] === undefined || args[key] === "") throw new Error(`missing required field "${key}"`)
    return String(args[key])
  }

  const register = (definition) => {
    ctx.tools.register(defineTool(definition))
  }

  register({
    name: "novel_entry",
    description: "Manage material-library entries (type is free: character/location/item/concept/scene/foreshadow…; register new types with novel_type). op: add|get|set|append|del|list|search. add needs type+name; set needs name+dim+key+value (overwrites); append needs name+dim+key+value (adds one more description line under the same key, write validity ranges in the text); get needs name (type optional); del needs name+dim (key optional); search needs keyword.",
    parameters: {
      op: { type: "string", required: true },
      name: { type: "string" },
      type: { type: "string" },
      dim: { type: "string" },
      key: { type: "string" },
      value: { type: "string" },
      keyword: { type: "string" },
    },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        const op = need(args, "op")
        let argv
        if (op === "add") argv = ["entry", "add", need(args, "type"), need(args, "name")]
        else if (op === "get") argv = ["entry", "get", need(args, "name")].concat(args.type ? ["--type", String(args.type)] : [])
        else if (op === "set") argv = ["entry", "set", need(args, "name"), need(args, "dim"), need(args, "key"), need(args, "value")]
        else if (op === "append") argv = ["entry", "append", need(args, "name"), need(args, "dim"), need(args, "key"), need(args, "value")]
        else if (op === "del") argv = ["entry", "del", need(args, "name"), need(args, "dim")].concat(args.key ? ["--key", String(args.key)] : [])
        else if (op === "list") argv = ["entry", "list"].concat(args.type ? ["--type", String(args.type)] : [])
        else if (op === "search") argv = ["entry", "search", need(args, "keyword")]
        else throw new Error(`unknown op "${op}"`)
        return await runner(exec).run(argv)
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_type",
    description: "Manage the type dictionary (types table): every custom entry/dimension type should be registered so any agent can understand it. op: add|list. add needs kind (entry|dimension) + type + description; list shows all registered types.",
    parameters: {
      op: { type: "string", required: true },
      kind: { type: "string" },
      type: { type: "string" },
      description: { type: "string" },
    },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        const op = need(args, "op")
        if (op === "add") return await runner(exec).run(["type", "add", need(args, "kind"), need(args, "type"), need(args, "description")])
        if (op === "list") return await runner(exec).run(["type", "list"])
        throw new Error(`unknown op "${op}"`)
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_plot",
    description: "Manage plot-tree nodes (L1 全书梗概 / L2 分卷梗概 / L3 事件梗概 / L4 正文) and reference relations. Gates are enforced by the data layer and rejections carry details: creating L2/L3/L4 requires a confirmed parent of the right level (L3←L2, L4←L3); confirming requires the whole ancestor chain confirmed; setting revised triggers tree + reference-network closure cascade; affected/published cannot be set manually. op: create|get|list|status|ref_add|ref_list|ref_del. create needs level (L1-L4) + title, parent required for L2-L4, volume/sort optional; get needs id; status needs id + status (draft|confirmed|revised); ref_add needs id (L4) + ref_id (L3); ref_list needs id; ref_del needs id (L4) + ref_id (L3).",
    parameters: {
      op: { type: "string", required: true },
      level: { type: "string" },
      title: { type: "string" },
      parent: { type: "string" },
      volume: { type: "integer" },
      sort: { type: "integer" },
      id: { type: "string" },
      status: { type: "string" },
      ref_id: { type: "string" },
    },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        const op = need(args, "op")
        if (op === "create") {
          const argv = ["plot", "create", need(args, "level"), need(args, "title")]
            .concat(args.parent !== undefined ? ["--parent", String(args.parent)] : [])
            .concat(args.volume !== undefined ? ["--volume", String(args.volume)] : [])
            .concat(args.sort !== undefined ? ["--sort", String(args.sort)] : [])
          return await runner(exec).run(argv)
        }
        if (op === "get") return await runner(exec).run(["plot", "get", need(args, "id")])
        if (op === "list") return await runner(exec).run(["plot", "list"].concat(args.level ? ["--level", String(args.level)] : [], args.parent ? ["--parent", String(args.parent)] : []))
        if (op === "status") return await runner(exec).run(["plot", "status", need(args, "id"), need(args, "status")])
        if (op === "ref_add") return await runner(exec).run(["plot", "ref", "add", need(args, "id"), need(args, "ref_id")])
        if (op === "ref_list") return await runner(exec).run(["plot", "ref", "list", need(args, "id")])
        if (op === "ref_del") return await runner(exec).run(["plot", "ref", "del", need(args, "id"), need(args, "ref_id")])
        throw new Error(`unknown op "${op}"`)
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_publish",
    description: "Publish an L4 node to contents/ — the ONLY path from plots/L4 to the formal output. Data-layer gated: only confirmed nodes publish; affected/revised/draft are rejected with instructions. Call only after the user confirms the chapter. Needs id (plot node id).",
    parameters: { id: { type: "string", required: true } },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        return await runner(exec).run(["publish", need(args, "id")])
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_log",
    description: "Append to or read the collaboration log. Read log show --tail 20 before starting work. op: append|show. append needs level+op+target+note; show optional tail (integer) or date (YMD).",
    parameters: {
      op: { type: "string", required: true },
      level: { type: "string" },
      op: { type: "string" },
      target: { type: "string" },
      note: { type: "string" },
      tail: { type: "integer" },
      date: { type: "string" },
    },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        const op = need(args, "op")
        if (op === "append") return await runner(exec).run(["log", "append", need(args, "level"), need(args, "op"), need(args, "target"), need(args, "note")])
        if (op === "show") return await runner(exec).run(["log", "show"].concat(args.tail !== undefined ? ["--tail", String(args.tail)] : [], args.date ? ["--date", String(args.date)] : []))
        throw new Error(`unknown op "${op}"`)
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_style",
    description: "Query the style library before writing L4 prose. op: rule_add|rule_list|rule_del|search|get|expand|tags|list|overview. rule_add needs action (keep|weaken) + content, optional ref (evidence fragment id) and note; rule_list optional action filter; rule_del needs id. search needs tags (comma-separated), optional any (boolean) and limit; get/expand need id; tags/list optional dimension (e.g. 2.4) and tag; list also optional source (boolean).",
    parameters: {
      op: { type: "string", required: true },
      action: { type: "string" },
      content: { type: "string" },
      ref: { type: "string" },
      note: { type: "string" },
      tags: { type: "string" },
      any: { type: "boolean" },
      limit: { type: "integer" },
      id: { type: "string" },
      dimension: { type: "string" },
      tag: { type: "string" },
      source: { type: "boolean" },
    },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        const op = need(args, "op")
        if (op === "rule_add") return await runner(exec).run(["style", "rule", "add", need(args, "action"), need(args, "content")].concat(args.ref ? ["--ref", String(args.ref)] : [], args.note ? ["--note", String(args.note)] : []))
        if (op === "rule_list") return await runner(exec).run(["style", "rule", "list"].concat(args.action ? ["--action", String(args.action)] : []))
        if (op === "rule_del") return await runner(exec).run(["style", "rule", "del", need(args, "id")])
        if (op === "search") return await runner(exec).run(["style", "search", "--tags", need(args, "tags")].concat(args.any ? ["--any"] : [], args.limit !== undefined ? ["--limit", String(args.limit)] : []))
        if (op === "get") return await runner(exec).run(["style", "get", need(args, "id")])
        if (op === "expand") return await runner(exec).run(["style", "expand", need(args, "id")].concat(args.limit !== undefined ? ["--limit", String(args.limit)] : []))
        if (op === "tags") return await runner(exec).run(["style", "tags"].concat(args.dimension ? ["--dimension", String(args.dimension)] : []))
        if (op === "list") return await runner(exec).run(["style", "list"].concat(args.dimension ? ["--dimension", String(args.dimension)] : [], args.tag ? ["--tag", String(args.tag)] : [], args.source ? ["--source"] : []))
        if (op === "overview") return await runner(exec).run(["style", "overview"])
        throw new Error(`unknown op "${op}"`)
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_fact",
    description: "Record and query the fact ledger — irreversible facts confirmed by the story so far, with source-node tracing for reverse impact analysis. Call fact add after a node is confirmed/published (what has happened, so later writing does not repeat it); call fact list before writing to see 'already happened — do not write again'. op: add|list. add needs text, optional category (completed|revealed|state_changed) and source (node id); list optional category and source filters.",
    parameters: {
      op: { type: "string", required: true },
      text: { type: "string" },
      category: { type: "string" },
      source: { type: "string" },
    },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        const op = need(args, "op")
        if (op === "add") return await runner(exec).run(["fact", "add", need(args, "text")].concat(args.category ? ["--category", String(args.category)] : [], args.source ? ["--source", String(args.source)] : []))
        if (op === "list") return await runner(exec).run(["fact", "list"].concat(args.category ? ["--category", String(args.category)] : [], args.source ? ["--source", String(args.source)] : []))
        throw new Error(`unknown op "${op}"`)
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_context",
    description: "Assemble the writing context for a node in one shot: parent chain, referenced events (main/secondary), the fact ledger (already-happened items, do not repeat), style rules (KEEP/WEAKEN), and previous/next chapter. Records which facts this node consumes (enables precise reverse impact when an upstream node changes). Call before writing L3/L4 content. Needs id (node id).",
    parameters: { id: { type: "string", required: true } },
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(args, exec) {
      try {
        return await runner(exec).run(["context", need(args, "id")])
      } catch (error) {
        return `invalid arguments: ${error.message}`
      }
    },
  })

  register({
    name: "novel_status",
    description: "Show the current workflow stage of the novel project: node counts per level, the level with unconfirmed nodes, AFFECTED nodes requiring review (long-term reminders after upstream edits, with reasons), confirmed-but-unpublished L4 drafts, and the suggested next step. Read-only; call at work start and before writing new content.",
    parameters: {},
    output: { schema: { type: "string" }, render: (_a, v) => [{ type: "text", text: String(v) }] },
    async execute(_args, exec) {
      const out = await runner(exec).run(["plot", "list"])
      // CLI 报错时透传（项目未初始化/cli.py 缺失/命令错误），不要误报"尚未创建任何节点"
      if (out.startsWith("[exit code") || out.includes("[novel-cli] 错误") || out.includes("[novel-cli] 未知命令") || out.includes("[novel-cli] 无法")) {
        return out
      }
      const re = /^[\uFEFF ]*[◇●○◎⚠] \[(L[1-4])\] #(\d+) (.*)  \((\w+)\)(?: — (.*))?$/
      const nodes = []
      for (const raw of out.split(/\r?\n/)) {
        const line = raw.replace(/\s+$/, "")
        const m = line.match(re)
        if (m) {
          let title = m[3].trim()
          title = title
            .replace(/\s+V\d+ #\d+\s*$/, "")
            .replace(/\s+V\d+\s*$/, "")
            .replace(/\s*#\d+\s*$/, "")
          nodes.push({ level: m[1], id: m[2], title, status: m[4], reason: m[5] || "" })
        }
      }
      const byLevel = { L1: [], L2: [], L3: [], L4: [] }
      for (const n of nodes) if (byLevel[n.level]) byLevel[n.level].push(n)
      const lines = []
      lines.push(`节点统计: ${["L1", "L2", "L3", "L4"].map((l) => `${l}:${byLevel[l].length}`).join("  ")}`)
      if (nodes.length === 0) {
        lines.push("当前阶段: 尚未创建任何节点 — 先 plot create L1 \"全书梗概\"")
      } else {
        const frontier = ["L1", "L2", "L3", "L4"].find((l) => byLevel[l].some((n) => n.status === "draft" || n.status === "revised"))
        if (!frontier) lines.push(`当前阶段: 所有节点均已确认${byLevel.L4.length ? " — 等待创作/发布 L4 正文" : ""}`)
        else lines.push(`当前阶段: ${frontier}（存在未确认节点）`)
        const affected = nodes.filter((n) => n.status === "affected")
        if (affected.length) {
          lines.push(`⚠ 受影响节点 (${affected.length} 个，需核查衔接):`)
          for (const n of affected.slice(0, 10)) lines.push(`  #${n.id} [${n.level}] ${n.title}${n.reason ? " — " + n.reason : ""}`)
        }
        const unconfirmed = nodes.filter((n) => n.status === "draft" || n.status === "revised")
        if (unconfirmed.length) {
          lines.push(`未确认节点 (${unconfirmed.length}):`)
          for (const n of unconfirmed.slice(0, 8)) lines.push(`  #${n.id} [${n.level}] ${n.title} (${n.status})`)
        }
        const confirmedL4 = byLevel.L4.filter((n) => n.status === "confirmed")
        if (confirmedL4.length) lines.push(`待发布 L4（已确认 ${confirmedL4.length} 个）: ${confirmedL4.map((n) => `#${n.id}`).join(" ")} — 用 novel_publish 发布`)
        const draftL4 = byLevel.L4.filter((n) => n.status === "draft")
        if (draftL4.length) lines.push(`L4 草稿未确认 (${draftL4.length} 个): 用户确认后用 plot status <id> confirmed`)
      }
      const suggest = () => {
        if (nodes.some((n) => n.status === "affected")) return "下一步: 先核查受影响（affected）节点的剧情衔接，核查后确认"
        if (byLevel.L1.some((n) => n.status !== "confirmed")) return "下一步: 确认 L1 全书梗概"
        if (byLevel.L2.some((n) => n.status !== "confirmed")) return "下一步: 确认 L2 分卷梗概（或先创建缺失的 L2）"
        if (byLevel.L3.some((n) => n.status !== "confirmed")) return "下一步: 确认 L3 事件梗概，之后才能创建其 L4"
        if (byLevel.L4.some((n) => n.status === "confirmed")) return "下一步: 发布已确认的 L4 节点"
        if (byLevel.L4.some((n) => n.status === "draft")) return "下一步: 请用户确认 L4 草稿"
        return "下一步: 所有层级完成，等待用户指示"
      }
      lines.push(suggest())
      return lines.join("\n")
    },
  })
}

export { apply, inject, name }
