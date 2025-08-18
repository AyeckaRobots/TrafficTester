# demod/adapters.py
from typing import Dict
from demod.iface import Demodulator

from restcore import restdemod
from snmpcore import hw6demod

class RestDemodAdapter(Demodulator):
    def __init__(self, inner: restdemod.RestDemod):
        self._d = inner

    def set_all(self, freq: float, symrate: float) -> None:
        self._d.set_all(freq, symrate)

    def switch_rx1(self) -> None:
        self._d.switch_rx1()

    def switch_rx2(self) -> None:
        self._d.switch_rx2()

    def is_locked(self) -> bool:
        return bool(self._d.is_locked())

    def get_packet_traffic(self) -> Dict[str, int]:
        raw = self._d.get_packet_traffic()
        return {
            "good":   raw.get("good_frame_counter",   raw.get("good",   0)),
            "bad":    raw.get("bad_frame_counter",    raw.get("bad",    0)),
            "missed": raw.get("missed_frame_counter", raw.get("missed", 0)),
        }

    def reset_counters(self) -> None:
        self._d.reset_counters()

    def get_esno(self) -> float:
        return float(self._d.get_esno())

    def get_general_info(self) -> Dict:
        return self._d.get_general_info()


class HW6DemodAdapter(Demodulator):
    def __init__(self, inner: hw6demod.HW6Demod):
        self._d = inner

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

    def get_packet_traffic(self) -> Dict[str, int]:
        """
        Parses iperf output to extract basic traffic stats.
        Returns a dictionary with keys like 'packets_sent', 'packets_lost', 'loss_percent'.
        """
        traffic_stats = {
            "packets_sent": 0,
            "packets_lost": 0,
            "loss_percent": 0
        }

        # Parse client output for loss stats
        output = self._d._iperf_outputs.get(self._d._client_ip, [])
        for line in output:
            if "datagrams" in line and "loss" in line:
                # Example line: [ ID] 0.0-10.0 sec  1000 datagrams received out of 1000 (0.0% loss)
                import re
                match = re.search(r'(\d+)\s+datagrams.*out of\s+(\d+).*?\(([\d\.]+)%\s+loss\)', line)
                if match:
                    received = int(match.group(1))
                    sent = int(match.group(2))
                    loss_pct = float(match.group(3))
                    traffic_stats["packets_sent"] = sent
                    traffic_stats["packets_lost"] = sent - received
                    traffic_stats["loss_percent"] = loss_pct
                    break

        return traffic_stats

    def reset_counters(self) -> None:
        # Try both possible method names
        if hasattr(self._d, "reset_advanced_status"):
            self._d.reset_advanced_status()
        elif hasattr(self._d, "reset_counters"):
            self._d.reset_counters()

    def get_esno(self) -> float:
        return float(self._d.get_esno())

