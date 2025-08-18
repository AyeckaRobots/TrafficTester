import paramiko
import threading
import time
import re
from collections import deque
from typing import Optional, Tuple

from snmpcore.base import BaseSnmpClient
from constants import *


class HW6Demod(BaseSnmpClient):
    """
    Simple SNMP-based interface to the Novelsat demodulator,
    with built-in iperf UDP-loss monitoring over SSH.
    """

    def __init__(
        self,
        ip: str = "192.168.10.200",
        public_comm: bytes = b"public",
        private_comm: str = "private",

        server_ip: str = "192.168.115.11",
        client_ip: str = "192.168.115.36",

        server_username: str = "user",
        server_password: str = "user",
        client_username: str = "user",
        client_password: str = "user",

        multicast: str = "225.1.1.1",
        server_gw: str = "192.168.9.11",
        client_gw: str = "192.168.9.1",
        runtime: int = 10000,
        bitrate: str = "25M"
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

        # in-memory buffers for each host’s iperf output (cumulative across runs)
        self._iperf_outputs = {host["ip"]: [] for host in self._hosts}

        # tracked PIDs (iperf and its descendants) per host
        self._iperf_pids = {host["ip"]: set() for host in self._hosts}
        self._pids_lock = threading.Lock()

        # per-run buffers (reset at the start of each _run_command for that host)
        # - outputs: raw iperf chunks from current run only
        # - pcts: extracted percentage values (as floats) from current run only
        self._current_run_outputs = {host["ip"]: [] for host in self._hosts}
        self._current_run_pcts = {host["ip"]: [] for host in self._hosts}

        self._active_rx = None

        # set switch mode to manual
        # self._snmp_set("1.3.6.1.4.1.27928.107.1.3.2.0", "i", 1)
        # set operation mode to single
        # self._snmp_set("1.3.6.1.4.1.27928.107.1.3.4.0", "i", 0)

    def initial_config(self):
        ...

    def switch_rx1(self):
        self._snmp_set(
            "1.3.6.1.4.1.27928.107.1.3.1.0", "i", 1
        )
        self._active_rx = 1

    def switch_rx2(self):
        self._snmp_set(
            "1.3.6.1.4.1.27928.107.1.3.1.0", "i", 2
        )
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
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.1.4.11.0", 2.0)
        elif self._active_rx == 2:
            raw = self._snmp_get_raw("1.3.6.1.4.1.27928.107.1.2.4.11.0", 2.0)
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

    def _run_command(self, ip: str, username: str, password: str, command: str):
        """
        Run a remote command over SSH, capture iperf output, track PIDs,
        and collect per-run values.

        Per-run collections (for this host only) are reset at the start:
        - self._current_run_outputs[ip]: list[str] of raw output chunks
        - self._current_run_pcts[ip]: list[float] of extracted percent values
        """
        # reset per-run lists for this host
        self._current_run_outputs[ip] = []
        self._current_run_pcts[ip] = []

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            print(f"[{ip}] Connecting…")
            ssh.connect(ip, username=username, password=password)
            print(f"[{ip}] Connected")

            chan = ssh.get_transport().open_session()
            chan.get_pty()
            chan.exec_command(command)

            # read until iperf quits
            while not chan.exit_status_ready():
                if chan.recv_ready():
                    out = chan.recv(1024).decode()
                    # cumulative store (across runs)
                    self._iperf_outputs[ip].append(out)
                    # per-run store (this run only)
                    self._current_run_outputs[ip].append(out)

                    # detect and store the iperf PID and its descendants
                    for m in self._pid_re.finditer(out):
                        parent_pid = int(m.group(1))
                        descendants = self._get_descendant_pids(ip, username, password, parent_pid)
                        with self._pids_lock:
                            self._iperf_pids[ip].add(parent_pid)
                            self._iperf_pids[ip].update(descendants)

                    # extract and keep percentage values for this run
                    for m in self._pct_re.finditer(out):
                        try:
                            pct_val = float(m.group(1))
                            self._current_run_pcts[ip].append(pct_val)
                        except ValueError:
                            pass

                    if ip == self._server_ip:
                        for m in self._pct_re.finditer(out):
                            print(m.group(1) + '%')
                time.sleep(0.1)

            # drain any leftover bytes
            while chan.recv_ready():
                out = chan.recv(1024).decode()
                self._iperf_outputs[ip].append(out)
                self._current_run_outputs[ip].append(out)

                for m in self._pid_re.finditer(out):
                    parent_pid = int(m.group(1))
                    descendants = self._get_descendant_pids(ip, username, password, parent_pid)
                    with self._pids_lock:
                        self._iperf_pids[ip].add(parent_pid)
                        self._iperf_pids[ip].update(descendants)

                for m in self._pct_re.finditer(out):
                    try:
                        pct_val = float(m.group(1))
                        self._current_run_pcts[ip].append(pct_val)
                    except ValueError:
                        pass

                if ip == self._server_ip:
                    for m in self._pct_re.finditer(out):
                        print(m.group(1) + '%')

        except Exception as e:
            print(f"[{ip}] Error: {e}")
        finally:
            ssh.close()
            print(f"[{ip}] Disconnected")

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

    def run_iperf(self):
        """
        Launches iperf on both server & client in parallel threads.
        Keeps all iperf output in memory without printing it.
        """
        # clear previous PID tracking
        with self._pids_lock:
            for ip in self._iperf_pids:
                self._iperf_pids[ip].clear()

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

    def term_iperf(self):
        """
        Send SIGTERM to all tracked iperf PIDs (including descendants) on both hosts.
        """
        for host in self._hosts:
            ip = host["ip"]
            with self._pids_lock:
                pids = sorted(self._iperf_pids.get(ip, set()))
            if not pids:
                continue
            pid_list = " ".join(str(p) for p in pids)
            self._remote_kill(ip, host["username"], host["password"], f"kill -TERM {pid_list} || true")

    def kill_iperf(self):
        """
        Send SIGKILL to all tracked iperf PIDs (including descendants) on both hosts.
        """
        for host in self._hosts:
            ip = host["ip"]
            with self._pids_lock:
                pids = sorted(self._iperf_pids.get(ip, set()))
            if not pids:
                continue
            pid_list = " ".join(str(p) for p in pids)
            self._remote_kill(ip, host["username"], host["password"], f"kill -KILL {pid_list} || true")

    def _remote_kill(self, ip: str, username: str, password: str, cmd: str):
        """
        Execute a kill command on the remote host.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(ip, username=username, password=password)
            client.exec_command(cmd)
        except Exception as e:
            print(f"[{ip}] kill error: {e}")
        finally:
            client.close()
