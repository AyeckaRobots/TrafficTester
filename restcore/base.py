import requests
from typing import Optional

class BaseRestClient:
    """
    Handles HTTP session, authentication, and basic GET/POST.
    """

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        verify_ssl: bool = False
    ):
        self.base_url = f"http://{ip}"
        self.verify = verify_ssl
        token = self._authenticate(username, password)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _authenticate(self, username: str, password: str) -> str:
        """
        POST /api/login → returns raw token string (raises on failure).
        """
        url = f"{self.base_url}/api/login"
        resp = requests.post(
            url,
            json={"username": username, "password": password},
            headers={"Accept": "application/json"},
            timeout=60,
            verify=self.verify
        )
        resp.raise_for_status()
        return resp.json()["token"]

    def _get(self, path: str) -> dict:
        """
        Generic GET against `{self.base_url}{path}` → parsed JSON.
        """
        url = f"{self.base_url}{path}"
        resp = requests.get(url, headers=self.headers, timeout=60, verify=self.verify)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        """
        Generic POST against `{self.base_url}{path}` → parsed JSON.
        Raises if status >= 400.
        """
        url = f"{self.base_url}{path}"
        resp = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=60,
            verify=self.verify
        )
        resp.raise_for_status()
        # return JSON only if caller needs it
        try:
            return resp.json()
        except ValueError:
            return {}
        
    def refresh_token(self, username: str, password: str) -> None:
        token = self._authenticate(username, password)
        self.headers["Authorization"] = f"Bearer {token}"

    def get_general_info(self):
        data = self._get("/api/status")

        serial_number = data["serial_number"]
        device_name = data["device_name"]
        mdc = data["system"]["sw_version"]["mdc"]
        bca = data["system"]["sw_version"]["bca"]
        web = data["system"]["sw_version"]["web"]
        demodulator_fpga = data["system"]["sw_version"]["demodulator_fpga"]
        modulator_firmware = data["system"]["sw_version"]["modulator_firmware"]
        modulator_software = data["system"]["sw_version"]["modulator_software"]
        hw_version = data["system"]["hw_version"]

        return {
            "serial_number": serial_number,
            "device_name": device_name,
            "mdc": mdc,
            "bca": bca,
            "web": web,
            "demodulator_fpga": demodulator_fpga,
            "modulator_firmware": modulator_firmware,
            "modulator_software": modulator_software,
            "hw_version": hw_version,
        }

        


