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
            timeout=10,
            verify=self.verify
        )
        resp.raise_for_status()
        return resp.json()["token"]

    def _get(self, path: str) -> dict:
        """
        Generic GET against `{self.base_url}{path}` → parsed JSON.
        """
        url = f"{self.base_url}{path}"
        resp = requests.get(url, headers=self.headers, timeout=5, verify=self.verify)
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
            timeout=5,
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

