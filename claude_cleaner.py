"""Claude 会话清理器 — 核心逻辑模块（无 GUI 依赖，可独立测试）。

纯本地工具：自动发现 Claude Code 的会话存储位置，扫描、列出、删除
会话文件与关联残留。

删除范围（按会话 UUID 精确匹配，全部限定在配置目录内）：
- 主会话文件：projects/<编码>/<uuid>.jsonl、sessions/<uuid>.jsonl
- 同名会话目录：projects/<编码>/<uuid>/（含 subagents/、tool-results/）
- file-history/<uuid>/、session-env/<uuid>/、tasks/<uuid>/、debug/<uuid>.txt
- security/security_warnings_state_<uuid>.json(.lock)（含旧布局根目录文件）
- telemetry/*.<uuid>.*.json（失败的遥测事件残留）
- todos/<uuid>-*.json
- jobs/<前缀>/（state.json 中 sessionId 匹配；fork 的 parent-transcript 副本）
- 索引：history.jsonl 行、projects/*/sessions-index.json 条目

铁律（本模块强制执行）：
- 不读取 .jsonl 聊天内容：只使用文件名/大小/修改时间；可读标题取自
  history.jsonl 索引的 display 字段（即首条用户消息摘要），绝不读会话正文；
- 所有路径自动发现（CLAUDE_CONFIG_DIR 环境变量，或 ~/.claude），
  代码中不写死任何盘符、用户名、设备信息；
- 不触碰 projects/<uuid>/memory/ 项目记忆，除非调用方显式传入。
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__version__ = "1.1.0"

# ---------------------------------------------------------------------------
# 路径编码（与 Claude Code 的行为保持一致，实测验证于 v2.1.220）
# ---------------------------------------------------------------------------


def encode_path(path: str) -> str:
    """Claude Code 的项目路径编码：所有非字母数字字符替换为 '-'。

    实测：'F:\\Claude code项目\\SteamDt' -> 'F--Claude-code---SteamDt'。
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def decode_path_guess(folder_name: str) -> Optional[str]:
    """尽力还原编码文件夹名为原始路径（有损，仅供参考）。

    编码是不可逆的（原路径中的 '-' 与分隔符 '-' 无法区分），所以此函数只做
    启发式还原：Windows 盘符模式 'X--xxx-yyy' -> 'X:\\xxx\\yyy'。
    权威还原请用 history.jsonl 中的 project 字段（见 attach_project_hints）。
    """
    m = re.match(r"^([A-Za-z])--(.*)$", folder_name)
    if not m:
        return None
    drive, rest = m.group(1), m.group(2)
    parts = [p for p in rest.split("-") if p]
    if not parts:
        return None
    return f"{drive}:\\" + "\\".join(parts)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class SessionInfo:
    """一个会话文件（.jsonl）。"""

    path: Path
    session_id: str
    size: int
    mtime: float
    title: Optional[str] = None   # 可读标题（来自索引 display 字段，非会话正文）


@dataclass
class FolderInfo:
    """一个会话文件夹（projects/ 或 sessions/ 下的一级子目录）。"""

    name: str                    # 编码后的文件夹名
    folder_path: Path            # 实际目录
    source: str                  # "projects" | "sessions"
    sessions: list = field(default_factory=list)
    memory_path: Optional[Path] = None
    memory_size: int = 0
    decoded_path: Optional[str] = None    # 尽力还原的项目路径
    decode_confident: bool = False        # True = 来自索引 project 字段且自洽
    empty_shell: bool = False             # True = 无会话无记忆的残留空目录（仅目录壳）

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def total_size(self) -> int:
        return sum(s.size for s in self.sessions)

    @property
    def last_activity(self) -> float:
        """最后活动时间 = 最新会话文件的修改时间。"""
        return max((s.mtime for s in self.sessions), default=0.0)

    @property
    def project_exists(self) -> Optional[bool]:
        """还原路径对应的项目是否仍存在。无法还原时返回 None。"""
        if not self.decoded_path:
            return None
        try:
            return Path(self.decoded_path).exists()
        except OSError:
            return None


# ---------------------------------------------------------------------------
# 配置目录自动发现
# ---------------------------------------------------------------------------


def discover_config_dir() -> Path:
    """自动发现 Claude Code 配置目录。

    优先 CLAUDE_CONFIG_DIR 环境变量；否则使用 ~/.claude。
    """
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".claude"


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------


def _dir_size(path: Path) -> int:
    """递归统计目录内文件总大小（不读文件内容）。"""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def scan(config_dir: Path) -> list:
    """扫描配置目录下的 projects/ 与 sessions/，返回文件夹列表。

    只统计 *.jsonl 会话文件（文件名/大小/修改时间），不读取内容。
    跳过 'agent-' 前缀的会话文件（后台 agent 会话，避免误删）。
    """
    folders: list = []
    for base_name in ("projects", "sessions"):
        base = config_dir / base_name
        if not base.is_dir():
            continue
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            folder = FolderInfo(name=child.name, folder_path=child, source=base_name)
            try:
                for f in sorted(child.glob("*.jsonl")):
                    if not f.is_file() or f.stem.startswith("agent-"):
                        continue
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    folder.sessions.append(
                        SessionInfo(path=f, session_id=f.stem, size=st.st_size, mtime=st.st_mtime)
                    )
            except OSError:
                pass
            mem = child / "memory"
            if mem.is_dir():
                folder.memory_path = mem
                folder.memory_size = _dir_size(mem)
            # 无会话且无记忆的目录 = 残留空壳（仅目录本身，可整体删除）；
            # 0 会话但有记忆的保留显示（用户可勾选清理记忆）
            if not folder.sessions and folder.memory_path is None:
                folder.empty_shell = True
            folders.append(folder)
    return folders


def load_history_index(config_dir: Path):
    """读取 history.jsonl 索引。

    返回 (sessionId -> project 映射, sessionId -> display 摘要映射, 文件路径)。
    索引行格式（实测 v2.1.220）：{"display":…, "pastedContents":…,
    "timestamp":…, "project":<真实路径>, "sessionId":<UUID>}
    只提取 sessionId / project / display 字段（display 即 claude -r 列表显示
    的首条消息摘要，用于生成可读标题；不读取会话 .jsonl 正文）。
    """
    hf = config_dir / "history.jsonl"
    if not hf.is_file():
        return {}, {}, hf
    mapping: dict = {}
    display_map: dict = {}
    try:
        text = hf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}, {}, hf
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = d.get("sessionId")
        if sid:
            if sid not in mapping:
                mapping[sid] = d.get("project")
            if sid not in display_map and isinstance(d.get("display"), str) and d["display"].strip():
                display_map[sid] = d["display"].strip()
    return mapping, display_map, hf


def attach_project_hints(config_dir: Path, folders: list) -> None:
    """用索引还原真实项目路径与可读标题，就地修改 folders。

    路径还原先做自洽校验：encode(project) == 文件夹名才采信；
    无法还原的文件夹退回启发式解码（decode_path_guess）。
    """
    mapping, display_map, _ = load_history_index(config_dir)
    for folder in folders:
        candidates: dict = {}
        for s in folder.sessions:
            pr = mapping.get(s.session_id)
            if pr:
                candidates[pr] = candidates.get(pr, 0) + 1
        if candidates:
            best = max(candidates, key=candidates.get)
            if encode_path(best) == folder.name:
                folder.decoded_path = best
                folder.decode_confident = True
        if folder.decoded_path is None:
            folder.decoded_path = decode_path_guess(folder.name)
        for s in folder.sessions:
            s.title = display_map.get(s.session_id)


# ---------------------------------------------------------------------------
# 关联残留收集（跟随参考项目 claude-chats-delete 的清理清单）
# ---------------------------------------------------------------------------


def _is_inside(base: Path, target: Path) -> bool:
    """防御性校验：target 必须位于 base 内，防止路径越界。"""
    try:
        return target.resolve().is_relative_to(base.resolve())
    except OSError:
        return False


def find_related_files(config_dir: Path, session_id: str) -> list:
    """收集一个会话 UUID 的全部关联残留（存在才返回）。

    参考 claude-chats-delete（v2.1.220 实测）：会话的磁盘足迹不止 jsonl，
    还包括同名会话目录、file-history、session-env、tasks、debug、security、
    telemetry 失败事件、todos、jobs 后台任务状态等。
    返回的路径全部位于 config_dir 内。
    """
    if not session_id:
        return []
    found: list = []

    def _add(p: Path):
        if p.exists() and _is_inside(config_dir, p):
            found.append(p)

    # projects/<folder>/<uuid>/ 同名会话目录（subagents/、tool-results/ 在其中）
    projs = config_dir / "projects"
    if projs.is_dir():
        for folder in projs.iterdir():
            if folder.is_dir():
                _add(folder / session_id)                      # 同名目录
                _add(folder / "memory" / session_id)           # 会话级记忆残留（如有）
    # sessions/<uuid>/（v2 目录下同名子目录）
    sessions_base = config_dir / "sessions"
    if sessions_base.is_dir():
        _add(sessions_base / session_id)

    # 顶层各残留目录
    _add(config_dir / "file-history" / session_id)
    _add(config_dir / "session-env" / session_id)
    _add(config_dir / "tasks" / session_id)
    _add(config_dir / "debug" / f"{session_id}.txt")
    for ext in (".json", ".lock"):
        _add(config_dir / "security" / f"security_warnings_state_{session_id}{ext}")
    _add(config_dir / f"security_warnings_state_{session_id}.json")  # 旧布局（根目录）

    # telemetry 失败事件：1p_failed_events.<session-uuid>.<id>.json（uuid 为完整点分隔组件）
    tele_dir = config_dir / "telemetry"
    if tele_dir.is_dir():
        try:
            for m in tele_dir.glob(f"*.{session_id}.*.json"):
                _add(m)
        except OSError:
            pass

    # todos 旧布局
    todos_dir = config_dir / "todos"
    if todos_dir.is_dir():
        try:
            for m in todos_dir.glob(f"{session_id}-*.json"):
                _add(m)
        except OSError:
            pass

    # jobs 后台任务状态：目录名是 uuid 前 8 位，须读 state.json 的 sessionId 确认；
    # fork 任务持有父会话的 transcript 副本，只删副本，不动 fork 自身
    jobs_dir = config_dir / "jobs"
    if jobs_dir.is_dir():
        for job in jobs_dir.iterdir():
            if not job.is_dir():
                continue
            state_file = job / "state.json"
            if not state_file.is_file():
                continue
            try:
                st = json.loads(state_file.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                continue
            if st.get("sessionId") == session_id:
                _add(job)
            elif st.get("forkParentSessionId") == session_id:
                _add(job / "tmp" / "parent-transcript.jsonl")

    return found


def _update_sessions_index(config_dir: Path, removed_ids: set) -> int:
    """从 projects/*/sessions-index.json 中移除已删会话条目（存在才动）。"""
    removed = 0
    projs = config_dir / "projects"
    if not projs.is_dir():
        return 0
    for folder in projs.iterdir():
        if not folder.is_dir():
            continue
        idx = folder / "sessions-index.json"
        if not idx.is_file():
            continue
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            entries = data.get("entries")
            if not isinstance(entries, list):
                continue
            new_entries = [e for e in entries if e.get("sessionId") not in removed_ids]
            if len(new_entries) != len(entries):
                data["entries"] = new_entries
                idx.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                removed += len(entries) - len(new_entries)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    return removed


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------


@dataclass
class DeleteResult:
    ok_sessions: list = field(default_factory=list)          # 已删除的 jsonl 文件名
    removed_index_lines: int = 0                             # 清理的 history.jsonl 行数
    removed_related: list = field(default_factory=list)      # 已清理的关联残留路径
    removed_memory: list = field(default_factory=list)       # 已删除的 memory 目录
    removed_empty_dirs: list = field(default_factory=list)   # 已删除的残留空壳目录
    sessions_index_removed: int = 0                          # sessions-index.json 移除条目数
    failed: list = field(default_factory=list)               # [(路径, 原因), …]


def _delete_one(target: Path, permanent: bool, send2trash) -> None:
    if permanent:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    else:
        send2trash.send2trash(str(target))


def delete_sessions(
    config_dir: Path,
    session_paths: list,
    memory_paths: list,
    permanent: bool = False,
    empty_dir_paths: list = None,
) -> DeleteResult:
    """删除会话 jsonl（+关联残留与索引条目）、memory 目录与残留空壳目录。

    permanent=False（默认）：移入回收站（send2trash）；
    permanent=True：彻底删除，不进回收站。

    顺序：
      1. 删除选中的 .jsonl 会话文件；
      2. 清理这些 sessionId 的关联残留（file-history/session-env/tasks/
         telemetry/jobs 等，见 find_related_files）；
      3. 从 history.jsonl 移除对应行、从 projects/*/sessions-index.json 移除条目；
      4. 删除勾选的 memory 目录；
      5. 删除勾选的残留空壳目录（empty_dir_paths，仅目录本身）。
    单个文件失败不影响其余，失败项记录在 result.failed。
    """
    result = DeleteResult()
    hist = config_dir / "history.jsonl"

    # send2trash：移入回收站所需（permanent=False 时使用）。提前导入，
    # 避免只删空壳/记忆（无会话文件）时分支内 import 未执行导致未定义。
    try:
        import send2trash
    except ImportError as e:  # pragma: no cover
        result.failed.append(("send2trash", f"缺少 send2trash 库（无法移入回收站）：{e}"))
        return result

    # 1) 删除 jsonl 主文件
    removed_ids: set = set()
    for p in session_paths:
        try:
            _delete_one(p, permanent, send2trash)
            removed_ids.add(p.stem)
            result.ok_sessions.append(p.name)
        except OSError as e:
            result.failed.append((str(p), f"删除失败：{e}"))

    # 3) 清理关联残留
    related_paths: list = []
    for sid in sorted(removed_ids):
        related_paths.extend(find_related_files(config_dir, sid))
    for p in related_paths:
        try:
            _delete_one(p, permanent, send2trash)
            result.removed_related.append(str(p))
        except OSError as e:
            result.failed.append((str(p), f"关联残留删除失败：{e}"))

    # 4) 清理索引
    if removed_ids and hist.is_file():
        try:
            text = hist.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            result.failed.append((str(hist), f"索引读取失败：{e}"))
            text = None
        if text is not None:
            out_lines = []
            removed = 0
            for line in text.splitlines():
                drop = False
                if line.strip():
                    try:
                        if json.loads(line).get("sessionId") in removed_ids:
                            drop = True
                    except json.JSONDecodeError:
                        pass
                if drop:
                    removed += 1
                else:
                    out_lines.append(line)
            if removed:
                try:
                    new_text = "\n".join(out_lines)
                    if out_lines:
                        new_text += "\n"
                    hist.write_text(new_text, encoding="utf-8")
                    result.removed_index_lines = removed
                except OSError as e:
                    result.failed.append((str(hist), f"索引清理失败：{e}"))
    if removed_ids:
        result.sessions_index_removed = _update_sessions_index(config_dir, removed_ids)

    # 5) memory 目录
    for p in memory_paths:
        try:
            _delete_one(p, permanent, send2trash)
            result.removed_memory.append(str(p))
        except OSError as e:
            result.failed.append((str(p), f"记忆目录删除失败：{e}"))

    # 6) 残留空壳目录（仅目录本身；防御：只删确认完全空的目录）
    for p in (empty_dir_paths or []):
        if not p.is_dir():
            continue
        try:
            has_content = any(p.iterdir())
        except OSError:
            has_content = True
        if has_content:
            result.failed.append((str(p), "目录非空，跳过（仅清理空壳目录）"))
            continue
        try:
            _delete_one(p, permanent, send2trash)
            result.removed_empty_dirs.append(str(p))
        except OSError as e:
            result.failed.append((str(p), f"空壳目录删除失败：{e}"))

    return result


# ---------------------------------------------------------------------------
# 展示辅助（无用户信息泄漏）
# ---------------------------------------------------------------------------


def display_path(path_str: str) -> str:
    """把用户主目录缩写为 ~，避免在界面上暴露真实用户名/主目录。"""
    home = str(Path.home())
    norm = os.path.normpath(path_str)
    if norm.casefold().startswith(home.casefold()) and len(norm) > len(home):
        return "~" + norm[len(home):]
    return norm


def human_size(num: int) -> str:
    """字节数 -> 人性化显示。"""
    if num < 1024:
        return f"{num} B"
    for unit in ("KB", "MB", "GB"):
        num /= 1024.0
        if num < 1024:
            return f"{num:.1f} {unit}"
    return f"{num:.1f} TB"
