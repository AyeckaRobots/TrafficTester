# demod/adapters.py
from typing import Dict
from demod.iface import Demodulator

from restcore import restdemod
from snmpcore import hw6demod
from constants import TEST_TIME
import time

class RestDemodAdapter(Demodulator):
    def __init__(self, inner: restdemod.RestDemod):
        self._d = inner

    def config_init(self):
        pass

    def set_all(self, freq: float, symrate: float) -> None:
        self._d.set_all(freq, symrate)

    def switch_rx1(self) -> None:
        self._d.switch_rx1()

    def switch_rx2(self) -> None:
        self._d.switch_rx2()

    def is_locked(self) -> bool:
        return bool(self._d.is_locked())

    def get_esno(self) -> float:
        return float(self._d.get_esno())

    def get_packet_traffic(self) -> float:
        raw = self._d.get_packet_traffic()

        good = raw["good_frame_counter"] if "good_frame_counter" in raw else 0
        bad = raw["bad_frame_counter"] if "bad_frame_counter" in raw else 0
        missed = raw["missed_frame_counter"] if "missed_frame_counter" in raw else 0

        total = good + bad + missed
        lost = bad + missed

        percentage = (lost / total) * 100 if total > 0 else 0.0
        return round(percentage, 4)
        
    def reset_counters(self):
        self._d.reset_counters()

    def get_general_info(self) -> dict:
        return self._d.get_general_info()

    def run_packet_traffic(self) -> None:
        time.sleep(TEST_TIME)

class HW6DemodAdapter(Demodulator):
    def __init__(self, inner: hw6demod.HW6Demod):
        self._d = inner

    def config_init(self):
        pass

    def set_all(self, freq: float, symrate: float) -> None:
        self._d.set_freq(freq)
        self._d.set_symrate(symrate)

    def switch_rx1(self) -> None:
        self._d.switch_rx1()

    def switch_rx2(self) -> None:
        self._d.switch_rx2()

    def is_locked(self) -> bool:
        return bool(self._d.is_locked())

    def get_esno(self) -> float:
        return float(self._d.get_esno())

    def get_packet_traffic(self) -> float:
        values = list(self._d._server_pct_values)
        if len(values) == 0:
            return None
        average = sum(values) / len(values)
        return round(average, 4)

    def reset_counters(self) -> None:
        self._d._server_pct_values = []

    def get_general_info(self) -> dict:
        return self._d.get_general_info()

    def run_packet_traffic(self) -> None:
        self._d.run_iperf()
