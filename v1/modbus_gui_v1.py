from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
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
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QSizePolicy,
)

from pymodbus.client import ModbusTcpClient


# ============================================================================
# CONFIGURATION / MODBUS MAP
# ============================================================================
DEVICE_ID = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5020
DEFAULT_POLL_MS = 1000

# Input registers / FC04
INPUT_REGISTERS = {
    0: ("Room Temperature", "°C", 0.1, "temperature", "-10 … 60"),
    1: ("Humidity", "%RH", 0.1, "humidity", "0 … 100"),
    2: ("Evaporator Temperature", "°C", 0.1, "temperature", "-20 … 50"),
    3: ("Condenser Temperature", "°C", 0.1, "temperature", "-20 … 100"),
    4: ("Suction Pressure", "bar", 0.1, "pressure", "0 … 20"),
    5: ("Discharge Pressure", "bar", 0.1, "pressure", "0 … 40"),
    6: ("Airflow", "m³/h", 1.0, "airflow", "0 … 3000"),
    7: ("Power", "W", 1.0, "power", "0 … 5000"),
    8: ("Energy", "kWh", 0.01, "energy", "0 … 655.35"),
    9: ("Filter Level", "%", 1.0, "filter", "0 … 100"),
    10: ("Runtime", "h", 1.0, "runtime", "0 … 65535"),
    11: ("Alarm Code", "", 1.0, "alarm", "0 … 999"),
}

# Holding registers / FC03
HOLDING_REGISTERS = {
    0: ("Set Temperature", "°C", 0.1, 16.0, 30.0, "float"),
    1: ("Fan Speed", "%", 1.0, 0.0, 100.0, "int"),
    2: ("Operating Mode", "", 1.0, 0, 3, "int"),
}

# Coils / FC01
COILS = {
    0: ("HVAC Enable",),
    1: ("Compressor",),
    2: ("Fan",),
}

MODE_NAMES = {0: "OFF", 1: "COOL", 2: "FAN", 3: "AUTO"}

ALARM_DEFINITIONS = {
    0: ("SYSTEM HEALTHY", "No active alarm."),
    101: ("HIGH DISCHARGE PRESSURE", "Discharge pressure is above the safe operating threshold."),
    102: ("EVAPORATOR FROST RISK", "Evaporator temperature is below the frost-risk limit."),
    103: ("FILTER SERVICE REQUIRED", "Filter condition is critically low."),
}


@dataclass
class ConnectionSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    device_id: int = DEVICE_ID
    poll_ms: int = DEFAULT_POLL_MS
    timeout: float = 2.0


class HistoryChart(QWidget):
    """A dependency-free live line chart painted with QPainter."""

    def __init__(self, title: str, unit: str, max_points: int = 120, parent=None):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.values = deque(maxlen=max_points)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def add_value(self, value: float):
        self.values.append(float(value))
        self.update()

    def clear(self):
        self.values.clear()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0f172a"))

        plot = self.rect().adjusted(48, 34, -18, -34)
        painter.setPen(QPen(QColor("#1e293b"), 1))
        for i in range(6):
            y = plot.top() + i * plot.height() / 5
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for i in range(7):
            x = plot.left() + i * plot.width() / 6
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        painter.setPen(QColor("#e2e8f0"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.drawText(14, 22, f"{self.title}  {self.unit}")

        if len(self.values) < 2:
            painter.setPen(QColor("#64748b"))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "Waiting for live data…")
            return

        data = list(self.values)
        lo, hi = min(data), max(data)
        if hi - lo < 1e-9:
            pad = max(1.0, abs(lo) * 0.05)
            lo -= pad
            hi += pad
        else:
            pad = (hi - lo) * 0.12
            lo -= pad
            hi += pad

        points = []
        for i, value in enumerate(data):
            x = plot.left() + i * plot.width() / max(1, len(data) - 1)
            y = plot.bottom() - (value - lo) / (hi - lo) * plot.height()
            points.append(QPointF(x, y))

        painter.setPen(QPen(QColor("#60a5fa"), 2.4))
        for a, b in zip(points, points[1:]):
            painter.drawLine(a, b)

        painter.setBrush(QColor("#60a5fa"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(points[-1], 3.5, 3.5)

        painter.setPen(QColor("#94a3b8"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(8, plot.top() + 4, f"{hi:.1f}")
        painter.drawText(8, plot.bottom(), f"{lo:.1f}")
        painter.drawText(plot.left(), plot.bottom() + 22, "Oldest")
        painter.drawText(plot.right() - 38, plot.bottom() + 22, "Now")


class MetricCard(QFrame):
    def __init__(self, title: str, unit: str = ""):
        super().__init__()
        self.setObjectName("MetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(2)

        self.title = QLabel(title.upper())
        self.title.setObjectName("CardTitle")
        self.value = QLabel("--")
        self.value.setObjectName("CardValue")
        self.unit = QLabel(unit)
        self.unit.setObjectName("CardUnit")
        self.state = QLabel("OFFLINE")
        self.state.setObjectName("CardState")

        row = QHBoxLayout()
        row.setSpacing(5)
        row.addWidget(self.value)
        row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()

        layout.addWidget(self.title)
        layout.addLayout(row)
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
        self.setWindowTitle("ClimaLab • Advanced HVAC Control Center")
        self.resize(1580, 980)
        self.setMinimumSize(1250, 820)

        self.client: ModbusTcpClient | None = None
        self.settings = ConnectionSettings()
        self.connected = False
        self.polling = False
        self.last_snapshot: dict | None = None
        self.last_update = None
        self.response_ms = 0.0
        self.active_alarm_code = 0
        self.alarm_acknowledged = False
        self.last_alarm_signature = None
        self.alarm_history = deque(maxlen=200)
        self.command_history = deque(maxlen=100)

        self.cards: dict[str, MetricCard] = {}
        self.charts: dict[str, HistoryChart] = {
            "room": HistoryChart("Room Temperature", "°C"),
            "pressure": HistoryChart("Discharge Pressure", "bar"),
            "power": HistoryChart("Power", "W"),
        }

        self.build_ui()
        self.apply_theme()
        self.set_connection_state(False)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_data)

        self.log("INFO", "ClimaLab HMI initialized.")
        self.log("INFO", "Ready for Modbus TCP connection.")

    # ====================================================================
    # UI BUILD
    # ====================================================================

    def build_ui(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self.build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 18, 18, 18)
        content_layout.setSpacing(12)
        content_layout.addWidget(self.build_header())
        content_layout.addWidget(self.build_dashboard())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.build_overview_tab(), "OVERVIEW")
        self.tabs.addTab(self.build_control_tab(), "ADVANCED CONTROL")
        self.tabs.addTab(self.build_read_write_tab(), "READ / WRITE")
        self.tabs.addTab(self.build_alarm_tab(), "ALARMS & DIAGNOSTICS")
        self.tabs.addTab(self.build_register_tab(), "REGISTER MAP")
        self.tabs.addTab(self.build_log_tab(), "COMMUNICATION LOG")
        content_layout.addWidget(self.tabs, 1)

        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

    @staticmethod
    def label(text: str, object_name: str | None = None):
        label = QLabel(text)
        if object_name:
            label.setObjectName(object_name)
        return label

    def section_title(self, text: str):
        return self.label(text, "SectionTitle")

    def field_label(self, text: str):
        return self.label(text, "FieldLabel")

    def build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(300)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(9)

        layout.addWidget(self.label("CLIMALAB", "Brand"))
        layout.addWidget(self.label("HVAC / MODBUS TCP HMI", "BrandSub"))
        layout.addSpacing(10)

        layout.addWidget(self.section_title("CONNECTION"))

        layout.addWidget(self.field_label("IP ADDRESS"))
        self.host_edit = QLineEdit(DEFAULT_HOST)
        layout.addWidget(self.host_edit)

        layout.addWidget(self.field_label("TCP PORT"))
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
        self.poll_spin.setValue(DEFAULT_POLL_MS)
        self.poll_spin.setSuffix(" ms")
        layout.addWidget(self.poll_spin)

        layout.addWidget(self.field_label("TIMEOUT"))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.5, 15.0)
        self.timeout_spin.setSingleStep(0.5)
        self.timeout_spin.setValue(2.0)
        self.timeout_spin.setSuffix(" s")
        layout.addWidget(self.timeout_spin)

        self.connect_btn = QPushButton("CONNECT")
        self.connect_btn.setObjectName("PrimaryButton")
        self.connect_btn.clicked.connect(self.toggle_connection)
        layout.addWidget(self.connect_btn)

        self.connection_label = QLabel("● DISCONNECTED")
        self.connection_label.setObjectName("ConnectionLabel")
        layout.addWidget(self.connection_label)

        self.link_info = QLabel("No active session")
        self.link_info.setObjectName("MutedText")
        self.link_info.setWordWrap(True)
        layout.addWidget(self.link_info)

        layout.addSpacing(12)
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

        self.side_runtime = QLabel("Runtime: -- h")
        self.side_runtime.setObjectName("SideMetric")
        layout.addWidget(self.side_runtime)

        self.side_alarm = QLabel("Alarm: 0 / HEALTHY")
        self.side_alarm.setObjectName("SideMetric")
        layout.addWidget(self.side_alarm)

        layout.addStretch()
        layout.addWidget(self.label("ClimaLab Virtual HVAC Simulator\nModbus TCP • Device 1", "Footer"))
        return sidebar

    def build_header(self):
        frame = QFrame()
        frame.setObjectName("Header")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 13, 18, 13)

        left = QVBoxLayout()
        left.addWidget(self.label("HVAC CONTROL CENTER", "HeaderTitle"))
        left.addWidget(self.label("Live monitoring • operator control • diagnostics", "HeaderSubtitle"))
        layout.addLayout(left)
        layout.addStretch()

        self.header_target = QLabel("TARGET -- °C")
        self.header_target.setObjectName("HeaderBadge")
        layout.addWidget(self.header_target)

        self.header_alarm = QLabel("● 0 ALARMS")
        self.header_alarm.setObjectName("HeaderBadgeHealthy")
        layout.addWidget(self.header_alarm)

        self.top_status = QLabel("● OFFLINE")
        self.top_status.setObjectName("TopStatus")
        layout.addWidget(self.top_status)
        return frame

    def build_dashboard(self):
        frame = QWidget()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        card_data = [
            ("room", "ROOM TEMPERATURE", "°C"),
            ("target", "TARGET TEMPERATURE", "°C"),
            ("humidity", "HUMIDITY", "%RH"),
            ("fan", "FAN SPEED", "%"),
            ("compressor", "COMPRESSOR", ""),
            ("suction", "SUCTION PRESSURE", "bar"),
            ("discharge", "DISCHARGE PRESSURE", "bar"),
            ("power", "POWER", "W"),
        ]
        for i, (key, title, unit) in enumerate(card_data):
            card = MetricCard(title, unit)
            self.cards[key] = card
            grid.addWidget(card, i // 4, i % 4)
        return frame

    def build_overview_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(10)

        trend_header = QHBoxLayout()
        trend_header.addWidget(self.label("LIVE TRENDS", "PanelTitle"))
        trend_header.addStretch()
        self.refresh_label = self.label("Last update: --", "MutedText")
        trend_header.addWidget(self.refresh_label)
        self.response_label = self.label("Response: -- ms", "MutedText")
        trend_header.addWidget(self.response_label)
        layout.addLayout(trend_header)

        chart_row = QHBoxLayout()
        chart_row.addWidget(self.charts["room"])
        chart_row.addWidget(self.charts["pressure"])
        chart_row.addWidget(self.charts["power"])
        layout.addLayout(chart_row, 2)

        status_row = QHBoxLayout()
        status_row.addWidget(self.build_status_panel(), 1)
        status_row.addWidget(self.build_condition_panel(), 1)
        layout.addLayout(status_row, 1)
        return page

    def build_status_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(8)
        layout.addWidget(self.label("SYSTEM STATUS", "PanelTitle"), 0, 0, 1, 4)

        self.status_labels = {}
        items = [
            ("hvac", "HVAC"),
            ("mode", "MODE"),
            ("fan_state", "FAN"),
            ("compressor_state", "COMPRESSOR"),
            ("evap", "EVAPORATOR"),
            ("condenser", "CONDENSER"),
            ("airflow", "AIRFLOW"),
            ("filter", "FILTER"),
        ]
        for i, (key, text) in enumerate(items):
            r = 1 + i // 2
            c = (i % 2) * 2
            layout.addWidget(self.field_label(text), r, c)
            value = QLabel("--")
            value.setObjectName("StatusValue")
            self.status_labels[key] = value
            layout.addWidget(value, r, c + 1)
        return panel

    def build_condition_panel(self):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(8)
        layout.addWidget(self.label("TEMPERATURE CONTROL", "PanelTitle"))

        self.temp_state_label = QLabel("--")
        self.temp_state_label.setObjectName("ConditionTitle")
        layout.addWidget(self.temp_state_label)

        self.temp_progress = QProgressBar()
        self.temp_progress.setRange(0, 100)
        self.temp_progress.setValue(0)
        self.temp_progress.setTextVisible(False)
        layout.addWidget(self.temp_progress)

        row = QHBoxLayout()
        self.temp_delta = QLabel("Δ -- °C")
        self.temp_delta.setObjectName("MutedText")
        row.addWidget(self.temp_delta)
        row.addStretch()
        self.temp_detail = QLabel("Current -- / Target --")
        self.temp_detail.setObjectName("MutedText")
        row.addWidget(self.temp_detail)
        layout.addLayout(row)

        self.condition_hint = QLabel("Waiting for data")
        self.condition_hint.setWordWrap(True)
        self.condition_hint.setObjectName("HintBox")
        layout.addWidget(self.condition_hint)
        return panel

    def build_control_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(12)

        control = QFrame()
        control.setObjectName("Panel")
        c = QVBoxLayout(control)
        c.setContentsMargins(18, 18, 18, 18)
        c.setSpacing(8)
        c.addWidget(self.label("OPERATOR COMMANDS", "PanelTitle"))
        c.addWidget(self.label("These controls write directly to Modbus holding registers / coils.", "MutedText"))

        c.addSpacing(8)
        c.addWidget(self.field_label("SET TEMPERATURE"))
        self.control_temp = QDoubleSpinBox()
        self.control_temp.setRange(16.0, 30.0)
        self.control_temp.setDecimals(1)
        self.control_temp.setSingleStep(0.5)
        self.control_temp.setValue(22.0)
        self.control_temp.setSuffix(" °C")
        c.addWidget(self.control_temp)

        c.addWidget(self.field_label("FAN SPEED"))
        self.control_fan = QSpinBox()
        self.control_fan.setRange(0, 100)
        self.control_fan.setValue(70)
        self.control_fan.setSuffix(" %")
        c.addWidget(self.control_fan)

        c.addWidget(self.field_label("OPERATING MODE"))
        self.control_mode = QComboBox()
        for code, name in MODE_NAMES.items():
            self.control_mode.addItem(name, code)
        self.control_mode.setCurrentIndex(1)
        c.addWidget(self.control_mode)

        self.control_enable = QCheckBox("HVAC ENABLE")
        self.control_enable.setChecked(True)
        c.addWidget(self.control_enable)

        c.addSpacing(8)
        self.apply_controls_btn = QPushButton("APPLY COMMANDS")
        self.apply_controls_btn.setObjectName("PrimaryButton")
        self.apply_controls_btn.clicked.connect(self.apply_control_commands)
        c.addWidget(self.apply_controls_btn)

        self.reset_defaults_btn = QPushButton("LOAD SAFE DEFAULTS")
        self.reset_defaults_btn.clicked.connect(self.load_safe_defaults)
        c.addWidget(self.reset_defaults_btn)

        c.addStretch()
        self.control_feedback = QLabel("Ready.")
        self.control_feedback.setObjectName("HintBox")
        self.control_feedback.setWordWrap(True)
        c.addWidget(self.control_feedback)

        monitor = QFrame()
        monitor.setObjectName("Panel")
        m = QVBoxLayout(monitor)
        m.setContentsMargins(18, 18, 18, 18)
        m.addWidget(self.label("CONTROL FEEDBACK", "PanelTitle"))

        self.control_rows = QTableWidget(0, 4)
        self.control_rows.setHorizontalHeaderLabels(["PARAMETER", "REQUEST", "ACTUAL", "STATUS"])
        self.prepare_table(self.control_rows)
        self.control_rows.horizontalHeader().setStretchLastSection(True)
        m.addWidget(self.control_rows)

        layout.addWidget(control, 1)
        layout.addWidget(monitor, 2)
        return page

    def build_read_write_tab(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(12)

        read = QFrame()
        read.setObjectName("Panel")
        r = QVBoxLayout(read)
        r.setContentsMargins(18, 18, 18, 18)
        r.addWidget(self.label("MANUAL READ", "PanelTitle"))
        r.addWidget(self.label("Read raw Modbus registers directly from the device.", "MutedText"))

        r.addWidget(self.field_label("AREA"))
        self.read_area = QComboBox()
        self.read_area.addItem("Input Register (FC04)", "input")
        self.read_area.addItem("Holding Register (FC03)", "holding")
        r.addWidget(self.read_area)
        r.addWidget(self.field_label("START ADDRESS"))
        self.read_address = QSpinBox(); self.read_address.setRange(0, 65534)
        r.addWidget(self.read_address)
        r.addWidget(self.field_label("QUANTITY"))
        self.read_quantity = QSpinBox(); self.read_quantity.setRange(1, 50); self.read_quantity.setValue(1)
        r.addWidget(self.read_quantity)
        self.read_btn = QPushButton("READ VALUES")
        self.read_btn.clicked.connect(self.manual_read)
        r.addWidget(self.read_btn)
        self.read_table = QTableWidget(0, 4)
        self.read_table.setHorizontalHeaderLabels(["ADDRESS", "RAW", "VALUE", "UNIT"])
        self.prepare_table(self.read_table)
        self.read_table.horizontalHeader().setStretchLastSection(True)
        r.addWidget(self.read_table, 1)

        write = QFrame(); write.setObjectName("Panel")
        w = QVBoxLayout(write); w.setContentsMargins(18, 18, 18, 18)
        w.addWidget(self.label("MANUAL WRITE", "PanelTitle"))
        w.addWidget(self.label("Validated operator writes to writable holding registers.", "MutedText"))
        w.addWidget(self.field_label("REGISTER"))
        self.write_register = QComboBox()
        for addr, meta in HOLDING_REGISTERS.items():
            self.write_register.addItem(f"{addr:04d} — {meta[0]}", addr)
        self.write_register.currentIndexChanged.connect(self.update_write_form)
        w.addWidget(self.write_register)
        w.addWidget(self.field_label("VALUE"))
        self.write_value = QDoubleSpinBox(); self.write_value.setRange(-99999, 99999)
        w.addWidget(self.write_value)
        self.write_unit = QLabel("Unit: —"); self.write_unit.setObjectName("MutedText")
        self.write_range = QLabel("Allowed range: —"); self.write_range.setObjectName("MutedText")
        self.write_range.setWordWrap(True)
        w.addWidget(self.write_unit); w.addWidget(self.write_range)
        self.write_btn = QPushButton("WRITE VALUE"); self.write_btn.setObjectName("PrimaryButton")
        self.write_btn.clicked.connect(self.manual_write)
        w.addWidget(self.write_btn)
        self.write_feedback = QLabel("Ready."); self.write_feedback.setObjectName("HintBox"); self.write_feedback.setWordWrap(True)
        w.addWidget(self.write_feedback)
        w.addStretch()
        layout.addWidget(read, 1)
        layout.addWidget(write, 1)
        self.update_write_form()
        return page

    def build_alarm_tab(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(8, 10, 8, 8); layout.setSpacing(10)
        banner = QFrame(); banner.setObjectName("AlarmBanner")
        b = QHBoxLayout(banner); b.setContentsMargins(16, 12, 16, 12)
        left = QVBoxLayout()
        self.alarm_title = QLabel("SYSTEM HEALTHY"); self.alarm_title.setObjectName("AlarmTitle")
        self.alarm_detail = QLabel("No active alarms"); self.alarm_detail.setObjectName("AlarmDetail")
        left.addWidget(self.alarm_title); left.addWidget(self.alarm_detail)
        b.addLayout(left); b.addStretch()
        self.alarm_count = QLabel("0 ACTIVE"); self.alarm_count.setObjectName("AlarmCount")
        b.addWidget(self.alarm_count)
        self.ack_btn = QPushButton("ACKNOWLEDGE ACTIVE ALARM")
        self.ack_btn.setEnabled(False); self.ack_btn.clicked.connect(self.acknowledge_alarm)
        b.addWidget(self.ack_btn)
        layout.addWidget(banner)

        stats = QHBoxLayout()
        self.diag_conn = self.build_diag_box("CONNECTION", "OFFLINE")
        self.diag_poll = self.build_diag_box("POLLING", "--")
        self.diag_latency = self.build_diag_box("LATENCY", "-- ms")
        self.diag_runtime = self.build_diag_box("RUNTIME", "-- h")
        for box in (self.diag_conn, self.diag_poll, self.diag_latency, self.diag_runtime):
            stats.addWidget(box)
        layout.addLayout(stats)

        layout.addWidget(self.label("ALARM HISTORY", "PanelTitle"))
        self.alarm_table = QTableWidget(0, 5)
        self.alarm_table.setHorizontalHeaderLabels(["TIME", "CODE", "SEVERITY", "EVENT", "STATE"])
        self.prepare_table(self.alarm_table)
        self.alarm_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.alarm_table, 1)

        buttons = QHBoxLayout()
        clear = QPushButton("CLEAR ALARM HISTORY"); clear.clicked.connect(lambda: self.alarm_table.setRowCount(0))
        buttons.addStretch(); buttons.addWidget(clear)
        layout.addLayout(buttons)
        return page

    def build_diag_box(self, title, initial):
        box = QFrame(); box.setObjectName("DiagBox")
        l = QVBoxLayout(box); l.setContentsMargins(13, 10, 13, 10)
        l.addWidget(self.label(title, "DiagTitle"))
        val = QLabel(initial); val.setObjectName("DiagValue")
        l.addWidget(val); setattr(self, f"diag_{title.lower()}", val)
        return box

    def build_register_tab(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(8, 10, 8, 8)
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(["AREA", "ADDRESS", "NAME", "UNIT", "SCALE", "RANGE", "ACCESS"])
        self.prepare_table(table)
        rows = []
        for addr, (name, unit, scale, _kind, rng) in INPUT_REGISTERS.items():
            rows.append(("INPUT / FC04", addr, name, unit, scale, rng, "READ ONLY"))
        for addr, (name, unit, scale, low, high, dtype) in HOLDING_REGISTERS.items():
            rows.append(("HOLDING / FC03", addr, name, unit, scale, f"{low} … {high}", f"READ / WRITE ({dtype})"))
        for addr, (name,) in COILS.items():
            rows.append(("COIL / FC01", addr, name, "bit", "1", "0 / 1", "READ / WRITE"))
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                table.setItem(r, c, QTableWidgetItem(str(value)))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        return page

    def build_log_tab(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(8, 10, 8, 8)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.label("COMMUNICATION & OPERATOR LOG", "PanelTitle"))
        toolbar.addStretch()
        clear = QPushButton("CLEAR LOG"); clear.clicked.connect(lambda: self.log_table.setRowCount(0))
        toolbar.addWidget(clear)
        layout.addLayout(toolbar)
        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(["TIME", "LEVEL", "TYPE", "MESSAGE"])
        self.prepare_table(self.log_table)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.log_table)
        return page

    def prepare_table(self, table: QTableWidget):
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(False)

    # ====================================================================
    # THEME
    # ====================================================================

    def apply_theme(self):
        self.setStyleSheet("""
            * { font-family: "Segoe UI"; color: #e5e7eb; }
            QMainWindow, QWidget { background: #0a1020; }
            #Sidebar { background: #0f172a; border-right: 1px solid #1e293b; }
            #Brand { font-size: 25px; font-weight: 900; color: #f8fafc; letter-spacing: 2px; }
            #BrandSub { color: #64748b; font-size: 10px; font-weight: 800; letter-spacing: 1.5px; }
            #SectionTitle { color: #64748b; font-size: 10px; font-weight: 900; letter-spacing: 1.4px; margin-top: 4px; }
            #FieldLabel { color: #94a3b8; font-size: 10px; font-weight: 800; }
            #Footer, #MutedText { color: #64748b; font-size: 10px; }
            #SideMetric { color: #94a3b8; background: #111827; border: 1px solid #1e293b; border-radius: 7px; padding: 6px 8px; font-size: 10px; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #111827; border: 1px solid #26354d; border-radius: 8px; padding: 8px 9px; }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #3b82f6; }
            QPushButton { background: #151f32; border: 1px solid #2b3a52; border-radius: 8px; padding: 9px 12px; font-weight: 800; }
            QPushButton:hover { background: #1b2941; border-color: #3b82f6; }
            QPushButton:disabled { color: #475569; background: #0f172a; }
            #PrimaryButton { background: #2563eb; border-color: #3b82f6; }
            #PrimaryButton:hover { background: #1d4ed8; }
            #ConnectionLabel { color: #f87171; font-weight: 900; padding: 4px 0; }
            #Header, #Panel, #MetricCard, #DiagBox, #AlarmBanner { background: #0f172a; border: 1px solid #1f2b3d; border-radius: 11px; }
            #HeaderTitle { font-size: 18px; font-weight: 900; }
            #HeaderSubtitle { color: #64748b; font-size: 10px; }
            #HeaderBadge, #HeaderBadgeHealthy, #TopStatus { background: #111827; border: 1px solid #26354d; border-radius: 8px; padding: 7px 10px; font-size: 10px; font-weight: 900; }
            #HeaderBadgeHealthy { color: #34d399; background: #10281f; border-color: #245941; }
            #TopStatus { color: #f87171; background: #2a151a; border-color: #5b2630; }
            #MetricCard { min-height: 105px; }
            #CardTitle { color: #64748b; font-size: 9px; font-weight: 900; letter-spacing: 1.1px; }
            #CardValue { color: #f8fafc; font-size: 26px; font-weight: 900; }
            #CardUnit { color: #94a3b8; font-size: 10px; }
            #CardState { color: #34d399; font-size: 9px; font-weight: 900; }
            #CardState[kind="warning"] { color: #fbbf24; }
            #CardState[kind="danger"] { color: #fb7185; }
            #CardState[kind="offline"] { color: #64748b; }
            #PanelTitle { color: #f1f5f9; font-size: 13px; font-weight: 900; }
            #StatusValue { color: #e2e8f0; font-size: 12px; font-weight: 800; }
            #ConditionTitle { color: #60a5fa; font-size: 18px; font-weight: 900; }
            #HintBox { background: #0b1220; border: 1px solid #22324b; border-radius: 8px; padding: 9px; color: #93c5fd; }
            #AlarmTitle { color: #34d399; font-size: 15px; font-weight: 900; }
            #AlarmDetail { color: #94a3b8; font-size: 10px; }
            #AlarmCount { background: #12372c; color: #6ee7b7; border-radius: 8px; padding: 9px 10px; font-weight: 900; }
            #DiagBox { min-width: 145px; }
            #DiagTitle { color: #64748b; font-size: 9px; font-weight: 900; }
            #DiagValue { color: #e2e8f0; font-size: 15px; font-weight: 900; }
            #MainTabs::pane { border: 1px solid #1e293b; background: #0f172a; border-radius: 10px; }
            QTabBar::tab { padding: 10px 17px; color: #64748b; font-size: 10px; font-weight: 800; }
            QTabBar::tab:selected { color: #e2e8f0; border-bottom: 2px solid #3b82f6; }
            QTableWidget { background: #0b1220; alternate-background-color: #0f172a; border: 1px solid #1e293b; border-radius: 9px; gridline-color: #1e293b; }
            QHeaderView::section { background: #111827; color: #94a3b8; border: none; padding: 8px; font-size: 9px; font-weight: 900; }
            QCheckBox { spacing: 8px; color: #cbd5e1; }
            QProgressBar { background: #0b1220; border: 1px solid #26354d; border-radius: 7px; height: 12px; }
            QProgressBar::chunk { background: #3b82f6; border-radius: 6px; }
        """)

    # ====================================================================
    # CONNECTION / POLLING
    # ====================================================================

    def toggle_connection(self):
        if self.connected:
            self.disconnect_modbus()
        else:
            self.connect_modbus()

    def connect_modbus(self):
        host = self.host_edit.text().strip()
        self.settings = ConnectionSettings(
            host=host,
            port=self.port_spin.value(),
            device_id=self.device_spin.value(),
            poll_ms=self.poll_spin.value(),
            timeout=self.timeout_spin.value(),
        )
        self.log("INFO", "CONNECT", f"Connecting to {host}:{self.settings.port}, Unit {self.settings.device_id}…")

        try:
            client = ModbusTcpClient(host=host, port=self.settings.port, timeout=self.settings.timeout)
            if not client.connect():
                raise ConnectionError("TCP connection failed.")
        except Exception as exc:
            self.set_connection_state(False)
            self.log("ERROR", "CONNECT", str(exc))
            QMessageBox.critical(self, "Connection Error", str(exc))
            return

        self.client = client
        self.connected = True
        self.poll_timer.start(self.settings.poll_ms)
        self.set_connection_state(True)
        self.log("SUCCESS", "CONNECT", "Connected successfully.")
        self.poll_data()

    def disconnect_modbus(self):
        self.poll_timer.stop()
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.client = None
        self.connected = False
        self.set_connection_state(False)
        self.log("INFO", "CONNECT", "Disconnected.")

    def set_connection_state(self, connected: bool):
        if connected:
            self.connect_btn.setText("DISCONNECT")
            self.connection_label.setText("● CONNECTED")
            self.connection_label.setStyleSheet("color:#34d399; font-weight:900;")
            self.top_status.setText("● ONLINE")
            self.top_status.setStyleSheet("color:#34d399; background:#10281f; border:1px solid #245941; padding:7px 10px; border-radius:8px; font-weight:900;")
            self.link_info.setText(f"{self.settings.host}:{self.settings.port}\nDevice ID {self.settings.device_id}")
            if hasattr(self, "diag_conn"):
                self.diag_conn.setText("ONLINE")
        else:
            self.connect_btn.setText("CONNECT")
            self.connection_label.setText("● DISCONNECTED")
            self.connection_label.setStyleSheet("color:#f87171; font-weight:900;")
            self.top_status.setText("● OFFLINE")
            self.top_status.setStyleSheet("color:#f87171; background:#2a151a; border:1px solid #5b2630; padding:7px 10px; border-radius:8px; font-weight:900;")
            self.link_info.setText("No active session")
            if hasattr(self, "diag_conn"):
                self.diag_conn.setText("OFFLINE")

    def poll_data(self):
        if not self.connected or not self.client or self.polling:
            return
        self.polling = True
        t0 = perf_counter()
        try:
            ir = self.client.read_input_registers(address=0, count=12, device_id=self.settings.device_id)
            if ir.isError():
                raise RuntimeError(str(ir))
            raw = list(ir.registers)
            hr = self.client.read_holding_registers(address=0, count=3, device_id=self.settings.device_id)
            coils = self.client.read_coils(address=0, count=3, device_id=self.settings.device_id)
            if hr.isError() or coils.isError():
                raise RuntimeError(str(hr if hr.isError() else coils))

            self.response_ms = (perf_counter() - t0) * 1000.0
            s = self.decode_snapshot(raw, list(hr.registers), list(coils.bits))
            self.last_snapshot = s
            self.last_update = datetime.now()
            self.apply_snapshot(s)
            self.refresh_label.setText("Last update: " + self.last_update.strftime("%H:%M:%S"))
            self.response_label.setText(f"Response: {self.response_ms:.1f} ms")
            self.diag_latency.setText(f"{self.response_ms:.1f} ms")
            self.diag_poll.setText(f"{self.settings.poll_ms} ms")
        except Exception as exc:
            self.log("ERROR", "POLL", str(exc))
            self.disconnect_modbus()
        finally:
            self.polling = False

    @staticmethod
    def decode_snapshot(ir: list[int], hr: list[int], coils: list[bool]):
        return {
            "room": ir[0] / 10.0,
            "humidity": ir[1] / 10.0,
            "evap": ir[2] / 10.0,
            "condenser": ir[3] / 10.0,
            "suction": ir[4] / 10.0,
            "discharge": ir[5] / 10.0,
            "airflow": ir[6],
            "power": ir[7],
            "energy": ir[8] / 100.0,
            "filter": ir[9],
            "runtime": ir[10],
            "alarm": ir[11],
            "setpoint": hr[0] / 10.0,
            "fan": hr[1],
            "mode": hr[2],
            "enabled": bool(coils[0]),
            "compressor": bool(coils[1]),
            "fan_on": bool(coils[2]),
        }

    def apply_snapshot(self, s: dict):
        temp_state = "COOLING" if s["enabled"] and s["room"] > s["setpoint"] + 0.3 else "AT TARGET" if abs(s["room"] - s["setpoint"]) <= 0.3 else "IDLE"
        temp_kind = "normal" if abs(s["room"] - s["setpoint"]) <= 3.0 else "warning"
        self.cards["room"].set_value(f"{s['room']:.1f}", temp_state, temp_kind)
        self.cards["target"].set_value(f"{s['setpoint']:.1f}", "SETPOINT")
        self.cards["humidity"].set_value(f"{s['humidity']:.1f}", "NORMAL")
        self.cards["fan"].set_value(f"{s['fan']:.0f}", "RUNNING" if s["fan"] > 0 else "STOPPED")
        self.cards["compressor"].set_value("ON" if s["compressor"] else "OFF", "RUNNING" if s["compressor"] else "IDLE")
        self.cards["suction"].set_value(f"{s['suction']:.1f}", "NORMAL")
        discharge_kind = "danger" if s["discharge"] >= 30 else "normal"
        self.cards["discharge"].set_value(f"{s['discharge']:.1f}", "HIGH" if s["discharge"] >= 30 else "NORMAL", discharge_kind)
        self.cards["power"].set_value(f"{s['power']:.0f}", "LIVE")

        self.side_runtime.setText(f"Runtime: {s['runtime']:.0f} h")
        mode = MODE_NAMES.get(s["mode"], f"UNKNOWN ({s['mode']})")
        self.side_alarm.setText(f"Alarm: {s['alarm']} / {ALARM_DEFINITIONS.get(s['alarm'], ('UNKNOWN', ''))[0]}")
        self.header_target.setText(f"TARGET {s['setpoint']:.1f} °C")

        self.status_labels["hvac"].setText("ENABLED" if s["enabled"] else "DISABLED")
        self.status_labels["mode"].setText(mode)
        self.status_labels["fan_state"].setText("RUNNING" if s["fan_on"] else "STOPPED")
        self.status_labels["compressor_state"].setText("RUNNING" if s["compressor"] else "IDLE")
        self.status_labels["evap"].setText(f"{s['evap']:.1f} °C")
        self.status_labels["condenser"].setText(f"{s['condenser']:.1f} °C")
        self.status_labels["airflow"].setText(f"{s['airflow']:.0f} m³/h")
        self.status_labels["filter"].setText(f"{s['filter']:.0f} %")
        self.diag_runtime.setText(f"{s['runtime']:.0f} h")

        delta = s["room"] - s["setpoint"]
        closeness = 100.0 if abs(delta) < 0.1 else max(0.0, min(100.0, 100.0 - abs(delta) * 18.0))
        self.temp_progress.setValue(int(closeness))
        self.temp_delta.setText(f"Δ {delta:+.1f} °C")
        self.temp_detail.setText(f"Current {s['room']:.1f} / Target {s['setpoint']:.1f}")
        if temp_state == "COOLING":
            self.temp_state_label.setText("Cooling toward target")
            self.condition_hint.setText("Compressor demand is active. Room temperature should gradually converge toward the setpoint.")
        elif temp_state == "AT TARGET":
            self.temp_state_label.setText("Target reached")
            self.condition_hint.setText("Room temperature is within the target deadband. Control demand is reduced.")
        else:
            self.temp_state_label.setText("Standby / heating away")
            self.condition_hint.setText("HVAC is not actively cooling the room toward the target.")

        self.charts["room"].add_value(s["room"])
        self.charts["pressure"].add_value(s["discharge"])
        self.charts["power"].add_value(s["power"])

        self.update_control_feedback(s)
        self.update_alarm(s["alarm"], s)
        self.update_checkboxes_from_state(s)

    def update_checkboxes_from_state(self, s):
        self.enable_check.blockSignals(True); self.enable_check.setChecked(s["enabled"]); self.enable_check.blockSignals(False)
        self.control_enable.blockSignals(True); self.control_enable.setChecked(s["enabled"]); self.control_enable.blockSignals(False)
        if 0 <= s["mode"] < self.mode_combo.count():
            self.mode_combo.blockSignals(True); self.mode_combo.setCurrentIndex(s["mode"]); self.mode_combo.blockSignals(False)
        if 0 <= s["mode"] < self.control_mode.count():
            self.control_mode.setCurrentIndex(s["mode"])
        self.control_temp.setValue(max(16.0, min(30.0, s["setpoint"])))
        self.control_fan.setValue(max(0, min(100, int(s["fan"]))))

    def update_control_feedback(self, s):
        rows = [
            ("Set Temperature", f"{self.control_temp.value():.1f} °C", f"{s['setpoint']:.1f} °C", "MATCH" if abs(self.control_temp.value() - s['setpoint']) < 0.05 else "PENDING"),
            ("Fan Speed", f"{self.control_fan.value()} %", f"{s['fan']} %", "MATCH" if self.control_fan.value() == s['fan'] else "PENDING"),
            ("Operating Mode", self.control_mode.currentText(), MODE_NAMES.get(s['mode'], str(s['mode'])), "MATCH" if self.control_mode.currentData() == s['mode'] else "PENDING"),
            ("HVAC Enable", "ON" if self.control_enable.isChecked() else "OFF", "ON" if s['enabled'] else "OFF", "MATCH" if self.control_enable.isChecked() == s['enabled'] else "PENDING"),
        ]
        self.control_rows.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self.control_rows.setItem(r, c, QTableWidgetItem(str(value)))

    # ====================================================================
    # CONTROLS
    # ====================================================================

    def apply_control_commands(self):
        if not self.ensure_connected():
            return
        try:
            setpoint = self.control_temp.value()
            fan = self.control_fan.value()
            mode = int(self.control_mode.currentData())
            enable = self.control_enable.isChecked()

            self.safe_write_register(0, int(round(setpoint * 10)))
            self.safe_write_register(1, fan)
            self.safe_write_register(2, mode)
            self.safe_write_coil(0, enable)

            self.control_feedback.setText("✓ Commands sent successfully. Waiting for device confirmation…")
            self.log("WRITE", "CONTROL", f"SET={setpoint:.1f}°C FAN={fan}% MODE={MODE_NAMES[mode]} ENABLE={enable}")
            QTimer.singleShot(100, self.poll_data)
        except Exception as exc:
            self.control_feedback.setText(f"✕ Command failed: {exc}")
            self.log("ERROR", "CONTROL", str(exc))
            QMessageBox.warning(self, "Control Error", str(exc))

    def load_safe_defaults(self):
        self.control_temp.setValue(22.0)
        self.control_fan.setValue(70)
        self.control_mode.setCurrentIndex(1)
        self.control_enable.setChecked(True)

    def on_enable_changed(self, state: int):
        if self.connected:
            self.safe_write_coil(0, state == Qt.CheckState.Checked.value)

    def on_mode_changed(self, _index: int):
        if self.connected:
            self.safe_write_register(2, int(self.mode_combo.currentData()))

    def safe_write_register(self, address: int, value: int):
        if not self.client:
            raise ConnectionError("Not connected.")
        result = self.client.write_register(address=address, value=int(value), device_id=self.settings.device_id)
        if result.isError():
            raise RuntimeError(str(result))

    def safe_write_coil(self, address: int, value: bool):
        if not self.client:
            raise ConnectionError("Not connected.")
        result = self.client.write_coil(address=address, value=bool(value), device_id=self.settings.device_id)
        if result.isError():
            raise RuntimeError(str(result))

    # ====================================================================
    # MANUAL READ / WRITE
    # ====================================================================

    def manual_read(self):
        if not self.ensure_connected():
            return
        area = self.read_area.currentData()
        address = self.read_address.value()
        quantity = self.read_quantity.value()
        try:
            if area == "input":
                result = self.client.read_input_registers(address=address, count=quantity, device_id=self.settings.device_id)
                mapping = INPUT_REGISTERS
            else:
                result = self.client.read_holding_registers(address=address, count=quantity, device_id=self.settings.device_id)
                mapping = HOLDING_REGISTERS
            if result.isError():
                raise RuntimeError(str(result))
            self.read_table.setRowCount(quantity)
            for i, raw in enumerate(result.registers):
                addr = address + i
                meta = mapping.get(addr)
                if meta:
                    value = raw * meta[2]
                    unit = meta[1]
                    text = f"{value:.2f}" if not float(value).is_integer() else str(int(value))
                else:
                    text = str(raw); unit = ""
                for col, val in enumerate((addr, raw, text, unit)):
                    self.read_table.setItem(i, col, QTableWidgetItem(str(val)))
            self.log("READ", "MANUAL", f"{('FC04' if area == 'input' else 'FC03')} address={address} count={quantity}")
        except Exception as exc:
            self.log("ERROR", "READ", str(exc))
            QMessageBox.warning(self, "Read Error", str(exc))

    def update_write_form(self):
        addr = int(self.write_register.currentData())
        name, unit, scale, low, high, dtype = HOLDING_REGISTERS[addr]
        self.write_unit.setText(f"Unit: {unit or 'raw'}")
        self.write_range.setText(f"Allowed: {low} … {high} • Data type: {dtype}")
        if dtype == "int":
            self.write_value.setDecimals(0); self.write_value.setSingleStep(1)
        else:
            self.write_value.setDecimals(1); self.write_value.setSingleStep(0.1)
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
        if dtype == "int" and abs(value - round(value)) > 1e-9:
            QMessageBox.warning(self, "Invalid Value", f"{name} accepts whole numbers only.")
            return
        raw = int(round(value / scale))
        try:
            self.safe_write_register(addr, raw)
            self.write_feedback.setText(f"✓ {name} = {value:g} {unit}".strip())
            self.log("WRITE", "MANUAL", f"FC06 address={addr} raw={raw} value={value:g} {unit}".strip())
            QTimer.singleShot(120, self.poll_data)
        except Exception as exc:
            self.log("ERROR", "WRITE", str(exc))
            QMessageBox.warning(self, "Write Error", str(exc))

    # ====================================================================
    # ALARMS
    # ====================================================================

    def update_alarm(self, code: int, s: dict):
        code = int(code)
        title, detail = ALARM_DEFINITIONS.get(code, (f"UNKNOWN ALARM ({code})", "Device reported an unknown alarm code."))
        if code != self.active_alarm_code:
            self.alarm_acknowledged = False
        self.active_alarm_code = code

        if code:
            self.header_alarm.setText("● 1 ALARM")
            self.header_alarm.setStyleSheet("color:#fb7185; background:#32151d; border:1px solid #6b2638; padding:7px 10px; border-radius:8px; font-weight:900;")
            self.side_alarm.setText(f"Alarm: {code} / {title}")
            self.alarm_title.setText(title + (" — ACKNOWLEDGED" if self.alarm_acknowledged else ""))
            self.alarm_detail.setText(detail + f" Current: discharge={s['discharge']:.1f} bar, evap={s['evap']:.1f} °C, filter={s['filter']:.0f}%.")
            self.alarm_count.setText("1 ACK" if self.alarm_acknowledged else "1 ACTIVE")
            self.alarm_count.setStyleSheet("background:#4a1721; color:#fda4af; border-radius:8px; padding:9px 10px; font-weight:900;")
            self.ack_btn.setEnabled(not self.alarm_acknowledged)
            self.ack_btn.setText("ACKNOWLEDGE ACTIVE ALARM")
            self.diag_alarm.setText("ACTIVE") if hasattr(self, "diag_alarm") else None
            signature = (code, round(s['discharge'], 1), round(s['evap'], 1), round(s['filter']))
            if signature != self.last_alarm_signature:
                self.last_alarm_signature = signature
                severity = "CRITICAL" if code in (101, 102) else "WARNING"
                row = self.alarm_table.rowCount()
                self.alarm_table.insertRow(row)
                fields = (datetime.now().strftime("%H:%M:%S"), code, severity, title, "ACTIVE")
                for c, value in enumerate(fields):
                    self.alarm_table.setItem(row, c, QTableWidgetItem(str(value)))
                self.log("ALARM", "DEVICE", f"Code {code}: {title}")
        else:
            self.header_alarm.setText("● 0 ALARMS")
            self.header_alarm.setStyleSheet("color:#34d399; background:#10281f; border:1px solid #245941; padding:7px 10px; border-radius:8px; font-weight:900;")
            self.side_alarm.setText("Alarm: 0 / HEALTHY")
            self.alarm_title.setText("SYSTEM HEALTHY")
            self.alarm_detail.setText("No active alarms")
            self.alarm_count.setText("0 ACTIVE")
            self.alarm_count.setStyleSheet("background:#12372c; color:#6ee7b7; border-radius:8px; padding:9px 10px; font-weight:900;")
            self.ack_btn.setEnabled(False)
            self.last_alarm_signature = None

    def acknowledge_alarm(self):
        if not self.active_alarm_code:
            return
        self.alarm_acknowledged = True
        self.ack_btn.setEnabled(False)
        self.alarm_title.setText(self.alarm_title.text().replace(" — ACKNOWLEDGED", "") + " — ACKNOWLEDGED")
        self.alarm_count.setText("1 ACK")
        self.log("ACK", "ALARM", f"Alarm {self.active_alarm_code} acknowledged by operator.")

    # ====================================================================
    # HELPERS / LOGGING
    # ====================================================================

    def ensure_connected(self):
        if self.connected and self.client:
            return True
        QMessageBox.information(self, "Not Connected", "Connect to the Modbus TCP server first.")
        return False

    def log(self, level: str, log_type: str, message: str):
        if not hasattr(self, "log_table"):
            return
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        values = (datetime.now().strftime("%H:%M:%S"), level, log_type, message)
        for c, value in enumerate(values):
            self.log_table.setItem(row, c, QTableWidgetItem(str(value)))
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