import paramiko
import threading
import time
import re
from typing import Optional

from snmpcore.base import BaseSnmpClient
from constants import *


class HW6Demod(BaseSnmpClient):
    """
    Simple SNMP-based interface to the Novelsat demodulator,
    with built-in iperf UDP-loss monitoring over SSH.
    """

    def __init__(
        self,
        ip: str = DEMOD_IP,
        runtime: int = TEST_TIME,
        bitrate: str = "25M",

        public_comm: bytes = b"public",
        private_comm: str = "private",

        server_ip: str = IPERF_SERVER_IP,
        client_ip: str = IPERF_CLIENT_IP,

        server_username: str = IPERF_CLIENT_USERNAME,
        server_password: str = IPERF_CLIENT_PASSWORD,
        client_username: str = IPERF_SERVER_USERNAME,
        client_password: str = IPERF_SERVER_PASSWORD,

        multicast: str = MULTICAST_ADDR,
        server_gw: str = SERVER_GW,
        client_gw: str = CLIENT_GW
    ):
        # initialize SNMP
        super().__init__(ip, public_comm, private_comm)

        self._pct_re = re.compile(r'\((?P<pct>(?:\d+(?:\.\d+)?|nan))%\)', re.IGNORECASE)
        self._pid_re = re.compile(r'IPERF_PID:(\d+)')
        self._server_ip = server_ip
        self._client_ip = client_ip
        self._multicast = multicast
        self._runtime = runtime
        self._bitrate = bitrate

        # build the two host entries for server & client, each with its own creds
        # Wrap iperf in background, echo its PID, then wait on it.
        self._hosts = [
            {
                "ip": server_ip,
                "username": server_username,
                "password": server_password,
                "cmd": (
                    f"(echo {server_password} | sudo -S route add {multicast} gw {server_gw} ; "
                    f"iperf -u -s -B {multicast} -i1 & echo IPERF_PID:$! ; wait $!)"
                ),
            },
            {
                "ip": client_ip,
                "username": client_username,
                "password": client_password,
                "cmd": (
                    f"(echo {client_password} | sudo -S route add {multicast} gw {client_gw} ; "
                    f"iperf -u -c {multicast} -i1 -t {runtime} -b {bitrate} & echo IPERF_PID:$! ; wait $!)"
                ),
            },
        ]

        # new cumulative storage for server percentage values
        self._server_pct_values = []

        self._active_rx = None

    def config_init(self):
        """
        Assuming mgmt DHCP is disabled and the mgmt ip addr is already configured
        """
        self._snmp_set("1.3.6.1.4.1.27928.107.2.12.0", "i", 0) # lan multicast enable

        # Switch mode manual
        self._snmp_set("1.3.6.1.4.1.27928.107.1.3.2.0", "i", 1)
        self._snmp_set("1.3.6.1.4.1.27928.107.1.1.3.3.0", "i", 1)
        self._snmp_set("1.3.6.1.4.1.27928.107.1.2.3.3.0", "i", 1)

        # Set active rx configuration
        self._snmp_set("1.3.6.1.4.1.27928.107.1.1.3.1.0", "i", 0)
        self._snmp_set("1.3.6.1.4.1.27928.107.1.2.3.1.0", "i", 0)

        # Power off lnb power
        self._snmp_set("1.3.6.1.4.1.27928.107.1.1.1.3.1.0", "i", 0) # rx1 lnb power off
        self._snmp_set("1.3.6.1.4.1.27928.107.1.1.1.3.2.0", "i", 0) # rx1 lnb compensation off
        self._snmp_set("1.3.6.1.4.1.27928.107.1.2.1.3.1.0", "i", 0) # rx2 lnb power off
        self._snmp_set("1.3.6.1.4.1.27928.107.1.2.1.3.2.0", "i", 0) # rx2 lnb compensation off

        # IMPORTANT: set labels in Filters Table, 1, label "D0-D1-D2-D3-D4-D5" and enable it

    def switch_rx1(self):
        active_rx = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.3.1.0")
        active_rx = self._parse_int(active_rx)
        if active_rx != 1:
            self._snmp_set(
                "1.3.6.1.4.1.27928.107.1.3.1.0", "i", 1
            )
        self._active_rx = 1

    def switch_rx2(self):
        active_rx = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.3.1.0")
        active_rx = self._parse_int(active_rx)
        if active_rx != 2:
            self._snmp_set("1.3.6.1.4.1.27928.107.1.3.1.0", "i", 2)
        self._active_rx = 2

    def get_freq(self) -> Optional[int]:
        if self._active_rx == 1:
            raw = self._snmp_get_raw(
                "1.3.6.1.4.1.27928.107.1.1.1.1.1.0", delay=2.0
            )
            return int(self._parse_int(raw) / 1000)
        elif self._active_rx == 2:
            raw = self._snmp_get_raw(
                "1.3.6.1.4.1.27928.107.1.2.1.1.1.0", delay=2.0
            )
            return int(self._parse_int(raw) / 1000)
        else:
            raise(Exception)

    def set_freq(self, freq_mhz: float) -> None:
        scaled = int(freq_mhz * 1000)

        if self._active_rx == 1:
            self._snmp_set(
                "1.3.6.1.4.1.27928.107.1.1.1.1.1.0", "u", str(scaled)
            )
        elif self._active_rx == 2:
            self._snmp_set(
                "1.3.6.1.4.1.27928.107.1.2.1.1.1.0", "u", str(scaled)
            )
        else:
            raise(Exception)

    def set_symrate(self, symrate_msps: float) -> None:
        scaled = int(symrate_msps * 1_000_000)

        if self._active_rx == 1:
            self._snmp_set(
                "1.3.6.1.4.1.27928.107.1.1.1.2.2.0", "i", str(scaled)
            )
        elif self._active_rx == 2:
            self._snmp_set(
                "1.3.6.1.4.1.27928.107.1.2.1.2.2.0", "i", str(scaled)
            )
        else:
            raise(Exception)

    def get_esno(self):
        if self._active_rx == 1:
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.1.4.4.0", 2.0)
        elif self._active_rx == 2:
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.2.4.4.0", 2.0)
        else:
            raise(Exception)

        esno = float(self._parse_int(raw) / 10)
        return esno

    def is_locked(self):
        if self._active_rx == 1:
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.1.4.11.0")
        elif self._active_rx == 2:
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.2.4.11.0")
        else:
            raise(Exception)

        lock = int(self._parse_int(raw))
        if lock == 0 or lock == 1 or lock == 2:
            return True
        elif lock == 3:
            return False
        elif lock == 4:
            raise("maybe error in board")
        else:
            raise(Exception)
        
    """    def set_label(self, label: str) -> None:
        if self._active_rx == 1:
            self._snmp_set(
                "1.3.6.1.4.1.27928.107.1.1.1.4.3.1.3.1.0", "x", label
            )
        elif self._active_rx == 2:
            self._snmp_set(
                "1.3.6.1.4.1.27928.107.1.2.1.4.3.1.3.1.0", "x", label
            )
        else:
            raise(Exception)"""
    
    def get_label(self) -> str:
        if self._active_rx == 1:
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.1.1.4.3.1.3.1.0", 2.0)
        elif self._active_rx == 2:
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.2.1.4.3.1.3.1.0", 2.0)
        else:
            raise(Exception)
        
        return raw


    def _process_output(self, ip: str, output: str) -> None:
        """
        Process iperf command output, accumulate percentage values and print them (server only).
        """
        # Changed to extract lost/total fraction and compute percentage to 4 decimal places
        lost_re = re.compile(r'\s(?P<lost>\d+)/(?P<total>\d+)\s')
        for m in lost_re.finditer(output):
            try:
                lost = float(m.group("lost"))
                total = float(m.group("total"))
                pct = (lost / total * 100) if total > 0 else 0.0
                formatted_pct = f"{pct:.4f}%"
                if ip == self._server_ip:
                    self._server_pct_values.append(pct)
            except Exception:
                continue

    def _run_command(self, ip: str, username: str, password: str, command: str):
        """
        Run a remote command over SSH, capture iperf percentage values.
        """
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            print(f"[{ip}] Connecting…")
            ssh.connect(ip, username=username, password=password)
            print(f"[{ip}] Connected")

            channel = ssh.get_transport().open_session()
            channel.get_pty()
            channel.exec_command(command)

            # read until iperf quits
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    out = channel.recv(1024).decode()
                    self._process_output(ip, out)
                time.sleep(0.1)

            # drain any leftover bytes
            while channel.recv_ready():
                out = channel.recv(1024).decode()
                self._process_output(ip, out)

        except Exception as e:
            print(f"[{ip}] Error: {e}")
        finally:
            ssh.close()
            print(f"[{ip}] Disconnected")

    def run_iperf(self):
        """
        Launches iperf on both server & client in parallel threads.
        """
        threads = []
        for host in self._hosts:
            t = threading.Thread(
                target=self._run_command,
                args=(host["ip"], host["username"], host["password"], host["cmd"]),
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        print("All iperf sessions completed.")
        
    def _get_descendant_pids(self, ip: str, username: str, password: str, parent_pid: int):
        """
        Return all descendant PIDs (children, grandchildren, ...) of parent_pid on the remote host.
        """
        cmd = (
            "sh -c '"
            "get_children() { "
            "local p=$1; "
            "for c in $(ps -o pid= --ppid \"$p\" 2>/dev/null); do "
            "echo $c; "
            "get_children $c; "
            "done; "
            "}; "
            f"get_children {parent_pid}"
            "'"
        )
        pids = []
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(ip, username=username, password=password)
            stdin, stdout, stderr = client.exec_command(cmd)
            for line in stdout.readlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
        except Exception as e:
            print(f"[{ip}] PID query error: {e}")
        finally:
            client.close()
        return pids

    def get_general_info(self):
        serial_number      = self._parse_int(self._snmp_get_raw("1.3.6.1.4.1.27928.107.3.3.0", 0))
        system_description = self._parse_octet_string(self._snmp_get_raw("1.3.6.1.2.1.1.1.0", 0))
        software_version   = self._parse_octet_string(self._snmp_get_raw("1.3.6.1.4.1.27928.107.3.5.0", 0))
        fpga_version       = self._parse_octet_string(self._snmp_get_raw("1.3.6.1.4.1.27928.107.3.6.0", 0))
        hardware_version   = self._parse_octet_string(self._snmp_get_raw("1.3.6.1.4.1.27928.107.3.7.0", 0))
        production_code    = self._parse_int(self._snmp_get_raw("1.3.6.1.4.1.27928.107.3.4.0", 0))

        return {
            "serial_number": serial_number,
            "system_description": system_description,
            "software_version": software_version,
            "fpga_version": fpga_version,
            "hardware_version": hardware_version,
            "production_code": production_code
        }

