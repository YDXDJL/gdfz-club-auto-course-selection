"""课程请求工具：分类选课、多账号、毫秒时间输入与可取消的轮次发送。"""
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
import uuid
from datetime import datetime, timedelta
from request_engine import (ACTION_URLS, USER_AGENT, TZ, Account, Plan,
                            normalize_cookie, parse_start, validate_plan,
                            run_plan, fetch_catalog, now_text)

HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / 'courses.json'


class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue()
        self.accounts = {}
        self.details = {}
        self.busy = False
        self.cancel = threading.Event()
        self.target_time = None
        self.task_error = None
        self.counters = {'成功': 0, '失败': 0, '待核对': 0}
        self.serial = 0
        self.locked = []
        try:
            self.catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.catalog = {'tid': '3389', 'courses': [], 'reported_total': 86}
        self.action = tk.StringVar(value='报名')
        self.tid = tk.StringVar(value='3389')
        self.cid = tk.StringVar(value='265899429')
        self.category = tk.StringVar(value='全部')
        self.search = tk.StringVar()
        self.choice = tk.StringVar()
        self.alias = tk.StringVar()
        self.cookie = tk.StringVar()
        self.rounds = tk.StringVar(value='1')
        self.interval = tk.StringVar(value='1000')
        opening = datetime(2026, 9, 13, 10, tzinfo=TZ)
        default_start = max(opening, datetime.now(TZ) + timedelta(minutes=1))
        self.start_text = tk.StringVar(value=default_start.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3])
        self.status = tk.StringVar(value='就绪：添加 Cookie 后，可立即开始或启动定时。')
        self.countdown = tk.StringVar()
        self.catalog_info = tk.StringVar()
        self.course_info = tk.StringVar()
        root.title('课程请求工具 · 多账号 / 定时发送')
        root.geometry('1060x980')
        root.minsize(940, 850)
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('Treeview', rowheight=26)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 17, 'bold'))
        style.configure('Primary.TButton', foreground='white', background='#245fba', padding=(12, 6))
        outer = ttk.Frame(root, padding=14)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)
        ttk.Label(outer, text='课程请求工具', style='Title.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 8))

        course = ttk.LabelFrame(outer, text='1  选择操作与课程', padding=8)
        course.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        course.columnconfigure(3, weight=1)
        actions = ttk.Frame(course)
        actions.grid(row=0, column=0, columnspan=2, sticky='w')
        for name in ACTION_URLS:
            ttk.Radiobutton(actions, text=name, variable=self.action, value=name, command=self.update_summary).pack(side='left', padx=(0, 18))
        ttk.Label(course, text='活动 ID').grid(row=0, column=2, sticky='e', padx=6)
        ttk.Entry(course, textvariable=self.tid, width=12).grid(row=0, column=3, sticky='w')
        ttk.Button(course, text='刷新全部账号可见课程', command=self.refresh_catalog).grid(row=0, column=4, padx=8)
        ttk.Label(course, text='分类').grid(row=1, column=0, sticky='w', pady=6)
        self.category_box = ttk.Combobox(course, textvariable=self.category, values=['全部', '选修课', '社团课', '未分类'], state='readonly', width=11)
        self.category_box.grid(row=1, column=1, sticky='w')
        ttk.Label(course, text='搜索').grid(row=1, column=2, sticky='e', padx=6)
        ttk.Entry(course, textvariable=self.search).grid(row=1, column=3, columnspan=2, sticky='ew')
        ttk.Label(course, text='课程').grid(row=2, column=0, sticky='w')
        self.course_box = ttk.Combobox(course, textvariable=self.choice, state='readonly', width=52)
        self.course_box.grid(row=2, column=1, columnspan=3, sticky='ew')
        id_frame = ttk.Frame(course)
        id_frame.grid(row=2, column=4, padx=8)
        ttk.Label(id_frame, text='课程 ID').pack(side='left', padx=(0, 4))
        ttk.Entry(id_frame, textvariable=self.cid, width=14).pack(side='left')
        ttk.Label(course, textvariable=self.catalog_info, foreground='#526176').grid(row=3, column=0, columnspan=5, sticky='w', pady=(6, 0))
        ttk.Label(course, textvariable=self.course_info, wraplength=960).grid(row=4, column=0, columnspan=5, sticky='w')

        account_frame = ttk.LabelFrame(outer, text='2  账号 Cookie（每轮每个已添加账号各发送一次）', padding=8)
        account_frame.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        account_frame.columnconfigure(3, weight=1)
        ttk.Label(account_frame, text='备注').grid(row=0, column=0, sticky='w')
        ttk.Entry(account_frame, textvariable=self.alias, width=12).grid(row=0, column=1, padx=6)
        ttk.Label(account_frame, text='Cookie').grid(row=0, column=2, sticky='w')
        ttk.Entry(account_frame, textvariable=self.cookie, show='*').grid(row=0, column=3, sticky='ew', padx=6)
        ttk.Button(account_frame, text='添加', command=self.add_account).grid(row=0, column=4, padx=4)
        ttk.Button(account_frame, text='批量添加', command=self.bulk_dialog).grid(row=0, column=5, padx=4)
        ttk.Button(account_frame, text='移除选中', command=self.remove_accounts).grid(row=0, column=6)
        self.account_tree = ttk.Treeview(account_frame, columns=('name', 'result', 'http', 'time'), show='headings', height=3)
        for name, label, width in [('name', '账号备注', 150), ('result', '最近结果', 360), ('http', 'HTTP', 65), ('time', '发送时间（北京时间）', 205)]:
            self.account_tree.heading(name, text=label)
            self.account_tree.column(name, width=width, stretch=name == 'result')
        self.account_tree.grid(row=1, column=0, columnspan=7, sticky='ew', pady=(6, 0))
        scroll = ttk.Scrollbar(account_frame, command=self.account_tree.yview)
        scroll.grid(row=1, column=7, sticky='ns')
        self.account_tree.configure(yscrollcommand=scroll.set)

        schedule = ttk.LabelFrame(outer, text='3  定时与轮次', padding=8)
        schedule.grid(row=3, column=0, sticky='ew', pady=(0, 8))
        ttk.Label(schedule, text='北京时间').grid(row=0, column=0, sticky='w')
        ttk.Entry(schedule, textvariable=self.start_text, width=27).grid(row=0, column=1, padx=6)
        ttk.Label(schedule, text='轮数').grid(row=0, column=2, padx=(12, 3))
        ttk.Spinbox(schedule, textvariable=self.rounds, from_=1, to=10000, width=7).grid(row=0, column=3)
        ttk.Label(schedule, text='轮间等待（毫秒）').grid(row=0, column=4, padx=(12, 3))
        ttk.Spinbox(schedule, textvariable=self.interval, from_=0, to=86400000, width=10).grid(row=0, column=5)
        ttk.Label(schedule, text='格式：2026-09-13 10:00:00.000；上轮全部请求结束后，再等待指定间隔。', foreground='#526176').grid(row=1, column=0, columnspan=6, sticky='w', pady=(6, 0))
        ttk.Label(schedule, text='每轮最多 8 个账号并行；毫秒为设定粒度，实际发送时间见日志。定时期间请保持程序运行、电脑唤醒。', foreground='#526176').grid(row=2, column=0, columnspan=6, sticky='w')

        toolbar = ttk.Frame(outer)
        toolbar.grid(row=4, column=0, sticky='ew', pady=(0, 8))
        self.preview_button = ttk.Button(toolbar, text='预览任务', command=self.preview)
        self.preview_button.pack(side='left', padx=(0, 8))
        self.send_button = ttk.Button(toolbar, text='立即开始', command=self.send, style='Primary.TButton')
        self.send_button.pack(side='left', padx=(0, 8))
        self.schedule_button = ttk.Button(toolbar, text='启动定时', command=lambda: self.send(True))
        self.schedule_button.pack(side='left', padx=(0, 8))
        self.stop_button = ttk.Button(toolbar, text='停止 / 取消定时', command=self.stop, state='disabled')
        self.stop_button.pack(side='left')
        ttk.Label(toolbar, textvariable=self.countdown, foreground='#245fba').pack(side='right')

        results_frame = ttk.Frame(outer)
        results_frame.grid(row=5, column=0, sticky='nsew')
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(1, weight=1)
        results_frame.rowconfigure(2, weight=1)
        self.banner = tk.Label(results_frame, textvariable=self.status, anchor='w', bg='#e8eef8', fg='#203b66', padx=8, pady=7, wraplength=970, font=('Microsoft YaHei UI', 10))
        self.banner.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 6))
        self.log = ttk.Treeview(results_frame, columns=('round', 'account', 'time', 'result', 'elapsed', 'message'), show='headings', height=5)
        for name, title, width in [('round', '轮次', 45), ('account', '账号', 110), ('time', '发出时间', 195), ('result', '结果', 65), ('elapsed', '耗时 ms', 85), ('message', '提示', 420)]:
            self.log.heading(name, text=title)
            self.log.column(name, width=width, stretch=name == 'message')
        self.log.grid(row=1, column=0, sticky='nsew')
        log_scroll = ttk.Scrollbar(results_frame, command=self.log.yview)
        log_scroll.grid(row=1, column=1, sticky='ns')
        self.log.configure(yscrollcommand=log_scroll.set)
        for tag, color in [('成功', '#15763c'), ('失败', '#af2929'), ('待核对', '#986800')]:
            self.log.tag_configure(tag, foreground=color)
        self.output = scrolledtext.ScrolledText(results_frame, height=5, wrap='word', font=('Consolas', 10))
        self.output.grid(row=2, column=0, columnspan=2, sticky='nsew', pady=(6, 0))
        self.log.bind('<<TreeviewSelect>>', self.show_detail)
        self.course_box.bind('<<ComboboxSelected>>', self.select_course)
        self.category.trace_add('write', lambda *_: self.filter_courses())
        self.search.trace_add('write', lambda *_: self.filter_courses())
        self.tid.trace_add('write', lambda *_: self.filter_courses())
        self.cid.trace_add('write', lambda *_: self.update_summary())
        self.filter_courses()
        for group in [course, account_frame, schedule]:
            self.collect_controls(group)
        self.locked.extend([(self.send_button, 'normal'), (self.schedule_button, 'normal'), (self.preview_button, 'normal')])
        self.display('选择“报名”会真实提交选课；选择“收藏”会添加收藏。\nCookie 仅存内存，关闭程序后清除。成功提示依据布尔值 status=true；最终结果可回网页核对。')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(50, self.poll)

    def collect_controls(self, parent):
        for child in parent.winfo_children():
            if isinstance(child, (ttk.Entry, ttk.Button, ttk.Combobox, ttk.Spinbox, ttk.Radiobutton)):
                self.locked.append((child, str(child.cget('state'))))
            self.collect_controls(child)

    def set_busy(self, busy):
        self.busy = busy
        for widget, state in self.locked:
            widget.configure(state='disabled' if busy else state)
        self.stop_button.configure(state='normal' if busy else 'disabled')

    def notify(self, text, kind='info'):
        colors = {'info': ('#e8eef8', '#203b66'), '成功': ('#e4f4e9', '#176b38'), '失败': ('#fbe8e8', '#a32727'), '待核对': ('#fff2d9', '#856000')}
        bg, fg = colors[kind]
        self.status.set(text)
        self.banner.configure(bg=bg, fg=fg)

    def display(self, text):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.insert('1.0', text)
        self.output.configure(state='disabled')

    def available_courses(self):
        return self.catalog['courses'] if self.catalog.get('tid') == self.tid.get().strip() else []

    def filter_courses(self):
        courses = self.available_courses()
        needle = self.search.get().strip().casefold()
        filtered = [c for c in courses if (self.category.get() == '全部' or c['category'] == self.category.get()) and (not needle or needle in (c['name'] + c['id']).casefold())]
        self.course_options = {f"{c['name']}  ·  {c['id']}": c for c in filtered}
        self.course_box.configure(values=list(self.course_options) + ['手动填写课程 ID'])
        chosen = next((label for label, c in self.course_options.items() if c['id'] == self.cid.get()), None)
        if chosen:
            self.choice.set(chosen)
        elif filtered:
            self.choice.set(next(iter(self.course_options)))
            self.select_course()
        else:
            self.choice.set('手动填写课程 ID')
        elective = sum(c['category'] == '选修课' for c in courses)
        club = sum(c['category'] == '社团课' for c in courses)
        self.catalog_info.set(f'目录 {len(courses)} 门：选修课 {elective} 门、社团课 {club} 门；当前筛选 {len(filtered)} 门。目录仅包含账号可见课程。')
        self.update_summary()

    def select_course(self, _event=None):
        c = self.course_options.get(self.choice.get())
        if c:
            self.cid.set(c['id'])
        self.update_summary()

    def update_summary(self):
        c = next((c for c in self.available_courses() if c['id'] == self.cid.get()), None)
        if hasattr(self, 'course_options'):
            label = next((label for label, row in self.course_options.items() if row['id'] == self.cid.get()), None)
            self.choice.set(label or '手动填写课程 ID')
        text = f"目标：{c['name']}（{c['category']}）" if c else '目标：手动输入的课程 ID'
        if c and c.get('teacher'):
            text += f" · 教师：{c['teacher']} · 地点：{c.get('place', '')}"
        self.course_info.set(text + f' · 操作：{self.action.get()}')

    def insert_account(self, raw, alias=''):
        cookie = normalize_cookie(raw)
        if any(a.cookie == cookie for a in self.accounts.values()):
            return False
        key = uuid.uuid4().hex
        alias = alias.strip()[:40] or f'账号 {len(self.accounts) + 1}'
        self.accounts[key] = Account(key, alias, cookie)
        self.account_tree.insert('', 'end', iid=key, values=(alias, '尚未发送', '—', '—'))
        return True

    def add_account(self):
        if self.busy:
            return
        try:
            added = self.insert_account(self.cookie.get(), self.alias.get())
        except ValueError as exc:
            self.notify(str(exc), '失败')
            return
        self.cookie.set('')
        self.alias.set('')
        self.notify(f'已添加，共 {len(self.accounts)} 个账号。' if added else '该 Cookie 已添加，未重复加入。')

    def bulk_dialog(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('批量添加 Cookie')
        dialog.geometry('700x330')
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text='每行一个账号的完整 Cookie；自动分配备注，完全相同的 Cookie 自动去重。', wraplength=660).pack(anchor='w', padx=12, pady=10)
        editor = scrolledtext.ScrolledText(dialog, height=10, wrap='none')
        editor.pack(fill='both', expand=True, padx=12)
        feedback = tk.StringVar()
        ttk.Label(dialog, textvariable=feedback).pack(anchor='w', padx=12)
        def add_all():
            lines = [line.strip() for line in editor.get('1.0', 'end').splitlines() if line.strip()]
            try:
                normalized = [normalize_cookie(line) for line in lines]
            except ValueError as exc:
                feedback.set(str(exc))
                return
            count = sum(self.insert_account(line) for line in normalized)
            editor.delete('1.0', 'end')
            dialog.destroy()
            self.notify(f'新增 {count} 个账号，共 {len(self.accounts)} 个；重复项已跳过。')
        ttk.Button(dialog, text='添加全部', command=add_all).pack(pady=10)

    def remove_accounts(self):
        if self.busy:
            return
        for key in self.account_tree.selection():
            self.accounts.pop(key, None)
            self.account_tree.delete(key)
        self.notify(f'剩余 {len(self.accounts)} 个账号。')

    def make_plan(self, scheduled=False):
        try:
            rounds, interval = int(self.rounds.get()), int(self.interval.get())
        except ValueError:
            raise ValueError('轮数和间隔必须为整数。') from None
        c = next((c for c in self.available_courses() if c['id'] == self.cid.get().strip()), None)
        target = parse_start(self.start_text.get().strip()) if scheduled else None
        if target is not None and target <= time.time():
            raise ValueError('定时时间已过去，请设置未来时间或点击“立即开始”。')
        plan = Plan(self.action.get(), self.tid.get().strip(), self.cid.get().strip(), c['name'] if c else '手动课程', tuple(self.accounts.values()), rounds, interval, target)
        validate_plan(plan)
        return plan

    def preview(self):
        try:
            p = self.make_plan()
        except ValueError as exc:
            self.notify(str(exc), '失败')
            return
        self.display(f'操作：{p.action}；课程：{p.course_name}（{p.cid}）\n账号：{len(p.accounts)} 个；轮数：{p.rounds}；共 {len(p.accounts) * p.rounds} 次请求\n轮间等待：{p.interval_ms} ms（上轮全部完成后计时）\n定时输入：{self.start_text.get()} 北京时间\n\nPOST {ACTION_URLS[p.action]}\nUser-Agent: {USER_AGENT}\nContent-Type: application/x-www-form-urlencoded; charset=UTF-8\nCookie: [分别使用每个账号的 Cookie，不显示凭据]\n\ntid={p.tid}&courseid={p.cid}\n\n立即开始和启动定时均使用上述轮数；只有点击相应按钮后才执行。')
        self.notify('任务预览已生成，尚未发送。')

    def send(self, scheduled=False):
        if self.busy:
            return
        try:
            plan = self.make_plan(scheduled)
        except ValueError as exc:
            self.notify(str(exc), '失败')
            return
        self.active_plan = plan
        self.task_error = None
        self.cancel = threading.Event()
        self.counters = {'成功': 0, '失败': 0, '待核对': 0}
        self.target_time = plan.start_at
        self.set_busy(True)
        self.notify(f'{"定时已启动" if scheduled else "开始发送"}：{plan.action} · {plan.course_name} · {len(plan.accounts)} 个账号 × {plan.rounds} 轮。')
        threading.Thread(target=run_plan, args=(plan, self.cancel, lambda kind, value: self.events.put((kind, value))), daemon=True).start()

    def stop(self):
        self.cancel.set()
        self.target_time = None
        self.notify('正在停止后续发送；已发出的请求会继续返回结果。')

    def refresh_catalog(self):
        if self.busy:
            return
        if not self.accounts:
            self.notify('请先添加账号 Cookie，再刷新课程。', '失败')
            return
        tid, accounts = self.tid.get().strip(), tuple(self.accounts.values())
        if not tid.isascii() or not tid.isdecimal():
            self.notify('活动 ID 必须为数字。', '失败')
            return
        self.cancel = threading.Event()
        self.set_busy(True)
        self.notify(f'正在合并 {len(accounts)} 个账号当前可见的课程目录……')
        def worker():
            merged = {}
            try:
                for account in accounts:
                    if self.cancel.is_set():
                        raise ValueError('目录刷新已取消，原目录保留。')
                    rows = fetch_catalog(tid, account.cookie)
                    for row in rows:
                        previous = merged.get(row['id'])
                        if previous and row['category'] == '未分类':
                            row['category'] = previous['category']
                        merged[row['id']] = row
                if self.cancel.is_set():
                    raise ValueError('目录刷新已取消，原目录保留。')
                self.events.put(('catalog', {'tid': tid, 'updated': now_text(), 'courses': list(merged.values()), 'visible_count': len(merged)}))
            except Exception as exc:
                self.events.put(('catalog_error', str(exc) if isinstance(exc, ValueError) else '刷新失败，请检查网络与各账号 Cookie；原目录保留。'))
        threading.Thread(target=worker, daemon=True).start()

    def show_detail(self, _event=None):
        selected = self.log.selection()
        if selected:
            self.display(self.details.get(selected[0], ''))

    def on_result(self, data):
        round_no, key, name, result = data
        self.counters[result.label] += 1
        self.serial += 1
        iid = str(self.serial)
        self.log.insert('', 'end', iid=iid, values=(round_no, name, result.started, result.label, f'{result.elapsed_ms:.1f}', result.message), tags=(result.label,))
        self.log.see(iid)
        detail = f'{name} · 第 {round_no} 轮 · {self.active_plan.action} · {self.active_plan.course_name}\n发送：{result.started} 北京时间；耗时 {result.elapsed_ms:.1f} ms\n结果：{result.label}；HTTP {result.http or "—"}；{result.message}\n\n{result.body}'
        self.details[iid] = detail
        self.display(detail)
        children = self.log.get_children()
        if len(children) > 2000:
            self.log.delete(children[0])
            self.details.pop(children[0], None)
        if key in self.accounts:
            self.account_tree.item(key, values=(name, f'{result.label}：{result.message}', result.http or '—', result.started))
        self.notify(f'{name} · 第 {round_no} 轮 · {self.active_plan.action}{result.label}：{result.message}', result.label)

    def poll(self):
        for _ in range(200):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'result':
                self.on_result(value)
            elif kind == 'waiting':
                self.target_time = value
            elif kind == 'round':
                self.target_time = None
                self.countdown.set(f'第 {value} / {self.active_plan.rounds} 轮')
            elif kind == 'error':
                self.task_error = value
                self.notify(value, '失败')
            elif kind == 'done':
                self.target_time = None
                self.set_busy(False)
                summary = ' · '.join(f'{k} {v}' for k, v in self.counters.items())
                if self.task_error:
                    self.notify('任务异常：' + self.task_error + ' | ' + summary, '失败')
                else:
                    self.notify(('已停止' if value else '任务完成') + ' | ' + summary, '成功' if self.counters['成功'] and not self.counters['失败'] and not self.counters['待核对'] else 'info')
                self.countdown.set('')
            elif kind == 'catalog':
                self.catalog = value
                saved = True
                try:
                    temp = CATALOG_PATH.with_suffix('.json.tmp')
                    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                    temp.replace(CATALOG_PATH)
                except OSError:
                    saved = False
                self.set_busy(False)
                self.filter_courses()
                self.notify(f'目录已更新，共 {len(value["courses"])} 门。' + ('' if saved else '磁盘保存失败，本次会话仍可使用。'))
            elif kind == 'catalog_error':
                self.set_busy(False)
                self.notify(value, '失败')
        if self.target_time is not None:
            remaining = max(0, self.target_time - time.time())
            hours, rest = divmod(remaining, 3600)
            minutes, seconds = divmod(rest, 60)
            self.countdown.set(f'倒计时 {int(hours):02}:{int(minutes):02}:{seconds:06.3f}')
        self.root.after(50, self.poll)

    def close(self):
        self.cancel.set()
        self.accounts.clear()
        self.cookie.set('')
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
