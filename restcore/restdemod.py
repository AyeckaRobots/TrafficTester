from restcore.base import BaseRestClient
from typing import Optional

class RestDemod(BaseRestClient):
    """
    HTTP wrapper for the REST demodulator API.
    """

    def get_esno(self) -> float:
        """
        Return current ES/N0 (dB).
        """
        data = self._get("/api/status")
        return float(data["rx"]["esno"])

    def set_freq(self, freq_mhz: float) -> None:
        """
        Set demodulator frequency, in mhz.
        """
        payload = {"frequency": int(freq_mhz * 1_000)}
        self._post("/api/demodulator", payload)

    def set_symrate(self, symrate_msps: float) -> None:
        """
        Set symbol rate, in ksymbols/sec.
        """
        payload = {"symbol_rate": int(symrate_msps * 1_000)}
        self._post("/api/demodulator", payload)

    def set_all(
        self,
        frequency: Optional[float] = None,
        symrate: Optional[float]   = None
    ) -> None:
        """
        Batch-set freq (mhz) and/or symrate (msps).
        """
        payload = {}
        if frequency is not None:
            payload["frequency"] = int(frequency * 1_000)
        if symrate is not None:
            payload["symbol_rate"] = int(symrate * 1_000)

        if payload:
            self._post("/api/demodulator", payload)

    def reset_advanced_status(self):
        self._get("/api/reset_advanced_status")

    def get_packet_traffic(self):
        data = self._get("/api/advanced_status")

        # Safely grab the test_pattern section (or empty dict if missing)
        test_pattern = data.get("test_pattern", {})

        # Only include these counters if they appear in test_pattern
        keys = (
            "good_frame_counter",
            "bad_frame_counter",
            "missed_frame_counter",
        )
        return {k: test_pattern[k] for k in keys if k in test_pattern}
    
    def is_locked(self):
        data = self._get("/api/status")
        lock_state = data["rx"]["state"]

        if lock_state == "OK":
            return True
        elif lock_state == "Warning":
            return False
        else:
            raise(1)
    

        


