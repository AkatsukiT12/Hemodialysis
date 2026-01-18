# hemodialysis_akatsuki_gui.py
# Ultra-Modern Dashboard for Hemodialysis Monitoring with Akatsuki Theme
# pip install opencv-python mediapipe pyserial tkinter pillow

import cv2
import mediapipe as mp
import time
import winsound
from collections import deque
import numpy as np
import serial
import serial.tools.list_ports
import threading
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw
from datetime import datetime
import os

# ========== CONFIG ==========
DROP_THRESHOLD = 40
SMOOTHING_WINDOW = 5
DROOP_SUSTAIN_SECS = 1.0
ALARM_COOLDOWN_SECS = 3.0

# ========== ARDUINO THRESHOLDS ==========
TEMP_THRESHOLD = 38.0
LEAK_THRESHOLD = 500
BUBBLE_THRESHOLD = 400

# ========== SERIAL CONFIG ==========
SERIAL_ENABLED = True
BAUD_RATE = 115200
SERIAL_TIMEOUT = 0.5
SERIAL_SEND_INTERVAL = 0.1

# ========== GLOBALS ==========
serial_conn = None
serial_lock = threading.Lock()
arduino_status = {
    "connected": False,
    "last_heartbeat": 0,
    "cv_state": -1,
    "temperature": 0.0,
    "ir_value": 0,
    "ldr_value": 0,
    "leak_detected": False,
    "bubble_detected": False,
    "high_temp": False
}

cv_status = {
    "state": 3,
    "head_tilt": 0.0,
    "state_text": "Initializing...",
    "messages_sent": 0
}

event_log = deque(maxlen=20)
temp_history = deque(maxlen=50)
ir_history = deque(maxlen=50)
ldr_history = deque(maxlen=50)


def log_event(message, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    event_log.append(f"[{timestamp}] {level}: {message}")


# ========== SERIAL FUNCTIONS ==========
def find_arduino_port():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        if any(keyword in port.description.upper() for keyword in ['ARDUINO', 'CH340', 'USB SERIAL']):
            return port.device
    if ports:
        return ports[0].device
    return None


def open_serial():
    global serial_conn
    port = find_arduino_port()
    if not port:
        log_event("No COM ports found!", "ERROR")
        return False

    try:
        serial_conn = serial.Serial(port, BAUD_RATE, timeout=SERIAL_TIMEOUT)
        time.sleep(2.5)
        serial_conn.reset_input_buffer()
        serial_conn.reset_output_buffer()

        log_event(f"Connected to {port}", "SUCCESS")
        serial_conn.write(b"PYTHON_READY\n")
        serial_conn.flush()

        start_wait = time.time()
        while time.time() - start_wait < 3:
            if serial_conn.in_waiting > 0:
                response = serial_conn.readline().decode('utf-8', errors='ignore').strip()
                if "ARDUINO_READY" in response or "ACK" in response:
                    arduino_status["connected"] = True
                    arduino_status["last_heartbeat"] = time.time()
                    log_event("Handshake complete", "SUCCESS")
                    return True
        log_event("Handshake timed out, proceeding...", "WARN")
        arduino_status["connected"] = True
        arduino_status["last_heartbeat"] = time.time()
        return True
    except Exception as e:
        log_event(f"Serial error: {e}", "ERROR")
        serial_conn = None
        return False


def serial_reader_thread():
    global serial_conn
    while True:
        try:
            if serial_conn and serial_conn.is_open:
                if serial_conn.in_waiting > 0:
                    with serial_lock:
                        line = serial_conn.readline().decode('utf-8', errors='ignore').strip()

                    if line:
                        if line.startswith("STATUS:"):
                            arduino_status["last_heartbeat"] = time.time()
                            arduino_status["connected"] = True
                            parts = line.replace("STATUS:", "").split(',')
                            if len(parts) >= 4:
                                arduino_status["cv_state"] = int(parts[0])
                                arduino_status["temperature"] = float(parts[1])
                                arduino_status["ir_value"] = int(parts[2])
                                arduino_status["ldr_value"] = int(parts[3])

                                temp_history.append(arduino_status["temperature"])
                                ir_history.append(arduino_status["ir_value"])
                                ldr_history.append(arduino_status["ldr_value"])

                                arduino_status["leak_detected"] = arduino_status["ir_value"] > LEAK_THRESHOLD
                                arduino_status["bubble_detected"] = arduino_status["ldr_value"] < BUBBLE_THRESHOLD
                                arduino_status["high_temp"] = arduino_status["temperature"] > TEMP_THRESHOLD

                        elif line.startswith("WARNING:"):
                            log_event(line.replace("WARNING:", ""), "WARN")
                        elif line.startswith("EMERGENCY:"):
                            log_event(line.replace("EMERGENCY:", ""), "ALARM")
                        elif line.startswith("ERROR:"):
                            log_event(line.replace("ERROR:", ""), "ERROR")
                else:
                    time.sleep(0.01)
            else:
                time.sleep(0.1)
        except Exception as e:
            if serial_conn:
                log_event(f"Serial reader error: {e}", "ERROR")
            serial_conn = None
            arduino_status["connected"] = False
            time.sleep(1)


def send_to_arduino(state_code, head_tilt):
    global serial_conn
    if not SERIAL_ENABLED or serial_conn is None or not serial_conn.is_open:
        return False

    try:
        with serial_lock:
            msg = f"CV:{int(state_code)},{head_tilt:.1f}\n"
            serial_conn.write(msg.encode('utf-8'))
            serial_conn.flush()
        cv_status["messages_sent"] += 1
        return True
    except Exception as e:
        log_event(f"Serial write error: {e}", "ERROR")
        serial_conn = None
        arduino_status["connected"] = False
        return False


# ========== CV PROCESSING ==========
mp_face = mp.solutions.face_mesh
face_mesh = mp_face.FaceMesh(min_detection_confidence=0.5, min_tracking_confidence=0.5)

cap = cv2.VideoCapture(0)
last_seen = time.time()
last_alarm_time = 0.0
tilt_buffer = deque(maxlen=SMOOTHING_WINDOW)
droop_start_time = None
last_serial_send = 0.0


def play_alert():
    try:
        winsound.Beep(1000, 300)
    except:
        pass


def get_landmark_xy(landmarks, idx, img_w, img_h):
    lm = landmarks[idx]
    return int(lm.x * img_w), int(lm.y * img_h)


# ========== ULTRA-MODERN DASHBOARD GUI ==========
class ModernDashboardGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Akatsuki Medical - Hemodialysis Monitor")
        self.root.geometry("1600x900")
        self.root.bind('<Configure>', self.resize_widgets)

        # Theme system
        self.dark_mode = True
        self.setup_themes()
        self.apply_theme()

        # Main container
        self.main_container = tk.Frame(root, bg=self.colors['bg'])
        self.main_container.pack(fill=tk.BOTH, expand=True)

        self.create_interface()

        self.root.after(50, self.update_video)
        self.root.after(100, self.update_dashboard)
        self.root.after(150, self.update_graphs)

    def setup_themes(self):
        self.themes = {
            'dark': {
                'bg': '#0a0e1a',
                'sidebar': '#151923',
                'card': '#1a1f2e',
                'card_hover': '#252d3d',
                'text': '#e8eaed',
                'text_secondary': '#8b92a0',
                'accent': '#cc0000',
                'accent_light': '#ff3333',
                'success': '#00ff88',
                'warning': '#ffa726',
                'danger': '#ff3d3d',
                'border': '#2d3548',
                'graph_line': '#00d4ff',
                'graph_bg': '#12161f',
                'shadow': '#000000'
            },
            'light': {
                'bg': '#f0f2f5',
                'sidebar': '#ffffff',
                'card': '#ffffff',
                'card_hover': '#f8f9fb',
                'text': '#1a1f29',
                'text_secondary': '#6b7280',
                'accent': '#cc0000',
                'accent_light': '#ff3333',
                'success': '#00c853',
                'warning': '#ff9800',
                'danger': '#f44336',
                'border': '#dce1e8',
                'graph_line': '#2196f3',
                'graph_bg': '#fafbfc',
                'shadow': '#cccccc'
            }
        }
        self.colors = self.themes['dark']

    def apply_theme(self):
        self.colors = self.themes['dark'] if self.dark_mode else self.themes['light']
        if hasattr(self, 'main_container'):
            self.refresh_ui()

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        if hasattr(self, 'theme_btn'):
            self.theme_btn.config(text="🌓 " + ("Light" if self.dark_mode else "Dark"))

    def refresh_ui(self):
        """Recursively update all widget colors based on current theme"""
        self.main_container.configure(bg=self.colors['bg'])

        if hasattr(self, 'bg_canvas'):
            self.draw_cube_pattern()

        def update_colors(widget, parent_type='bg'):
            try:
                widget_class = widget.winfo_class()

                if 'nav' in str(widget).lower() or parent_type == 'sidebar':
                    bg = self.colors['sidebar']
                    parent_type = 'sidebar'
                elif 'card' in str(widget).lower() or parent_type == 'card':
                    bg = self.colors['card']
                    parent_type = 'card'
                elif 'graph' in str(widget).lower() or parent_type == 'graph':
                    bg = self.colors['graph_bg']
                    parent_type = 'graph'
                else:
                    bg = self.colors['bg']

                if widget_class == 'Frame':
                    if widget.cget('bg') in ['#000000', '#00000020', '#cccccc']:
                        widget.configure(bg=self.colors['shadow'])
                    else:
                        widget.configure(bg=bg)

                elif widget_class == 'Label':
                    widget.configure(bg=bg)
                    current_fg = widget.cget('fg')

                    color_map = {
                        '#00ff88': self.colors['success'],
                        '#00c853': self.colors['success'],
                        '#27ae60': self.colors['success'],
                        '#ff3d3d': self.colors['danger'],
                        '#f44336': self.colors['danger'],
                        '#e74c3c': self.colors['danger'],
                        '#ffa726': self.colors['warning'],
                        '#ff9800': self.colors['warning'],
                        '#f39c12': self.colors['warning'],
                    }

                    if current_fg in color_map:
                        widget.configure(fg=color_map[current_fg])
                    elif current_fg in ['#e8eaed', '#2c3e50', '#1a1f29']:
                        widget.configure(fg=self.colors['text'])
                    elif current_fg in ['#8b92a0', '#6b7280', '#7f8c8d']:
                        widget.configure(fg=self.colors['text_secondary'])

                elif widget_class == 'Button':
                    widget.configure(bg=self.colors['card'], fg=self.colors['text'],
                                     activebackground=self.colors['card_hover'])

                elif widget_class == 'Text':
                    widget.configure(bg=self.colors['graph_bg'], fg=self.colors['text'],
                                     insertbackground=self.colors['text'])

                elif widget_class == 'Canvas':
                    widget.configure(bg=self.colors['graph_bg'])

                elif widget_class == 'Scrollbar':
                    widget.configure(bg=self.colors['card'], troughcolor=self.colors['graph_bg'])

                for child in widget.winfo_children():
                    update_colors(child, parent_type)

            except Exception as e:
                pass

        update_colors(self.main_container)

        if hasattr(self, 'graph_canvas'):
            self.draw_graph()

        if hasattr(self, 'log_text'):
            self.log_text.tag_config('alarm', foreground=self.colors['danger'])
            self.log_text.tag_config('warn', foreground=self.colors['warning'])
            self.log_text.tag_config('error', foreground=self.colors['danger'])
            self.log_text.tag_config('success', foreground=self.colors['success'])

        if hasattr(self, 'theme_btn'):
            self.theme_btn.config(
                text="🌓 " + ("Light" if self.dark_mode else "Dark"),
                bg=self.colors['card'],
                fg=self.colors['text'],
                activebackground=self.colors['card_hover']
            )

    def create_interface(self):
        self.create_background_pattern()
        self.create_top_nav()

        content = tk.Frame(self.main_container, bg=self.colors['bg'])
        content.pack(fill=tk.BOTH, expand=True, padx=25, pady=(10, 25))

        content.grid_rowconfigure(0, weight=1, minsize=100)
        content.grid_rowconfigure(1, weight=3, minsize=200)
        content.grid_rowconfigure(2, weight=3, minsize=200)

        content.grid_columnconfigure(0, weight=2)
        content.grid_columnconfigure(1, weight=2)
        content.grid_columnconfigure(2, weight=1)

        stat_frame = tk.Frame(content, bg=self.colors['bg'])
        stat_frame.grid(row=0, column=0, columnspan=3, sticky='nsew', pady=(0, 8))

        stat_frame.grid_columnconfigure(0, weight=1)
        stat_frame.grid_columnconfigure(1, weight=1)
        stat_frame.grid_columnconfigure(2, weight=1)
        stat_frame.grid_rowconfigure(0, weight=1)

        self.create_stat_card(stat_frame, "CV Status", "state", 0, 0)
        self.create_stat_card(stat_frame, "Temperature", "temp", 0, 1)
        self.create_stat_card(stat_frame, "System Health", "health", 0, 2)

        self.create_camera_card(content, 1, 0, columnspan=2)
        self.create_warnings_card(content, 1, 2)

        self.create_sensor_panel(content, 2, 0, columnspan=2)
        self.create_log_card(content, 2, 2)

    def create_background_pattern(self):
        """Create geometric cube pattern background"""
        self.bg_canvas = tk.Canvas(self.main_container,
                                   bg=self.colors['bg'],
                                   highlightthickness=0)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.root.update_idletasks()
        self.draw_cube_pattern()

    def draw_cube_pattern(self):
        """Draw isometric cube pattern"""
        self.bg_canvas.delete("all")
        self.bg_canvas.configure(bg=self.colors['bg'])

        self.root.update_idletasks()
        width = self.bg_canvas.winfo_width()
        height = self.bg_canvas.winfo_height()

        if width <= 1 or height <= 1:
            return

        cube_size = 60
        row_offset = cube_size * 0.866
        col_offset = cube_size * 1.5

        for row in range(-2, int(height / row_offset) + 3):
            for col in range(-2, int(width / col_offset) + 3):
                x = col * col_offset + (row % 2) * (col_offset / 2)
                y = row * row_offset

                if -cube_size <= x <= width + cube_size and -cube_size <= y <= height + cube_size:
                    self.draw_isometric_cube(x, y, cube_size)

    def draw_isometric_cube(self, x, y, size):
        """Draw a single isometric cube"""
        h = size * 0.866

        top = [
            x, y,
            x + size / 2, y + h / 2,
            x, y + h,
            x - size / 2, y + h / 2
        ]

        left = [
            x - size / 2, y + h / 2,
            x, y + h,
            x, y + h + size / 2,
            x - size / 2, y + h + size / 2
        ]

        right = [
            x, y + h,
               x + size / 2, y + h / 2,
               x + size / 2, y + h + size / 2,
            x, y + h + size / 2
        ]

        if self.dark_mode:
            top_color = '#1a1f2e'
            left_color = '#151923'
            right_color = '#12161f'
            outline = '#2d3548'
        else:
            top_color = '#ffffff'
            left_color = '#f5f7fa'
            right_color = '#e8ecf1'
            outline = '#dde2e8'

        self.bg_canvas.create_polygon(left, fill=left_color, outline=outline, width=1)
        self.bg_canvas.create_polygon(right, fill=right_color, outline=outline, width=1)
        self.bg_canvas.create_polygon(top, fill=top_color, outline=outline, width=1)

    def create_top_nav(self):
        nav = tk.Frame(self.main_container, bg=self.colors['sidebar'], height=75)
        nav.pack(fill=tk.X)
        nav.pack_propagate(False)

        left_frame = tk.Frame(nav, bg=self.colors['sidebar'])
        left_frame.pack(side=tk.LEFT, padx=30, pady=15)

        logo_container = tk.Frame(left_frame, bg=self.colors['accent'],
                                  width=45, height=45, relief=tk.FLAT)
        logo_container.pack(side=tk.LEFT, padx=(0, 15))
        logo_container.pack_propagate(False)

        try:
            logo_path = "akatsuki_logo.png"
            if os.path.exists(logo_path):
                logo_img = Image.open(logo_path)
                logo_img = logo_img.resize((40, 40), Image.Resampling.LANCZOS)
                logo_photo = ImageTk.PhotoImage(logo_img)
                logo_label = tk.Label(logo_container, image=logo_photo, bg=self.colors['accent'])
                logo_label.image = logo_photo
                logo_label.pack(expand=True)
            else:
                raise FileNotFoundError("Logo not found")
        except Exception as e:
            log_event(f"Failed to load logo: {e}", "WARN")
            tk.Label(logo_container, text="暁", font=('MS Gothic', 20, 'bold'),
                     bg=self.colors['accent'], fg='white').pack(expand=True)

        title_frame = tk.Frame(left_frame, bg=self.colors['sidebar'])
        title_frame.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(title_frame, text="AKATSUKI MEDICAL",
                 font=('Segoe UI', 15, 'bold'),
                 bg=self.colors['sidebar'], fg=self.colors['text'],
                 anchor='w').pack(anchor='w')
        tk.Label(title_frame, text="Hemodialysis Monitoring System",
                 font=('Segoe UI', 9),
                 bg=self.colors['sidebar'], fg=self.colors['text_secondary'],
                 anchor='w').pack(anchor='w')

        right_frame = tk.Frame(nav, bg=self.colors['sidebar'])
        right_frame.pack(side=tk.RIGHT, padx=30, pady=15)

        conn_frame = tk.Frame(right_frame, bg=self.colors['sidebar'])
        conn_frame.pack(side=tk.RIGHT, padx=20)

        self.connection_indicator = tk.Label(conn_frame, text="●",
                                             font=('Arial', 14),
                                             bg=self.colors['sidebar'],
                                             fg=self.colors['danger'])
        self.connection_indicator.pack(side=tk.LEFT, padx=(0, 5))

        self.connection_label = tk.Label(conn_frame, text="Disconnected",
                                         font=('Segoe UI', 9),
                                         bg=self.colors['sidebar'],
                                         fg=self.colors['text_secondary'])
        self.connection_label.pack(side=tk.LEFT)

        self.theme_btn = tk.Button(right_frame,
                                   text="🌓 " + ("Light" if self.dark_mode else "Dark"),
                                   font=('Segoe UI', 10),
                                   bg=self.colors['card'],
                                   fg=self.colors['text'],
                                   activebackground=self.colors['card_hover'],
                                   relief=tk.FLAT,
                                   padx=20, pady=8,
                                   command=self.toggle_theme,
                                   cursor='hand2',
                                   bd=0)
        self.theme_btn.pack(side=tk.RIGHT, padx=10)

        self.time_display = tk.Label(right_frame, text="",
                                     font=('Segoe UI', 11),
                                     bg=self.colors['sidebar'],
                                     fg=self.colors['text'])
        self.time_display.pack(side=tk.RIGHT, padx=20)
        self.update_time()

    def create_card_frame(self, parent, row, col, rowspan=1, columnspan=1):
        shadow = tk.Frame(parent, bg=self.colors['shadow'], relief=tk.FLAT)
        shadow.grid(row=row, column=col, rowspan=rowspan, columnspan=columnspan,
                    sticky='nsew', padx=8, pady=8)

        card = tk.Frame(shadow, bg=self.colors['card'], relief=tk.FLAT, bd=0)
        card.place(x=0, y=0, relwidth=1, relheight=1, bordermode='outside')
        shadow.bind('<Configure>', lambda e: card.place(x=3, y=3, relwidth=1, relheight=1))

        return card

    def create_stat_card(self, parent, title, stat_type, row, col):
        card = self.create_card_frame(parent, row, col)

        container = tk.Frame(card, bg=self.colors['card'])
        container.pack(fill=tk.BOTH, expand=True, padx=20, pady=18)

        tk.Label(container, text=title,
                 font=('Segoe UI', 9),
                 bg=self.colors['card'],
                 fg=self.colors['text_secondary'],
                 anchor='w').pack(anchor='w', pady=(0, 10))

        value_label = tk.Label(container, text="--",
                               font=('Segoe UI', 36, 'bold'),
                               bg=self.colors['card'],
                               fg=self.colors['text'],
                               anchor='w')
        value_label.pack(anchor='w', fill=tk.X)

        subtitle = tk.Label(container, text="Initializing...",
                            font=('Segoe UI', 9),
                            bg=self.colors['card'],
                            fg=self.colors['text_secondary'],
                            anchor='w')
        subtitle.pack(anchor='w', pady=(5, 0))

        if stat_type == "state":
            self.cv_stat_value = value_label
            self.cv_stat_subtitle = subtitle
        elif stat_type == "temp":
            self.temp_stat_value = value_label
            self.temp_stat_subtitle = subtitle
        elif stat_type == "health":
            self.health_stat_value = value_label
            self.health_stat_subtitle = subtitle

    def create_camera_card(self, parent, row, col, columnspan=1):
        card = self.create_card_frame(parent, row, col, columnspan=columnspan)

        header = tk.Frame(card, bg=self.colors['card'], height=55)
        header.pack(fill=tk.X, padx=25, pady=(18, 8))
        header.pack_propagate(False)

        tk.Label(header, text="📹 Patient Surveillance",
                 font=('Segoe UI', 13, 'bold'),
                 bg=self.colors['card'],
                 fg=self.colors['text']).pack(side=tk.LEFT, anchor='w')

        self.cv_status_badge = tk.Label(header, text="● NORMAL",
                                        font=('Segoe UI', 9, 'bold'),
                                        bg=self.colors['success'],
                                        fg='white',
                                        padx=15, pady=5)
        self.cv_status_badge.pack(side=tk.RIGHT)

        video_container = tk.Frame(card, bg='#000000', relief=tk.FLAT)
        video_container.pack(fill=tk.BOTH, expand=True, padx=25, pady=(0, 20))

        self.video_container_ref = video_container

        self.video_label = tk.Label(video_container, bg='#000000')
        self.video_label.pack(expand=True)

        self.video_label_aspect_ratio = 4.0 / 3.0
        self.video_label_ref = self.video_label

    def create_warnings_card(self, parent, row, col):
        card = self.create_card_frame(parent, row, col)

        header = tk.Frame(card, bg=self.colors['card'], height=55)
        header.pack(fill=tk.X, padx=25, pady=(18, 12))
        header.pack_propagate(False)

        tk.Label(header, text="⚠️ System Warnings",
                 font=('Segoe UI', 13, 'bold'),
                 bg=self.colors['card'],
                 fg=self.colors['text']).pack(anchor='w')

        warnings_container = tk.Frame(card, bg=self.colors['card'])
        warnings_container.pack(fill=tk.BOTH, expand=True, padx=25, pady=(0, 20))

        self.warning_widgets = []
        warnings = [
            ("Blood Leak Detection", "IR Sensor", "💧"),
            ("Air Bubble Detection", "LDR Sensor", "🫧"),
            ("Temperature Monitor", "Thermal Sensor", "🌡️"),
            ("Patient Position", "CV Analysis", "👤")
        ]

        for i, (title, subtitle, icon) in enumerate(warnings):
            warning_dict = self.create_warning_item(warnings_container, title, subtitle, icon)
            warning_dict['frame'].pack(fill=tk.X, pady=5)
            self.warning_widgets.append(warning_dict)

    def create_warning_item(self, parent, title, subtitle, icon):
        item = tk.Frame(parent, bg=self.colors['card'], relief=tk.FLAT)
        item.configure(highlightbackground=self.colors['border'],
                       highlightcolor=self.colors['border'],
                       highlightthickness=1)

        content = tk.Frame(item, bg=self.colors['card'])
        content.pack(fill=tk.X, padx=12, pady=10)

        left_frame = tk.Frame(content, bg=self.colors['card'])
        left_frame.pack(side=tk.LEFT)

        icon_label = tk.Label(left_frame, text=icon,
                              font=('Segoe UI', 16),
                              bg=self.colors['card'],
                              width=2)
        icon_label.pack(side=tk.LEFT, padx=(0, 8))

        indicator = tk.Label(left_frame, text="●",
                             font=('Arial', 12),
                             bg=self.colors['card'],
                             fg=self.colors['success'],
                             width=1)
        indicator.pack(side=tk.LEFT, padx=(0, 8))

        text_frame = tk.Frame(content, bg=self.colors['card'])
        text_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        title_label = tk.Label(text_frame, text=title,
                               font=('Segoe UI', 10, 'bold'),
                               bg=self.colors['card'],
                               fg=self.colors['text'],
                               anchor='w')
        title_label.pack(fill=tk.X, anchor='w')

        subtitle_label = tk.Label(text_frame, text=subtitle,
                                  font=('Segoe UI', 8),
                                  bg=self.colors['card'],
                                  fg=self.colors['text_secondary'],
                                  anchor='w')
        subtitle_label.pack(fill=tk.X, anchor='w')

        value_label = tk.Label(content, text="OK",
                               font=('Segoe UI', 9, 'bold'),
                               bg=self.colors['card'],
                               fg=self.colors['success'],
                               anchor='e',
                               width=8)
        value_label.pack(side=tk.RIGHT, padx=(10, 0))

        return {
            'frame': item,
            'indicator': indicator,
            'title': title_label,
            'subtitle': subtitle_label,
            'value': value_label,
            'icon': icon_label
        }

    def create_sensor_panel(self, parent, row, col, columnspan=1):
        card = self.create_card_frame(parent, row, col, columnspan=columnspan)

        header = tk.Frame(card, bg=self.colors['card'], height=55)
        header.pack(fill=tk.X, padx=25, pady=(18, 8))
        header.pack_propagate(False)

        tk.Label(header, text="📊 Sensor Data & Trends",
                 font=('Segoe UI', 13, 'bold'),
                 bg=self.colors['card'],
                 fg=self.colors['text']).pack(side=tk.LEFT)

        sensor_values = tk.Frame(header, bg=self.colors['card'])
        sensor_values.pack(side=tk.RIGHT)

        self.temp_display = self.create_mini_sensor(sensor_values, "Temp", "--°C", 0)
        self.ir_display = self.create_mini_sensor(sensor_values, "IR", "--", 1)
        self.ldr_display = self.create_mini_sensor(sensor_values, "LDR", "--", 2)

        self.graph_canvas = tk.Canvas(card, bg=self.colors['graph_bg'],
                                      highlightthickness=0, height=250)
        self.graph_canvas.pack(fill=tk.BOTH, expand=True, padx=25, pady=(0, 20))
        self.graph_canvas_ref = self.graph_canvas

    def create_mini_sensor(self, parent, label, value, index):
        frame = tk.Frame(parent, bg=self.colors['card'])
        frame.grid(row=0, column=index, padx=10)

        tk.Label(frame, text=label,
                 font=('Segoe UI', 8),
                 bg=self.colors['card'],
                 fg=self.colors['text_secondary']).pack()

        value_label = tk.Label(frame, text=value,
                               font=('Segoe UI', 11, 'bold'),
                               bg=self.colors['card'],
                               fg=self.colors['text'])
        value_label.pack()

        return value_label

    def create_log_card(self, parent, row, col):
        card = self.create_card_frame(parent, row, col)

        header = tk.Frame(card, bg=self.colors['card'], height=55)
        header.pack(fill=tk.X, padx=25, pady=(18, 8))
        header.pack_propagate(False)

        tk.Label(header, text="📋 Event Log",
                 font=('Segoe UI', 13, 'bold'),
                 bg=self.colors['card'],
                 fg=self.colors['text']).pack(anchor='w')

        log_container = tk.Frame(card, bg=self.colors['graph_bg'])
        log_container.pack(fill=tk.BOTH, expand=True, padx=25, pady=(0, 20))

        scrollbar = tk.Scrollbar(log_container,
                                 bg=self.colors['card'],
                                 troughcolor=self.colors['graph_bg'])
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text = tk.Text(log_container,
                                bg=self.colors['graph_bg'],
                                fg=self.colors['text'],
                                font=('Consolas', 9),
                                yscrollcommand=scrollbar.set,
                                relief=tk.FLAT, bd=0,
                                wrap=tk.WORD,
                                padx=10, pady=10)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.log_text.yview)

    def draw_graph(self):
        try:
            self.graph_canvas.delete("all")

            self.root.update_idletasks()
            width = self.graph_canvas.winfo_width()
            height = self.graph_canvas.winfo_height()

            if width < 100 or height < 100:
                return

            pad_x, pad_y = 60, 40
            graph_width = width - (2 * pad_x)
            graph_height = height - (2 * pad_y)

            if graph_width <= 0 or graph_height <= 0:
                return

            datasets = [
                (list(temp_history), 0.0, 50.0, self.colors['danger'], "Temp (°C)"),
                (list(ir_history), 0.0, 1024.0, self.colors['graph_line'], "IR"),
                (list(ldr_history), 0.0, 1024.0, self.colors['warning'], "LDR")
            ]

            num_y_labels = 5
            for i in range(num_y_labels):
                y = pad_y + (graph_height / (num_y_labels - 1)) * i
                self.graph_canvas.create_line(pad_x, y, width - pad_x, y,
                                              fill=self.colors['border'],
                                              width=1, dash=(2, 4))

                label_val = 1024.0 * (1 - (i / (num_y_labels - 1)))
                self.graph_canvas.create_text(pad_x - 10, y, anchor='e',
                                              text=f"{label_val:.0f}",
                                              fill=self.colors['text_secondary'],
                                              font=('Segoe UI', 8))

            self.graph_canvas.create_text(pad_x - 40, height/2, anchor='center',
                                          text="Value",
                                          fill=self.colors['text_secondary'],
                                          font=('Segoe UI', 9, 'bold'),
                                          angle=90)

            max_len = 50
            for data, min_val, max_val, color, label_text in datasets:
                if not data:
                    continue

                points = []
                for i, value in enumerate(data):
                    x = pad_x + (i / (max_len - 1)) * graph_width

                    normalized_value = max(min_val, min(max_val, value))

                    y_val = (normalized_value - min_val) / (max_val - min_val) if (max_val - min_val) != 0 else 0
                    y = pad_y + graph_height - (y_val * graph_height)
                    points.extend([x, y])

                if len(points) >= 4:
                    self.graph_canvas.create_line(points, fill=color,
                                                  width=2, smooth=True, tags=(f"graph_{label_text}"))

                    last_x, last_y = points[-2], points[-1]
                    self.graph_canvas.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4,
                                                  fill=color, outline=color, width=2, tags=(f"graph_{label_text}_marker"))

                    self.graph_canvas.create_text(last_x + 8, last_y - 8, anchor='w',
                                                  text=f"{data[-1]:.1f}",
                                                  fill=color, font=('Segoe UI', 8), tags=(f"graph_{label_text}_value"))

            num_x_labels = 5
            for i in range(num_x_labels):
                x = pad_x + (graph_width / (num_x_labels - 1)) * i

                time_val = (max_len - 1) * (i / (num_x_labels - 1))
                label_text = f"-{max_len - 1 - time_val:.0f}s"

                self.graph_canvas.create_text(x, height - pad_y + 15, anchor='n',
                                              text=label_text,
                                              fill=self.colors['text_secondary'],
                                              font=('Segoe UI', 8))

            legend_x = pad_x + 20
            legend_y = pad_y - 20
            for i, (data, min_val, max_val, color, label_text) in enumerate(datasets):
                x_start = legend_x + (i * 100)
                self.graph_canvas.create_rectangle(x_start, legend_y, x_start + 15, legend_y + 10, fill=color, outline='')
                self.graph_canvas.create_text(x_start + 20, legend_y + 5, anchor='w', text=label_text,
                                              fill=self.colors['text'], font=('Segoe UI', 9))
        except Exception as e:
            log_event(f"Error drawing graph: {e}", "ERROR")

    def update_time(self):
        current_time = datetime.now().strftime("%H:%M:%S")
        self.time_display.config(text=current_time)
        self.root.after(1000, self.update_time)

    def update_video(self):
        global last_seen, droop_start_time, last_serial_send, last_alarm_time

        ret, img = cap.read()
        if ret:
            try:
                img = cv2.flip(img, 1)
                img_h, img_w = img.shape[:2]
                img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                results = face_mesh.process(img_rgb)
                now = time.time()

                send_state_code = 3
                send_tilt_val = 0.0
                state_text = "No Face"
                color = (255, 100, 100)

                if getattr(results, "multi_face_landmarks", None):
                    last_seen = now
                    face_landmarks = results.multi_face_landmarks[0].landmark

                    nose_x, nose_y = get_landmark_xy(face_landmarks, 1, img_w, img_h)
                    chin_x, chin_y = get_landmark_xy(face_landmarks, 152, img_w, img_h)

                    head_tilt_raw = chin_y - nose_y
                    tilt_buffer.append(head_tilt_raw)
                    head_tilt = float(np.mean(tilt_buffer))
                    send_tilt_val = head_tilt

                    if head_tilt < DROP_THRESHOLD:
                        if droop_start_time is None:
                            droop_start_time = now
                        elapsed = now - droop_start_time
                        color = (255, 165, 0)
                        state_text = "Possible Droop"
                        send_state_code = 1

                        if elapsed >= DROOP_SUSTAIN_SECS:
                            if now - last_alarm_time > ALARM_COOLDOWN_SECS:
                                last_alarm_time = now
                                play_alert()
                                log_event("HEAD DROOP DETECTED!", "ALARM")
                            color = (255, 50, 50)
                            state_text = "HEAD DROOP"
                            send_state_code = 2
                    else:
                        droop_start_time = None
                        state_text = "NORMAL"
                        send_state_code = 0
                        color = (0, 255, 150)

                    xs = [lm.x * img_w for lm in face_landmarks]
                    ys = [lm.y * img_h for lm in face_landmarks]
                    x1, y1 = int(min(xs)) - 20, int(min(ys)) - 60
                    x2, y2 = int(max(xs)) + 20, int(max(ys)) + 20

                    cv2.rectangle(img_rgb, (x1, y1), (x2, y2), color, 2)

                    overlay = img_rgb.copy()
                    cv2.rectangle(overlay, (x1, y1), (x2, y1 + 40), color, -1)
                    cv2.addWeighted(overlay, 0.7, img_rgb, 0.3, 0, img_rgb)
                    text = state_text.upper()
                    text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)[0]
                    text_x = x1 + ((x2 - x1) - text_size[0]) // 2
                    text_y = y1 + 28
                    cv2.putText(img_rgb, text, (text_x, text_y),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                else:
                    if now - last_seen > 5:
                        overlay = img_rgb.copy()
                        cv2.rectangle(overlay, (0, img_h - 100), (img_w, img_h), (50, 0, 0), -1)
                        cv2.addWeighted(overlay, 0.6, img_rgb, 0.4, 0, img_rgb)
                        cv2.putText(img_rgb, "PATIENT MISSING", (30, img_h - 55),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 100, 100), 3)
                        cv2.putText(img_rgb, "Check camera positioning", (30, img_h - 25),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)

                        if now - last_alarm_time > ALARM_COOLDOWN_SECS:
                            last_alarm_time = now
                            play_alert()
                            log_event("Patient missing for 5+ seconds", "ALARM")
                        send_state_code = 3
                        state_text = "Missing"

                cv_status["state"] = send_state_code
                cv_status["head_tilt"] = send_tilt_val
                cv_status["state_text"] = state_text

                if now - last_serial_send >= SERIAL_SEND_INTERVAL:
                    send_to_arduino(send_state_code, send_tilt_val)
                    last_serial_send = now

                if self.video_container_ref:
                    self.root.update_idletasks()
                    container_w = self.video_container_ref.winfo_width()
                    container_h = self.video_container_ref.winfo_height()

                    if container_w > 10 and container_h > 10:
                        img_aspect = img_w / img_h
                        container_aspect = container_w / container_h

                        if img_aspect > container_aspect:
                            new_w = container_w - 40
                            new_h = int(new_w / img_aspect)
                        else:
                            new_h = container_h - 40
                            new_w = int(new_h * img_aspect)

                        new_w = max(100, min(new_w, container_w - 20))
                        new_h = max(75, min(new_h, container_h - 20))

                        img_resized = cv2.resize(img_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
                        photo = ImageTk.PhotoImage(image=Image.fromarray(img_resized))
                        self.video_label.config(image=photo)
                        self.video_label.image = photo
                    else:
                        img_resized = cv2.resize(img_rgb, (640, 480), interpolation=cv2.INTER_AREA)
                        photo = ImageTk.PhotoImage(image=Image.fromarray(img_resized))
                        self.video_label.config(image=photo)
                        self.video_label.image = photo
            except Exception as e:
                log_event(f"Error in video loop: {e}", "ERROR")

        self.root.after(30, self.update_video)

    def update_dashboard(self):
        try:
            state_configs = {
                0: (self.colors['success'], 'NORMAL', '✓'),
                1: (self.colors['warning'], 'WARNING', '⚠'),
                2: (self.colors['danger'], 'ALARM', '⚠'),
                3: (self.colors['danger'], 'MISSING', '✕')
            }

            color, text, icon = state_configs.get(cv_status["state"],
                                                  (self.colors['text_secondary'], 'UNKNOWN', '?'))

            self.cv_stat_value.config(text=icon, fg=color)
            self.cv_stat_subtitle.config(text=text)

            self.cv_status_badge.config(text=f"● {text}", bg=color)

            temp = arduino_status['temperature']
            if not arduino_status["connected"]:
                temp_text = "--"
                temp_color = self.colors['text_secondary']
                temp_sub = "Disconnected"
            elif temp == -127.0 or temp < -100:
                temp_text = "ERR"
                temp_color = self.colors['danger']
                temp_sub = "Sensor Error"
            else:
                temp_text = f"{temp:.1f}°"
                temp_color = self.colors['danger'] if arduino_status['high_temp'] else self.colors['text']
                temp_sub = "Normal" if not arduino_status['high_temp'] else "High Temperature!"

            self.temp_stat_value.config(text=temp_text, fg=temp_color)
            self.temp_stat_subtitle.config(text=temp_sub)

            if not arduino_status["connected"]:
                 self.temp_display.config(text="--°C", fg=self.colors['text_secondary'])
                 self.ir_display.config(text="--", fg=self.colors['text_secondary'])
                 self.ldr_display.config(text="--", fg=self.colors['text_secondary'])
            else:
                if temp == -127.0:
                    self.temp_display.config(text="ERR", fg=self.colors['danger'])
                else:
                    self.temp_display.config(
                        text=f"{temp:.1f}°C",
                        fg=self.colors['danger'] if arduino_status['high_temp'] else self.colors['success']
                    )

                ir_val = arduino_status['ir_value']
                self.ir_display.config(
                    text=f"{ir_val}",
                    fg=self.colors['danger'] if arduino_status['leak_detected'] else self.colors['success']
                )

                ldr_val = arduino_status['ldr_value']
                self.ldr_display.config(
                    text=f"{ldr_val}",
                    fg=self.colors['danger'] if arduino_status['bubble_detected'] else self.colors['success']
                )

            issues = sum([
                arduino_status.get('leak_detected', False),
                arduino_status.get('bubble_detected', False),
                arduino_status.get('high_temp', False) or temp == -127.0,
                cv_status['state'] in [1, 2, 3]
            ])

            if issues == 0:
                health_text = "100%"
                health_color = self.colors['success']
                health_sub = "All Systems Normal"
            elif issues == 1:
                health_text = "75%"
                health_color = self.colors['warning']
                health_sub = "1 Warning Active"
            elif issues == 2:
                health_text = "50%"
                health_color = self.colors['warning']
                health_sub = "2 Warnings Active"
            else:
                health_text = "25%"
                health_color = self.colors['danger']
                health_sub = f"{issues} Active Issues"

            self.health_stat_value.config(text=health_text, fg=health_color)
            self.health_stat_subtitle.config(text=health_sub)

            if arduino_status["connected"]:
                if (time.time() - arduino_status["last_heartbeat"]) > 5.0:
                    self.connection_indicator.config(fg=self.colors['warning'])
                    self.connection_label.config(text="No Heartbeat", fg=self.colors['warning'])
                    arduino_status["connected"] = False
                    log_event("Arduino heartbeat lost!", "ERROR")
                else:
                    self.connection_indicator.config(fg=self.colors['success'])
                    self.connection_label.config(text="Connected", fg=self.colors['success'])
            else:
                self.connection_indicator.config(fg=self.colors['danger'])
                self.connection_label.config(text="Disconnected", fg=self.colors['danger'])

            warnings_data = [
                (arduino_status.get('leak_detected', False),
                 f"IR: {arduino_status.get('ir_value', 0)}" if arduino_status['connected'] else "No Data"),
                (arduino_status.get('bubble_detected', False),
                 f"LDR: {arduino_status.get('ldr_value', 0)}" if arduino_status['connected'] else "No Data"),
                (arduino_status.get('high_temp', False) or temp == -127.0,
                 f"{arduino_status.get('temperature', 0):.1f}°C" if arduino_status['connected'] and temp != -127.0 else "ERROR"),
                (cv_status['state'] in [1, 2, 3],
                 cv_status['state_text'])
            ]

            for i, (is_active, value) in enumerate(warnings_data):
                if i < len(self.warning_widgets):
                    widget = self.warning_widgets[i]
                    if is_active:
                        widget['indicator'].config(fg=self.colors['danger'])
                        widget['value'].config(text=value, fg=self.colors['danger'])
                        widget['frame'].config(
                            highlightbackground=self.colors['danger'],
                            highlightcolor=self.colors['danger'],
                            highlightthickness=2
                        )
                    else:
                        widget['indicator'].config(fg=self.colors['success'])
                        display_val = "OK"
                        if i < 2:
                            display_val = f"{value.split(': ')[-1]}" if 'Data' not in value else 'OK'
                        elif i == 2:
                             display_val = f"{value.split('°')[0]}°C" if 'ERROR' not in value else 'OK'

                        widget['value'].config(text=display_val if cv_status['state'] == 0 or i < 3 else "OK",
                                               fg=self.colors['success'])
                        widget['frame'].config(
                            highlightbackground=self.colors['border'],
                            highlightcolor=self.colors['border'],
                            highlightthickness=1
                        )

            self.log_text.config(state=tk.NORMAL)
            self.log_text.delete(1.0, tk.END)
            for event in reversed(list(event_log)):
                if "ALARM" in event:
                    self.log_text.insert(tk.END, event + "\n", 'alarm')
                elif "WARN" in event:
                    self.log_text.insert(tk.END, event + "\n", 'warn')
                elif "ERROR" in event:
                    self.log_text.insert(tk.END, event + "\n", 'error')
                elif "SUCCESS" in event:
                    self.log_text.insert(tk.END, event + "\n", 'success')
                else:
                    self.log_text.insert(tk.END, event + "\n")

            self.log_text.tag_config('alarm', foreground=self.colors['danger'],
                                     font=('Consolas', 9, 'bold'))
            self.log_text.tag_config('warn', foreground=self.colors['warning'])
            self.log_text.tag_config('error', foreground=self.colors['danger'])
            self.log_text.tag_config('success', foreground=self.colors['success'])
            self.log_text.config(state=tk.DISABLED)
            self.log_text.see(tk.END)

        except Exception as e:
            log_event(f"Error updating dashboard: {e}", "ERROR")

        self.root.after(100, self.update_dashboard)

    def update_graphs(self):
        self.draw_graph()
        self.root.after(500, self.update_graphs)

    def resize_widgets(self, event=None):
        if hasattr(self, 'bg_canvas'):
            if hasattr(self, 'resize_timer'):
                self.root.after_cancel(self.resize_timer)
            self.resize_timer = self.root.after(100, self._perform_resize_draw)

    def _perform_resize_draw(self):
        if hasattr(self, 'bg_canvas'):
            self.draw_cube_pattern()
        if hasattr(self, 'graph_canvas'):
            self.draw_graph()


# ========== MAIN ==========
if __name__ == "__main__":
    log_event("Akatsuki Medical System Initializing", "INFO")
    log_event("暁 Dawn Protocol Active", "INFO")

    if SERIAL_ENABLED:
        if open_serial():
            reader_thread = threading.Thread(target=serial_reader_thread, daemon=True)
            reader_thread.start()
            log_event("Hardware interface established", "SUCCESS")
        else:
            log_event("Running in CV-only mode", "WARN")

    root = tk.Tk()
    app = ModernDashboardGUI(root)

    try:
        root.mainloop()
    finally:
        if serial_conn:
            serial_conn.close()
        cap.release()
        log_event("System shutdown complete", "INFO")