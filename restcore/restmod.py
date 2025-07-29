from restcore.base import BaseRestClient
from typing import Optional

class RestMod(BaseRestClient):
    """
    HTTP wrapper for the REST modulator API.
    """

    def get_freq(self) -> float:
        data = self._get("/api/modulator")
        return data.get("frequency", 0) / 1_000.0

    def get_symrate(self) -> float:
        data = self._get("/api/modulator")
        return data.get("symbol_rate", 0) / 1_000.0

    def get_noise(self) -> int:
        resp = self._post("/api/fpga_read", [{"address": 24688}])
        return resp[0]["value"]

    def set_freq(self, freq_mhz: float) -> None:
        self._post("/api/modulator", {"frequency": int(freq_mhz * 1_000)})

    def set_symrate(self, symrate_msps: float) -> None:
        self._post("/api/modulator", {"symbol_rate": int(symrate_msps * 1_000)})

    def set_power(self, pwr_dbm: float) -> None:
        self._post("/api/modulator", {"power": pwr_dbm})

    def set_noise(self, noise_val: int) -> None:
        self._post("/api/fpga_write", [{"address": 24688, "value": noise_val}])

    def set_pls(self, pls_code: int) -> None:
        body = {
            "tx": {
                "symbol_rate": {"min": 100, "max": 460_000},
                "test_pattern_pls_code": pls_code
            }
        }
        self._post("/api/settings", body)

    def set_all(
        self,
        frequency: Optional[float] = None,
        symrate:   Optional[float] = None,
        power:     Optional[float] = None,
        noise:     Optional[int]   = None,
        pls:       Optional[int]   = None
    ) -> None:
        # 1) Batch modulator params into one payload
        mod_payload = {}
        if frequency is not None:
            mod_payload["frequency"] = int(frequency * 1_000)
        if symrate is not None:
            mod_payload["symbol_rate"] = int(symrate * 1_000)
        if power is not None:
            mod_payload["power"] = power

        if mod_payload:
            self._post("/api/modulator", mod_payload)

        # 2) Handle FPGA noise write separately
        if noise is not None:
            # using the same low-level format as set_noise()
            self._post("/api/fpga_write", [{"address": 24688, "value": noise}])

        # 3) Handle PLS code in settings endpoint
        if pls is not None:
            body = {
                "tx": {
                    "symbol_rate": {"min": 100, "max": 460_000},
                    "test_pattern_pls_code": pls
                }
            }
            self._post("/api/settings", body)
