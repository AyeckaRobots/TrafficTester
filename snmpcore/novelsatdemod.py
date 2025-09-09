# novelsatdemod.py

import time
import re
from typing import Optional

from snmpcore.base import *
from constants import *


class NovelsatDemod(BaseSnmpClient):
    """
    Simple SNMP-based interface to the Novelsat demodulator.
    """

    def __init__(
        self,
        ip: str = NOVELSAT_IP,
        public_comm: bytes = b"public",
        private_comm: str = "private",
    ):
        super().__init__(ip, public_comm, private_comm)

    def get_freq(self) -> Optional[int]:
        """Read and return frequency (as raw integer) from the demod."""
        raw = self._snmp_get_raw(".1.3.6.1.4.1.37576.4.1.1.2.0", delay=2.0)
        return self._parse_int(raw)

    def set_freq(self, freq_mhz: float) -> None:
        """
        Set frequency in mhz. Internally multiplies by 100_000 to match device scaling.
        """
        scaled = int(freq_mhz * 100_000)
        self._snmp_set(".1.3.6.1.4.1.37576.4.1.1.2.0", "u", str(scaled))

    def get_symrate(self) -> Optional[int]:
        """Read and return symbol rate (as raw integer) from the demod."""
        raw = self._snmp_get_raw(".1.3.6.1.4.1.37576.4.1.1.4.0", delay=2.0)
        return self._parse_int(raw)

    def set_symrate(self, symrate_ksps: float) -> None:
        """
        Set symbol rate in ksymbols/sec. Multiplies by 1_000_000 for device.
        """
        scaled = int(symrate_ksps * 1_000_000)
        self._snmp_set(".1.3.6.1.4.1.37576.4.1.1.4.0", "i", str(scaled))

    def is_locked(self) -> Optional[bool]:
        """
        Return True if the demodulator is locked, False if not, None on parse failure.
        """
        raw = self._snmp_get_raw(".1.3.6.1.4.1.37576.4.2.1.2.0", delay=1.0)
        val = self._parse_int(raw)
        return bool(val) if val is not None else None

    def measure_esno(
        self,
        trials: int = 5,
        pre_delay: float = 0.7,
        interval: float = 0.2,
    ) -> Optional[float]:
        """
        Poll ES/N0 multiple times, average the hundredths‐of‐dB values,
        divide by 100, and round to two decimals.
        """
        time.sleep(pre_delay)
        samples = []
        pattern = re.compile(r'Integer32\((-?\d+)\)')

        with Engine(SNMPv1, defaultCommunity=self.public) as engine:
            host = engine.Manager(self.ip)
            for _ in range(trials):
                raw = host.get(".1.3.6.1.4.1.37576.4.2.1.3.0").toString()
                m = pattern.search(raw)
                if m:
                    samples.append(int(m.group(1)))
                time.sleep(interval)

        if not samples:
            return None

        avg_hundredths = sum(samples) / len(samples)
        return round(avg_hundredths / 100.0, 2)


def main():
    demod = NovelsatDemod()

    # one-off changes
    demod.set_freq(1234.0)
    demod.set_symrate(13.0)

    # reads
    print("Freq:", demod.get_freq())
    print("Symrate:", demod.get_symrate())
    print("Locked:", demod.is_locked())
    print("Avg ES/N0:", demod.measure_esno())


if __name__ == "__main__":
    main()
