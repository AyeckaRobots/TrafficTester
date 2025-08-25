# demod/adapters.py
from typing import Dict
from demod.iface import Demodulator

from restcore import restdemod
from snmpcore import hw6demod

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

    def get_packet_loss_percentage(self) -> float:
        raw = self._d.get_packet_traffic()
        good = raw.get("good_frame_counter", raw.get("good", 0))
        bad = raw.get("bad_frame_counter", raw.get("bad", 0))
        missed = raw.get("missed_frame_counter", raw.get("missed", 0))
        total = good + bad + missed
        lost = bad + missed
        if total > 0:
            percentage = (lost / total) * 100
        else:
            percentage = 0.0
        return percentage


    def reset_counters(self) -> None:
        self._d.reset_counters()

    def get_general_info(self) -> dict:
        return self._d.get_general_info()

class HW6DemodAdapter(Demodulator):
    def __init__(self, inner: hw6demod.HW6Demod):
        self._d = inner

    def config_init(self):
        pass

    def set_all(self, freq: float, symrate: float) -> None:
        # Set frequency in MHz and symbol rate in Msps
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
        values = self._d._server_pct_values
        if not values:
            return 0.0
        average = sum(values) / len(values)
        return round(average, 4)

    def reset_counters(self) -> None:
        self._d._server_pct_values = []

    def get_general_info(self) -> dict:
        pass
