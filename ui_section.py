# ======================================================================
# 7. 界面
# ======================================================================
#
# Tk 的控件本身很朴素，所以观感靠这几件事撑起来：
#   * 卡片化：内容区放在带 1px 描边的圆角观感面板里，而不是裸文本框
#   * 明确的层级：强调色只给主操作（翻译）和结果文本
#   * 悬停反馈：所有可点元素都有 hover 态，避免"不知道能不能点"
#   * DPI 感知：高分屏下不开 DPI 感知会被系统拉伸成糊的
#   * 图标：exe/快捷方式/窗口/任务栏统一用同一枚 .ico

import tkinter as tk                              # noqa: E402
from tkinter import messagebox, ttk               # noqa: E402

import re                                         # noqa: E402


def _enable_dpi_awareness() -> bool:
    """尽量开启 DPI 感知。必须在 Tk 创建窗口之前调用。

    高分屏上如果不声明感知，Windows 会把整个窗口位图拉伸，字会糊。
    返回是否成功 —— 这决定了 Tk 报告的屏幕尺寸是物理像素还是逻辑像素。
    """
    import ctypes
    if os.name != "nt":
        return False
    try:                                          # Win10 1703+
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):             # PER_MONITOR_AWARE_V2
            log.info("DPI awareness: per-monitor v2")
            return True
    except Exception:
        pass
    try:                                          # Win8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        log.info("DPI awareness: per-monitor")
        return True
    except Exception:
        pass
    try:                                          # Vista+
        ctypes.windll.user32.SetProcessDPIAware()
        log.info("DPI awareness: system")
        return True
    except Exception:
        pass
    log.info("DPI awareness: unavailable")
    return False


def _system_dpi_scale() -> float:
    """系统 DPI 缩放系数（1.0 = 100%）。用注册表读，不依赖进程 DPI 感知状态。"""
    if os.name != "nt":
        return 1.0
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop\WindowMetrics")
        try:
            # AppliedDPI 存在时最准
            val, _ = winreg.QueryValueEx(key, "AppliedDPI")
            return max(1.0, float(val) / 96.0)
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass
    try:
        import ctypes
        dc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)   # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, dc)
        return max(1.0, float(dpi) / 96.0) if dpi else 1.0
    except Exception:
        return 1.0


def _screen_logical_size(root) -> tuple[int, int, float]:
    """返回当前屏幕的**逻辑**宽高以及换算用的缩放系数。

    Tk 报告的是物理像素还是逻辑像素取决于进程的 DPI 感知状态：
      * 感知成功 -> winfo_screenwidth 给物理像素，需要除以缩放才是可用逻辑空间
      * 感知失败 -> Windows 已经帮忙虚拟化过，winfo_screenwidth 就是逻辑像素
    两种情况都要让"窗口宽度不超过可用空间"，否则窗口会伸到屏幕外面，
    用户看到的就是"右半截不见了"。
    """
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    scale = _system_dpi_scale()
    if _DPI_AWARE_OK and scale > 1.0:
        return max(640, int(sw / scale)), max(480, int(sh / scale)), scale
    return sw, sh, scale


def _set_app_user_model_id() -> None:
    """给进程一个独立 AppUserModelID，任务栏才会用自己的图标而不是 Python 的。"""
    import ctypes
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "ArgosTranslate.DesktopTranslator.Offline.1")
    except Exception:
        log.info("SetCurrentProcessExplicitAppUserModelID failed")


def _icon_path() -> str:
    base = os.path.join(APP_DIR, "assets", "app.ico")
    if os.path.exists(base):
        return base
    bundled = os.path.join(getattr(sys, "_MEIPASS", "") or "", "assets", "app.ico")
    return bundled if os.path.exists(bundled) else ""


def _pick_font(root: tk.Tk) -> str:
    """挑一个屏幕上真的存在、且中英都好看的字族。"""
    from tkinter import font as tkfont
    available = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI",
                 "PingFang SC", "Noto Sans CJK SC", "SimHei"):
        if name in available:
            return name
    return "TkDefaultFont"


def _fmt_size(n: int) -> str:
    """把字节数变成人能读的大小。"""
    v = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GB"


# --- 配色：偏冷的深色，紫蓝强调 ---------------------------------------
THEME = {
    "bg":        "#0F1115",
    "surface":   "#171A21",
    "surface2":  "#1E222B",
    "border":    "#272C37",
    "border_hi": "#39404F",
    "fg":        "#E6E9EF",
    "fg_dim":    "#98A2B3",
    "fg_mute":   "#5C6675",
    "accent":    "#7C8CF8",
    "accent_hi": "#98A6FF",
    "accent_dk": "#5B6BE0",
    "good":      "#4ADE80",
    "warn":      "#FBBF24",
    "bad":       "#F87171",
    "result":    "#DCE4FF",
}


class Tooltip:
    """轻量 tooltip：悬停约半秒后在指针下方浮出说明。"""

    def __init__(self, widget, text: str, font, delay: int = 500):
        self.widget = widget
        self.text = text
        self.font = font
        self.delay = delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 4
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.attributes("-topmost", True)
        tk.Label(self._tip, text=self.text, font=self.font,
                 bg=THEME["surface2"], fg=THEME["fg"],
                 bd=0, padx=8, pady=4).pack()
        self._tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _event=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


class TranslateApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.results: queue.Queue = queue.Queue()
        self._busy = False
        self._after_id = None
        self._detect_gen = 0
        self._has_placeholder = False
        self._tooltips: list[Tooltip] = []

        self.root = tk.Tk()
        self.root.title("桌面翻译助手")
        self.root.configure(bg=THEME["bg"])

        # 初始尺寸必须按屏幕算，不能写死。
        # 之前写死 1040x700，配上一行放不下的顶部区域，窄屏上右半截直接被推到
        # 屏幕外，看起来就像"只有左边能用"。
        sw, sh, local_scale = _screen_logical_size(self.root)
        log.info("usable screen: %dx%d (dpi scale %.2f)", sw, sh, local_scale)
        win_w = max(760, min(1280, int(sw * 0.86)))
        win_h = max(520, min(820, int((sh - 60) * 0.92)))
        self.root.minsize(760, 520)
        self.root.geometry(f"{win_w}x{win_h}+{max(0, (sw - win_w) // 2)}"
                           f"+{max(0, (sh - win_h) // 2 - 15)}")

        # 有保存过的窗口位置就沿用 —— 但尺寸一律夹到当前可用空间内。
        # 只判"是否在屏幕内"是不够的：换显示器、改分辨率、或旧版本存过一个
        # 偏大的尺寸时，窗口会比屏幕还宽，右半截照样看不到。
        saved = self.settings.get("geometry") or ""
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", saved)
        if m:
            gw, gh, gx, gy = (int(v) for v in m.groups())
            cw = max(760, min(gw, sw - 40))
            ch = max(520, min(gh, sh - 60))
            cx = max(0, min(gx, sw - cw))
            cy = max(0, min(gy, sh - ch))
            if (cw, ch, cx, cy) != (gw, gh, gx, gy):
                log.info("clamped saved geometry %s -> %dx%d+%d+%d",
                         saved, cw, ch, cx, cy)
            self.root.geometry(f"{cw}x{ch}+{cx}+{cy}")
        elif saved:
            log.warning("ignoring unparsable geometry %r", saved)

        self.root.attributes("-topmost", bool(self.settings.get("topmost", True)))

        # 记一笔实际尺寸，排查"窗口跑出屏幕"类问题时不用猜
        self.root.after(300, self._log_geometry)

        _set_app_user_model_id()
        ico = _icon_path()
        if ico:
            try:
                self.root.iconbitmap(default=ico)
            except tk.TclError:
                log.warning("iconbitmap failed for %s", ico)

        self.family = _pick_font(self.root)
        self.f_tiny = (self.family, 8)
        self.f_small = (self.family, 9)
        self.f_body = (self.family, 11)
        self.f_title = (self.family, 13, "bold")
        self.f_btn = (self.family, 11, "bold")
        self.f_mono = ("Consolas", 9)

        # 显示器数字要跟着 DPI：4K 高分屏上 11pt 太小，低分屏上又偏大
        if local_scale >= 1.5:
            self.f_body = (self.family, 12)
            self.f_small = (self.family, 10)
            self.f_tiny = (self.family, 9)
            self.f_title = (self.family, 14, "bold")

        self._init_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(60, self._pump)
        self._start_warmup()

    # ---------------- ttk 主题 ----------------
    def _init_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "App.TCombobox",
            fieldbackground=THEME["surface2"],
            background=THEME["surface2"],
            foreground=THEME["fg"],
            arrowcolor=THEME["fg_dim"],
            bordercolor=THEME["border"],
            lightcolor=THEME["border"],
            darkcolor=THEME["border"],
            relief="flat",
            padding=(8, 5),
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", THEME["surface2"])],
            foreground=[("readonly", THEME["fg"])],
            bordercolor=[("focus", THEME["accent"]), ("hover", THEME["border_hi"])],
            arrowcolor=[("hover", THEME["accent"])],
        )
        # combobox 下拉列表的配色（Tk 的 popdown 是 Listbox，走 option 数据库）
        self.root.option_add("*TCombobox*Listbox.background", THEME["surface2"])
        self.root.option_add("*TCombobox*Listbox.foreground", THEME["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", THEME["accent_dk"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

        style.configure("Card.Treeview",
                        background=THEME["surface2"],
                        fieldbackground=THEME["surface2"],
                        foreground=THEME["fg"],
                        bordercolor=THEME["border"],
                        rowheight=24,
                        font=self.f_small)
        style.configure("Card.Treeview.Heading",
                        background=THEME["surface"],
                        foreground=THEME["fg_dim"],
                        relief="flat",
                        font=self.f_small)
        style.map("Card.Treeview",
                  background=[("selected", THEME["accent_dk"])],
                  foreground=[("selected", "#FFFFFF")])
        style.map("Card.Treeview.Heading",
                  background=[("active", THEME["surface2"])])

        style.configure("Vertical.TScrollbar",
                        background=THEME["surface2"],
                        troughcolor=THEME["bg"],
                        bordercolor=THEME["bg"],
                        arrowcolor=THEME["fg_mute"],
                        relief="flat")
        style.map("Vertical.TScrollbar",
                  background=[("active", THEME["border_hi"])])

    # ---------------- 小组件工厂 ----------------
    def _tip(self, widget, text: str) -> None:
        self._tooltips.append(Tooltip(widget, text, self.f_tiny))

    def _hover_label(self, parent, text, command, *, fg=None, bg=None,
                     font=None, pad=(10, 5), tip=None, hover_bg=None):
        """可点标签：带 hover 变色，观感上像按钮但没有原生边框。"""
        fg = fg or THEME["accent"]
        bg = bg or THEME["bg"]
        font = font or self.f_small
        hover_bg = hover_bg or THEME["surface2"]
        lbl = tk.Label(parent, text=text, fg=fg, bg=bg, font=font,
                       cursor="hand2", padx=pad[0], pady=pad[1])
        lbl.bind("<Enter>", lambda e: lbl.config(bg=hover_bg, fg=THEME["accent_hi"]))
        lbl.bind("<Leave>", lambda e: lbl.config(bg=bg, fg=fg))
        lbl.bind("<Button-1>", lambda e: command())
        if tip:
            self._tip(lbl, tip)
        return lbl

    def _button(self, parent, text, command, *, kind="normal", width=None,
                font=None, tip=None):
        """两种层级的按钮：primary 用强调色实心，normal 用描边风格。"""
        if kind == "primary":
            bg, fg, hov = THEME["accent"], "#FFFFFF", THEME["accent_hi"]
            abg, afg = THEME["accent_dk"], "#FFFFFF"
            bd, relief = 0, tk.FLAT
        else:
            bg, fg, hov = THEME["surface2"], THEME["fg"], THEME["border_hi"]
            abg, afg = THEME["border_hi"], THEME["fg"]
            bd, relief = 0, tk.FLAT
        btn = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                        activebackground=abg, activeforeground=afg,
                        font=font or self.f_small, bd=bd, relief=relief,
                        cursor="hand2", padx=14, pady=6,
                        highlightthickness=0)
        if width:
            btn.config(width=width)
        btn.bind("<Enter>", lambda e: btn.config(bg=hov))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg))
        if tip:
            self._tip(btn, tip)
        return btn

    def _card(self, parent, *, side=None, fill=tk.BOTH, expand=True,
              padx=(0, 0), pady=(0, 0)) -> tk.Frame:
        """带 1px 描边的卡片：外面一层当边框，里面一层当内容。"""
        outer = tk.Frame(parent, bg=THEME["border"], bd=0, highlightthickness=0)
        if side:
            outer.pack(side=side, fill=fill, expand=expand, padx=padx, pady=pady)
        inner = tk.Frame(outer, bg=THEME["surface"], bd=0, highlightthickness=0)
        inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        return inner

    # ---------------- 界面 ----------------
    def _build_ui(self) -> None:
        self._build_header()

        body = tk.Frame(self.root, bg=THEME["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(12, 0))
        self._build_input_card(body)
        self._build_output_card(body)

        self._build_footer()
        self._bind_keys()

    # ---- 顶部：品牌 + 语言选择 + 主操作 ----
    # 一行放不下时（窄窗口）自动折成两行：上行品牌+按钮，下行语言选择。
    # 之前写死一行，760px 宽时语言选择器会和按钮重叠。
    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=THEME["bg"])
        header.pack(fill=tk.X, padx=16, pady=(14, 0))
        self._header = header

        self._brand_row = tk.Frame(header, bg=THEME["bg"])
        self._brand_row.pack(fill=tk.X, side=tk.TOP)

        brand = tk.Frame(self._brand_row, bg=THEME["bg"])
        brand.pack(side=tk.LEFT)

        mark = tk.Canvas(brand, width=34, height=34, bg=THEME["bg"],
                         highlightthickness=0, bd=0)
        mark.pack(side=tk.LEFT)
        self._draw_mark(mark)

        titles = tk.Frame(brand, bg=THEME["bg"])
        titles.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(titles, text="桌面翻译助手", fg=THEME["fg"], bg=THEME["bg"],
                 font=self.f_title).pack(anchor=tk.W)
        self.sub_label = tk.Label(titles, text=self._subtitle(), fg=THEME["fg_mute"],
                                  bg=THEME["bg"], font=self.f_tiny)
        self.sub_label.pack(anchor=tk.W)

        # 主操作
        self.cancel_btn = self._button(self._brand_row, "取消", self._cancel_translate,
                                       tip="中止当前翻译")
        self.translate_btn = self._button(
            self._brand_row, "译  Translate", self._translate, kind="primary",
            font=self.f_btn, tip="Ctrl+Enter / F5")
        self.translate_btn.pack(side=tk.RIGHT, padx=(10, 0))

        self._picker = tk.Frame(header, bg=THEME["bg"])

        src_box = tk.Frame(self._picker, bg=THEME["bg"])
        src_box.pack(side=tk.LEFT)
        tk.Label(src_box, text="源语言", fg=THEME["fg_mute"], bg=THEME["bg"],
                 font=self.f_tiny).pack(anchor=tk.W)
        self.src_var = tk.StringVar(value=self._label_for(self.settings["src_lang"]))
        src_values = ["自动检测"] + [f"{c} · {n}" for c, n in _LANG_ROWS]
        self.src_combo = ttk.Combobox(src_box, textvariable=self.src_var,
                                      state="readonly", values=src_values,
                                      width=17, font=self.f_small,
                                      style="App.TCombobox")
        self.src_combo.pack()

        # 交换
        self.swap_lbl = tk.Label(self._picker, text="⇄", fg=THEME["fg_dim"],
                                 bg=THEME["bg"], font=(self.family, 14),
                                 cursor="hand2", padx=10)
        self.swap_lbl.pack(side=tk.LEFT, pady=(14, 0))
        self.swap_lbl.bind("<Enter>", lambda e: self.swap_lbl.config(
            fg=THEME["accent"], bg=THEME["surface2"]))
        self.swap_lbl.bind("<Leave>", lambda e: self.swap_lbl.config(
            fg=THEME["fg_dim"], bg=THEME["bg"]))
        self.swap_lbl.bind("<Button-1>", lambda e: self._swap_langs())
        self._tip(self.swap_lbl, "交换源语言与目标语言")

        tgt_box = tk.Frame(self._picker, bg=THEME["bg"])
        tgt_box.pack(side=tk.LEFT)
        tk.Label(tgt_box, text="目标语言", fg=THEME["fg_mute"], bg=THEME["bg"],
                 font=self.f_tiny).pack(anchor=tk.W)
        tgt_values = [f"{c} · {n}" for c, n in _LANG_ROWS]
        self.tgt_var = tk.StringVar(value=self._label_for(self.settings["tgt_lang"]))
        self.tgt_combo = ttk.Combobox(tgt_box, textvariable=self.tgt_var,
                                      state="readonly", values=tgt_values,
                                      width=17, font=self.f_small,
                                      style="App.TCombobox")
        self.tgt_combo.pack()

        self._header_stacked = None
        self.root.bind("<Configure>", self._on_root_configure)

    def _picker_needs_own_row(self) -> int:
        """返回"一行放不下"的宽度阈值。"""
        self.root.update_idletasks()
        need = (self._brand_row.winfo_reqwidth()
                + self._picker.winfo_reqwidth() + 40)
        return need

    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        need = self._picker_needs_own_row()
        stacked = event.width < need
        if stacked == self._header_stacked:
            return
        self._header_stacked = stacked
        self._picker.pack_forget()
        if stacked:
            # 语言选择单独占一行
            self._picker.pack(side=tk.TOP, anchor=tk.W, pady=(8, 0))
        else:
            self._picker.pack(side=tk.RIGHT)

    def _draw_mark(self, canvas: tk.Canvas) -> None:
        """在窗口里画一个和图标同款的小标记（渐变不好画，用同色系分层近似）。"""
        canvas.create_oval(2, 2, 32, 32, fill=THEME["accent_dk"], outline="")
        canvas.create_arc(2, 2, 32, 32, start=0, extent=180,
                          fill=THEME["accent"], outline="", style=tk.CHORD)
        canvas.create_text(17, 17, text="译", fill="#FFFFFF",
                           font=(self.family, 13, "bold"))

    def _subtitle(self) -> str:
        if not HAS_ENGINE:
            return "引擎不可用"
        where = "便携" if IS_PORTABLE else "LOCALAPPDATA"
        return f"离线 · {where} · Argos Translate"

    # ---- 输入卡片 ----
    def _build_input_card(self, parent) -> None:
        card = self._card(parent, side=tk.LEFT, padx=(0, 7))
        head = tk.Frame(card, bg=THEME["surface"])
        head.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(head, text="原文", fg=THEME["fg_dim"], bg=THEME["surface"],
                 font=self.f_small).pack(side=tk.LEFT)
        self.src_hint = tk.Label(head, text="", fg=THEME["fg_mute"],
                                 bg=THEME["surface"], font=self.f_tiny)
        self.src_hint.pack(side=tk.RIGHT)

        wrap = tk.Frame(card, bg=THEME["surface"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 12))
        self.input_text = tk.Text(
            wrap, bg=THEME["surface2"], fg=THEME["fg"], font=self.f_body,
            # width/height 必须显式给：Tk 的 Text 默认 80x24 字符，
            # 那会让每张卡片的最小宽度变成 ~1100px，双栏布局撑爆窗口。
            width=1, height=1,
            insertbackground=THEME["accent"], relief=tk.FLAT, bd=0,
            padx=12, pady=10, wrap=tk.WORD, undo=True,
            selectbackground=THEME["accent_dk"], selectforeground="#FFFFFF",
            highlightthickness=1, highlightbackground=THEME["border"],
            highlightcolor=THEME["accent"], spacing1=1, spacing3=2)
        self.input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(wrap, command=self.input_text.yview, style="Vertical.TScrollbar")
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(2, 0))
        self.input_text.config(yscrollcommand=sb.set)

        self._placeholder = ("在此输入或粘贴文本…\n\n"
                            "Ctrl+Enter 翻译　　Ctrl+Shift+T 读剪贴板并翻译")
        self._set_placeholder()
        self.input_text.bind("<FocusIn>", self._on_focus_in)
        self.input_text.bind("<FocusOut>", self._on_focus_out)
        self.input_text.bind("<<Modified>>", self._on_modified)

    # ---- 输出卡片 ----
    def _build_output_card(self, parent) -> None:
        card = self._card(parent, side=tk.RIGHT, padx=(7, 0))
        head = tk.Frame(card, bg=THEME["surface"])
        head.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(head, text="译文", fg=THEME["fg_dim"], bg=THEME["surface"],
                 font=self.f_small).pack(side=tk.LEFT)
        self.result_hint = tk.Label(head, text="", fg=THEME["fg_mute"],
                                    bg=THEME["surface"], font=self.f_tiny)
        self.result_hint.pack(side=tk.RIGHT)
        self._hover_label(head, "复制", self._copy_result, pad=(8, 2),
                          tip="复制译文到剪贴板").pack(side=tk.RIGHT, padx=(0, 6))

        wrap = tk.Frame(card, bg=THEME["surface"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 12))
        self.output_text = tk.Text(
            wrap, bg=THEME["surface2"], fg=THEME["result"], font=self.f_body,
            width=1, height=1,
            insertbackground=THEME["accent"], relief=tk.FLAT, bd=0,
            padx=12, pady=10, wrap=tk.WORD, state=tk.DISABLED,
            selectbackground=THEME["accent_dk"], selectforeground="#FFFFFF",
            highlightthickness=1, highlightbackground=THEME["border"],
            highlightcolor=THEME["accent"], spacing1=1, spacing3=2)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(wrap, command=self.output_text.yview, style="Vertical.TScrollbar")
        sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(2, 0))
        self.output_text.config(yscrollcommand=sb.set)

    # ---- 底栏 ----
    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=THEME["bg"])
        footer.pack(fill=tk.X, padx=16, pady=(10, 12))

        self.status_lbl = tk.Label(footer, text="就绪", fg=THEME["fg_dim"],
                                   bg=THEME["bg"], font=self.f_small)
        self.status_lbl.pack(side=tk.LEFT)

        self._hover_label(footer, "管理模型", self._open_model_manager,
                          fg=THEME["fg_dim"], tip="查看/下载翻译模型")\
            .pack(side=tk.LEFT, padx=(16, 0))
        self._hover_label(footer, "缓存管理", self._open_cache_manager,
                          fg=THEME["fg_dim"], tip="查看磁盘占用并清理可再生缓存")\
            .pack(side=tk.LEFT, padx=(4, 0))
        self._hover_label(footer, "历史", self._open_history,
                          fg=THEME["fg_dim"], tip="查看/恢复翻译历史")\
            .pack(side=tk.LEFT, padx=(4, 0))
        self._hover_label(footer, "模型目录",
                          lambda: self._open_folder(PACKAGES_DIR),
                          fg=THEME["fg_dim"], tip=PACKAGES_DIR)\
            .pack(side=tk.LEFT, padx=(4, 0))

        self.counter = tk.Label(footer, text="", fg=THEME["fg_mute"],
                                bg=THEME["bg"], font=self.f_tiny)
        self.counter.pack(side=tk.RIGHT)

        self._hover_label(footer, "清空", self._clear, fg=THEME["fg_dim"],
                          pad=(8, 2), tip="Esc").pack(side=tk.RIGHT, padx=(0, 10))

        self.topmost_var = tk.BooleanVar(value=bool(self.settings.get("topmost", True)))
        pin = tk.Checkbutton(footer, text="置顶", variable=self.topmost_var,
                             command=self._toggle_topmost, bg=THEME["bg"],
                             fg=THEME["fg_dim"], selectcolor=THEME["surface2"],
                             activebackground=THEME["bg"],
                             activeforeground=THEME["fg"], bd=0,
                             highlightthickness=0, font=self.f_tiny,
                             cursor="hand2", padx=0)
        pin.pack(side=tk.RIGHT, padx=(0, 10))
        self._tip(pin, "窗口始终显示在最前面")

    def _bind_keys(self) -> None:
        self.root.bind("<Control-Return>", lambda e: (self._translate(), "break")[1])
        self.root.bind("<Control-Shift-T>",
                       lambda e: (self._paste_and_translate(), "break")[1])
        self.root.bind("<Control-Shift-C>", lambda e: (self._copy_result(), "break")[1])
        self.root.bind("<Escape>", lambda e: self._clear())
        self.root.bind("<F5>", lambda e: (self._translate(), "break")[1])

    # ---------------- 状态 ----------------
    def _set_status(self, text: str, color: str = None) -> None:
        self.status_lbl.config(text=text, fg=color or THEME["fg_dim"])

    def _env_hint(self) -> str:
        if not HAS_ENGINE:
            return f"⚠ 翻译引擎不可用：{ENGINE_ERROR}"
        return f"模型目录：{PACKAGES_DIR}"

    @staticmethod
    def _label_for(code: str) -> str:
        if code == "auto":
            return "自动检测"
        return f"{code} · {LANG_NAMES.get(code, code)}"

    def _code_from(self, var: tk.StringVar) -> str:
        raw = var.get()
        if raw == "自动检测":
            return "auto"
        return raw.split(" · ")[0].strip()

    # ---------------- 占位文字 ----------------
    def _set_placeholder(self) -> None:
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", self._placeholder)
        self.input_text.config(fg=THEME["fg_mute"])
        self._has_placeholder = True

    def _get_input(self) -> str:
        if self._has_placeholder:
            return ""
        return self.input_text.get("1.0", "end-1c")

    def _on_focus_in(self, _event=None) -> None:
        if self._has_placeholder:
            self.input_text.delete("1.0", tk.END)
            self.input_text.config(fg=THEME["fg"])
            self._has_placeholder = False

    def _on_focus_out(self, _event=None) -> None:
        if not self.input_text.get("1.0", "end-1c").strip():
            self._set_placeholder()

    # ---------------- 输入变化 ----------------
    def _on_modified(self, _event=None) -> None:
        self.input_text.edit_modified(False)
        text = self._get_input()
        if not text.strip():
            self.src_hint.config(text="")
            self.counter.config(text="")
            return
        self.counter.config(text=f"{len(text)} 字")
        self.src_hint.config(text="检测中…", fg=THEME["fg_mute"])
        # 防抖：langdetect 是纯 Python，长文本很慢，不能每敲一个字都跑
        if self._after_id:
            try:
                self.root.after_cancel(self._after_id)
            except tk.TclError:
                pass
        self._detect_gen += 1
        gen = self._detect_gen
        self._after_id = self.root.after(250, lambda: self._run_detect(gen, text))

    def _run_detect(self, gen: int, text: str) -> None:
        if gen != self._detect_gen:
            return
        code = detect_language(text)
        if gen != self._detect_gen:
            return
        name = LANG_NAMES.get(code, code)
        src = self._code_from(self.src_var)
        if src == "auto":
            self.src_hint.config(text=f"识别为 {name}", fg=THEME["fg_dim"])
        elif src != code:
            self.src_hint.config(
                text=f"⚠ 与所选 {LANG_NAMES.get(src, src)} 不一致", fg=THEME["warn"])
        else:
            self.src_hint.config(text=name, fg=THEME["fg_mute"])

    def _toggle_topmost(self) -> None:
        on = bool(self.topmost_var.get())
        self.root.attributes("-topmost", on)
        self.settings["topmost"] = on
        save_settings(self.settings)

    @staticmethod
    def _open_folder(path: str) -> None:
        try:
            os.startfile(path)                      # noqa: S606 (Windows only)
        except Exception:
            log.exception("open folder failed: %s", path)

    # ---------------- 核心动作 ----------------
    def _translate(self) -> None:
        if self._busy:
            self._set_status("正在翻译中…", THEME["warn"])
            return
        text = self._get_input()
        if not text.strip():
            self._set_status("请输入文本", THEME["bad"])
            return

        src = self._code_from(self.src_var)
        if src == "auto":
            src = detect_language(text)
        tgt = self._code_from(self.tgt_var)
        if src == tgt:
            self._set_status("源语言与目标语言相同", THEME["bad"])
            return

        # 长文提醒：这个模型是 CPU 跑的，约 350 字符/秒，长篇要等一会儿
        n_chars = len(text)
        est = n_chars / 350.0
        if n_chars > 4000:
            self._set_status(f"长文本 {n_chars} 字，预计 {est:.0f} 秒…", THEME["warn"])

        self._busy = True
        self._cancel = threading.Event()
        self._started = time.time()
        # 原文要留到结果回来时写历史。只存引用，不复制（长文复制一次不划算）。
        # 并发性：_busy 保证同一时刻只有一个翻译在跑，所以不会被下一次覆盖。
        self._pending_source = text
        self._pending_src = src
        self.translate_btn.config(state=tk.DISABLED, text="翻译中…",
                                  bg=THEME["surface2"], fg=THEME["fg_mute"])
        self.cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self._set_status("翻译中…", THEME["warn"])
        self.settings["src_lang"] = self._code_from(self.src_var)
        self.settings["tgt_lang"] = tgt

        cancel = self._cancel

        def report(message: str) -> None:
            self.results.put(("progress", message))

        def work() -> None:
            started = time.time()
            try:
                result = ENGINE.translate(text, src, tgt, report, cancel)
                self.results.put(("ok", result, time.time() - started, src, tgt))
            except TranslationCancelled:
                self.results.put(("cancelled", None))
            except Exception as exc:
                log.exception("translate failed")
                self.results.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True, name="translate").start()

    def _cancel_translate(self) -> None:
        if self._busy and getattr(self, "_cancel", None):
            self._cancel.set()
            self._set_status("正在取消…", THEME["warn"])

    def _paste_and_translate(self) -> None:
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            self._set_status("剪贴板里没有文本", THEME["bad"])
            return
        if not clip.strip():
            self._set_status("剪贴板里没有文本", THEME["bad"])
            return
        self.input_text.delete("1.0", tk.END)
        self.input_text.insert("1.0", clip)
        self.input_text.config(fg=THEME["fg"])
        self._has_placeholder = False
        self._on_modified()
        self._translate()

    def _swap_langs(self) -> None:
        src = self._code_from(self.src_var)
        tgt = self._code_from(self.tgt_var)
        if src == "auto":
            self._set_status("源语言是自动检测，无法交换", THEME["warn"])
            return
        self.src_var.set(self._label_for(tgt))
        self.tgt_var.set(self._label_for(src))
        # 顺手把译文换到输入框，符合"反过来再翻回去"的直觉
        self.output_text.config(state=tk.NORMAL)
        out = self.output_text.get("1.0", "end-1c")
        self.output_text.config(state=tk.DISABLED)
        if out.strip():
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert("1.0", out)
            self.input_text.config(fg=THEME["fg"])
            self._has_placeholder = False
            self._on_modified()
        self._set_status("已交换语言")

    def _clear(self) -> None:
        if self._busy:
            return
        self._set_placeholder()
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete("1.0", tk.END)
        self.output_text.config(state=tk.DISABLED)
        self.counter.config(text="")
        self.src_hint.config(text="")
        self.result_hint.config(text="")
        self._set_status("就绪")

    def _copy_result(self) -> None:
        self.output_text.config(state=tk.NORMAL)
        text = self.output_text.get("1.0", "end-1c")
        self.output_text.config(state=tk.DISABLED)
        if not text.strip():
            self._set_status("还没有译文可复制", THEME["warn"])
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("已复制到剪贴板", THEME["good"])

    # ---------------- 工作线程 -> 主线程 ----------------
    def _pump(self) -> None:
        try:
            while True:
                self._handle(self.results.get_nowait())
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "progress":
            self._set_status(msg[1], THEME["warn"])
        elif kind == "ok":
            _, text, elapsed, src, tgt = msg
            self.output_text.config(state=tk.NORMAL)
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", text)
            self.output_text.config(state=tk.DISABLED)
            note = ""
            if text.count("\n") and len(text) > 2000:
                note = "　(分段翻译)"
            self.result_hint.config(
                text=f"{LANG_NAMES.get(src, src)} → {LANG_NAMES.get(tgt, tgt)}"
                     f"　{len(text)} 字　{elapsed:.1f}s{note}",
                fg=THEME["fg_mute"])
            self._set_status("翻译完成", THEME["good"])
            # 先记历史再解 busy：反过来的话按钮已经可点，用户可能在历史写完
            # 之前又发起一次翻译，_pending_source 就被覆盖了。
            self._record_history(src, tgt, text, elapsed)
            self._finish_busy()
        elif kind == "cancelled":
            self._set_status("已取消", THEME["warn"])
            self.result_hint.config(text="已取消", fg=THEME["warn"])
            self._finish_busy()
        elif kind == "error":
            self._set_status("翻译失败", THEME["bad"])
            self.output_text.config(state=tk.NORMAL)
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", f"翻译失败\n\n{msg[1]}")
            self.output_text.config(state=tk.DISABLED)
            self._finish_busy()
            messagebox.showerror("翻译失败", msg[1])
        elif kind == "warm":
            self._set_status(msg[1])

    def _finish_busy(self) -> None:
        self._busy = False
        self.cancel_btn.pack_forget()
        self.translate_btn.config(state=tk.NORMAL, text="译  Translate",
                                  bg=THEME["accent"], fg="#FFFFFF")
        save_settings(self.settings)

    def _record_history(self, src: str, tgt: str, result: str,
                        elapsed: float) -> None:
        """把这次翻译写进历史。

        这里**必须**吞掉一切异常。调用点在"翻译已经成功"之后：如果记录失败
        把异常抛出来，用户看到的是"翻译失败"，而翻译其实成功了 —— 拿一个
        附加功能去毁掉主功能，是最糟的失败方式。

        （HistoryStore.record 本身已保证不抛；这里再包一层是防御性的，
        因为将来可能有人改动它，或者 HISTORY 单例被换掉。）
        """
        try:
            source = getattr(self, "_pending_source", "") or ""
            HISTORY.record(src, tgt, source, result, elapsed)
        except Exception:
            log.exception("history record failed")     # 只记日志，不影响界面
        finally:
            self._pending_source = ""

    def _start_warmup(self) -> None:
        """后台把已安装模型扫进内存，让第一次翻译不用现扫。"""
        if not HAS_ENGINE:
            self._set_status("引擎未安装", THEME["bad"])
            return

        def work() -> None:
            try:
                count = ENGINE.installed_count()
                if count:
                    self.results.put(("warm", f"就绪 · {count} 个模型"))
                else:
                    self.results.put(("warm", "就绪 · 尚未安装模型"))
            except Exception:
                log.exception("warmup failed")

        threading.Thread(target=work, daemon=True, name="warmup").start()

    # ---------------- 模型管理 ----------------
    def _open_model_manager(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("模型管理")
        dlg.configure(bg=THEME["bg"])
        dlg.geometry("780x620")
        dlg.minsize(620, 460)
        dlg.transient(self.root)
        dlg.attributes("-topmost", bool(self.topmost_var.get()))
        ico = _icon_path()
        if ico:
            try:
                dlg.iconbitmap(default=ico)
            except tk.TclError:
                pass

        head = tk.Frame(dlg, bg=THEME["bg"])
        head.pack(fill=tk.X, padx=16, pady=(14, 0))
        self._dlg_count = tk.Label(head, text="", fg=THEME["fg"], bg=THEME["bg"],
                                   font=(self.family, 11, "bold"))
        self._dlg_count.pack(side=tk.LEFT)
        tk.Label(head, text=PACKAGES_DIR, fg=THEME["fg_mute"], bg=THEME["bg"],
                 font=self.f_tiny).pack(side=tk.RIGHT)

        tk.Label(dlg,
                 text="任意语言对都能翻：只要「源→英」与「英→目标」两个模型齐全，会自动经英文中转。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W).pack(fill=tk.X, padx=16, pady=(2, 8))

        # 已安装列表
        tree_wrap = self._card(dlg)
        tree_wrap.master.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))
        cols = ("src", "tgt", "names")
        tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                            style="Card.Treeview", selectmode="browse")
        tree.heading("src", text="源")
        tree.heading("tgt", text="目标")
        tree.heading("names", text="语言")
        tree.column("src", width=70, anchor=tk.CENTER, stretch=False)
        tree.column("tgt", width=70, anchor=tk.CENTER, stretch=False)
        tree.column("names", width=440, anchor=tk.W)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        tsb = ttk.Scrollbar(tree_wrap, command=tree.yview, style="Vertical.TScrollbar")
        tsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 1), pady=1)
        tree.config(yscrollcommand=tsb.set)

        def refresh_list() -> None:
            tree.delete(*tree.get_children())
            pairs = sorted(ENGINE.installed_pairs())
            self._dlg_count.config(text=f"已安装 {len(pairs)} 个模型")
            if not pairs:
                tree.insert("", tk.END, values=("", "", "（还没有模型，用下面的下载区装几个）"))
                return
            for a, b in pairs:
                tree.insert("", tk.END, values=(
                    a, b, f"{LANG_NAMES.get(a, a)} → {LANG_NAMES.get(b, b)}"))

        refresh_list()

        # 下载区
        box = self._card(dlg, side=None, fill=tk.X)
        box.master.pack(fill=tk.X, padx=16, pady=(0, 8))
        inner = tk.Frame(box, bg=THEME["surface"])
        inner.pack(fill=tk.X, padx=14, pady=12)
        tk.Label(inner, text="下载 / 安装新模型", fg=THEME["fg_dim"],
                 bg=THEME["surface"], font=self.f_small).pack(anchor=tk.W)

        row = tk.Frame(inner, bg=THEME["surface"])
        row.pack(fill=tk.X, pady=(8, 0))
        from_var = tk.StringVar(value="en")
        to_var = tk.StringVar(value="zh")
        ttk.Combobox(row, textvariable=from_var, state="readonly", width=7,
                     values=LANG_CODES, font=self.f_small,
                     style="App.TCombobox").pack(side=tk.LEFT)
        tk.Label(row, text="→", fg=THEME["fg_mute"], bg=THEME["surface"],
                 font=self.f_small).pack(side=tk.LEFT, padx=8)
        ttk.Combobox(row, textvariable=to_var, state="readonly", width=7,
                     values=LANG_CODES, font=self.f_small,
                     style="App.TCombobox").pack(side=tk.LEFT)

        dl_state = tk.Label(inner, text="", fg=THEME["warn"], bg=THEME["surface"],
                            font=self.f_tiny, anchor=tk.W, justify=tk.LEFT,
                            wraplength=680)
        dl_state.pack(fill=tk.X, pady=(8, 0))

        def ui(fn, *a, **kw):
            dlg.after(0, lambda: fn(*a, **kw))

        def do_refresh_index() -> None:
            def work() -> None:
                ui(dl_state.config, text="正在获取可下载清单…", fg=THEME["warn"])
                try:
                    n = ENGINE.refresh_available()
                    ui(dl_state.config,
                       text=f"清单已更新，共 {n} 个包可下载。", fg=THEME["good"])
                except Exception as exc:
                    log.exception("refresh index failed")
                    ui(dl_state.config, text=f"获取失败：{exc}", fg=THEME["bad"])
            threading.Thread(target=work, daemon=True).start()

        def do_download() -> None:
            a, b = from_var.get(), to_var.get()
            if a == b:
                dl_state.config(text="源语言和目标语言不能相同", fg=THEME["bad"])
                return
            if (a, b) in ENGINE.installed_pairs():
                dl_state.config(text=f"{a} → {b} 已经装好了", fg=THEME["good"])
                return

            def work() -> None:
                try:
                    ENGINE.install_pair(
                        a, b, lambda m: ui(dl_state.config, text=m,
                                           fg=THEME["warn"]))
                    ui(dl_state.config, text=f"{a} → {b} 安装完成",
                       fg=THEME["good"])
                    ui(refresh_list)
                except Exception as exc:
                    log.exception("install failed")
                    ui(dl_state.config, text=f"安装失败：{exc}", fg=THEME["bad"])
            threading.Thread(target=work, daemon=True).start()

        btns = tk.Frame(inner, bg=THEME["surface"])
        btns.pack(fill=tk.X, pady=(10, 0))
        self._button(btns, "刷新可下载清单", do_refresh_index).pack(side=tk.LEFT)
        self._button(btns, "下载并安装", do_download, kind="primary")\
            .pack(side=tk.LEFT, padx=(8, 0))
        self._button(btns, "打开模型目录",
                     lambda: self._open_folder(PACKAGES_DIR)).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(dlg,
                 text="下载需要联网。离线环境下可把别处的 models/packages 目录整个复制过来，"
                      "或用目录链接指过去（mklink /J）。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W, justify=tk.LEFT).pack(fill=tk.X, padx=16, pady=(0, 12))
        dlg.bind("<Escape>", lambda e: dlg.destroy())

    # ---------------- 缓存管理 ----------------
    def _open_cache_manager(self) -> None:
        """磁盘占用一览 + 清理可再生缓存。

        两个刻意的设计：
          * 模型（models）只报告、不提供删除。它占 99% 的空间但属于用户资产，
            删掉要重新下几百 MB。清空按钮在接口层就够不到它（CacheManager
            只接受 disposable 的名字），所以界面不需要靠自觉来防误删。
          * 目录遍历实测约 350 ms（20 GB 量级），放到后台线程算，
            开窗不卡。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("缓存管理")
        dlg.configure(bg=THEME["bg"])
        dlg.geometry("760x560")
        dlg.minsize(620, 420)
        dlg.transient(self.root)
        dlg.attributes("-topmost", bool(self.topmost_var.get()))
        ico = _icon_path()
        if ico:
            try:
                dlg.iconbitmap(default=ico)
            except tk.TclError:
                pass

        head = tk.Frame(dlg, bg=THEME["bg"])
        head.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(head, text="磁盘占用", fg=THEME["fg"], bg=THEME["bg"],
                 font=(self.family, 11, "bold")).pack(side=tk.LEFT)
        self._cache_total = tk.Label(head, text="统计中…", fg=THEME["fg_mute"],
                                     bg=THEME["bg"], font=self.f_tiny)
        self._cache_total.pack(side=tk.RIGHT)

        tk.Label(dlg,
                 text="翻译模型是你的资产，不会在这里被删除。「可再生」的项目删掉后会在"
                      "下次用到时自动重建；「用户记录」（翻译历史）删了不可恢复，"
                      "所以单独一个按钮、单独确认。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W, justify=tk.LEFT, wraplength=700)\
            .pack(fill=tk.X, padx=16, pady=(2, 8))

        holder = self._card(dlg)
        holder.master.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))
        cols = ("name", "size", "kind")
        tree = ttk.Treeview(holder, columns=cols, show="headings",
                            style="Card.Treeview", selectmode="browse")
        tree.heading("name", text="项目")
        tree.heading("size", text="占用")
        tree.heading("kind", text="保留等级")
        tree.column("name", width=210, anchor=tk.W, stretch=False)
        tree.column("size", width=110, anchor=tk.E, stretch=False)
        tree.column("kind", width=380, anchor=tk.W)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        tsb = ttk.Scrollbar(holder, command=tree.yview, style="Vertical.TScrollbar")
        tsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 1), pady=1)
        tree.config(yscrollcommand=tsb.set)

        state = tk.Label(dlg, text="", fg=THEME["fg_mute"], bg=THEME["bg"],
                         font=self.f_tiny, anchor=tk.W, justify=tk.LEFT,
                         wraplength=700)
        state.pack(fill=tk.X, padx=16, pady=(0, 6))

        cache_q: queue.Queue = queue.Queue()
        alive = {"yes": True}

        def ui(fn, *a, **kw):
            """把界面更新排进队列，由主线程的轮询消费。

            不能在后台线程里直接调 dlg.after() —— Tk 不是线程安全的
            （主窗口用的是同一套 queue + 轮询的做法）。对话框自己一个队列，
            关窗时用 stop 标志让轮询退出。
            """
            cache_q.put((fn, a, kw))

        def pump() -> None:
            try:
                while True:
                    fn, a, kw = cache_q.get_nowait()
                    fn(*a, **kw)
            except queue.Empty:
                pass
            except tk.TclError:
                return                        # 窗口已销毁
            # 同历史对话框：窗口没了还调 after() 会在 Tcl 层报后台错误
            if alive["yes"] and dlg.winfo_exists():
                dlg.after(60, pump)

        def on_close() -> None:
            alive["yes"] = False
            dlg.destroy()

        def render(items) -> None:
            tree.delete(*tree.get_children())
            total = 0
            for it in items:
                total += it.size
                # 等级标签直接来自模块，界面不自己判断能不能删
                tree.insert("", tk.END, values=(
                    f"{it.label}  ({it.name})", _fmt_size(it.size),
                    it.retention_label))
                tree.insert("", tk.END, values=("    " + it.purpose, "", ""))
            self._cache_total.config(text=f"合计 {_fmt_size(total)}")

        def load(force: bool = False) -> None:
            def work() -> None:
                try:
                    items = CACHE.inventory(force=force)
                except Exception as exc:                     # pragma: no cover
                    log.exception("cache inventory failed")
                    ui(state.config, text=f"统计失败：{exc}", fg=THEME["bad"])
                    return
                ui(render, items)
                dl = sum(i.size for i in items if i.disposable)
                rec = sum(i.size for i in items
                          if i.retention == Retention.USER_RECORD)
                ui(state.config,
                   text=f"可再生 {_fmt_size(dl)}　·　用户记录 {_fmt_size(rec)}"
                        f"　·　翻译模型不计入清理",
                   fg=THEME["fg_mute"])
            threading.Thread(target=work, daemon=True, name="cache-scan").start()

        def do_clear() -> None:
            if self._busy:
                state.config(text="正在翻译中，先等它结束再清理", fg=THEME["warn"])
                return
            dl = CACHE.disposable_size()
            if dl == 0:
                state.config(text="可清理的项目已经是空的", fg=THEME["good"])
                return
            if not messagebox.askyesno(
                    "确认清理",
                    f"将清理约 {_fmt_size(dl)} 的可再生缓存：\n\n"
                    "· 分句模型（下次翻译时会重新下载或从随包副本恢复）\n"
                    "· 模型清单索引（下次刷新清单时重建）\n"
                    "· 运行日志\n\n"
                    "翻译模型和翻译历史都不会被删除。继续吗？",
                    parent=dlg):
                return

            def work() -> None:
                ui(state.config, text="正在清理…", fg=THEME["warn"])
                try:
                    rep = CACHE.clear()          # 只删 DISPOSABLE
                except Exception as exc:
                    log.exception("cache clear failed")
                    ui(state.config, text=f"清理失败：{exc}", fg=THEME["bad"])
                    return
                ui(state.config, text=rep.summary(), fg=THEME["good"])
                ui(load, True)
            threading.Thread(target=work, daemon=True, name="cache-clear").start()

        def do_purge_history() -> None:
            """删除用户记录。与清理缓存分开：这个不可恢复，措辞和确认都更重。"""
            if self._busy:
                state.config(text="正在翻译中，先等它结束", fg=THEME["warn"])
                return
            try:
                st = HISTORY.summary()
            except Exception as exc:
                state.config(text=f"读取历史失败：{exc}", fg=THEME["bad"])
                return
            if st.count == 0:
                state.config(text="历史已经是空的", fg=THEME["good"])
                return
            if not messagebox.askyesno(
                    "确认删除历史",
                    f"将删除全部 {st.count} 条翻译历史（{_fmt_size(st.bytes)}）。\n\n"
                    "这个操作**不可恢复**。翻译模型和缓存不受影响。\n\n继续吗？",
                    parent=dlg, icon="warning", default="no"):
                return

            def work() -> None:
                ui(state.config, text="正在删除历史…", fg=THEME["warn"])
                try:
                    rep = CACHE.purge(["history"])
                except Exception as exc:
                    log.exception("history purge failed")
                    ui(state.config, text=f"删除失败：{exc}", fg=THEME["bad"])
                    return
                ui(state.config, text=f"历史已删除（{rep.summary()}）",
                   fg=THEME["good"])
                ui(load, True)
            threading.Thread(target=work, daemon=True, name="hist-purge").start()

        btns = tk.Frame(dlg, bg=THEME["bg"])
        btns.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._button(btns, "重新统计", lambda: load(True)).pack(side=tk.LEFT)
        self._button(btns, "清理可再生缓存", do_clear).pack(side=tk.LEFT, padx=(8, 0))
        self._button(btns, "删除翻译历史", do_purge_history)\
            .pack(side=tk.LEFT, padx=(8, 0))
        self._button(btns, "打开程序目录",
                     lambda: self._open_folder(DATA_ROOT)).pack(side=tk.LEFT, padx=(8, 0))

        load()
        dlg.after(60, pump)
        dlg.protocol("WM_DELETE_WINDOW", on_close)
        dlg.bind("<Escape>", lambda e: on_close())

    # ---------------- 翻译历史 ----------------
    PAGE = 50                                # 一页多少条

    def _open_history(self) -> None:
        """历史窗口：分页浏览、恢复到输入框、清空。

        分页而不是一次全塞：条数上限是 200，但每条内容不截断，长文条的文本
        很大，一次性填进 Treeview 会卡。
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("翻译历史")
        dlg.configure(bg=THEME["bg"])
        dlg.geometry("860x620")
        dlg.minsize(680, 460)
        dlg.transient(self.root)
        dlg.attributes("-topmost", bool(self.topmost_var.get()))
        ico = _icon_path()
        if ico:
            try:
                dlg.iconbitmap(default=ico)
            except tk.TclError:
                pass

        head = tk.Frame(dlg, bg=THEME["bg"])
        head.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(head, text="翻译历史", fg=THEME["fg"], bg=THEME["bg"],
                 font=(self.family, 11, "bold")).pack(side=tk.LEFT)
        self._hist_info = tk.Label(head, text="", fg=THEME["fg_mute"],
                                   bg=THEME["bg"], font=self.f_tiny)
        self._hist_info.pack(side=tk.RIGHT)

        tk.Label(dlg,
                 text="双击一条可把原文放回输入框；也可以只恢复译文。"
                      "历史只存在本机，不会上传。",
                 fg=THEME["fg_mute"], bg=THEME["bg"], font=self.f_tiny,
                 anchor=tk.W, justify=tk.LEFT, wraplength=820)\
            .pack(fill=tk.X, padx=16, pady=(2, 8))

        holder = self._card(dlg)
        holder.master.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))
        cols = ("time", "pair", "size", "preview")
        tree = ttk.Treeview(holder, columns=cols, show="headings",
                            style="Card.Treeview", selectmode="browse")
        for cid, text, w, anchor in (("time", "时间", 130, tk.W),
                                     ("pair", "语言", 90, tk.CENTER),
                                     ("size", "字数", 90, tk.E),
                                     ("preview", "原文", 420, tk.W)):
            tree.heading(cid, text=text)
            tree.column(cid, width=w, anchor=anchor,
                        stretch=(cid == "preview"))
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(1, 0), pady=1)
        tsb = ttk.Scrollbar(holder, command=tree.yview, style="Vertical.TScrollbar")
        tsb.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 1), pady=1)
        tree.config(yscrollcommand=tsb.set)

        detail = tk.Text(dlg, height=6, bg=THEME["surface2"], fg=THEME["result"],
                         font=self.f_small, relief=tk.FLAT, bd=0, wrap=tk.WORD,
                         padx=10, pady=8, highlightthickness=1,
                         highlightbackground=THEME["border"])
        detail.pack(fill=tk.X, padx=16, pady=(0, 8))
        detail.insert("1.0", "选中一条历史查看内容。")
        detail.config(state=tk.DISABLED)

        state = tk.Label(dlg, text="", fg=THEME["fg_mute"], bg=THEME["bg"],
                         font=self.f_tiny, anchor=tk.W)
        state.pack(fill=tk.X, padx=16, pady=(0, 6))

        page_no = {"n": 0}
        shown: list = []                    # 当前页的 HistoryEntry

        def render() -> None:
            tree.delete(*tree.get_children())
            stats = HISTORY.summary()
            total_pages = max(1, (stats.count + self.PAGE - 1) // self.PAGE)
            if page_no["n"] >= total_pages:
                page_no["n"] = max(0, total_pages - 1)
            shown.clear()
            shown.extend(HISTORY.page(page_no["n"] * self.PAGE, self.PAGE))
            for idx, e in enumerate(shown):
                when = time.strftime("%m-%d %H:%M",
                                     time.localtime(e.ts)) if e.ts else "—"
                a, b = e.chars
                tree.insert("", tk.END, iid=str(idx), values=(
                    when, f"{e.src}→{e.tgt}", f"{a}→{b}", e.head(64)))
            self._hist_info.config(
                text=f"{stats.count} 条 · {_fmt_size(stats.bytes)}")
            empty = "（还没有历史）" if stats.count == 0 else ""
            state.config(
                text=f"第 {page_no['n'] + 1}/{total_pages} 页　"
                     f"上限 {stats.max_entries} 条 / {_fmt_size(stats.max_bytes)}"
                     f"　{empty}")

        def on_select(_e=None) -> None:
            sel = tree.selection()
            if not sel:
                return
            i = int(sel[0])
            if i >= len(shown):
                return
            e = shown[i]
            detail.config(state=tk.NORMAL)
            detail.delete("1.0", tk.END)
            detail.insert("1.0",
                          f"【原文 {e.src}】\n{e.source}\n\n"
                          f"【译文 {e.tgt}】\n{e.target}")
            detail.config(state=tk.DISABLED)

        def put(text: str, label: str) -> None:
            self.input_text.delete("1.0", tk.END)
            self.input_text.insert("1.0", text)
            self.input_text.config(fg=THEME["fg"])
            self._has_placeholder = False
            self._on_modified()
            self._set_status(f"已恢复到输入框（{label}）", THEME["good"])

        def restore_source(_e=None) -> None:
            sel = tree.selection()
            if not sel or int(sel[0]) >= len(shown):
                state.config(text="先选中一条历史", fg=THEME["warn"])
                return
            put(shown[int(sel[0])].source, "原文")

        def restore_target() -> None:
            sel = tree.selection()
            if not sel or int(sel[0]) >= len(shown):
                state.config(text="先选中一条历史", fg=THEME["warn"])
                return
            put(shown[int(sel[0])].target, "译文")

        def goto(delta: int) -> None:
            page_no["n"] = max(0, page_no["n"] + delta)
            render()

        def do_clear() -> None:
            stats = HISTORY.summary()
            if stats.count == 0:
                state.config(text="历史已经是空的", fg=THEME["good"])
                return
            if not messagebox.askyesno(
                    "确认清空历史",
                    f"将删除全部 {stats.count} 条翻译历史（{_fmt_size(stats.bytes)}）。\n\n"
                    "这个操作**不可恢复**，原文和译文都会丢失。\n"
                    "翻译模型和缓存不受影响。\n\n继续吗？",
                    parent=dlg, icon="warning", default="no"):
                return

            def work() -> None:
                ui(state.config, text="正在清空…", fg=THEME["warn"])
                try:
                    rep = CACHE.purge(["history"])      # 走缓存管理，委托给 store
                except Exception as exc:
                    log.exception("history purge failed")
                    ui(state.config, text=f"清空失败：{exc}", fg=THEME["bad"])
                    return
                ui(state.config, text=f"已清空（{rep.summary()}）", fg=THEME["good"])
                ui(render)
            threading.Thread(target=work, daemon=True, name="hist-purge").start()

        hist_q: queue.Queue = queue.Queue()
        alive = {"yes": True}

        def ui(fn, *a, **kw):
            hist_q.put((fn, a, kw))

        def pump() -> None:
            try:
                while True:
                    fn, a, kw = hist_q.get_nowait()
                    fn(*a, **kw)
            except queue.Empty:
                pass
            except tk.TclError:
                return
            # 窗口可能已经被销毁；不先确认就 after() 会在解释器里报
            # "invalid command name ..._pump"。这不是异常，是 Tcl 的
            # 后台错误，只能靠事前检查避免。
            if alive["yes"] and dlg.winfo_exists():
                dlg.after(60, pump)

        def on_close() -> None:
            alive["yes"] = False
            dlg.destroy()

        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-1>", restore_source)

        bar = tk.Frame(dlg, bg=THEME["bg"])
        bar.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._button(bar, "上一页", lambda: goto(-1)).pack(side=tk.LEFT)
        self._button(bar, "下一页", lambda: goto(1)).pack(side=tk.LEFT, padx=(6, 0))
        self._button(bar, "刷新", render).pack(side=tk.LEFT, padx=(6, 0))
        self._button(bar, "清空历史", do_clear).pack(side=tk.LEFT, padx=(18, 0))
        self._button(bar, "恢复译文", restore_target).pack(side=tk.RIGHT)
        self._button(bar, "恢复原文", restore_source,
                     kind="primary").pack(side=tk.RIGHT, padx=(0, 6))

        render()
        dlg.after(60, pump)
        dlg.protocol("WM_DELETE_WINDOW", on_close)
        dlg.bind("<Escape>", lambda e: on_close())

    # ---------------- 生命周期 ----------------
    def _log_geometry(self) -> None:
        self.root.update_idletasks()
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            x, y = self.root.winfo_x(), self.root.winfo_y()
            log.info("window: %dx%d+%d+%d  screen=%dx%d  client=%dx%d", w, h, x, y,
                     sw, sh, self.root.winfo_reqwidth(),
                     self.root.winfo_reqheight())
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        try:
            self.settings["geometry"] = self.root.winfo_geometry()
            save_settings(self.settings)
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# 必须在 Tk 建窗口之前声明 DPI 感知，否则高分屏下整窗被系统拉伸、字发糊。
_DPI_AWARE_OK = _enable_dpi_awareness()
