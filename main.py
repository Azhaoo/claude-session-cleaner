#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude 会话清理器 — GUI 入口（ttkbootstrap，浅色 cosmo 主题）。

用法：
    python main.py             正常启动
    python main.py --smoke-test  自检模式（构建窗口后自动退出）

纯本地工具：无任何网络请求、无统计上报、不收集信息。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import messagebox

import ttkbootstrap as ttk

import claude_cleaner as cc

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

FONT = ("Microsoft YaHei UI", 12)
FONT_HEAD = ("Microsoft YaHei UI", 12, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 10)

CHECK_ON = "☑"
CHECK_OFF = "☐"
CHECK_HALF = "◐"

COLS = ("check", "name", "path", "status", "count", "size", "time")
HEADINGS = {
    "check": "勾选",
    "name": "名称",
    "path": "项目路径",
    "status": "状态",
    "count": "会话数",
    "size": "大小",
    "time": "最后活动",
}
WIDTHS = {"check": 50, "name": 220, "path": 240, "status": 190, "count": 72, "size": 80, "time": 100}

# 项目配色（浅色系，白色主题下文字可读；按排序位置循环分配，相邻项目必不同色）
PALETTE = [
    "#e3f2fd",  # 浅蓝
    "#e8f5e9",  # 浅绿
    "#fff8e1",  # 浅黄
    "#fce4ec",  # 浅粉
    "#f3e5f5",  # 浅紫
    "#e0f7fa",  # 浅青
    "#fff3e0",  # 浅橙
    "#efebe9",  # 浅棕
]


# ---------------------------------------------------------------------------
# 确认删除对话框
# ---------------------------------------------------------------------------


class ConfirmDialog(ttk.Toplevel):
    """二次确认对话框：显示删除内容 + 「彻底删除」选项。"""

    def __init__(self, parent, summary: str, memory_names: list, backup_text: str):
        super().__init__(parent)
        self.title("确认删除")
        self.resizable(False, False)
        self.result: tuple = None  # (confirmed, permanent)
        self._build(summary, memory_names, backup_text)
        self.transient(parent)
        self.grab_set()
        # 居中于父窗口
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    def _build(self, summary: str, memory_names: list, backup_text: str):
        pad = {"padx": 16, "pady": 4}
        ttk.Label(self, text="即将删除以下内容：", font=FONT_HEAD).pack(anchor="w", **pad)
        ttk.Label(self, text=summary, font=FONT, wraplength=420, justify="left").pack(anchor="w", **pad)
        if memory_names:
            ttk.Label(self, text="记忆目录（勾选后才会删除）：", font=FONT).pack(anchor="w", **pad)
            for m in memory_names:
                ttk.Label(self, text="  • " + cc.display_path(m), font=FONT_SMALL, foreground="#555555").pack(anchor="w", padx=(16, 16))
        ttk.Label(self, text=backup_text, font=FONT_SMALL, foreground="#777777", wraplength=420, justify="left").pack(anchor="w", **pad)

        self.permanent_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="彻底删除（不进回收站，不可恢复）", variable=self.permanent_var).pack(anchor="w", padx=16, pady=(8, 4))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=16, pady=(8, 14))
        ttk.Button(btns, text="取消", bootstyle="secondary", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="确认删除", bootstyle="danger", command=self._confirm).pack(side="right", padx=(0, 8))

    def _confirm(self):
        self.result = (True, self.permanent_var.get())
        self.destroy()

    def _cancel(self):
        self.result = (False, False)
        self.destroy()


# ---------------------------------------------------------------------------
# 主应用
# ---------------------------------------------------------------------------


class App:
    def __init__(self):
        self.config_dir = cc.discover_config_dir()
        self.folders: list = []
        self.checked_sessions: dict = {}   # iid -> cc.SessionInfo
        self.checked_memories: dict = {}   # iid -> cc.FolderInfo
        self.checked_empty_dirs: dict = {}  # iid -> cc.FolderInfo（残留空壳目录）

        self.root = ttk.Window(title="Claude 会话清理器", themename="cosmo",
                               size=(1000, 620), resizable=(False, False))
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self._build_ui()
        self.refresh()

    # ---------------- UI 构建 ----------------

    def _build_ui(self):
        style = ttk.Style()
        style.configure("Treeview", font=FONT, rowheight=30)
        style.configure("Treeview.Heading", font=FONT_HEAD)
        style.map("Treeview", background=[("selected", "#cce5ff")], foreground=[("selected", "#000000")])

        # grid 布局：row2（树形表）伸缩，其余行固定。
        # 注意不要用 pack+expand 布局树形表——rowheight=30 时树请求高度
        # 会超过窗口剩余空间，expand 抢占把底栏挤成 0 高度（按钮不可见）。
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        # 顶栏：配置目录 + 刷新
        top = ttk.Frame(self.root, padding=(12, 10, 12, 2))
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="配置目录：", font=FONT).pack(side="left")
        cfg_text = cc.display_path(str(self.config_dir))
        if not self.config_dir.is_dir():
            cfg_text += "（不存在）"
        self.cfg_label = ttk.Label(top, text=cfg_text, font=FONT, bootstyle="secondary")
        self.cfg_label.pack(side="left")
        ttk.Button(top, text="刷新", bootstyle="primary", command=self.refresh).pack(side="right")

        # 统计栏
        stats = ttk.Frame(self.root, padding=(12, 6, 12, 2))
        stats.grid(row=1, column=0, sticky="ew")
        self.stats_label = ttk.Label(stats, text="", font=FONT)
        self.stats_label.pack(side="left")
        self.checked_label = ttk.Label(stats, text="已勾选 0 个会话 · 0 B", font=FONT, bootstyle="primary")
        self.checked_label.pack(side="right")

        # 树形表
        tree_frame = ttk.Frame(self.root, padding=(12, 4, 12, 4))
        tree_frame.grid(row=2, column=0, sticky="nsew")
        self.tree = ttk.Treeview(tree_frame, columns=COLS, show="tree headings",
                                 selectmode="none", height=18)
        for c in COLS:
            self.tree.heading(c, text=HEADINGS[c])
            anchor = "center" if c in ("check", "count", "size") else "w"
            self.tree.column(c, width=WIDTHS[c], anchor=anchor, stretch=False)
        self.tree.column("#0", width=0, stretch=False)  # 树形缩进列不可见
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("deleted", foreground="#9e9e9e")
        self.tree.tag_configure("memory", foreground="#7a5a00")
        self.tree.bind("<Button-1>", self._on_tree_click)

        # 底栏：删除按钮 + 状态 + 进度
        bottom = ttk.Frame(self.root, padding=(12, 4, 12, 12))
        bottom.grid(row=3, column=0, sticky="ew")
        ttk.Button(bottom, text="删除勾选项", bootstyle="danger",
                   command=self._start_delete).pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self.status_var, font=FONT_SMALL, bootstyle="secondary").pack(side="left", padx=(14, 0))

    # ---------------- 扫描与填充 ----------------

    def refresh(self):
        """重新扫描并填充树形表。"""
        self.status_var.set("正在扫描…")
        self.root.update_idletasks()
        try:
            self.folders = cc.scan(self.config_dir)
            cc.attach_project_hints(self.config_dir, self.folders)
        except Exception as e:  # 扫描失败不应崩溃
            self.status_var.set("扫描失败")
            messagebox.showerror("扫描失败", str(e))
            return
        self.folders.sort(key=lambda f: f.last_activity, reverse=True)
        self._rebuild_tree()
        self.checked_sessions.clear()
        self.checked_memories.clear()
        self.checked_empty_dirs.clear()
        self.status_var.set(f"就绪 · 共 {len(self.folders)} 个文件夹")
        self._update_stats()

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        # 项目配色：按排序后的位置循环分配，保证相邻项目颜色不同
        for i, folder in enumerate(self.folders):
            color_tag = f"c{i % len(PALETTE)}"
            self.tree.tag_configure(color_tag, background=PALETTE[i % len(PALETTE)])
            # 残留空壳目录：单独一行，勾选 = 删除目录本身
            if folder.empty_shell:
                ei = f"e{i}"
                self.tree.insert("", "end", iid=ei, text="",
                                 values=(CHECK_OFF, folder.name,
                                         cc.display_path(folder.decoded_path) if folder.decoded_path else "—",
                                         "残留空目录（无会话）", 0,
                                         cc.human_size(0), "—"),
                                 tags=("deleted", color_tag))
                continue
            fid = f"g{i}"
            exists = folder.project_exists
            if exists is True:
                status = f"✓ 项目在：{cc.display_path(folder.decoded_path)}"
            elif exists is False:
                status = "✗ 项目已删除"
            else:
                status = "项目路径未知（编码推测）"
            path_col = cc.display_path(folder.decoded_path) if folder.decoded_path else "—"
            # 灰色行 = 项目已删除（灰字 + 项目底色；tag 顺序保证前景用灰色）
            tags = ("deleted", color_tag) if exists is False else (color_tag,)
            self.tree.insert("", "end", iid=fid, text="",
                             values=(CHECK_OFF, folder.name, path_col, status,
                                     folder.session_count,
                                     cc.human_size(folder.total_size),
                                     self._fmt_time(folder.last_activity)),
                             tags=tags, open=True)
            for j, s in enumerate(folder.sessions):
                sid = f"{fid}_s{j}"
                self.tree.insert(fid, "end", iid=sid, text="",
                                 values=(CHECK_OFF, self._session_name(s), "—", "—", "—",
                                         cc.human_size(s.size), self._fmt_time(s.mtime)),
                                 tags=tags)
            if folder.memory_path:
                mid = f"{fid}_m"
                self.tree.insert(fid, "end", iid=mid, text="",
                                 values=(CHECK_OFF, "🧠 memory（记忆目录）", "—", "—", "—",
                                         cc.human_size(folder.memory_size), "—"),
                                 tags=("memory", color_tag))

    # ---------------- 勾选交互 ----------------

    def _on_tree_click(self, event):
        tree = self.tree
        region = tree.identify("region", event.x, event.y)
        col = tree.identify_column(event.x)
        if region != "cell" or col != "#1":
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return
        self._toggle(iid)
        self._update_stats()

    def _toggle(self, iid):
        if iid.startswith("e"):                               # 残留空壳目录行：单个切换
            self._set_check(iid, not self._is_on(iid))
        elif iid.startswith("g") and iid.count("_") == 0:      # 一级：整组切换
            children = self.tree.get_children(iid)
            if not children:
                return
            new_state = not all(self._is_on(c) for c in children)
            for c in children:
                self._set_check(c, new_state)
            self._set_group_indicator(iid)
        elif iid.endswith("_m"):                              # memory 行
            self._set_check(iid, not self._is_on(iid))
            gid = iid.rsplit("_", 1)[0]
            self._set_group_indicator(gid)
        else:                                                 # 会话行
            self._set_check(iid, not self._is_on(iid))
            gid = iid.rsplit("_", 1)[0]
            self._set_group_indicator(gid)

    def _is_on(self, iid) -> bool:
        if iid in self.checked_sessions or iid in self.checked_memories or iid in self.checked_empty_dirs:
            return True
        return self.tree.set(iid, "check") == CHECK_ON

    def _set_check(self, iid, on: bool):
        """设置勾选状态并更新存储与界面。"""
        cur = self.tree.set(iid, "check") == CHECK_ON
        if on and not cur:
            if iid.startswith("e"):                           # 空壳目录
                ei = int(iid[1:])
                if ei < len(self.folders):
                    self.checked_empty_dirs[iid] = self.folders[ei]
            elif iid.endswith("_m"):                          # memory 行
                # 找到所属 folder
                gid = iid.rsplit("_", 1)[0]
                gi = int(gid[1:])
                if gi < len(self.folders):
                    self.checked_memories[iid] = self.folders[gi]
            else:                                             # 会话行
                gid = iid.rsplit("_", 1)[0]
                gi = int(gid[1:])
                si = int(iid.rsplit("_", 1)[1][1:])
                folder = self.folders[gi]
                if si < len(folder.sessions):
                    self.checked_sessions[iid] = folder.sessions[si]
        elif not on and cur:
            self.checked_sessions.pop(iid, None)
            self.checked_memories.pop(iid, None)
            self.checked_empty_dirs.pop(iid, None)
        self.tree.set(iid, "check", CHECK_ON if on else CHECK_OFF)

    def _set_group_indicator(self, gid):
        """根据子项勾选状态刷新一级行的 ☑/☐/◐。"""
        children = self.tree.get_children(gid)
        if not children:
            return
        on = sum(1 for c in children if self.tree.set(c, "check") == CHECK_ON)
        if on == len(children):
            mark = CHECK_ON
        elif on == 0:
            mark = CHECK_OFF
        else:
            mark = CHECK_HALF
        self.tree.set(gid, "check", mark)

    # ---------------- 统计 ----------------

    def _update_stats(self):
        total_folders = len(self.folders)
        total_sessions = sum(f.session_count for f in self.folders)
        total_size = sum(f.total_size for f in self.folders)
        self.stats_label.config(
            text=f"文件夹 {total_folders} · 会话 {total_sessions} · 总大小 {cc.human_size(total_size)}")
        sel_count = len(self.checked_sessions)
        sel_size = sum(s.size for s in self.checked_sessions.values())
        mem_n = len(self.checked_memories)
        empty_n = len(self.checked_empty_dirs)
        parts = [f"已勾选 {sel_count} 个会话"]
        if mem_n:
            parts.append(f"{mem_n} 个记忆目录")
        if empty_n:
            parts.append(f"{empty_n} 个空目录")
        self.checked_label.config(text=" + ".join(parts) + f" · {cc.human_size(sel_size)}")

    # ---------------- 删除流程 ----------------

    def _start_delete(self):
        sessions = list(self.checked_sessions.values())
        memories = list(self.checked_memories.values())
        empty_dirs = list(self.checked_empty_dirs.values())
        if not sessions and not memories and not empty_dirs:
            messagebox.showinfo("提示", "请先勾选要删除的会话、记忆目录或残留空目录。")
            return
        sel_size = sum(s.size for s in sessions)
        mem_size = sum(m.memory_size for m in memories)
        summary = (f"• 会话文件 {len(sessions)} 个（{cc.human_size(sel_size)}）\n"
                   f"• 对应的 history.jsonl 索引条目（自动同步清理）\n"
                   f"• 关联残留：file-history / session-env / tasks / telemetry 等同 UUID 文件（自动同步清理）")
        if memories:
            summary += f"\n• 记忆目录 {len(memories)} 个（{cc.human_size(mem_size)}）"
        if empty_dirs:
            summary += f"\n• 残留空目录 {len(empty_dirs)} 个（目录本身将被删除）"
        backup_text = "默认移入回收站（可从回收站还原），勾选「彻底删除」则不可恢复。"
        dlg = ConfirmDialog(self.root, summary,
                            [str(m.memory_path) for m in memories] + [str(d.folder_path) for d in empty_dirs],
                            backup_text)
        self.root.wait_window(dlg)
        if not dlg.result or not dlg.result[0]:
            return
        confirmed, permanent = dlg.result
        self.status_var.set("正在删除…")
        self.root.update_idletasks()
        try:
            result = cc.delete_sessions(
                self.config_dir,
                [s.path for s in sessions],
                [m.memory_path for m in memories],
                permanent=permanent,
                empty_dir_paths=[d.folder_path for d in empty_dirs],
            )
        except Exception as e:
            self.status_var.set("删除失败")
            messagebox.showerror("删除失败", str(e))
            return
        self._show_delete_result(result, permanent)
        self.refresh()  # 自动刷新

    def _show_delete_result(self, result, permanent: bool):
        msgs = []
        if result.ok_sessions:
            msgs.append(f"已删除 {len(result.ok_sessions)} 个会话（{'彻底删除' if permanent else '已移入回收站'}）")
        if result.removed_index_lines:
            msgs.append(f"已同步清理索引 {result.removed_index_lines} 行")
        if result.sessions_index_removed:
            msgs.append(f"已更新 sessions-index.json（移除 {result.sessions_index_removed} 条）")
        if result.removed_related:
            msgs.append(f"已清理关联残留 {len(result.removed_related)} 项")
        if result.removed_empty_dirs:
            msgs.append(f"已删除残留空目录 {len(result.removed_empty_dirs)} 个")
        if result.removed_memory:
            msgs.append(f"已删除 {len(result.removed_memory)} 个记忆目录")
        if not msgs and not result.failed:
            msgs.append("没有可删除的内容。")
        if result.failed:
            detail = "\n".join(f"{cc.display_path(p)}：{why}" for p, why in result.failed[:20])
            if len(result.failed) > 20:
                detail += f"\n…等共 {len(result.failed)} 项失败"
            messagebox.showwarning("部分操作失败", "\n".join(msgs) + "\n\n失败明细：\n" + detail)
        else:
            messagebox.showinfo("完成", "\n".join(msgs))

    # ---------------- 工具 ----------------

    @staticmethod
    def _session_name(s) -> str:
        """会话行名称：优先索引里的可读摘要（截断），无摘要时显示 UUID 文件名。"""
        if s.title:
            t = s.title.replace("\n", " ").strip()
            return t if len(t) <= 40 else t[:40] + "…"
        return s.path.name

    @staticmethod
    def _fmt_time(ts: float) -> str:
        if ts <= 0:
            return "—"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main():
    app = App()
    if "--smoke-test" in sys.argv:
        def _probe():
            print("geometry:", app.root.geometry())
            print("tree_columns:", len(COLS))
            print("folders_shown:", len(app.tree.get_children()))
            print("SMOKE_OK")
            app.root.destroy()
        app.root.after(2000, _probe)
    app.root.mainloop()


if __name__ == "__main__":
    main()
