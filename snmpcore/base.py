# base.py

import time
import subprocess
import re
from typing import Optional
from snmp import Engine, SNMPv1


class BaseSnmpClient:
    """
    Base SNMP client providing common SNMP GET/SET and integer‐parsing.
    """

    def __init__(
        self,
        ip: str,
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

    def _snmp_set(self, oid: str, type_: str, value) -> None:
        """Run snmpset against `oid` with given SNMP datatype and value."""
        cmd = [
            "snmpset",
            "-v", "1",
            "-c", self.private,
            self.ip, oid, type_, str(value)
        ]
        #print("Running:", " ".join(cmd))
        try:
            res = subprocess.run(cmd, check=True, capture_output=True, text=True)
            # Show what snmpset reported on success
            if res.stdout.strip():
                ...#print(res.stdout.strip())
            if res.stderr.strip():
                ...#print("[snmpset stderr]", res.stderr.strip())
        except subprocess.CalledProcessError as e:
            print(f"SNMP SET failed for {oid}: {e.stderr.strip() or e.stdout.strip()}")


    def _parse_int(self, raw: str) -> Optional[int]:
        """Extract the first integer inside parentheses, or None."""
        m = self._int_pattern.search(raw)
        return int(m.group(1)) if m else None
