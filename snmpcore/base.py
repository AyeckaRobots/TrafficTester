# base.py

import time
import subprocess
import re
from typing import Optional, Union
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
        self._gauge_pattern  = re.compile(r'Gauge32\((-?\d+)\)')
        self._octet_pattern  = re.compile(r"OctetString\(b'([^']*)'\)")

    def _snmp_get_raw(self, oid: str, delay: float) -> str:
        """Sleep for `delay`, then fetch raw SNMP response string for `oid`."""
        time.sleep(delay)
        with Engine(SNMPv1, defaultCommunity=self.public) as engine:
            host = engine.Manager(self.ip)
            return host.get(oid).toString()

    def _snmp_set(self, oid: str, type_: str, value) -> None:
        time.sleep(2)
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
        # Match the pattern: word followed by parentheses with a number inside
        match = re.search(r'(\w+)\(([-+]?\d*\.?\d+)\)', raw)
        if match:
            type_word = match.group(1).lower()
            number_str = match.group(2)

            # Check if the type contains "integer" or "unsigned"
            if "integer" in type_word or "unsigned" in type_word or "gauge" in type_word:
                # Convert to int or float depending on the format
                if '.' in number_str:
                    return float(number_str)
                else:
                    return int(number_str)
        return None  # If no match or condition not met

    def _parse_octet_string(self, raw: str) -> Optional[str]:
        """
        Extract and decode bytes from "OctetString(b'…')" and strip.
        Returns the inner text, or None if no match.
        """
        m = self._octet_pattern.search(raw)
        if not m:
            return None
        # m.group(1) might include leading/trailing spaces
        return m.group(1).strip()

    def _parse_value(self, raw: str) -> Union[int, str, None]:
        """
        Generic dispatcher: tries Gauge32, then OctetString.
        Falls back to None if neither pattern matches.
        """
        if 'Gauge32' in raw:
            return self._parse_gauge32(raw)
        if 'OctetString' in raw:
            return self._parse_octet_string(raw)
        return None
    
    """
           The TYPE is a single character, one of:
              i  INTEGER
              u  UNSIGNED
              s  STRING
              x  HEX STRING
              d  DECIMAL STRING
              n  NULLOBJ
              o  OBJID
              t  TIMETICKS
              a  IPADDRESS
              b  BITS
    """

