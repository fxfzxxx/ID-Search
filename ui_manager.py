import json
import queue
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


@dataclass
class ProcessInfo:
    serial: str
    process: subprocess.Popen
    started_at: float
    assigned_count: int
    ids_file: Path


TYPE_LABEL_MAP = {
    "qq available": "QQ可用",
    "wx available": "微信可用",
    "both available": "双区可用",
    "none available": "双区不可用",
    # Backward compatibility for older values.
    "qq": "QQ可用",
    "wx": "微信可用",
}


class AutomationUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ADB 自动化管理器")
        self.geometry("1250x760")

        self.base_dir = Path(__file__).resolve().parent
        self.ids_dir = self.base_dir / "_assigned_ids"
        self.ids_dir.mkdir(parents=True, exist_ok=True)
        self.cooldown_path = self.base_dir / ".device_cooldown.json"

        self.db_name_var = tk.StringVar(value="input.db")
        self.threshold_var = tk.StringVar(value="0.55")
        self.batch_size_var = tk.IntVar(value=60)
        self.cooldown_minutes_var = tk.IntVar(value=90)
        self.only_untyped_var = tk.BooleanVar(value=True)
        self.only_q_var = tk.BooleanVar(value=False)
        self.only_w_var = tk.BooleanVar(value=False)
        self.only_both_var = tk.BooleanVar(value=False)
        self.search_var = tk.StringVar(value="")
        self.page_size_var = tk.IntVar(value=100)
        self.page_info_var = tk.StringVar(value="第 1/1 页")
        self.stats_var = tk.StringVar(value="就绪")
        self.current_page = 1
        self.total_filtered_rows = 0
        self.total_pages = 1
        self.loading_depth = 0
        self.loading_started_at = 0.0
        self.loading_finish_after_id = None
        self.loading_hide_after_id = None
        self.loading_tick_after_id = None
        self.loading_force_until = 0.0
        self.loading_current_message = ""

        self.log_queue = queue.Queue()
        self.devices = []
        self.processes = {}
        self.cooldowns = self._load_cooldowns()

        self._build_ui()
        self._refresh_devices()
        self._refresh_db()
        self._poll_events()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="数据库").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(top, textvariable=self.db_name_var, width=26).grid(
            row=0, column=1, sticky="w", padx=(0, 10)
        )

        ttk.Label(top, text="匹配阈值").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(top, textvariable=self.threshold_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(0, 10)
        )

        ttk.Label(top, text="每设备条数").grid(row=0, column=4, sticky="w", padx=(0, 6))
        ttk.Entry(top, textvariable=self.batch_size_var, width=8).grid(
            row=0, column=5, sticky="w", padx=(0, 10)
        )

        ttk.Label(top, text="冷却(分钟)").grid(row=0, column=6, sticky="w", padx=(0, 6))
        ttk.Entry(top, textvariable=self.cooldown_minutes_var, width=8).grid(
            row=0, column=7, sticky="w", padx=(0, 10)
        )

        btn_row = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_row.pack(fill=tk.X)

        ttk.Button(btn_row, text="刷新设备", command=self._refresh_devices).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btn_row, text="刷新数据库", command=self._refresh_db).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btn_row, text="开始", command=self._start_tasks).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(btn_row, text="停止", command=self._stop_tasks).pack(side=tk.LEFT)

        ttk.Label(btn_row, textvariable=self.stats_var).pack(side=tk.RIGHT)

        self.loading_text_var = tk.StringVar(value="")
        self.loading_row = ttk.Frame(self, padding=(10, 0, 10, 8))
        self.loading_row.configure(height=26)
        self.loading_row.pack_propagate(False)
        self.loading_label = ttk.Label(
            self.loading_row, textvariable=self.loading_text_var
        )
        self.loading_label.pack(side=tk.LEFT, padx=(0, 8))
        self.loading_bar = ttk.Progressbar(
            self.loading_row,
            mode="determinate",
            length=260,
            maximum=100,
        )
        self.loading_row.pack(side=tk.BOTTOM, fill=tk.X)

        panes = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        left = ttk.Labelframe(panes, text="已连接设备")
        right = ttk.Labelframe(panes, text="数据库记录")
        panes.add(left, weight=1)
        panes.add(right, weight=2)

        self.device_tree = ttk.Treeview(
            left,
            columns=("serial", "state", "cooldown", "assigned", "process"),
            show="headings",
            height=14,
            selectmode="extended",
        )
        for col, title, width in (
            ("serial", "设备序列号", 210),
            ("state", "状态", 100),
            ("cooldown", "冷却", 100),
            ("assigned", "分配数", 90),
            ("process", "进程", 100),
        ):
            self.device_tree.heading(col, text=title)
            self.device_tree.column(col, width=width, anchor="w")
        self.device_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        ttk.Label(
            left,
            text="提示：按住 Ctrl 可多选设备；点击开始时仅分配给选中的设备。",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        db_filter = ttk.Frame(right, padding=(8, 6, 8, 0))
        db_filter.pack(fill=tk.X)
        ttk.Label(db_filter, text="搜索").pack(side=tk.LEFT)
        search_entry = ttk.Entry(db_filter, textvariable=self.search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(6, 8))
        search_entry.bind("<Return>", lambda _e: self._search_db())
        ttk.Button(db_filter, text="查询", command=self._search_db).pack(
            side=tk.LEFT, padx=(0, 10)
        )

        ttk.Label(db_filter, text="每页").pack(side=tk.LEFT)
        page_size_combo = ttk.Combobox(
            db_filter,
            textvariable=self.page_size_var,
            width=6,
            state="readonly",
            values=(50, 100, 200, 500),
        )
        page_size_combo.pack(side=tk.LEFT, padx=(6, 6))
        page_size_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_page_size_changed()
        )

        ttk.Button(db_filter, text="下一页", command=self._next_page).pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        ttk.Button(db_filter, text="上一页", command=self._prev_page).pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        ttk.Label(db_filter, textvariable=self.page_info_var).pack(side=tk.RIGHT)

        db_filter_extra = ttk.Frame(right, padding=(8, 2, 8, 0))
        db_filter_extra.pack(fill=tk.X)
        ttk.Checkbutton(
            db_filter_extra,
            text="仅显示未处理(type为空)",
            variable=self.only_untyped_var,
            command=self._on_filter_changed,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(
            db_filter_extra,
            text="Q区可用",
            variable=self.only_q_var,
            command=self._on_filter_changed,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(
            db_filter_extra,
            text="W区可用",
            variable=self.only_w_var,
            command=self._on_filter_changed,
        ).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(
            db_filter_extra,
            text="双区可用",
            variable=self.only_both_var,
            command=self._on_filter_changed,
        ).pack(side=tk.LEFT)

        db_table_frame = ttk.Frame(right)
        db_table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.db_tree = ttk.Treeview(
            db_table_frame,
            columns=("id", "content", "type"),
            show="headings",
            height=14,
        )
        for col, title, width in (
            ("id", "ID", 80),
            ("content", "Content", 260),
            ("type", "Type", 180),
        ):
            self.db_tree.heading(col, text=title)
            self.db_tree.column(col, width=width, anchor="w")

        db_scroll_y = ttk.Scrollbar(
            db_table_frame,
            orient="vertical",
            command=self.db_tree.yview,
        )
        db_scroll_x = ttk.Scrollbar(
            db_table_frame,
            orient="horizontal",
            command=self.db_tree.xview,
        )
        self.db_tree.configure(
            yscrollcommand=db_scroll_y.set, xscrollcommand=db_scroll_x.set
        )

        self.db_tree.grid(row=0, column=0, sticky="nsew")
        db_scroll_y.grid(row=0, column=1, sticky="ns")
        db_scroll_x.grid(row=1, column=0, sticky="ew")
        db_table_frame.grid_rowconfigure(0, weight=1)
        db_table_frame.grid_columnconfigure(0, weight=1)

        log_frame = ttk.Labelframe(self, text="日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(log_frame, height=14, wrap="word")
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _append_log(self, message: str):
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {message}\n")
        self.log_text.see(tk.END)

    def _set_loading(self, message: str):
        if self.loading_finish_after_id is not None:
            try:
                self.after_cancel(self.loading_finish_after_id)
            except Exception:
                pass
            self.loading_finish_after_id = None
        if self.loading_hide_after_id is not None:
            try:
                self.after_cancel(self.loading_hide_after_id)
            except Exception:
                pass
            self.loading_hide_after_id = None

        self.loading_depth += 1
        self.loading_current_message = message
        self.loading_text_var.set(message)
        if self.loading_depth == 1:
            self.loading_started_at = time.time()
            self.loading_force_until = 0.0
            self.loading_bar.configure(value=0)
            if not self.loading_bar.winfo_ismapped():
                self.loading_bar.pack(side=tk.LEFT)
            self._schedule_loading_tick()
        self.update_idletasks()

    def _clear_loading(self):
        if self.loading_depth > 0:
            self.loading_depth -= 1
        if self.loading_depth > 0:
            self.update_idletasks()
            return

        elapsed = max(0.0, time.time() - self.loading_started_at)
        remain = max(0.0, 1.5 - elapsed)
        if remain > 0:
            self.loading_force_until = self.loading_started_at + 1.5
            self._schedule_loading_tick()
            self.loading_finish_after_id = self.after(
                int(remain * 1000), self._finish_loading_ui
            )
            return
        self._finish_loading_ui()

    def _done_message(self, loading_message: str):
        msg = (loading_message or "").strip()
        if not msg:
            return "操作已完成"
        msg = msg.replace("...", "").replace("…", "")
        if msg.startswith("正在"):
            msg = msg[2:]
        return f"{msg}已完成"

    def _schedule_loading_tick(self):
        if self.loading_tick_after_id is not None:
            return
        self.loading_tick_after_id = self.after(33, self._tick_loading_bar)

    def _loading_curve(self, elapsed: float, total: float, cap: float):
        if total <= 0:
            return cap
        t = max(0.0, min(1.0, elapsed / total))
        # Front 80% faster, last 20% slower.
        if t <= 0.4:
            mapped = (t / 0.4) * 0.8
        else:
            mapped = 0.8 + ((t - 0.4) / 0.6) * 0.2
        return min(cap, mapped * cap)

    def _tick_loading_bar(self):
        self.loading_tick_after_id = None
        now = time.time()
        if self.loading_depth <= 0 and now >= self.loading_force_until:
            return

        elapsed = max(0.0, now - self.loading_started_at)
        if self.loading_depth > 0:
            # Keep a visible headroom during active loading.
            progress = self._loading_curve(elapsed, total=1.5, cap=95.0)
        else:
            progress = self._loading_curve(elapsed, total=1.5, cap=100.0)
        self.loading_bar.configure(value=progress)

        if self.loading_depth > 0 or progress < 100.0:
            self._schedule_loading_tick()

    def _finish_loading_ui(self):
        self.loading_finish_after_id = None
        if self.loading_depth > 0:
            return
        self.loading_force_until = 0.0
        if self.loading_tick_after_id is not None:
            try:
                self.after_cancel(self.loading_tick_after_id)
            except Exception:
                pass
            self.loading_tick_after_id = None
        self.loading_bar.configure(value=100)
        if not self.loading_bar.winfo_ismapped():
            self.loading_bar.pack(side=tk.LEFT)
        self.loading_text_var.set(self._done_message(self.loading_current_message))
        self.loading_hide_after_id = self.after(2000, self._hide_loading_ui)
        self.update_idletasks()

    def _hide_loading_ui(self):
        self.loading_hide_after_id = None
        if self.loading_depth > 0:
            return
        self.loading_bar.configure(value=0)
        if self.loading_bar.winfo_ismapped():
            self.loading_bar.pack_forget()
        self.loading_text_var.set("")
        self.update_idletasks()

    def _to_cn_type_label(self, raw_value: str):
        value = (raw_value or "").strip()
        if not value:
            return ""
        return TYPE_LABEL_MAP.get(value.lower(), value)

    def _db_path(self) -> Path:
        return self.base_dir / self.db_name_var.get().strip()

    def _ensure_words_table(self):
        db_path = self._db_path()
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS words ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "content TEXT NOT NULL, "
                "type TEXT NULL"
                ")"
            )
            conn.commit()
        finally:
            conn.close()

    def _fetch_db_rows(
        self,
        only_untyped: bool,
        only_q: bool,
        only_w: bool,
        only_both: bool,
        keyword: str,
        page: int,
        page_size: int,
    ):
        self._ensure_words_table()
        db_path = self._db_path()
        kw = keyword.strip()
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            base_where = ["content <> ''"]
            params = []

            type_filters = []
            if only_untyped:
                type_filters.append("(type IS NULL OR TRIM(type) = '')")
            if only_q:
                type_filters.append("LOWER(COALESCE(type, '')) IN ('qq available', 'qq')")
            if only_w:
                type_filters.append("LOWER(COALESCE(type, '')) IN ('wx available', 'wx')")
            if only_both:
                type_filters.append("LOWER(COALESCE(type, '')) = 'both available'")
            if type_filters:
                base_where.append("(" + " OR ".join(type_filters) + ")")

            if kw:
                base_where.append("(content LIKE ? OR COALESCE(type, '') LIKE ?)")
                like_kw = f"%{kw}%"
                params.extend([like_kw, like_kw])

            where_sql = " AND ".join(base_where)

            cur.execute(
                f"SELECT COUNT(*) FROM words WHERE {where_sql}",
                tuple(params),
            )
            filtered_count = int(cur.fetchone()[0])

            safe_page = max(page, 1)
            safe_page_size = max(page_size, 1)
            offset = (safe_page - 1) * safe_page_size

            cur.execute(
                f"SELECT id, content, COALESCE(type, '') FROM words WHERE {where_sql} ORDER BY id LIMIT ? OFFSET ?",
                tuple(params + [safe_page_size, offset]),
            )
            rows = cur.fetchall()

            cur.execute(
                "SELECT COUNT(*) FROM words WHERE content <> '' AND (type IS NULL OR TRIM(type) = '')"
            )
            untyped_count = int(cur.fetchone()[0])

            cur.execute("SELECT COUNT(*) FROM words WHERE content <> ''")
            total_count = int(cur.fetchone()[0])
            return rows, total_count, untyped_count, filtered_count
        finally:
            conn.close()

    def _fetch_untyped_for_assign(self, wanted: int):
        self._ensure_words_table()
        db_path = self._db_path()
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, content FROM words "
                "WHERE content <> '' AND (type IS NULL OR TRIM(type) = '') "
                "ORDER BY id LIMIT ?",
                (wanted,),
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _get_connected_devices(self):
        p = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True)
        if p.returncode != 0:
            msg = (p.stdout + p.stderr).strip() or "adb devices failed"
            raise RuntimeError(msg)

        devices = []
        for line in p.stdout.splitlines()[1:]:
            raw = line.strip()
            if not raw:
                continue
            parts = raw.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            devices.append({"serial": serial, "state": state, "raw": raw})
        return devices

    def _load_cooldowns(self):
        if not self.cooldown_path.exists():
            return {}
        try:
            data = json.loads(self.cooldown_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            cleaned = {}
            now = time.time()
            for serial, until in data.items():
                try:
                    value = float(until)
                except (TypeError, ValueError):
                    continue
                if value > now:
                    cleaned[str(serial)] = value
            return cleaned
        except Exception:
            return {}

    def _save_cooldowns(self):
        self.cooldown_path.write_text(
            json.dumps(self.cooldowns, ensure_ascii=True, indent=2), encoding="utf-8"
        )

    def _format_cooldown(self, serial: str):
        if serial in self.processes:
            return "运行中"
        until = self.cooldowns.get(serial, 0)
        remain = int(until - time.time())
        if remain <= 0:
            return "-"
        h = remain // 3600
        m = (remain % 3600) // 60
        s = remain % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _refresh_devices(self):
        self._set_loading("正在刷新设备列表...")
        try:
            try:
                self.devices = self._get_connected_devices()
            except Exception as exc:
                messagebox.showerror("ADB 错误", str(exc))
                return

            self._render_device_tree()
            self._update_stats()
        finally:
            self._clear_loading()

    def _refresh_db(self):
        self._set_loading("正在加载数据库列表...")
        try:
            page_size = max(int(self.page_size_var.get()), 1)
            try:
                rows, _, _, filtered_count = self._fetch_db_rows(
                    only_untyped=self.only_untyped_var.get(),
                    only_q=self.only_q_var.get(),
                    only_w=self.only_w_var.get(),
                    only_both=self.only_both_var.get(),
                    keyword=self.search_var.get(),
                    page=self.current_page,
                    page_size=page_size,
                )
            except Exception as exc:
                messagebox.showerror("数据库错误", str(exc))
                return

            self.total_filtered_rows = filtered_count
            self.total_pages = max((filtered_count + page_size - 1) // page_size, 1)
            if self.current_page > self.total_pages:
                self.current_page = self.total_pages
                try:
                    rows, _, _, filtered_count = self._fetch_db_rows(
                        only_untyped=self.only_untyped_var.get(),
                        only_q=self.only_q_var.get(),
                        only_w=self.only_w_var.get(),
                        only_both=self.only_both_var.get(),
                        keyword=self.search_var.get(),
                        page=self.current_page,
                        page_size=page_size,
                    )
                except Exception:
                    rows = []
                self.total_filtered_rows = filtered_count
            self.page_info_var.set(
                f"第 {self.current_page}/{self.total_pages} 页，共 {self.total_filtered_rows} 条"
            )

            for item in self.db_tree.get_children():
                self.db_tree.delete(item)
            for row_id, content, raw_type in rows:
                self.db_tree.insert(
                    "",
                    tk.END,
                    values=(row_id, content, self._to_cn_type_label(raw_type)),
                )
            self._update_stats()
        finally:
            self._clear_loading()

    def _search_db(self):
        self.current_page = 1
        self._refresh_db()

    def _on_filter_changed(self):
        self.current_page = 1
        self._refresh_db()

    def _on_page_size_changed(self):
        self.current_page = 1
        self._refresh_db()

    def _prev_page(self):
        if self.current_page <= 1:
            return
        self.current_page -= 1
        self._refresh_db()

    def _next_page(self):
        if self.current_page >= self.total_pages:
            return
        self.current_page += 1
        self._refresh_db()

    def _render_device_tree(self):
        selected_serials = set(self._selected_device_serials())
        for item in self.device_tree.get_children():
            self.device_tree.delete(item)

        for dev in self.devices:
            serial = dev["serial"]
            proc_info = self.processes.get(serial)
            assigned = proc_info.assigned_count if proc_info else 0
            proc_state = "运行中" if proc_info else "空闲"
            self.device_tree.insert(
                "",
                tk.END,
                values=(
                    serial,
                    dev["state"],
                    self._format_cooldown(serial),
                    assigned,
                    proc_state,
                ),
            )

        # Keep previously selected devices after refresh.
        for item in self.device_tree.get_children():
            values = self.device_tree.item(item, "values")
            if values and values[0] in selected_serials:
                self.device_tree.selection_add(item)

    def _selected_device_serials(self):
        serials = []
        for item in self.device_tree.selection():
            values = self.device_tree.item(item, "values")
            if values:
                serials.append(values[0])
        return serials

    def _update_stats(self):
        try:
            _, total, untyped, _ = self._fetch_db_rows(
                only_untyped=False,
                only_q=False,
                only_w=False,
                only_both=False,
                keyword="",
                page=1,
                page_size=1,
            )
        except Exception:
            total = 0
            untyped = 0

        online = sum(1 for d in self.devices if d["state"] == "device")
        running = len(self.processes)
        self.stats_var.set(
            f"在线设备: {online} | 运行中: {running} | 数据总数: {total} | 未处理: {untyped}"
        )

    def _chunk(self, rows, size: int):
        return [rows[i : i + size] for i in range(0, len(rows), size)]

    def _start_tasks(self):
        self._set_loading("正在准备任务并启动脚本...")
        try:
            if self.processes:
                messagebox.showwarning("忙碌", "当前仍有任务在运行")
                return

            if self.batch_size_var.get() <= 0:
                messagebox.showwarning("配置错误", "每设备条数必须大于 0")
                return

            try:
                threshold = float(self.threshold_var.get())
            except ValueError:
                messagebox.showwarning("配置错误", "匹配阈值必须是数字")
                return

            selected_serials = set(self._selected_device_serials())
            self._refresh_devices()
            online_devices = [d for d in self.devices if d["state"] == "device"]
            if selected_serials:
                online_devices = [
                    d for d in online_devices if d["serial"] in selected_serials
                ]
                if not online_devices:
                    messagebox.showinfo("无可用设备", "你选择的设备当前不在线")
                    return

            now = time.time()
            available = [
                d
                for d in online_devices
                if d["serial"] not in self.processes
                and self.cooldowns.get(d["serial"], 0) <= now
            ]
            if not available:
                messagebox.showinfo("无可用设备", "没有可用设备（需在线且不在冷却中）")
                return

            batch_size = int(self.batch_size_var.get())
            wanted = len(available) * batch_size
            rows = self._fetch_untyped_for_assign(wanted)
            if not rows:
                messagebox.showinfo("无可分配数据", "没有 type 为空的数据可分配")
                return

            chunks = self._chunk(rows, batch_size)
            assignments = list(zip(available, chunks))
            if not assignments:
                messagebox.showinfo("无分配结果", "没有生成有效的数据分片")
                return

            script_path = self.base_dir / "adb_automation.py"
            db_name = self.db_name_var.get().strip()

            for dev, chunk in assignments:
                serial = dev["serial"]
                ids = [str(r[0]) for r in chunk]
                ids_file = (
                    self.ids_dir / f"{int(time.time())}_{serial.replace(':', '_')}.txt"
                )
                ids_file.write_text("\n".join(ids), encoding="utf-8")

                cmd = [
                    sys.executable,
                    str(script_path),
                    "--device",
                    serial,
                    "--db",
                    db_name,
                    "--threshold",
                    f"{threshold}",
                    "--ids-file",
                    str(ids_file),
                ]
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.base_dir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    bufsize=1,
                )

                self.processes[serial] = ProcessInfo(
                    serial=serial,
                    process=proc,
                    started_at=time.time(),
                    assigned_count=len(chunk),
                    ids_file=ids_file,
                )
                thread = threading.Thread(
                    target=self._stream_process_output,
                    args=(serial, proc),
                    daemon=True,
                )
                thread.start()
                self._append_log(f"开始 {serial}: 分配 {len(chunk)} 条")

            self._render_device_tree()
            self._update_stats()
            self._refresh_db()
        finally:
            self._clear_loading()

    def _stream_process_output(self, serial: str, proc: subprocess.Popen):
        if proc.stdout is None:
            return
        for line in iter(proc.stdout.readline, ""):
            text = line.strip()
            if text:
                self.log_queue.put(f"[{serial}] {text}")

    def _stop_tasks(self):
        if not self.processes:
            messagebox.showinfo("提示", "当前没有运行中的进程")
            return

        for serial, info in list(self.processes.items()):
            if info.process.poll() is None:
                info.process.terminate()
                self._append_log(f"已向 {serial} 发送终止信号")

        self.after(2000, self._kill_remaining)

    def _kill_remaining(self):
        for serial, info in list(self.processes.items()):
            if info.process.poll() is None:
                info.process.kill()
                self._append_log(f"已强制结束 {serial}")

    def _mark_cooldown(self, serial: str):
        cooldown_seconds = int(self.cooldown_minutes_var.get()) * 60
        self.cooldowns[serial] = time.time() + cooldown_seconds
        self._save_cooldowns()

    def _poll_events(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self._append_log(msg)

        changed = False
        for serial, info in list(self.processes.items()):
            code = info.process.poll()
            if code is None:
                continue
            changed = True
            self._append_log(f"完成 {serial}: 退出码={code}")
            self._mark_cooldown(serial)
            try:
                if info.ids_file.exists():
                    info.ids_file.unlink()
            except Exception:
                pass
            del self.processes[serial]

        if changed:
            self._refresh_devices()
            self._refresh_db()
        else:
            self._render_device_tree()
            self._update_stats()

        self.after(1000, self._poll_events)

    def _on_close(self):
        if self.processes:
            ok = messagebox.askyesno(
                "退出",
                "仍有任务在运行，是否停止全部任务并退出？",
            )
            if not ok:
                return
            self._stop_tasks()
        self.destroy()


if __name__ == "__main__":
    app = AutomationUI()
    app.mainloop()
