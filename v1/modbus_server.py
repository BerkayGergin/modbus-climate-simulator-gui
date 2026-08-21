"""
modbus_server.py

Sanal klima cihazı - Modbus TCP server.

Register adresleri (0-based):

INPUT REGISTERS (FC04)
0  Oda sıcaklığı       °C x10
1  Nem                 %RH x10
2  Evaporatör sıcaklığı °C x10
3  Kondenser sıcaklığı  °C x10
4  Emme basıncı         bar x10
5  Basma basıncı        bar x10
6  Hava debisi          m3/h
7  Güç tüketimi         W
8  Enerji              kWh x100
9  Filtre seviyesi      %
10 Çalışma süresi       h
11 Alarm kodu

HOLDING REGISTERS (FC03)
0  Set sıcaklığı        °C x10      [16.0 .. 30.0]
1  Fan hızı             %           [0 .. 100]
2  Çalışma modu         0=OFF, 1=COOL, 2=FAN, 3=AUTO
3  Command revision     GUI tarafından artırılır
4  Applied revision     Cihaz tarafından onaylanır
5  Command status       0=IDLE, 1=APPLIED

COILS (FC01)
0  Klima enable
1  Kompresör            (durum / simulator tarafından yönetilir)
2  Fan                  (durum)

Not: Modbus register adresleri GUI/API tarafında 0'dan başlar. Bu sürüm PyModbus
3.13+/3.15'in SimData/SimDevice mimarisini kullanır; eski datastore sınıfları kullanılmaz.
"""

from __future__ import annotations

import asyncio
import logging
import math
import signal
import time
from dataclasses import dataclass

from pymodbus import ModbusDeviceIdentification
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

# ---------------------------------------------------------------------------
# Server configuration
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 5020  # 502 yerine 5020: Windows'ta admin/root yetkisi gerektirmez.
DEVICE_ID = 1
SIMULATION_INTERVAL = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("clima-server")


@dataclass
class KlimaState:
    room_temp: float = 27.0
    setpoint: float = 22.0
    humidity: float = 48.0
    evaporator_temp: float = 11.0
    condenser_temp: float = 34.0
    suction_pressure: float = 5.8
    discharge_pressure: float = 16.0
    airflow: float = 1150.0
    power_w: float = 0.0
    energy_kwh: float = 124.6
    filter_level: float = 82.0
    runtime_h: float = 1482.5
    alarm_code: int = 0
    compressor_on: bool = False
    fan_on: bool = False


# ---------------------------------------------------------------------------
# Helpers: register scaling
# ---------------------------------------------------------------------------
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def encode_10(value: float) -> int:
    return int(round(value * 10))


def encode_100(value: float) -> int:
    return int(round(value * 100))


def safe_u16(value: int) -> int:
    return int(clamp(value, 0, 65535))


# ---------------------------------------------------------------------------
# Modbus datamodel (modern PyModbus 3.13+ / 3.15)
# ---------------------------------------------------------------------------
def build_device() -> SimDevice:
    """Build one real SimDevice with separate Modbus areas.

    The current PyModbus 3.13+ API integrates SimDevice directly into the server.
    We deliberately define only Device ID 1: no duplicate catch-all device is needed.
    """
    input_values = [
        encode_10(27.0),       # 0 room temp
        encode_10(48.0),       # 1 humidity
        encode_10(11.0),       # 2 evaporator
        encode_10(34.0),       # 3 condenser
        encode_10(5.8),        # 4 suction
        encode_10(16.0),       # 5 discharge
        1150,                  # 6 airflow
        0,                     # 7 power
        encode_100(124.6),     # 8 energy
        82,                    # 9 filter
        int(1482.5),           # 10 runtime
        0,                     # 11 alarm
    ]

    holding_values = [
        encode_10(22.0),       # 0 setpoint
        70,                    # 1 fan %
        1,                     # 2 COOL
        0,                     # 3 command revision
        0,                     # 4 applied revision
        1,                     # 5 command status
    ]

    # Non-shared model: each coil address is a single Modbus coil.
    coil_values = [True, False, False] + [False] * 7
    discrete_values = [False] * 10

    return SimDevice(
        id=DEVICE_ID,
        simdata=(
            [SimData(0, values=coil_values, datatype=DataType.BITS)],
            [SimData(0, values=discrete_values, datatype=DataType.BITS)],
            [SimData(0, values=holding_values, datatype=DataType.REGISTERS)],
            [SimData(0, values=input_values, datatype=DataType.REGISTERS)],
        ),
    )


# ---------------------------------------------------------------------------
# Simulator logic
# ---------------------------------------------------------------------------
async def read_hr(server: ModbusTcpServer, address: int, count: int = 1) -> list[int]:
    return await server.async_getValues(DEVICE_ID, 3, address, count)


async def read_coil(server: ModbusTcpServer, address: int) -> bool:
    values = await server.async_getValues(DEVICE_ID, 1, address, 1)
    return bool(values[0])


async def write_ir(server: ModbusTcpServer, address: int, value: int) -> None:
    await server.async_setValues(DEVICE_ID, 4, address, [safe_u16(value)])


async def write_coil(server: ModbusTcpServer, address: int, value: bool) -> None:
    await server.async_setValues(DEVICE_ID, 1, address, [bool(value)])


async def simulator_loop(server: ModbusTcpServer, state: KlimaState, stop_event: asyncio.Event) -> None:
    """Simple but believable HVAC dynamics model."""
    last = time.monotonic()

    while not stop_event.is_set():
        now = time.monotonic()
        dt = clamp(now - last, 0.1, 2.0)
        last = now

        try:
            # Operator settings coming from Modbus writes.
            hr = await read_hr(server, 0, 4)
            setpoint = clamp(hr[0] / 10.0, 16.0, 30.0)
            fan_pct = clamp(float(hr[1]), 0.0, 100.0)
            mode = int(hr[2]) if 0 <= int(hr[2]) <= 3 else 0
            command_revision = int(hr[3])
            enabled = await read_coil(server, 0)

            state.setpoint = setpoint

            # Simple control logic.
            cooling_demand = state.room_temp - state.setpoint
            if enabled and mode in (1, 3) and cooling_demand > 0.3:
                compressor = True
            elif enabled and mode == 2:
                compressor = False
            elif not enabled or mode == 0 or cooling_demand < -0.2:
                compressor = False
            else:
                compressor = state.compressor_on

            # Fan runs whenever enabled and user selected non-zero speed.
            fan_on = enabled and fan_pct > 0
            state.compressor_on = compressor
            state.fan_on = fan_on

            # Room heat gain + cooling effect.
            heat_gain = 0.035 * dt
            cooling_gain = 0.0
            if compressor:
                cooling_gain = 0.11 * (0.35 + fan_pct / 100.0) * dt
            elif fan_on:
                # Fan-only mode mixes air but does not strongly cool the room.
                cooling_gain = 0.018 * (fan_pct / 100.0) * dt

            # Mild pull towards ambient = 28 C.
            ambient_pull = (28.0 - state.room_temp) * 0.004 * dt
            state.room_temp += heat_gain + ambient_pull - cooling_gain
            state.room_temp = clamp(state.room_temp, 10.0, 45.0)

            # Humidity follows temperature and compressor operation.
            humidity_target = 44.0 if compressor else 52.0
            state.humidity += (humidity_target - state.humidity) * 0.015 * dt
            state.humidity += math.sin(now / 25.0) * 0.012
            state.humidity = clamp(state.humidity, 20.0, 90.0)

            # Evaporator / condenser dynamics.
            if compressor:
                target_evap = 6.0 + (fan_pct / 100.0) * 5.0
                target_cond = 48.0 + (100.0 - fan_pct) * 0.07
                state.evaporator_temp += (target_evap - state.evaporator_temp) * 0.12 * dt
                state.condenser_temp += (target_cond - state.condenser_temp) * 0.08 * dt

                target_suction = 4.5 + (state.room_temp - state.setpoint) * 0.5
                target_discharge = 15.0 + (state.condenser_temp - 30.0) * 0.15
                state.suction_pressure += (target_suction - state.suction_pressure) * 0.10 * dt
                state.discharge_pressure += (target_discharge - state.discharge_pressure) * 0.10 * dt
            else:
                state.evaporator_temp += (12.0 - state.evaporator_temp) * 0.08 * dt
                state.condenser_temp += (32.0 - state.condenser_temp) * 0.06 * dt
                state.suction_pressure += (3.5 - state.suction_pressure) * 0.08 * dt
                state.discharge_pressure += (7.0 - state.discharge_pressure) * 0.08 * dt

            # Airflow: roughly proportional to fan speed.
            target_airflow = 250.0 + fan_pct * 13.0 if fan_on else 0.0
            state.airflow += (target_airflow - state.airflow) * 0.20 * dt
            state.airflow = clamp(state.airflow, 0.0, 1800.0)

            # Power/energy.
            target_power = 0.0
            if fan_on:
                target_power += 120.0 + fan_pct * 3.0
            if compressor:
                target_power += 850.0 + max(0.0, state.discharge_pressure - 12.0) * 35.0
            state.power_w += (target_power - state.power_w) * 0.25 * dt
            state.power_w = clamp(state.power_w, 0.0, 5000.0)
            state.energy_kwh += (state.power_w / 1000.0) * dt / 3600.0

            if fan_on or compressor:
                state.runtime_h += dt / 3600.0

            # Filter slowly gets dirty while running.
            if fan_on:
                state.filter_level -= dt / 3600.0 * 0.04
                state.filter_level = clamp(state.filter_level, 0.0, 100.0)

            # Alarm evaluation.
            if state.discharge_pressure > 30.0:
                state.alarm_code = 101  # High discharge pressure
            elif state.evaporator_temp < 0.0:
                state.alarm_code = 102  # Evaporator frost risk
            elif state.filter_level < 15.0:
                state.alarm_code = 103  # Dirty filter
            else:
                state.alarm_code = 0

            # The simulator has accepted the current operator command.
            # Echo the revision back as a real device-level acknowledgement.
            await server.async_setValues(DEVICE_ID, 3, 4, [safe_u16(command_revision)])
            await server.async_setValues(DEVICE_ID, 3, 5, [1])

            # Publish live values to input registers.
            await write_ir(server, 0, encode_10(state.room_temp))
            await write_ir(server, 1, encode_10(state.humidity))
            await write_ir(server, 2, encode_10(state.evaporator_temp))
            await write_ir(server, 3, encode_10(state.condenser_temp))
            await write_ir(server, 4, encode_10(state.suction_pressure))
            await write_ir(server, 5, encode_10(state.discharge_pressure))
            await write_ir(server, 6, int(round(state.airflow)))
            await write_ir(server, 7, int(round(state.power_w)))
            await write_ir(server, 8, encode_100(state.energy_kwh))
            await write_ir(server, 9, int(round(state.filter_level)))
            await write_ir(server, 10, int(state.runtime_h))
            await write_ir(server, 11, state.alarm_code)

            # Publish status coils.
            await write_coil(server, 1, compressor)
            await write_coil(server, 2, fan_on)

        except Exception:
            log.exception("Simülasyon döngüsünde hata oluştu.")

        await asyncio.sleep(SIMULATION_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_identity() -> ModbusDeviceIdentification:
    identity = ModbusDeviceIdentification()
    identity.VendorName = "ClimaLab"
    identity.ProductCode = "CLM-01"
    identity.VendorUrl = "https://example.com"
    identity.ProductName = "ClimaLab Virtual HVAC"
    identity.ModelName = "HVAC-SIM-1000"
    identity.MajorMinorRevision = "1.0.0"
    return identity


async def main() -> None:
    device = build_device()
    state = KlimaState()
    stop_event = asyncio.Event()

    server = ModbusTcpServer(
        context=device,
        address=(HOST, PORT),
        identity=build_identity(),
    )

    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        log.info("Kapatma sinyali alındı...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            # Windows bazı event-loop konfigürasyonlarında add_signal_handler
            # desteklemeyebilir; KeyboardInterrupt yine çalışır.
            pass

    log.info("============================================================")
    log.info("CLIMALAB VIRTUAL HVAC - MODBUS TCP SERVER")
    log.info("Adres       : %s:%d", HOST, PORT)
    log.info("Device ID   : %d", DEVICE_ID)
    log.info("Başlangıç   : ROOM=%.1f°C  SET=%.1f°C  HUM=%.1f%%",
             state.room_temp, state.setpoint, state.humidity)
    log.info("============================================================")

    server_task = asyncio.create_task(server.serve_forever())
    simulator_task = asyncio.create_task(simulator_loop(server, state, stop_event))

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        request_stop()
    finally:
        await server.shutdown()
        simulator_task.cancel()
        server_task.cancel()
        await asyncio.gather(simulator_task, server_task, return_exceptions=True)
        log.info("Server kapatıldı.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass