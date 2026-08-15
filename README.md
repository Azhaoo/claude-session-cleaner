# Claude code 会话清理（Claude Session Cleaner）

纯本地的 Claude Code 会话清理 GUI 工具。

**核心目标**：删除指定会话后，在终端执行 `claude -r`（resume）时，被删会话从历史列表**彻底消失**；其他会话照常显示。


## 特性

- **自动发现会话存储位置**：优先读取环境变量 `CLAUDE_CONFIG_DIR`，否则使用 `~/.claude/`（pathlib 动态取，不写死任何盘符/用户名）
- **树形表两级展示**：
  - 一级 = 项目文件夹：编码名、解码后的真实项目路径（`~` 缩写）、项目是否还存在（`✓ 项目在` / `✗ 项目已删除` 灰显）、会话数、总大小、最后活动时间
  - 二级 = 会话文件：**可读标题**（取自索引 display 元数据，无索引时显示 UUID）、大小、修改时间
- **项目配色**：不同项目自动分配不同浅色背景，一眼区分所属项目；"项目已删除"行保留灰色文字 + 项目底色
- **残留空目录清理**：无会话、无记忆的空壳记录目录（如删除全部会话后留下的 `projects/<编码>/`）显示为「残留空目录」行，勾选即可删除目录本身
- **记忆目录**：`memory/` 子目录按项目显示"含记忆(大小)"，默认不勾选，勾选才连同删除（按项目隔离，不影响其他项目）
- **删除默认移入回收站**（send2trash），可勾选"彻底删除"；删除后自动刷新
- **同步清理完整磁盘足迹**：除 jsonl 外，按会话 UUID 精确匹配清理 `file-history/`、`session-env/`、`tasks/`、`telemetry/`、`debug/`、`todos/`、`jobs/`（含 fork 的 parent-transcript 副本）等关联残留，并更新 `history.jsonl` 索引行与 `projects/*/sessions-index.json` 条目
- **纯本地工具**：无任何网络请求、无统计上报、不收集信息

## 删除机制实测结论

在 Claude Code **2.1.220** 上以真实数据实测（备份 → 删除 → 校验和 → 100% 还原）：

| # | 实验 | 结果 |
|---|------|------|
| 1 | 只删 `projects/<编码>/<uuid>.jsonl`，保留索引 | 会话从 `claude -r` 列表**立即消失** |
| 2 | 删 jsonl 后 `claude --resume <id>` | 报 `No conversation found with session ID` |
| 3 | 保留 jsonl、只删 `history.jsonl` 索引行 | 列表**仍显示**该会话 |
| 4 | 从回收站还原 jsonl | 会话重新出现在列表 |

**结论**：
1. `claude -r` 历史列表**完全由 `projects/<编码>/<uuid>.jsonl` 构建**——列表文本 = jsonl 首条消息、时间 = 文件修改时间、大小 = 文件大小，拾取器会校验文件存在性；`history.jsonl` 不参与列表渲染
2. **删除 jsonl 即彻底消失**；工具仍同步清理 `history.jsonl` 中该 sessionId 的全部行，避免孤儿索引残留（每会话多行，按行 JSON 的 `sessionId` 字段精确匹配）
3. 会话真实路径的**权威解码来源是索引的 `project` 字段**（比目录名反推可靠）；目录名编码规则为 `re.sub(r"[^a-zA-Z0-9]", "-", path)`（如 `F:\Claude code\项目` → `F--Claude-code------`），该解码有损，仅作"尽力还原"的兜底
4. 完整磁盘足迹（除 jsonl 外的关联残留）参考 [ataleckij/claude-chats-delete](https://github.com/ataleckij/claude-chats-delete) 的 storage 模型，按 UUID 精确匹配清理

## 会话存储自动发现

1. 优先读取环境变量 `CLAUDE_CONFIG_DIR`（Claude Code 支持自定义配置目录）；若设置，使用其下的 `projects/` 与 `sessions/`
2. 否则使用 `Path.home()/.claude/projects/` 与 `Path.home()/.claude/sessions/`
3. 会话正文：`<config_dir>/projects/<路径编码>/<会话UUID>.jsonl`；兼容扫描 `<config_dir>/sessions/`
4. 会话索引：`<config_dir>/history.jsonl`（每行 `{display, pastedContents, timestamp, project, sessionId}`，每会话可有多行）
5. 路径编码规则：`\`、`:`、`.` 等非字母数字字符替换为 `-`

## 安装与运行

要求：**Python 3.9+**（tkinter 需可用）

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 安装依赖（仅 send2trash 与 ttkbootstrap 两个）
#    Windows:
venv\Scripts\python -m pip install -r requirements.txt

# 3. 启动
venv\Scripts\python main.py        # Windows

```

## 使用说明

1. 启动后自动扫描会话（顶栏显示配置目录位置）
2. **勾选**：点一级文件夹行的 ☐ 整组全选/取消；二级会话行可单独勾选（部分勾选时一级行显示 ◐）
3. **记忆**：勾选 🧠 memory 行才连带删除该项目记忆目录
4. **空壳**：「残留空目录」行 = 无会话无记忆的记录目录，勾选删除目录本身（仅删目录，绝不碰真实项目文件夹）
5. **删除**：点左下角红色「删除勾选项」→ 确认框列出明细 → 默认移入回收站，勾选「彻底删除」则不可恢复 → 自动刷新
6. 顶栏「刷新」按钮可随时重新扫描；底部状态栏实时显示统计与结果

## 安全边界（铁律）

- 只操作 Claude Code 配置目录（自动发现路径）范围内的 `.jsonl` 会话文件、`history.jsonl` 索引条目、`sessions-index.json`、以及用户明确勾选的 `memory\` 子目录与残留空目录
- **绝不触碰、不列出用户项目文件夹**；**不读取 `.jsonl` 聊天内容**（只读文件名/大小/修改时间；可读标题取自索引 `display` 元数据字段）
- 界面显示路径时用 `~` 代替用户主目录（如 `~/.claude/projects/...`），不暴露真实用户名
- 纯本地工具：无网络请求、无统计上报、不收集信息

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py"
```

覆盖：编码规则、自动发现、扫描（含空壳标记）、索引解码、删除（jsonl/索引/关联残留/memory/空壳/非空防御）、`~` 路径缩写、GUI 树填充/勾选交互/配色/删除流程。

## 许可证

[MIT](./LICENSE)
