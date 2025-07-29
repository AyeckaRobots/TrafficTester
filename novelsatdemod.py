import time
import re
import subprocess
from snmp import Engine, SNMPv1
from typing import Optional, Tuple
from constants import *


class NovelsatDemod:
    """
    Simple SNMP-based interface to the Novelsat demodulator.
    """

    def __init__(
        self,
        ip: str = NOVELSAT_IP,
        public_comm: bytes = b"public",
        private_comm: str = "private",
    ):
        self.ip = ip
        self.public = public_comm
        self.private = private_comm
        self._int_pattern = re.compile(r'\(-?(\d+)\)')

    def _snmp_get_raw(self, oid: str, delay: float) -> str:
        """Sleep for `delay`, then fetch raw SNMP response string for `oid`."""
        time.sleep(delay)
        with Engine(SNMPv1, defaultCommunity=self.public) as engine:
            host = engine.Manager(self.ip)
            return host.get(oid).toString()

    def _parse_int(self, raw: str) -> Optional[int]:
        """Extract the first integer inside parentheses, or None."""
        m = self._int_pattern.search(raw)
        return int(m.group(1)) if m else None

    def _snmp_set(self, oid: str, type_: str, value: str) -> None:
        """Run snmpset against `oid` with given SNMP datatype and value."""
        cmd = [
            "snmpset", "-v1",
            "-c", self.private,
            self.ip, oid, type_, value
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            print(f"SNMP SET failed for {oid}: {e.stderr.strip()}")

    def get_freq(self) -> Optional[int]:
        """
        Read and return frequency (as raw integer) from the demod.
        """
        raw = self._snmp_get_raw(FREQ_OID, delay=2.0)
        return self._parse_int(raw)

    def set_freq(self, freq_khz: float) -> None:
        """
        Set frequency in kHz. Internally multiplies by 100_000 to match device scaling.
        """
        scaled = int(freq_khz * 100_000)
        self._snmp_set(FREQ_OID, "u", str(scaled))

    def get_symrate(self) -> Optional[int]:
        """
        Read and return symbol rate (as raw integer) from the demod.
        """
        raw = self._snmp_get_raw(SYMRATE_OID, delay=2.0)
        return self._parse_int(raw)

    def set_symrate(self, symrate_ksps: float) -> None:
        """
        Set symbol rate in ksymbols/sec. Multiplies by 1_000_000 for device.
        """
        scaled = int(symrate_ksps * 1_000_000)
        self._snmp_set(SYMRATE_OID, "i", str(scaled))

    def is_locked(self) -> Optional[bool]:
        """
        Return True if the demodulator is locked, False if not, None on parse failure.
        """
        raw = self._snmp_get_raw(LINESTATE_OID, delay=1.0)
        val = self._parse_int(raw)
        return bool(val) if val is not None else None

    def measure_esno(
        self,
        trials: int = 5,
        pre_delay: float = 0.7,
        interval: float = 0.2
    ) -> Optional[float]:
        """
        Poll ES/N0 multiple times, average the hundredths-of-dB values,
        divide by 100, and round to two decimals.
        """
        time.sleep(pre_delay)
        samples = []
        pattern = re.compile(r'Integer32\((-?\d+)\)')

        with Engine(SNMPv1, defaultCommunity=self.public) as engine:
            host = engine.Manager(self.ip)
            for _ in range(trials):
                raw = host.get(ESNO_OID).toString()
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
