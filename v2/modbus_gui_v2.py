from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from collections import deque

from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pymodbus.client import ModbusTcpClient


# -----------------------------------------------------------------------------
# Modbus map - matches modbus_server.py
# -----------------------------------------------------------------------------
DEVICE_ID = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5020

INPUT_REGISTERS = {
    0: ("Room Temperature", "°C", 0.1),
    1: ("Humidity", "%RH", 0.1),
    2: ("Evaporator Temperature", "°C", 0.1),
    3: ("Condenser Temperature", "°C", 0.1),
    4: ("Suction Pressure", "bar", 0.1),
    5: ("Discharge Pressure", "bar", 0.1),
    6: ("Airflow", "m³/h", 1.0),
    7: ("Power", "W", 1.0),
    8: ("Energy", "kWh", 0.01),
    9: ("Filter Level", "%", 1.0),
    10: ("Runtime", "h", 1.0),
    11: ("Alarm Code", "", 1.0),
}

HOLDING_REGISTERS = {
    0: ("Set Temperature", "°C", 0.1, 16.0, 30.0, "float"),
    1: ("Fan Speed", "%", 1.0, 0.0, 100.0, "int"),
    2: ("Operating Mode", "", 1.0, 0, 3, "int"),
}

COILS = {
    0: ("HVAC Enable",),
    1: ("Compressor",),
    2: ("Fan",),
}

MODE_NAMES = {0: "OFF", 1: "COOL", 2: "FAN", 3: "AUTO"}


@dataclass
class ConnectionSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    device_id: int = DEVICE_ID
    polling_ms: int = 1000


class MiniChart(QWidget):
    def __init__(self, title: str, unit: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.values = deque(maxlen=90)
        self.setMinimumHeight(180)

    def add_value(self, value: float) -> None:
        self.values.append(float(value))
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#111827"))

        chart = self.rect().adjusted(14, 34, -12, -18)
        p.setPen(QPen(QColor("#263247"), 1))
        for i in range(1, 5):
            y = chart.top() + (chart.height() * i / 5)
            p.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))

        p.setPen(QColor("#e5e7eb"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.drawText(14, 20, f"{self.title}  {self.unit}")

        if len(self.values) < 2:
            p.setPen(QColor("#64748b"))
            p.drawText(chart, Qt.AlignmentFlag.AlignCenter, "Waiting for data…")
            return

        data = list(self.values)
        lo, hi = min(data), max(data)
        if abs(hi - lo) < 1e-9:
            hi = lo + 1.0

        points = []
        for i, value in enumerate(data):
            x = chart.left() + i * chart.width() / max(1, len(data) - 1)
            y = chart.bottom() - (value - lo) / (hi - lo) * chart.height()
            points.append(QPointF(x, y))

        p.setPen(QPen(QColor("#60a5fa"), 2.2))
        for a, b in zip(points, points[1:]):
            p.drawLine(a, b)

        p.setPen(QColor("#94a3b8"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(chart.left(), chart.bottom() + 14, f"{lo:.1f}")
        p.drawText(chart.right() - 48, chart.bottom() + 14, f"{hi:.1f}")


class MetricCard(QFrame):
    def __init__(self, title: str, value: str, unit: str):
        super().__init__()
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self.title = QLabel(title.upper())
        self.title.setObjectName("CardTitle")
        self.value = QLabel(value)
        self.value.setObjectName("CardValue")
        self.unit = QLabel(unit)
        self.unit.setObjectName("CardUnit")
        self.state = QLabel("OFFLINE")
        self.state.setObjectName("CardState")

        value_row = QHBoxLayout()
        value_row.addWidget(self.value)
        value_row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch()

        layout.addWidget(self.title)
        layout.addLayout(value_row)
        layout.addWidget(self.state)

    def set_value(self, value: str, state: str = "NORMAL", kind: str = "normal"):
        self.value.setText(value)
        self.state.setText(state)
        self.state.setProperty("kind", kind)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ClimaLab • HVAC Control Center")
        self.resize(1500, 920)
        self.setMinimumSize(1200, 760)

        self.client: ModbusTcpClient | None = None
        self.settings = ConnectionSettings()
        self.connected = False
        self.busy = False

        self.cards: dict[str, MetricCard] = {}
        self.temp_chart = MiniChart("Room Temperature", "°C")
        self.pressure_chart = MiniChart("Discharge Pressure", "bar")
        self.power_chart = MiniChart("Power", "W")

        self.active_alarm_code = 0
        self.alarm_history = deque(maxlen=100)
        self.last_alarm_signature = None

        self.build_ui()
        self.apply_theme()
        self.set_connection_state(False)
        self.log("INFO", "Application ready.")

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_data)

    # ------------------------------------------------------------------ UI
    def build_ui(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self.build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 18, 18, 18)
        content_layout.setSpacing(14)
        content_layout.addWidget(self.build_header())
        content_layout.addWidget(self.build_cards())

        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_sensors_tab(), "SENSORS & TRENDS")
        self.tabs.addTab(self.build_read_write_tab(), "READ / WRITE")
        self.tabs.addTab(self.build_register_tab(), "REGISTER MAP")
        self.tabs.addTab(self.build_alarm_tab(), "ALARMS & DIAGNOSTICS")
        self.tabs.addTab(self.build_log_tab(), "COMMUNICATION LOG")
        content_layout.addWidget(self.tabs, 1)

        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

    def build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(285)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(10)

        brand = QLabel("CLIMALAB")
        brand.setObjectName("Brand")
        sub = QLabel("HVAC / MODBUS TCP")
        sub.setObjectName("BrandSub")
        layout.addWidget(brand)
        layout.addWidget(sub)
        layout.addSpacing(14)

        layout.addWidget(self.section_title("CONNECTION"))
        layout.addWidget(self.field_label("IP ADDRESS"))
        self.host_edit = QLineEdit(DEFAULT_HOST)
        layout.addWidget(self.host_edit)

        layout.addWidget(self.field_label("PORT"))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_PORT)
        layout.addWidget(self.port_spin)

        layout.addWidget(self.field_label("DEVICE ID"))
        self.device_spin = QSpinBox()
        self.device_spin.setRange(1, 247)
        self.device_spin.setValue(DEVICE_ID)
        layout.addWidget(self.device_spin)

        layout.addWidget(self.field_label("POLLING INTERVAL"))
        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(250, 10000)
        self.poll_spin.setSingleStep(250)
        self.poll_spin.setValue(1000)
        self.poll_spin.setSuffix(" ms")
        layout.addWidget(self.poll_spin)

        self.connect_btn = QPushButton("CONNECT")
        self.connect_btn.setObjectName("PrimaryButton")
        self.connect_btn.clicked.connect(self.toggle_connection)
        layout.addWidget(self.connect_btn)

        self.connection_label = QLabel("● DISCONNECTED")
        self.connection_label.setObjectName("ConnectionLabel")
        layout.addWidget(self.connection_label)

        layout.addSpacing(18)
        layout.addWidget(self.section_title("QUICK CONTROL"))

        self.enable_check = QCheckBox("HVAC ENABLE")
        self.enable_check.setChecked(True)
        self.enable_check.stateChanged.connect(self.on_enable_changed)
        layout.addWidget(self.enable_check)

        layout.addWidget(self.field_label("OPERATING MODE"))
        self.mode_combo = QComboBox()
        for code, name in MODE_NAMES.items():
            self.mode_combo.addItem(name, code)
        self.mode_combo.setCurrentIndex(1)
        self.mode_combo.currentIndexChanged.connect(self.on_mode_changed)
        layout.addWidget(self.mode_combo)

        layout.addStretch()
        footer = QLabel("Virtual HVAC Simulator\nModbus TCP • Device 1")
        footer.setObjectName("Footer")
        layout.addWidget(footer)
        return sidebar

    def build_header(self):
        frame = QFrame()
        frame.setObjectName("Header")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)

        left = QVBoxLayout()
        title = QLabel("HVAC CONTROL CENTER")
        title.setObjectName("HeaderTitle")
        subtitle = QLabel("Live monitoring • Modbus TCP • Read / Write")
        subtitle.setObjectName("HeaderSubtitle")
        left.addWidget(title)
        left.addWidget(subtitle)
        layout.addLayout(left)
        layout.addStretch()

        self.online_label = QLabel("● OFFLINE")
        self.online_label.setObjectName("TopStatus")
        layout.addWidget(self.online_label)
        return frame

    def build_cards(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        specs = [
            ("room", "ROOM TEMPERATURE", "--", "°C"),
            ("setpoint", "TARGET TEMPERATURE", "--", "°C"),
            ("humidity", "HUMIDITY", "--", "%RH"),
            ("fan", "FAN SPEED", "--", "%"),
            ("compressor", "COMPRESSOR", "OFF", ""),
            ("suction", "SUCTION PRESSURE", "--", "bar"),
            ("discharge", "DISCHARGE PRESSURE", "--", "bar"),
            ("power", "POWER", "--", "W"),
        ]
        for i, spec in enumerate(specs):
            key, title, value, unit = spec
            card = MetricCard(title, value, unit)
            self.cards[key] = card
            grid.addWidget(card, i // 4, i % 4)
        return frame

    def build_sensors_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)

        head = QHBoxLayout()
        head.addWidget(QLabel("Live sensor telemetry"))
        head.addStretch()
        self.last_update = QLabel("Last update: --")
        self.last_update.setObjectName("MutedText")
        head.addWidget(self.last_update)
        layout.addLayout(head)

        self.sensor_table = QTableWidget(0, 5)
        self.sensor_table.setHorizontalHeaderLabels(["ADDRESS", "PARAMETER", "VALUE", "UNIT", "STATUS"])
        self.sensor_table.verticalHeader().setVisible(False)
        self.sensor_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sensor_table.horizontalHeader().setStretchLastSection(True)
        self.sensor_table.setAlternatingRowColors(True)
        layout.addWidget(self.sensor_table, 1)

        chart_row = QHBoxLayout()
        chart_row.addWidget(self.temp_chart)
        chart_row.addWidget(self.pressure_chart)
        chart_row.addWidget(self.power_chart)
        layout.addLayout(chart_row)
        return page

    def build_read_write_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(12)

        # Read panel
        read_panel = QFrame()
        read_panel.setObjectName("Panel")
        rl = QVBoxLayout(read_panel)
        rl.setContentsMargins(18, 18, 18, 18)
        title = QLabel("READ REGISTER")
        title.setObjectName("PanelTitle")
        rl.addWidget(title)

        rl.addWidget(self.field_label("REGISTER AREA"))
        self.read_area = QComboBox()
        self.read_area.addItem("Input Register • FC04", "input")
        self.read_area.addItem("Holding Register • FC03", "holding")
        rl.addWidget(self.read_area)

        rl.addWidget(self.field_label("START ADDRESS"))
        self.read_address = QSpinBox()
        self.read_address.setRange(0, 65534)
        rl.addWidget(self.read_address)

        rl.addWidget(self.field_label("QUANTITY"))
        self.read_quantity = QSpinBox()
        self.read_quantity.setRange(1, 125)
        self.read_quantity.setValue(1)
        rl.addWidget(self.read_quantity)

        self.read_btn = QPushButton("READ VALUES")
        self.read_btn.clicked.connect(self.manual_read)
        rl.addWidget(self.read_btn)

        self.read_table = QTableWidget(0, 4)
        self.read_table.setHorizontalHeaderLabels(["ADDRESS", "RAW", "VALUE", "UNIT"])
        self.read_table.verticalHeader().setVisible(False)
        self.read_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.read_table.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.read_table, 1)

        # Write panel
        write_panel = QFrame()
        write_panel.setObjectName("Panel")
        wl = QVBoxLayout(write_panel)
        wl.setContentsMargins(18, 18, 18, 18)
        title = QLabel("WRITE VALUE")
        title.setObjectName("PanelTitle")
        wl.addWidget(title)

        wl.addWidget(self.field_label("HOLDING REGISTER"))
        self.write_register = QComboBox()
        for addr, (name, unit, *_rest) in HOLDING_REGISTERS.items():
            self.write_register.addItem(f"{addr:04d} — {name} ({unit or 'raw'})", addr)
        self.write_register.currentIndexChanged.connect(self.update_write_form)
        wl.addWidget(self.write_register)

        wl.addWidget(self.field_label("VALUE"))
        self.write_value = QDoubleSpinBox()
        self.write_value.setDecimals(1)
        wl.addWidget(self.write_value)

        self.write_unit = QLabel("Unit: —")
        self.write_range = QLabel("Allowed range: —")
        self.write_unit.setObjectName("MutedText")
        self.write_range.setObjectName("MutedText")
        wl.addWidget(self.write_unit)
        wl.addWidget(self.write_range)

        self.write_btn = QPushButton("WRITE VALUE")
        self.write_btn.setObjectName("PrimaryButton")
        self.write_btn.clicked.connect(self.manual_write)
        wl.addWidget(self.write_btn)

        self.write_feedback = QLabel("Ready.")
        self.write_feedback.setObjectName("Feedback")
        self.write_feedback.setWordWrap(True)
        wl.addWidget(self.write_feedback)
        wl.addStretch()

        layout.addWidget(read_panel, 1)
        layout.addWidget(write_panel, 1)
        self.update_write_form()
        return page

    def build_register_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(["AREA", "ADDRESS", "NAME", "UNIT", "SCALE", "RANGE / ROLE"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)

        rows = []
        for addr, (name, unit, scale) in INPUT_REGISTERS.items():
            rows.append(("INPUT", addr, name, unit, scale, "Read only"))
        for addr, (name, unit, scale, low, high, dtype) in HOLDING_REGISTERS.items():
            rows.append(("HOLDING", addr, name, unit, scale, f"{low} … {high} ({dtype})"))
        for addr, (name,) in COILS.items():
            rows.append(("COIL", addr, name, "", "1 bit", "Digital"))

        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(value)))
        layout.addWidget(table)
        return page

    def build_alarm_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(12)

        top = QHBoxLayout()

        self.alarm_banner = QFrame()
        self.alarm_banner.setObjectName("AlarmBanner")
        banner_layout = QHBoxLayout(self.alarm_banner)
        banner_layout.setContentsMargins(16, 12, 16, 12)
        self.alarm_title = QLabel("SYSTEM HEALTHY")
        self.alarm_title.setObjectName("AlarmTitle")
        self.alarm_detail = QLabel("No active alarms")
        self.alarm_detail.setObjectName("MutedText")
        banner_layout.addWidget(self.alarm_title)
        banner_layout.addSpacing(12)
        banner_layout.addWidget(self.alarm_detail)
        banner_layout.addStretch()
        top.addWidget(self.alarm_banner, 1)

        self.alarm_count = QLabel("0 ACTIVE")
        self.alarm_count.setObjectName("AlarmCount")
        top.addWidget(self.alarm_count)
        layout.addLayout(top)

        summary = QFrame()
        summary.setObjectName("Panel")
        grid = QGridLayout(summary)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(24)

        items = [
            ("DISCHARGE HIGH LIMIT", "30.0 bar"),
            ("EVAPORATOR FROST LIMIT", "0.0 °C"),
            ("FILTER SERVICE LIMIT", "15 %"),
            ("DEVICE ID", str(self.settings.device_id)),
        ]
        self.diagnostic_values = []
        for i, (name, value) in enumerate(items):
            box = QVBoxLayout()
            lbl = QLabel(name)
            lbl.setObjectName("FieldLabel")
            val = QLabel(value)
            val.setObjectName("DiagValue")
            box.addWidget(lbl)
            box.addWidget(val)
            grid.addLayout(box, 0, i)
        layout.addWidget(summary)

        self.alarm_table = QTableWidget(0, 4)
        self.alarm_table.setHorizontalHeaderLabels(["TIME", "CODE", "SEVERITY", "DESCRIPTION"])
        self.alarm_table.verticalHeader().setVisible(False)
        self.alarm_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.alarm_table.horizontalHeader().setStretchLastSection(True)
        self.alarm_table.setAlternatingRowColors(True)
        layout.addWidget(self.alarm_table, 1)

        clear = QPushButton("CLEAR ALARM HISTORY")
        clear.clicked.connect(lambda: self.alarm_table.setRowCount(0))
        layout.addWidget(clear, 0, Qt.AlignmentFlag.AlignRight)
        return page

    def build_log_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)
        self.log_table = QTableWidget(0, 3)
        self.log_table.setHorizontalHeaderLabels(["TIME", "LEVEL", "MESSAGE"])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.log_table)

        btn = QPushButton("CLEAR LOG")
        btn.clicked.connect(lambda: self.log_table.setRowCount(0))
        layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
        return page

    def section_title(self, text):
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def field_label(self, text):
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    # ------------------------------------------------------------------ Theme
    def apply_theme(self):
        self.setStyleSheet("""
            * { font-family: "Segoe UI"; color: #e5e7eb; }
            QMainWindow, QWidget { background: #0b1220; }
            #Sidebar { background: #0f172a; border-right: 1px solid #1e293b; }
            #Brand { color: #f8fafc; font-size: 24px; font-weight: 800; letter-spacing: 2px; }
            #BrandSub { color: #64748b; font-size: 10px; font-weight: 800; letter-spacing: 1.3px; }
            #SectionTitle { color: #64748b; font-size: 10px; font-weight: 800; letter-spacing: 1.2px; }
            #FieldLabel { color: #94a3b8; font-size: 10px; font-weight: 700; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background: #111827; border: 1px solid #273449; border-radius: 8px; padding: 8px; }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #3b82f6; }
            QPushButton { background: #172033; border: 1px solid #2b3a52; border-radius: 9px; padding: 9px 13px; font-weight: 700; }
            QPushButton:hover { background: #1d2940; border-color: #3b82f6; }
            #PrimaryButton { background: #2563eb; border-color: #3b82f6; }
            #PrimaryButton:hover { background: #1d4ed8; }
            #ConnectionLabel { color: #f87171; font-weight: 800; padding: 7px 0; }
            #Footer { color: #475569; font-size: 10px; }
            #Header { background: #0f172a; border: 1px solid #1e293b; border-radius: 12px; }
            #HeaderTitle { font-size: 18px; font-weight: 800; }
            #HeaderSubtitle { color: #64748b; font-size: 11px; }
            #TopStatus { background: #2a151a; color: #f87171; border-radius: 8px; padding: 8px 12px; font-weight: 800; }
            #MetricCard { background: #111827; border: 1px solid #1f2b3d; border-radius: 12px; }
            #CardTitle { color: #64748b; font-size: 10px; font-weight: 800; letter-spacing: 1px; }
            #CardValue { color: #f8fafc; font-size: 27px; font-weight: 800; }
            #CardUnit { color: #94a3b8; font-size: 11px; padding-bottom: 4px; }
            #CardState { color: #34d399; font-size: 10px; font-weight: 800; }
            #CardState[kind="warning"] { color: #fbbf24; }
            #CardState[kind="danger"] { color: #fb7185; }
            #CardState[kind="offline"] { color: #64748b; }
            #MainTabs::pane { border: 1px solid #1e293b; border-radius: 10px; background: #0f172a; }
            QTabBar::tab { padding: 10px 18px; color: #64748b; }
            QTabBar::tab:selected { color: #e2e8f0; border-bottom: 2px solid #3b82f6; }
            #Panel { background: #111827; border: 1px solid #1f2b3d; border-radius: 12px; }
            #PanelTitle { font-size: 15px; font-weight: 800; }
            #MutedText { color: #64748b; font-size: 10px; }
            #Feedback { color: #93c5fd; background: #0b1220; border: 1px solid #1e3a5f; border-radius: 8px; padding: 10px; }
            #AlarmBanner { background: #0f2e26; border: 1px solid #1f6b50; border-radius: 10px; }
            #AlarmTitle { color: #34d399; font-size: 15px; font-weight: 800; }
            #AlarmCount { background: #12372c; color: #6ee7b7; border-radius: 8px; padding: 10px 12px; font-weight: 800; }
            #DiagValue { color: #e2e8f0; font-size: 14px; font-weight: 800; }
            QTableWidget { background: #0b1220; alternate-background-color: #0f172a; border: 1px solid #1e293b; border-radius: 10px; gridline-color: #1e293b; }
            QHeaderView::section { background: #111827; color: #94a3b8; border: none; padding: 9px; font-size: 10px; font-weight: 800; }
            QCheckBox { spacing: 8px; }
        """)

    # ------------------------------------------------------------- Modbus I/O
    def toggle_connection(self):
        if self.connected:
            self.disconnect_modbus()
        else:
            self.connect_modbus()

    def connect_modbus(self):
        host = self.host_edit.text().strip()
        port = self.port_spin.value()
        device_id = self.device_spin.value()
        self.settings = ConnectionSettings(host, port, device_id, self.poll_spin.value())
        self.log("INFO", f"Connecting to {host}:{port}, Device ID {device_id}...")

        try:
            client = ModbusTcpClient(host=host, port=port, timeout=2)
            if not client.connect():
                self.log("ERROR", "Connection failed.")
                client.close()
                return
        except Exception as exc:
            self.log("ERROR", f"Connection exception: {exc}")
            return

        self.client = client
        self.connected = True
        self.poll_timer.start(self.settings.polling_ms)
        self.set_connection_state(True)
        self.log("SUCCESS", "Connected successfully.")
        self.poll_data()

    def disconnect_modbus(self):
        self.poll_timer.stop()
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.connected = False
        self.set_connection_state(False)
        self.log("INFO", "Disconnected.")

    def set_connection_state(self, connected: bool):
        if connected:
            self.connect_btn.setText("DISCONNECT")
            self.connection_label.setText("● CONNECTED")
            self.connection_label.setStyleSheet("color:#34d399; font-weight:800;")
            self.online_label.setText("● ONLINE")
            self.online_label.setStyleSheet("background:#0f2e26; color:#34d399; border-radius:8px; padding:8px 12px; font-weight:800;")
        else:
            self.connect_btn.setText("CONNECT")
            self.connection_label.setText("● DISCONNECTED")
            self.connection_label.setStyleSheet("color:#f87171; font-weight:800;")
            self.online_label.setText("● OFFLINE")
            self.online_label.setStyleSheet("background:#2a151a; color:#f87171; border-radius:8px; padding:8px 12px; font-weight:800;")

    def ensure_connected(self) -> bool:
        if self.connected and self.client is not None:
            return True
        QMessageBox.information(self, "Not connected", "Connect to the Modbus server first.")
        return False

    def poll_data(self):
        if not self.connected or self.client is None or self.busy:
            return
        self.busy = True
        try:
            result = self.client.read_input_registers(address=0, count=12, device_id=self.settings.device_id)
            if result.isError():
                raise RuntimeError(str(result))
            raw = list(result.registers)
            snapshot = {
                "room": raw[0] / 10.0,
                "humidity": raw[1] / 10.0,
                "evap": raw[2] / 10.0,
                "condenser": raw[3] / 10.0,
                "suction": raw[4] / 10.0,
                "discharge": raw[5] / 10.0,
                "airflow": raw[6],
                "power": raw[7],
                "energy": raw[8] / 100.0,
                "filter": raw[9],
                "runtime": raw[10],
                "alarm": raw[11],
            }
            self.apply_snapshot(snapshot)
            self.last_update.setText("Last update: " + datetime.now().strftime("%H:%M:%S"))
        except Exception as exc:
            self.log("ERROR", f"Polling failed: {exc}")
            self.disconnect_modbus()
        finally:
            self.busy = False

    def apply_snapshot(self, s):
        control = self.read_control_snapshot()
        setpoint = control["setpoint"]
        fan = control["fan"]
        mode = control["mode"]
        enabled = control["enabled"]
        compressor = control["compressor"]
        fan_on = control["fan_on"]

        room_kind = "normal"
        room_state = "AT TARGET" if abs(s["room"] - setpoint) <= 0.4 else ("COOLING" if s["room"] > setpoint else "HEATING")
        self.cards["room"].set_value(f"{s['room']:.1f}", room_state, room_kind)
        self.cards["setpoint"].set_value(f"{setpoint:.1f}", MODE_NAMES.get(mode, "UNKNOWN"), "normal")
        self.cards["humidity"].set_value(f"{s['humidity']:.1f}", "NORMAL")
        self.cards["fan"].set_value(f"{fan:.0f}", "RUNNING" if fan_on else "STOPPED")
        self.cards["compressor"].set_value("ON" if compressor else "OFF", "RUNNING" if compressor else "IDLE")
        self.cards["suction"].set_value(f"{s['suction']:.1f}", "NORMAL")
        if s["discharge"] > 30:
            self.cards["discharge"].set_value(f"{s['discharge']:.1f}", "HIGH", "danger")
        else:
            self.cards["discharge"].set_value(f"{s['discharge']:.1f}", "NORMAL")
        self.cards["power"].set_value(f"{s['power']:.0f}", "LIVE")

        self.enable_check.blockSignals(True)
        self.enable_check.setChecked(enabled)
        self.enable_check.blockSignals(False)

        if 0 <= mode < self.mode_combo.count():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(mode)
            self.mode_combo.blockSignals(False)

        self.temp_chart.add_value(s["room"])
        self.pressure_chart.add_value(s["discharge"])
        self.power_chart.add_value(s["power"])
        self.update_sensor_table(s)
        self.update_alarm_state(s["alarm"], s)

    def read_control_snapshot(self):
        result = {"setpoint": 22.0, "fan": 0, "mode": 0, "enabled": False, "compressor": False, "fan_on": False}
        if not self.client:
            return result
        try:
            hr = self.client.read_holding_registers(address=0, count=3, device_id=self.settings.device_id)
            if not hr.isError() and len(hr.registers) >= 3:
                result["setpoint"] = hr.registers[0] / 10.0
                result["fan"] = int(hr.registers[1])
                result["mode"] = int(hr.registers[2])
            coils = self.client.read_coils(address=0, count=3, device_id=self.settings.device_id)
            if not coils.isError() and len(coils.bits) >= 3:
                result["enabled"] = bool(coils.bits[0])
                result["compressor"] = bool(coils.bits[1])
                result["fan_on"] = bool(coils.bits[2])
        except Exception as exc:
            self.log("ERROR", f"Control status read failed: {exc}")
        return result

    def update_alarm_state(self, code: int, snapshot):
        descriptions = {
            0: ("SYSTEM HEALTHY", "No active alarms", "normal"),
            101: ("HIGH DISCHARGE PRESSURE", f"Current: {snapshot['discharge']:.1f} bar • Limit: 30.0 bar", "danger"),
            102: ("EVAPORATOR FROST RISK", f"Current: {snapshot['evap']:.1f} °C • Limit: 0.0 °C", "danger"),
            103: ("FILTER SERVICE REQUIRED", f"Filter level: {snapshot['filter']:.0f}% • Service limit: 15%", "warning"),
        }
        title, detail, kind = descriptions.get(code, ("UNKNOWN ALARM", f"Alarm code: {code}", "danger"))

        self.active_alarm_code = code
        if code:
            self.alarm_title.setText(title)
            self.alarm_detail.setText(detail)
            self.alarm_count.setText("1 ACTIVE")
            self.alarm_banner.setStyleSheet("background:#3a141b; border:1px solid #7f1d31; border-radius:10px;")
            self.alarm_title.setStyleSheet("color:#fb7185; font-size:15px; font-weight:800;")
            self.alarm_count.setStyleSheet("background:#4a1721; color:#fda4af; border-radius:8px; padding:10px 12px; font-weight:800;")
        else:
            self.alarm_title.setText("SYSTEM HEALTHY")
            self.alarm_detail.setText("No active alarms")
            self.alarm_count.setText("0 ACTIVE")
            self.alarm_banner.setStyleSheet("background:#0f2e26; border:1px solid #1f6b50; border-radius:10px;")
            self.alarm_title.setStyleSheet("color:#34d399; font-size:15px; font-weight:800;")
            self.alarm_count.setStyleSheet("background:#12372c; color:#6ee7b7; border-radius:8px; padding:10px 12px; font-weight:800;")

        signature = (code, detail)
        if code and signature != self.last_alarm_signature:
            self.last_alarm_signature = signature
            row = self.alarm_table.rowCount()
            self.alarm_table.insertRow(row)
            for col, text in enumerate((datetime.now().strftime("%H:%M:%S"), str(code), "CRITICAL" if code in (101,102) else "WARNING", title + " — " + detail)):
                self.alarm_table.setItem(row, col, QTableWidgetItem(text))
            self.alarm_table.scrollToBottom()
            self.log("ALARM", f"Code {code}: {title} — {detail}")
        elif code == 0:
            self.last_alarm_signature = None

    def read_holding(self, address: int) -> int:
        if not self.client:
            return 0
        result = self.client.read_holding_registers(address=address, count=1, device_id=self.settings.device_id)
        if result.isError():
            return 0
        return int(result.registers[0])

    def read_coil(self, address: int) -> bool:
        if not self.client:
            return False
        result = self.client.read_coils(address=address, count=1, device_id=self.settings.device_id)
        if result.isError():
            return False
        return bool(result.bits[0])

    def update_sensor_table(self, s):
        rows = [
            (0, "Room Temperature", s["room"], "°C", "NORMAL"),
            (1, "Humidity", s["humidity"], "%RH", "NORMAL"),
            (2, "Evaporator Temperature", s["evap"], "°C", "NORMAL"),
            (3, "Condenser Temperature", s["condenser"], "°C", "NORMAL"),
            (4, "Suction Pressure", s["suction"], "bar", "NORMAL"),
            (5, "Discharge Pressure", s["discharge"], "bar", "HIGH" if s["discharge"] > 30 else "NORMAL"),
            (6, "Airflow", s["airflow"], "m³/h", "NORMAL"),
            (7, "Power", s["power"], "W", "NORMAL"),
            (8, "Energy", s["energy"], "kWh", "NORMAL"),
            (9, "Filter Level", s["filter"], "%", "LOW" if s["filter"] < 15 else "NORMAL"),
            (10, "Runtime", s["runtime"], "h", "NORMAL"),
            (11, "Alarm Code", s["alarm"], "", "ALARM" if s["alarm"] else "OK"),
        ]
        self.sensor_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if isinstance(value, float):
                    text = f"{value:.2f}"
                else:
                    text = str(value)
                self.sensor_table.setItem(r, c, QTableWidgetItem(text))

    def manual_read(self):
        if not self.ensure_connected():
            return
        area = self.read_area.currentData()
        address = self.read_address.value()
        count = self.read_quantity.value()
        try:
            if area == "input":
                result = self.client.read_input_registers(address=address, count=count, device_id=self.settings.device_id)
                mapping = INPUT_REGISTERS
            else:
                result = self.client.read_holding_registers(address=address, count=count, device_id=self.settings.device_id)
                mapping = HOLDING_REGISTERS
            if result.isError():
                raise RuntimeError(str(result))

            self.read_table.setRowCount(count)
            for i, raw in enumerate(result.registers):
                addr = address + i
                meta = mapping.get(addr)
                if area == "input" and meta:
                    value = raw * meta[2]
                    unit = meta[1]
                    value_text = f"{value:.2f}"
                elif area == "holding" and meta:
                    value = raw * meta[2]
                    unit = meta[1]
                    value_text = f"{value:.2f}"
                else:
                    value_text = str(raw)
                    unit = ""
                self.read_table.setItem(i, 0, QTableWidgetItem(str(addr)))
                self.read_table.setItem(i, 1, QTableWidgetItem(str(raw)))
                self.read_table.setItem(i, 2, QTableWidgetItem(value_text))
                self.read_table.setItem(i, 3, QTableWidgetItem(unit))
            self.log("READ", f"{('FC04' if area == 'input' else 'FC03')} address={address} count={count}")
        except Exception as exc:
            self.log("ERROR", f"Manual read failed: {exc}")
            QMessageBox.warning(self, "Read Error", str(exc))

    def update_write_form(self):
        addr = int(self.write_register.currentData())
        name, unit, scale, low, high, dtype = HOLDING_REGISTERS[addr]
        self.write_unit.setText(f"Unit: {unit or 'raw'}")
        self.write_range.setText(f"Allowed range: {low} … {high} • Type: {dtype}")
        if dtype == "int":
            self.write_value.setDecimals(0)
            self.write_value.setSingleStep(1)
        else:
            self.write_value.setDecimals(1)
            self.write_value.setSingleStep(0.1)
        self.write_value.setRange(float(low), float(high))

    def manual_write(self):
        if not self.ensure_connected():
            return
        addr = int(self.write_register.currentData())
        name, unit, scale, low, high, dtype = HOLDING_REGISTERS[addr]
        value = float(self.write_value.value())

        if not (low <= value <= high):
            QMessageBox.warning(self, "Invalid Value", f"{name} must be between {low} and {high} {unit}.")
            return

        raw = int(round(value / scale))
        if dtype == "int" and abs(value - round(value)) > 1e-9:
            QMessageBox.warning(self, "Invalid Value", f"{name} accepts whole numbers only.")
            return
        try:
            result = self.client.write_register(address=addr, value=raw, device_id=self.settings.device_id)
            if result.isError():
                raise RuntimeError(str(result))
            self.write_feedback.setText(f"✓ {name} = {value:g} {unit}".strip())
            self.log("WRITE", f"FC06 address={addr} raw={raw} value={value:g} {unit}".strip())
            QTimer.singleShot(150, self.poll_data)
        except Exception as exc:
            self.log("ERROR", f"Write failed: {exc}")
            QMessageBox.warning(self, "Write Error", str(exc))

    def on_enable_changed(self, state: int):
        if self.connected:
            self.write_coil(0, state == Qt.CheckState.Checked.value)

    def on_mode_changed(self, _index: int):
        if self.connected:
            self.write_register_raw(2, int(self.mode_combo.currentData()))

    def write_coil(self, address: int, value: bool):
        try:
            result = self.client.write_coil(address=address, value=value, device_id=self.settings.device_id)
            if result.isError():
                raise RuntimeError(str(result))
            self.log("WRITE", f"FC05 coil={address} value={value}")
        except Exception as exc:
            self.log("ERROR", f"Coil write failed: {exc}")

    def write_register_raw(self, address: int, value: int):
        try:
            result = self.client.write_register(address=address, value=value, device_id=self.settings.device_id)
            if result.isError():
                raise RuntimeError(str(result))
            self.log("WRITE", f"FC06 address={address} raw={value}")
        except Exception as exc:
            self.log("ERROR", f"Register write failed: {exc}")

    # ------------------------------------------------------------------ Log
    def log(self, level: str, message: str):
        if not hasattr(self, "log_table"):
            return
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        self.log_table.setItem(row, 0, QTableWidgetItem(datetime.now().strftime("%H:%M:%S")))
        self.log_table.setItem(row, 1, QTableWidgetItem(level))
        self.log_table.setItem(row, 2, QTableWidgetItem(message))
        self.log_table.scrollToBottom()

    def closeEvent(self, event):
        self.disconnect_modbus()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
