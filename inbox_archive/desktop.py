"""Compact native desktop UI. Widgets live exclusively on the Tk main thread."""
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from concurrent.futures import CancelledError
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, ttk
from tkinter import font as tkfont

from .database import Database
from .desktop_runtime import DesktopController, redact
from .desktop_settings import ConfigStore, DEFAULTS, SECRET_KEYS
from .export import export_archive
from .graph import GraphClient, GraphError


BG = '#ffffff'
CHROME = '#f6f7f9'
TEXT = '#20252d'
MUTED = '#626975'
LINE = '#e5e8ed'
BLUE = '#285dd5'
GREEN = '#22734c'
RED = '#b23c3c'


def local_time(value):
    if not value:
        return 'Not yet synced'
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone()
        offset = stamp.strftime('%z')
        return stamp.strftime('%b %d, %I:%M %p') + f' (UTC{offset[:3]}:{offset[3:]})'
    except ValueError:
        return 'Time unavailable'


class DesktopApp:
    def __init__(self, window, app_dir, autostart=True, controller_factory=DesktopController):
        self.window = window
        self.store = ConfigStore(app_dir)
        self.events = queue.Queue(maxsize=200)
        self.controller = controller_factory(self.store.root, self.events)
        self.variables = {key: tk.StringVar(value=value) for key, value in self.store.read().items() if key in DEFAULTS}
        self.phase = 'Stopped'
        self.busy = False
        self.exporting = False
        self.closing = False
        self.close_deadline = None
        self.cancel_export = threading.Event()
        self.export_thread = None
        self.callback = ''
        self.latest_export = None
        self.activity = []
        self.logs_open = False
        self.settings_open = False
        self.advanced_open = False
        self.callback_open = False
        self.configured = False
        self.worker_status = {}
        self._style()
        self._build()
        self.set_phase('Stopped')
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.report_callback_exception = self.callback_error
        self.window.after(100, self.pump)
        self._refresh_archive()
        try:
            self.store.validate(self.store.read())
        except (ValueError, OSError):
            self.show_settings()
            self.settings_feedback.set('Add your Page credentials to get started.')
        else:
            self.configured = True
            if autostart:
                self.window.after(400, self.start)

    def _style(self):
        self.window.title('Inbox Archive')
        self.window.geometry('720x610')
        self.window.minsize(600, 560)
        self.window.configure(bg=BG)
        for name in ('TkDefaultFont', 'TkTextFont'):
            tkfont.nametofont(name).configure(family='Segoe UI', size=10)
        style = ttk.Style(self.window)
        style.theme_use('clam')
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=TEXT, font=('Segoe UI', 10))
        style.configure('Muted.TLabel', foreground=MUTED, font=('Segoe UI', 9))
        style.configure('Title.TLabel', font=('Segoe UI', 18))
        style.configure('Status.TLabel', font=('Segoe UI', 15))
        style.configure('Count.TLabel', font=('Segoe UI', 23))
        style.configure('TButton', background=BG, foreground=TEXT, padding=(13, 8), borderwidth=1,
            relief='solid', bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.map('TButton', background=[('active', CHROME)], foreground=[('disabled', MUTED)])
        style.configure('Primary.TButton', background=BLUE, foreground='#ffffff', bordercolor=BLUE)
        style.map('Primary.TButton', background=[('active', '#204fb9'), ('disabled', '#c5cfdf')], foreground=[('disabled', '#ffffff')])
        style.configure('Link.TButton', background=BG, foreground=BLUE, padding=(0, 4), borderwidth=0)
        style.configure('TEntry', padding=8, fieldbackground=BG, foreground=TEXT, bordercolor=LINE)
        style.configure('TCombobox', padding=6, fieldbackground=BG, foreground=TEXT)
        style.configure('TSeparator', background=LINE, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)

    def label(self, parent, text='', style='TLabel', **kwargs):
        return ttk.Label(parent, text=text, style=style, **kwargs)

    def button(self, parent, text, command, primary=False):
        return ttk.Button(parent, text=text, command=command, style='Primary.TButton' if primary else 'TButton')

    def _build(self):
        footer = tk.Frame(self.window, bg=CHROME, height=36)
        footer.pack(side='bottom', fill='x')
        tk.Label(footer, text='Saved on this PC', bg=CHROME, fg=MUTED, font=('Segoe UI', 8)).pack(side='left', padx=18, pady=10)
        tk.Label(footer, text='Close window to stop', bg=CHROME, fg=MUTED, font=('Segoe UI', 8)).pack(side='right', padx=18)
        self.content = ttk.Frame(self.window)
        self.content.pack(fill='both', expand=True)
        self.overview = ttk.Frame(self.content, padding=(30, 24))
        self.overview.pack(fill='both', expand=True)
        header = ttk.Frame(self.overview)
        header.pack(fill='x', pady=(0, 28))
        titles = ttk.Frame(header)
        titles.pack(side='left')
        self.label(titles, 'Overview', 'Title.TLabel').pack(anchor='w')
        self.label(titles, 'Your Facebook Page archive', 'Muted.TLabel').pack(anchor='w', pady=(4, 0))
        self.settings_button = self.button(header, 'Settings', self.show_settings)
        self.settings_button.pack(side='right')
        status = ttk.Frame(self.overview)
        status.pack(fill='x', pady=(0, 24))
        self.status_icon = tk.Label(status, text='✓', bg='#eaf5ee', fg=GREEN, font=('Segoe UI', 18), width=2)
        self.status_icon.pack(side='left', padx=(0, 14), ipady=4)
        status_text = ttk.Frame(status)
        status_text.pack(side='left')
        self.status_label = self.label(status_text, 'Sync stopped', 'Status.TLabel')
        self.status_label.pack(anchor='w')
        self.last_sync = tk.StringVar(value='Not yet synced')
        self.label(status_text, style='Muted.TLabel', textvariable=self.last_sync).pack(anchor='w', pady=(4, 0))
        self.auto_label = self.label(status, 'Auto-sync off', 'Muted.TLabel')
        self.auto_label.pack(side='right')
        ttk.Separator(self.overview).pack(fill='x')
        counts = ttk.Frame(self.overview)
        counts.pack(fill='x', pady=18)
        self.counts = {}
        for column, (key, title) in enumerate([('conversations', 'Conversations'), ('messages', 'Messages'), ('downloaded_attachments', 'Files saved')]):
            counts.columnconfigure(column, weight=1)
            box = ttk.Frame(counts)
            box.grid(row=0, column=column, sticky='ew', padx=(0 if not column else 20, 0))
            self.label(box, title, 'Muted.TLabel').pack(anchor='w')
            self.counts[key] = tk.StringVar(value='0')
            self.label(box, style='Count.TLabel', textvariable=self.counts[key]).pack(anchor='w', pady=(4, 0))
        ttk.Separator(self.overview).pack(fill='x', pady=(0, 18))
        connections = ttk.Frame(self.overview)
        connections.pack(fill='x')
        self.page_status = self._connection_row(connections, 'Facebook Page', 'Not checked')
        self.tunnel_status = self._connection_row(connections, 'Webhook tunnel', 'Stopped')
        callback_row = ttk.Frame(connections)
        callback_row.pack(fill='x', pady=(2, 0))
        ttk.Button(callback_row, text='Callback URL ▾', style='Link.TButton', command=self.toggle_callback).pack(side='left')
        self.interval_label = self.label(callback_row, 'Checks every 5 minutes', 'Muted.TLabel')
        self.interval_label.pack(side='right')
        self.callback_box = ttk.Frame(connections)
        self.callback_text = tk.StringVar(value='Start sync to create a temporary tunnel address.')
        self.label(self.callback_box, textvariable=self.callback_text, style='Muted.TLabel', wraplength=580).pack(anchor='w')
        self.label(self.callback_box, 'If the address changes, update the callback in Meta.', 'Muted.TLabel').pack(anchor='w', pady=5)
        callback_buttons = ttk.Frame(self.callback_box)
        callback_buttons.pack(anchor='w')
        self.button(callback_buttons, 'Copy URL', self.copy_callback).pack(side='left', padx=(0, 8))
        self.button(callback_buttons, 'Copy verify token', self.copy_verify).pack(side='left')
        actions = ttk.Frame(self.overview)
        actions.pack(fill='x', pady=(24, 0))
        self.export_button = self.button(actions, 'Export archive', self.export, primary=True)
        self.export_button.pack(side='left', padx=(0, 10))
        self.toggle_button = self.button(actions, 'Start sync', self.toggle)
        self.toggle_button.pack(side='left')
        self.open_export_button = ttk.Button(actions, text='Open folder', style='Link.TButton', command=self.open_export)
        self.feedback = tk.StringVar()
        self.feedback_label = self.label(self.overview, textvariable=self.feedback, style='Muted.TLabel', wraplength=620)
        self.feedback_label.pack(anchor='w', pady=(12, 0))
        ttk.Button(self.overview, text='Recent activity ▾', style='Link.TButton', command=self.toggle_activity).pack(anchor='w', pady=(12, 0))
        self.activity_text = tk.Text(self.overview, height=5, bg=CHROME, fg=MUTED, relief='flat', wrap='word', font=('Segoe UI', 9), state='disabled')
        self._build_settings()

    def _connection_row(self, parent, title, initial):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=(0, 12))
        self.label(row, title).pack(side='left')
        value = tk.StringVar(value=initial)
        self.label(row, textvariable=value, style='Muted.TLabel').pack(side='right')
        return value

    def _build_settings(self):
        self.settings_view = ttk.Frame(self.content)
        settings_footer = ttk.Frame(self.settings_view, padding=(30, 12))
        settings_footer.pack(side='bottom', fill='x')
        self.settings_actions = ttk.Frame(settings_footer)
        self.settings_actions.pack(fill='x')
        self.settings_feedback = tk.StringVar()
        self.label(settings_footer, textvariable=self.settings_feedback, style='Muted.TLabel', wraplength=570).pack(anchor='w', pady=(8, 0))
        self.settings_canvas = tk.Canvas(self.settings_view, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(self.settings_view, orient='vertical', command=self.settings_canvas.yview)
        self.settings_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.settings_canvas.pack(side='left', fill='both', expand=True)
        body = ttk.Frame(self.settings_canvas, padding=(30, 24))
        item = self.settings_canvas.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda event: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox('all')))
        self.settings_canvas.bind('<Configure>', lambda event: self.settings_canvas.itemconfigure(item, width=event.width))
        self.window.bind('<MouseWheel>', self._scroll_settings, add='+')
        header = ttk.Frame(body)
        header.pack(fill='x', pady=(0, 20))
        self.label(header, 'Settings', 'Title.TLabel').pack(side='left')
        self.button(header, 'Back', self.show_overview).pack(side='right')
        self.label(body, 'Facebook Page').pack(anchor='w', pady=(0, 12))
        self._field(body, 'Page ID', 'META_PAGE_ID')
        self._field(body, 'Page access token', 'META_PAGE_ACCESS_TOKEN', secret=True)
        self._field(body, 'App secret', 'META_APP_SECRET', secret=True)
        test_row = ttk.Frame(body)
        test_row.pack(fill='x', pady=(0, 18))
        self.test_button = self.button(test_row, 'Test connection', self.test_connection)
        self.test_button.pack(side='left', padx=(0, 12))
        self.test_feedback = tk.StringVar()
        self.label(test_row, textvariable=self.test_feedback, style='Muted.TLabel', wraplength=360).pack(side='left')
        ttk.Separator(body).pack(fill='x', pady=(0, 16))
        self.label(body, 'Archive').pack(anchor='w', pady=(0, 12))
        self._field(body, 'Save location', 'DATA_DIR', browse='directory')
        interval_row = ttk.Frame(body)
        interval_row.pack(fill='x', pady=(0, 12))
        self.label(interval_row, 'Check for new messages', 'Muted.TLabel').pack(anchor='w', pady=(0, 5))
        self.interval_options = {'Every minute': '60', 'Every 5 minutes': '300', 'Every 15 minutes': '900'}
        self.interval_choice = tk.StringVar()
        self.interval_picker = ttk.Combobox(interval_row, textvariable=self.interval_choice, state='readonly')
        self.interval_picker.pack(fill='x')
        ttk.Button(body, text='Advanced settings ▾', style='Link.TButton', command=self.toggle_advanced).pack(anchor='w', pady=(3, 12))
        self.advanced = ttk.Frame(body)
        self._field(self.advanced, 'Webhook verify token', 'WEBHOOK_VERIFY_TOKEN', secret=True)
        self._field(self.advanced, 'API version', 'META_API_VERSION')
        self._field(self.advanced, 'Webhook port', 'PORT')
        self._field(self.advanced, 'Full history check (hours)', 'FULL_SCAN_HOURS')
        self._field(self.advanced, 'Maximum file size (MB)', 'MAX_ATTACHMENT_MB')
        self._field(self.advanced, 'Cloudflared executable (optional)', 'CLOUDFLARED_PATH', browse='executable')
        ttk.Button(self.advanced, text='Cloudflared installation help', style='Link.TButton',
            command=lambda: webbrowser.open('https://developers.cloudflare.com/tunnel/downloads/')).pack(anchor='w', pady=(0, 12))
        self.label(self.advanced, 'Desktop mode uses saved settings. Terminal environment overrides do not apply.',
            'Muted.TLabel', wraplength=560).pack(anchor='w', pady=(0, 12))
        self.save_button = self.button(self.settings_actions, 'Save settings', self.save_settings, primary=True)
        self.save_button.pack(side='left', padx=(0, 10))
        self.button(self.settings_actions, 'Cancel', self.cancel_settings).pack(side='left')
        self.label(self.settings_actions, 'Stored on this PC', 'Muted.TLabel').pack(side='right')

    def _field(self, parent, title, key, secret=False, browse=None, choices=None):
        box = ttk.Frame(parent)
        box.pack(fill='x', pady=(0, 12))
        self.label(box, title, 'Muted.TLabel').pack(anchor='w', pady=(0, 5))
        row = ttk.Frame(box)
        row.pack(fill='x')
        if choices:
            entry = ttk.Combobox(row, textvariable=self.variables[key], values=choices, state='normal')
        else:
            entry = ttk.Entry(row, textvariable=self.variables[key], show='●' if secret else '')
        entry.pack(side='left', fill='x', expand=True)
        if secret:
            reveal = ttk.Button(row, text='Show', width=6)
            def toggle():
                hidden = bool(entry.cget('show'))
                entry.configure(show='' if hidden else '●')
                reveal.configure(text='Hide' if hidden else 'Show')
            reveal.configure(command=toggle)
            reveal.pack(side='right', padx=(8, 0))
        if browse:
            def choose():
                if browse == 'directory':
                    value = filedialog.askdirectory(parent=self.window, title='Select archive folder')
                else:
                    value = filedialog.askopenfilename(parent=self.window, title='Locate cloudflared.exe', filetypes=[('Executable', '*.exe')])
                if value:
                    self.variables[key].set(value)
            self.button(row, 'Browse', choose).pack(side='right', padx=(8, 0))
        if key == 'POLL_SECONDS':
            self.label(box, 'Seconds · 300 = every 5 minutes', 'Muted.TLabel').pack(anchor='w', pady=(4, 0))

    def _scroll_settings(self, event):
        if self.settings_open:
            self.settings_canvas.yview_scroll(int(-event.delta / 120), 'units')

    def show_settings(self):
        if self.closing:
            return
        for key, value in self.store.read().items():
            if key in self.variables:
                self.variables[key].set(value)
        current = self.variables['POLL_SECONDS'].get()
        options = dict(self.interval_options)
        choice = next((label for label, value in options.items() if value == current), None)
        if choice is None:
            choice = f'Every {current} seconds'
            options[choice] = current
        self.visible_intervals = options
        self.interval_picker.configure(values=list(options))
        self.interval_choice.set(choice)
        self.settings_open = True
        self.overview.pack_forget()
        self.settings_view.pack(fill='both', expand=True)
        self.resize_window(740)

    def show_overview(self):
        self.settings_open = False
        self.settings_view.pack_forget()
        self.overview.pack(fill='both', expand=True)
        self.resize_window(610)

    def resize_window(self, desired_height):
        width = min(720, self.window.winfo_screenwidth() - 40)
        height = min(desired_height, self.window.winfo_screenheight() - 100)
        x = max(0, min(self.window.winfo_x(), self.window.winfo_screenwidth() - width - 20))
        y = max(0, min(self.window.winfo_y(), self.window.winfo_screenheight() - height - 70))
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    def cancel_settings(self):
        self.show_overview()

    def toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.advanced.pack(fill='x')
        else:
            self.advanced.pack_forget()

    def toggle_callback(self):
        self.callback_open = not self.callback_open
        if self.callback_open:
            self.callback_box.pack(fill='x', pady=(8, 0))
        else:
            self.callback_box.pack_forget()

    def toggle_activity(self):
        self.logs_open = not self.logs_open
        if self.logs_open:
            self.activity_text.pack(fill='x', pady=(6, 0))
            self.resize_window(720)
        else:
            self.activity_text.pack_forget()
            self.resize_window(610)

    def background(self, callback, done_kind='task_done'):
        def run():
            try:
                result = callback()
                self.events.put({'kind': done_kind, 'result': result})
            except CancelledError:
                self.events.put({'kind': 'export_cancelled'})
            except Exception as exc:
                values = self.store.read()
                safe = str(exc) if isinstance(exc, (ValueError, GraphError)) else 'Could not complete that action. Check connection, folder access and available disk space.'
                self.events.put({'kind': 'task_error', 'task': done_kind,
                    'message': redact(safe, [values.get(key, '') for key in SECRET_KEYS])})
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def _refresh_archive(self):
        def read():
            settings = self.store.settings()
            if settings.page_id and (settings.data_dir / 'inbox.sqlite3').exists():
                return Database(settings.data_dir, settings.page_id).status()
        self.background(read, 'archive_loaded')

    def start(self):
        if self.busy or self.closing or self.controller.running:
            return
        try:
            self.store.validate(self.store.read())
        except (ValueError, OSError) as exc:
            self.show_settings()
            self.settings_feedback.set(str(exc))
            return
        self.busy = True
        self.feedback.set('')
        self.set_phase('Starting')
        self.background(self.controller.start)

    def toggle(self):
        if self.busy or self.closing:
            return
        if self.controller.running:
            self.busy = True
            self.set_phase('Stopping')
            self.background(self.controller.stop)
        else:
            self.start()

    def set_phase(self, phase):
        self.phase = phase
        self.status_label.configure(text='Sync stopped' if phase == 'Stopped' else phase)
        self.status_icon.configure(text='✓' if phase == 'Up to date' else '!' if phase == 'Needs attention' else '•',
            fg=RED if phase == 'Needs attention' else GREEN if phase == 'Up to date' else MUTED)
        active = phase in ('Starting', 'Syncing', 'Up to date', 'Needs attention') and self.controller.running
        self.auto_label.configure(text='Auto-sync on' if active else 'Auto-sync off', foreground=GREEN if active else MUTED)
        self.toggle_button.configure(text='Stop sync' if self.controller.running else 'Start sync',
            state='disabled' if phase in ('Starting', 'Stopping') else 'normal')

    def update_counts(self, counts):
        if not counts:
            return
        self.worker_status = counts
        for key, variable in self.counts.items():
            variable.set(f"{counts.get(key, 0):,}")
        stamp = counts.get('sync', {}).get('last_sync_success')
        self.last_sync.set('Last synced ' + local_time(stamp) if stamp else 'Not yet synced')
        if counts.get('sync', {}).get('last_error'):
            self.page_status.set('Needs attention')
            self.feedback.set(counts['sync']['last_error'])
        elif stamp:
            self.page_status.set('Connected' if self.controller.running else 'Last check successful')
        problems = counts.get('failed_attachments', 0) + counts.get('unavailable_attachments', 0)
        if problems:
            self.feedback.set(f'{problems} file(s) need attention. Collection will retry available downloads.')
        try:
            seconds = self.store.settings().poll_seconds
            self.interval_label.configure(text=f'Checks every {seconds // 60:g} minutes' if seconds % 60 == 0 else f'Checks every {seconds} seconds')
        except ValueError:
            pass

    def save_settings(self):
        if self.busy or self.exporting or self.closing:
            self.settings_feedback.set('Wait for the current action to finish before saving.')
            return
        values = {key: value.get().strip() for key, value in self.variables.items()}
        values['POLL_SECONDS'] = self.visible_intervals[self.interval_choice.get()]
        try:
            self.store.validate(values)
        except (ValueError, OSError) as exc:
            self.settings_feedback.set(str(exc))
            return
        was_running = self.controller.running
        first_setup = not self.configured
        self.busy = True
        self.save_button.configure(state='disabled')
        self.settings_feedback.set('Saving settings…')
        def save():
            # Stop before applying a new port or archive path.
            if was_running:
                self.controller.stop()
            self.store.save(values)
            if was_running:
                self.controller.start()
            return {'was_running': was_running, 'first_setup': first_setup}
        self.background(save, 'settings_saved')

    def test_connection(self):
        if self.closing:
            return
        values = {key: value.get().strip() for key, value in self.variables.items()}
        try:
            settings = self.store.validate(values)
        except (ValueError, OSError) as exc:
            self.test_feedback.set(str(exc))
            return
        self.test_button.configure(state='disabled')
        self.test_feedback.set('Checking…')
        def check():
            graph = GraphClient(settings.page_token, settings.api_version, settings.app_secret)
            page = graph.get(settings.page_id, {'fields': 'id,name'})
            if str(page.get('id')) != settings.page_id:
                raise ValueError('The token returned a different Page.')
            graph.get(settings.page_id + '/conversations', {'platform': 'MESSENGER', 'limit': 1, 'fields': 'id'})
            return 'Connected to ' + str(page.get('name') or 'your Page')
        self.background(check, 'connection_tested')

    def export(self):
        if self.exporting or self.closing:
            return
        try:
            settings = self.store.settings()
            if not (settings.data_dir / 'inbox.sqlite3').exists():
                self.feedback.set('No archive yet. Start sync before exporting.')
                return
            self.store.validate(self.store.read())
        except (ValueError, OSError) as exc:
            self.feedback.set(str(exc))
            return
        self.exporting = True
        self.cancel_export.clear()
        self.export_button.configure(text='Exporting…', state='disabled')
        self.feedback.set('Creating an archive snapshot…')
        target = self.store.root / 'exports' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.export_thread = self.background(lambda: str(export_archive(Database(settings.data_dir, settings.page_id), target, cancel=self.cancel_export)), 'exported')

    def open_export(self):
        if self.latest_export and Path(self.latest_export).is_dir():
            os.startfile(self.latest_export)

    def copy_callback(self):
        if self.callback:
            self.window.clipboard_clear()
            self.window.clipboard_append(self.callback)
            self.feedback.set('Callback URL copied.')
        else:
            self.feedback.set('No tunnel address yet. Start sync and check the tunnel status.')

    def copy_verify(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.store.read()['WEBHOOK_VERIFY_TOKEN'])
        self.feedback.set('Verify token copied.')

    def add_activity(self, message):
        self.activity.append(datetime.now().strftime('%I:%M %p') + '   ' + message)
        self.activity = self.activity[-50:]
        self.activity_text.configure(state='normal')
        self.activity_text.delete('1.0', 'end')
        self.activity_text.insert('1.0', '\n'.join(reversed(self.activity)))
        self.activity_text.configure(state='disabled')

    def pump(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event['kind']
            if kind == 'phase':
                self.set_phase(event['state'])
            elif kind == 'status':
                self.update_counts(event['counts'])
            elif kind == 'archive_loaded':
                self.update_counts(event.get('result'))
            elif kind == 'tunnel':
                self.tunnel_status.set(event['state'])
                if event.get('callback') and event['callback'] != self.callback:
                    self.callback = event['callback']
                    self.callback_text.set(self.callback)
                    self.feedback.set('New callback URL. Update it in Meta if it changed.')
                    self.add_activity('A temporary callback address is available.')
            elif kind in ('activity', 'error'):
                self.add_activity(event['message'])
                if kind == 'error':
                    self.feedback.set(event['message'])
                    self.set_phase('Needs attention')
            elif kind == 'task_done':
                self.busy = False
                self.set_phase(self.phase)
            elif kind == 'settings_saved':
                self.configured = True
                self.busy = False
                self.save_button.configure(state='normal')
                self.settings_feedback.set('Saved.')
                self.feedback.set('Settings saved.')
                self.show_overview()
                self._refresh_archive()
                if event['result']['first_setup'] and not event['result']['was_running']:
                    self.start()
            elif kind == 'connection_tested':
                self.test_button.configure(state='normal')
                self.test_feedback.set(event['result'])
            elif kind in ('exported', 'export_cancelled'):
                self.exporting = False
                self.export_button.configure(text='Export archive', state='normal')
                if kind == 'exported':
                    self.latest_export = event['result']
                    self.open_export_button.pack(side='right')
                    self.feedback.set('Export ready. Open the folder to view your files.')
                    self.add_activity('Archive exported successfully.')
            elif kind == 'task_error':
                self.busy = False
                self.save_button.configure(state='normal')
                self.test_button.configure(state='normal')
                self.export_button.configure(text='Export archive', state='normal')
                if event['task'] == 'exported':
                    self.exporting = False
                self.feedback.set(event['message'])
                self.settings_feedback.set(event['message'])
                self.test_feedback.set(event['message'] if event['task'] == 'connection_tested' else '')
                self.add_activity(event['message'])
                self.set_phase('Needs attention')
            elif kind == 'closed':
                self._finish_close()
                return
        if not self.closing or self.window.winfo_exists():
            self.window.after(100, self.pump)

    def callback_error(self, exception, value, traceback):
        self.feedback.set('That action could not finish. Check your settings and try again.')

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.cancel_export.set()
        self.close_deadline = time.monotonic() + 5
        self.set_phase('Stopping')
        self.feedback.set('Stopping collection and closing the tunnel…')
        self.settings_view.pack_forget()
        self.overview.pack(fill='both', expand=True)
        self.settings_button.configure(state='disabled')
        self.export_button.configure(state='disabled')
        def finish():
            self.controller.close()
            if self.export_thread:
                self.export_thread.join(timeout=max(0, self.close_deadline - time.monotonic()))
        self.background(finish, 'closed')
        self.window.after(5100, self._finish_close)

    def _finish_close(self):
        if self.window.winfo_exists():
            self.controller.job.close()
            self.window.destroy()


def run_desktop(root, autostart=True):
    window = tk.Tk()
    DesktopApp(window, root, autostart=autostart)
    window.mainloop()
