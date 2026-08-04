## ==========================================================
## SM5K WRC Rotator Control — network client
## Same compass UI as rotor_gui.py, but talks JSON-lines to
## rotor_daemon.py over the network instead of GS-232 directly.
## Use this to control the Ramnäs WRC rotor from another QTH
## (e.g. Blecket) via the ramnas-station Pi daemon.
## ==========================================================

import tkinter as tk
import threading
import socket
import math
import json
import os
import sys
import webbrowser

if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_BASE_DIR, "rotor_klient_settings.json")
DEFAULT_HOST    = "192.168.68.107"   # ramnas-station Pi
DEFAULT_PORT    = 5561
DEFAULT_UI_MODE = "classic"
SIZE_BY_MODE = {
    "classic": {"default": 460, "min": 300},
    "compact": {"default": 153, "min": 120},
}
MAX_SIZE = 700
DONATE_URL = "https://www.paypal.com/donate/?hosted_button_id=5VX8NE7LQEGF4"
HELP_URL   = "https://github.com/sm5tog/wrc-rotor-control/blob/master/README_ROTOR.md"

# ── Palette ────────────────────────────────────────────────
BG     = "#16181d"
PANEL  = "#1e2028"
BORDER = "#2a2d3a"
TEXT   = "#d4d8e8"
MUTED  = "#555a6e"
GREEN  = "#48c774"
RED    = "#e05252"
AMBER  = "#e8a030"
BTNBG  = "#2a2d3a"

# ── Settings ───────────────────────────────────────────────

def load_settings():
    try:
        with open(CONFIG_FILE) as f:
            d = json.load(f)
            ui_mode = d.get("ui_mode", DEFAULT_UI_MODE)
            if ui_mode not in SIZE_BY_MODE:
                ui_mode = DEFAULT_UI_MODE
            return {
                "host":    d.get("host", DEFAULT_HOST),
                "port":    int(d.get("port", DEFAULT_PORT)),
                "ui_mode": ui_mode,
                "size":    int(d.get("size", SIZE_BY_MODE[ui_mode]["default"])),
            }
    except Exception:
        return {"host": DEFAULT_HOST, "port": DEFAULT_PORT,
                "ui_mode": DEFAULT_UI_MODE, "size": SIZE_BY_MODE[DEFAULT_UI_MODE]["default"]}

def save_settings(s):
    with open(CONFIG_FILE, "w") as f:
        json.dump(s, f, indent=2)

# ── Daemon connection (JSON-lines over TCP) ────────────────

class DaemonLink:
    def __init__(self, host, port, on_message):
        self.host = host
        self.port = port
        self.on_message = on_message   # callback(dict), called from reader thread
        self.sock = None
        self.lock = threading.Lock()
        self._running = True
        self._connect()
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _connect(self):
        try:
            s = socket.socket()
            s.settimeout(3)
            s.connect((self.host, self.port))
            with self.lock:
                self.sock = s
            return True
        except Exception:
            with self.lock:
                self.sock = None
            return False

    def reconnect(self, host, port):
        with self.lock:
            if self.sock:
                try: self.sock.close()
                except Exception: pass
            self.host = host
            self.port = port
            self.sock = None
        return self._connect()

    def _send(self, obj):
        line = (json.dumps(obj) + "\n").encode("utf-8")
        with self.lock:
            if self.sock is None:
                return
            try:
                self.sock.sendall(line)
            except Exception:
                self.sock = None

    def goto(self, degrees):
        self._send({"cmd": "goto", "az": max(0, min(360, int(degrees)))})

    def stop(self):
        self._send({"cmd": "stop"})

    def set_udp_port(self, port):
        self._send({"cmd": "set_udp_port", "port": int(port)})

    def _reader_loop(self):
        buf = b""
        while self._running:
            with self.lock:
                sock = self.sock
            if sock is None:
                if not self._connect():
                    threading.Event().wait(2.0)
                continue
            try:
                sock.settimeout(1.0)
                chunk = sock.recv(4096)
                if not chunk:
                    with self.lock:
                        self.sock = None
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    self.on_message(msg)
            except socket.timeout:
                continue
            except (ConnectionResetError, OSError):
                with self.lock:
                    self.sock = None

    def close(self):
        self._running = False
        with self.lock:
            if self.sock:
                try: self.sock.close()
                except Exception: pass


# ── Settings dialog ────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg, on_apply):
        super().__init__(parent)
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()
        self.on_apply = on_apply
        self.ui_mode = cfg["ui_mode"]

        def row(label, default):
            tk.Label(self, text=label, bg=BG, fg=MUTED,
                     font=("Consolas", 10), anchor="w").pack(fill="x", padx=20, pady=(12, 2))
            e = tk.Entry(self, bg=BTNBG, fg=TEXT, insertbackground=TEXT,
                         font=("Consolas", 12), relief="flat",
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=AMBER)
            e.insert(0, str(default))
            e.pack(fill="x", padx=20, ipady=4)
            return e

        tk.Label(self, text="Window style", bg=BG, fg=MUTED,
                 font=("Consolas", 10), anchor="w").pack(fill="x", padx=20, pady=(12, 2))
        mode_row = tk.Frame(self, bg=BG)
        mode_row.pack(fill="x", padx=20)
        self.mode_buttons = {}
        for mode, label in (("classic", "Classic (windowed)"), ("compact", "Compact (borderless)")):
            b = tk.Button(mode_row, text=label,
                          font=("Consolas", 9, "bold"), relief="flat", bd=0,
                          padx=8, pady=6, cursor="hand2",
                          command=lambda m=mode: self._select_mode(m))
            b.pack(side="left", expand=True, fill="x", padx=(0, 6) if mode == "classic" else 0)
            self.mode_buttons[mode] = b
        self._select_mode(self.ui_mode)

        self.e_host     = row("Rotor daemon IP address (Pi)", cfg["host"])
        self.e_port     = row("Rotor daemon TCP port",        cfg["port"])
        self.e_udp_port = row("N1MM/PstRotator UDP port (on the Pi)", cfg.get("udp_port", "?"))
        self.e_size     = row(f"Compass size ({SIZE_BY_MODE[self.ui_mode]['min']}–{MAX_SIZE} px)", cfg["size"])
        self.lbl_size_hint = self.e_size

        self.lbl_status = tk.Label(self, text="", bg=BG, fg=MUTED,
                                   font=("Consolas", 10))
        self.lbl_status.pack(pady=(10, 0))

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=(6, 16), padx=20, fill="x")

        tk.Button(btn_row, text="Connect & save",
                  bg=BTNBG, fg=TEXT, font=("Consolas", 11, "bold"),
                  relief="flat", bd=0, padx=16, pady=6,
                  activebackground=BORDER, activeforeground=TEXT,
                  cursor="hand2", command=self._apply).pack(side="left")

        tk.Button(btn_row, text="Close",
                  bg=BG, fg=MUTED, font=("Consolas", 10),
                  relief="flat", bd=0, padx=12, pady=6,
                  activebackground=BORDER, activeforeground=TEXT,
                  cursor="hand2", command=self.destroy).pack(side="right")

        donate = tk.Label(self, text="♥ Support this project (PayPal)",
                          bg=BG, fg=MUTED, font=("Consolas", 9, "underline"),
                          cursor="hand2")
        donate.pack(pady=(0, 4))
        donate.bind("<Button-1>", lambda e: webbrowser.open(DONATE_URL))
        donate.bind("<Enter>", lambda e: donate.config(fg=AMBER))
        donate.bind("<Leave>", lambda e: donate.config(fg=MUTED))

        help_lbl = tk.Label(self, text="? Help / logging software setup",
                          bg=BG, fg=MUTED, font=("Consolas", 9, "underline"),
                          cursor="hand2")
        help_lbl.pack(pady=(0, 14))
        help_lbl.bind("<Button-1>", lambda e: webbrowser.open(HELP_URL))
        help_lbl.bind("<Enter>", lambda e: help_lbl.config(fg=AMBER))
        help_lbl.bind("<Leave>", lambda e: help_lbl.config(fg=MUTED))

    def _select_mode(self, mode):
        self.ui_mode = mode
        for m, b in self.mode_buttons.items():
            selected = (m == mode)
            b.config(bg=AMBER if selected else BTNBG,
                     fg=BG if selected else TEXT,
                     activebackground=AMBER if selected else BORDER)
        if hasattr(self, "lbl_size_hint"):
            try:
                cur = int(self.e_size.get().strip())
            except ValueError:
                cur = 0
            min_for_mode = SIZE_BY_MODE[mode]["min"]
            if cur < min_for_mode:
                self.e_size.delete(0, "end")
                self.e_size.insert(0, str(SIZE_BY_MODE[mode]["default"]))

    def _apply(self):
        host = self.e_host.get().strip()
        udp_port_raw = self.e_udp_port.get().strip()
        try:
            port     = int(self.e_port.get().strip())
            udp_port = int(udp_port_raw) if udp_port_raw != "?" else None
            min_size = SIZE_BY_MODE[self.ui_mode]["min"]
            size     = max(min_size, min(MAX_SIZE, int(self.e_size.get().strip())))
        except ValueError:
            self.lbl_status.config(text="Invalid value.", fg=RED)
            return
        self.lbl_status.config(text="Connecting...", fg=MUTED)
        self.update_idletasks()
        threading.Thread(
            target=lambda: self.on_apply(
                {"host": host, "port": port, "udp_port": udp_port,
                 "ui_mode": self.ui_mode, "size": size},
                self._on_result),
            daemon=True).start()

    def _on_result(self, ok):
        def update():
            if not self.winfo_exists():
                return
            self.lbl_status.config(
                text="● Connected!" if ok else "● Failed.",
                fg=GREEN if ok else RED)
        self.after(0, update)


# ── Main app ───────────────────────────────────────────────

class RotorApp(tk.Tk):

    STOP_R_FRAC  = 0.12
    TICK_GAP_MAJ = 18
    TICK_GAP_MIN = 10

    def __init__(self):
        super().__init__()
        self.title("SM5K WRC Rotator Control (nätverksklient)")
        self.configure(bg=BG)
        self.resizable(False, False)

        self._cfg = load_settings()
        self.az_actual = None
        self.az_target = None
        self._canvas_size = self._cfg["size"]
        self.lbl_conn = None
        self.lbl_n1mm = None
        self._n1mm_clear_job = None
        self.daemon_udp_port = None

        self.link = DaemonLink(self._cfg["host"], self._cfg["port"], self._on_daemon_message)

        self._apply_window_style()
        self._build_ui()

    # ── Geometry ──────────────────────────────────────────

    @property
    def _is_compact(self): return self._cfg["ui_mode"] == "compact"
    @property
    def _cx(self): return self._canvas_size // 2
    @property
    def _cy(self): return self._canvas_size // 2
    @property
    def _radius(self): return int(self._canvas_size * 0.44)
    @property
    def _stop_r(self): return max(18, int(self._radius * self.STOP_R_FRAC))

    # ── Window chrome ─────────────────────────────────────

    def _apply_window_style(self):
        self.overrideredirect(self._is_compact)

    # ── Layout ────────────────────────────────────────────

    def _build_ui(self):
        for w in self.winfo_children():
            w.destroy()

        if not self._is_compact:
            bar = tk.Frame(self, bg=BORDER)
            bar.pack(fill="x")

            self.lbl_conn = tk.Label(bar, text="Rotor: ---", bg=BORDER, fg=MUTED,
                                     font=("Consolas", 9), anchor="w")
            self.lbl_conn.pack(side="left", padx=6, pady=3)

            self.lbl_n1mm = tk.Label(bar, text="N1MM: ---", bg=BORDER, fg=MUTED,
                                     font=("Consolas", 9), anchor="w")
            self.lbl_n1mm.pack(side="left", padx=6, pady=3)

            cfg_btn = tk.Button(bar, text="⚙", bg=BORDER, fg=MUTED,
                                activebackground=BORDER, activeforeground=TEXT,
                                relief="flat", font=("Consolas", 12),
                                cursor="hand2", bd=0,
                                command=self._open_settings)
            cfg_btn.pack(side="right", padx=8, pady=2)
        else:
            self.lbl_conn = None
            self.lbl_n1mm = None

        C = self._canvas_size
        self.canvas = tk.Canvas(self, width=C, height=C, bg=BG,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)

        self._draw_static()

    def _rebuild_canvas(self):
        self._apply_window_style()
        self._build_ui()
        self._update_canvas()

    # ── Compass rose ──────────────────────────────────────

    def _draw_static(self):
        cx = cy = self._cx
        R = self._radius
        outer     = R
        inner_maj = R - self.TICK_GAP_MAJ
        inner_min = R - self.TICK_GAP_MIN

        self.canvas.create_oval(cx - R - 3, cy - R - 3,
                                cx + R + 3, cy + R + 3,
                                outline=BORDER, width=2, fill=PANEL)

        for deg in range(0, 360, 5):
            rad = math.radians(deg - 90)
            c, s = math.cos(rad), math.sin(rad)
            if deg % 30 == 0:
                inner, color, w = inner_maj - 6, TEXT, 2
            elif deg % 10 == 0:
                inner, color, w = inner_maj, MUTED, 1
            else:
                inner, color, w = inner_min, BORDER, 1
            self.canvas.create_line(cx + c * inner, cy + s * inner,
                                    cx + c * outer, cy + s * outer,
                                    fill=color, width=w)

        if not self._is_compact:
            lbl_r = inner_maj - int(R * 0.12)
            for deg, lbl in {0: "N", 90: "E", 180: "S", 270: "W"}.items():
                rad = math.radians(deg - 90)
                self.canvas.create_text(cx + math.cos(rad) * lbl_r,
                                        cy + math.sin(rad) * lbl_r,
                                        text=lbl, fill=TEXT,
                                        font=("Consolas", max(8, R // 14), "bold"))

            deg_r = inner_maj - int(R * 0.09)
            cardinals = {0, 90, 180, 270}
            fsz = max(7, R // 20)
            for deg in range(0, 360, 30):
                if deg in cardinals:
                    continue
                rad = math.radians(deg - 90)
                self.canvas.create_text(cx + math.cos(rad) * deg_r,
                                        cy + math.sin(rad) * deg_r,
                                        text=str(deg), fill=MUTED,
                                        font=("Consolas", fsz))

        sr = self._stop_r
        self.stop_circle = self.canvas.create_oval(
            cx - sr, cy - sr, cx + sr, cy + sr,
            fill="#2a1a1a", outline="#5a2020", width=1)
        self.stop_label = self.canvas.create_text(
            cx, cy, text="■", fill="#5a2020",
            font=("Consolas", max(9, sr - 6), "bold"))

        self.needle_target    = None
        self.needle_actual    = None
        self.label_actual_deg = None
        self.label_target_deg = None

    # ── Dynamic drawing ───────────────────────────────────

    def _label_offset(self, degrees, base):
        # Flip the label to the opposite half from its own needle, so text
        # never sits on the needle it belongs to (or the other one).
        rad = math.radians(degrees - 90)
        return -base if math.sin(rad) > 0 else base

    def _needle(self, degrees, color, length_frac, width):
        cx = cy = self._cx
        rad = math.radians(degrees - 90)
        L = self._radius * length_frac
        return self.canvas.create_line(cx, cy,
                                       cx + math.cos(rad) * L,
                                       cy + math.sin(rad) * L,
                                       fill=color, width=width,
                                       arrow=tk.LAST,
                                       arrowshape=(max(8, width * 3),
                                                   max(10, width * 3 + 2),
                                                   max(3, width)),
                                       capstyle=tk.ROUND)

    def _update_canvas(self):
        for item in (self.needle_target, self.needle_actual,
                     self.label_actual_deg, self.label_target_deg):
            if item:
                self.canvas.delete(item)

        cx = cy = self._cx
        R = self._radius
        sr = self._stop_r
        if self._is_compact:
            fsz_az, fsz_tgt = max(10, R // 9), max(8, R // 13)
            off_actual, off_target = R * 0.27, R * 0.42
        else:
            fsz_az, fsz_tgt = max(12, R // 8), max(10, R // 11)
            off_actual, off_target = R * 0.48, R * 0.63

        # Keep the digits clear of the stop ring, which always renders on
        # top of everything else.
        off_actual = max(off_actual, sr + fsz_az * 0.7)
        off_target = max(off_target, off_actual + fsz_tgt * 1.3)

        if self.az_target is not None:
            self.needle_target = self._needle(self.az_target, AMBER, 0.74, 2)

        if self.az_actual is not None:
            self.needle_actual = self._needle(self.az_actual, GREEN, 0.87, max(3, R // 50))
            y = self._label_offset(self.az_actual, off_actual)
            self.label_actual_deg = self.canvas.create_text(
                cx, cy + y,
                text=f"{int(self.az_actual)}°",
                fill=GREEN, font=("Consolas", fsz_az, "bold"))

        if self.az_target is not None:
            y = self._label_offset(self.az_target, off_target)
            self.label_target_deg = self.canvas.create_text(
                cx, cy + y,
                text=f"→ {int(self.az_target)}°",
                fill=AMBER, font=("Consolas", fsz_tgt))

        self.canvas.tag_raise(self.stop_circle)
        self.canvas.tag_raise(self.stop_label)
        for item in (self.label_actual_deg, self.label_target_deg):
            if item:
                self.canvas.tag_raise(item)

    # ── Mouse click ───────────────────────────────────────

    def _on_press(self, event):
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin  = (self.winfo_x(), self.winfo_y())
        self._moved = False

    def _on_drag(self, event):
        if not self._is_compact:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        if abs(dx) > 5 or abs(dy) > 5:
            self._moved = True
        if self._moved:
            self.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")

    def _on_release(self, event):
        if not self._moved:
            self._handle_click(event.x, event.y)

    def _handle_click(self, x, y):
        cx = cy = self._cx
        dist = math.hypot(x - cx, y - cy)
        if dist <= self._stop_r:
            self._stop()
            return
        if dist > self._radius + 10:
            return
        angle = (math.degrees(math.atan2(y - cy, x - cx)) + 90) % 360
        self._set_target(angle)

    def _on_right_click(self, event):
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=TEXT,
                       activebackground=BORDER, activeforeground=TEXT, bd=0)
        menu.add_command(label="Settings…", command=self._open_settings)
        menu.add_command(label="Help / logging software setup", command=lambda: webbrowser.open(HELP_URL))
        menu.add_separator()
        menu.add_command(label="Avsluta", command=self.destroy)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ── Target / stop ─────────────────────────────────────

    def _set_target(self, angle):
        self.az_target = angle
        self.link.goto(angle)
        self._update_canvas()

    def _stop(self):
        self.az_target = None
        self.link.stop()
        self._update_canvas()

    # ── Daemon messages (called from reader thread) ───────

    def _on_daemon_message(self, msg):
        self.after(0, self._apply_daemon_message, msg)

    def _apply_daemon_message(self, msg):
        t = msg.get("type")
        if t == "telemetry":
            self.az_actual = msg.get("az")
            if msg.get("target") is not None:
                self.az_target = msg.get("target")
            elif self.az_target is not None and self.az_actual is not None \
                    and abs(self.az_actual - self.az_target) <= 2:
                self.az_target = None
            if self.lbl_conn is not None:
                self.lbl_conn.config(text="Rotor: connected", fg=GREEN)
            self._update_canvas()
        elif t == "wrc_status":
            if msg.get("value") != "connected":
                self.az_actual = None
                if self.lbl_conn is not None:
                    self.lbl_conn.config(text="Rotor: no connection", fg=RED)
                self._update_canvas()
        elif t == "n1mm_bearing":
            if self.lbl_n1mm is not None:
                self.lbl_n1mm.config(text=f"N1MM: {int(msg['value'])}°", fg=AMBER)
                if self._n1mm_clear_job:
                    self.after_cancel(self._n1mm_clear_job)
                self._n1mm_clear_job = self.after(8000, self._clear_n1mm_label)
        elif t == "n1mm_stop":
            if self.lbl_n1mm is not None:
                self.lbl_n1mm.config(text="N1MM: ---", fg=MUTED)
        elif t == "config":
            self.daemon_udp_port = msg.get("udp_port")

    def _clear_n1mm_label(self):
        if self.lbl_n1mm is not None:
            self.lbl_n1mm.config(text="N1MM: ---", fg=MUTED)

    # ── Settings ──────────────────────────────────────────

    def _open_settings(self):
        dlg_cfg = dict(self._cfg)
        dlg_cfg["udp_port"] = self.daemon_udp_port if self.daemon_udp_port is not None else "?"
        SettingsDialog(self, dlg_cfg, self._apply_settings)

    def _apply_settings(self, new_cfg, callback):
        host_changed = new_cfg["host"] != self._cfg["host"] or new_cfg["port"] != self._cfg["port"]
        ok = self.link.reconnect(new_cfg["host"], new_cfg["port"]) if host_changed else True
        if new_cfg["udp_port"] is not None and new_cfg["udp_port"] != self.daemon_udp_port:
            self.link.set_udp_port(new_cfg["udp_port"])
        style_changed = new_cfg["ui_mode"] != self._cfg["ui_mode"]
        self._cfg = {k: v for k, v in new_cfg.items() if k != "udp_port"}
        self._canvas_size = new_cfg["size"]
        save_settings(self._cfg)
        self.after(0, self._rebuild_canvas if style_changed else self._resize_only)
        callback(ok)

    def _resize_only(self):
        self._build_ui()
        self._update_canvas()


# ── Entry point ───────────────────────────────────────────

if __name__ == "__main__":
    app = RotorApp()
    app.mainloop()
