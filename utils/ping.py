import socket
import threading

from constants import *

class CheckAlive:
    def __init__(self):
        pass

    def _is_host_up(self, host, tcp_ports=None, udp_ports=None):
        """
        Strict host check: requires a real TCP connect or UDP reply.
        Runs TCP and UDP checks in parallel, returns True if ANY succeed.
        """
        tcp_ports = tcp_ports or []
        udp_ports = udp_ports or []
        results = []
        threads = []

        def check_tcp(port):
            try:
                with socket.create_connection((host, port), timeout=IS_ALIVE_TIMEOUT):
                    results.append(True)
            except Exception:
                pass  # no connect → not alive on this port

        def check_udp(port):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(IS_ALIVE_TIMEOUT)
                try:
                    s.sendto(b"", (host, port))
                    try:
                        s.recvfrom(1024)  # must reply to be alive
                        results.append(True)
                    except socket.timeout:
                        pass  # no reply → not alive
                finally:
                    s.close()
            except Exception:
                pass

        for p in tcp_ports:
            t = threading.Thread(target=check_tcp, args=(p,), daemon=True)
            threads.append(t)
            t.start()

        for p in udp_ports:
            t = threading.Thread(target=check_udp, args=(p,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(IS_ALIVE_TIMEOUT + 0.5)

        return any(results)


    def check_all_hosts(self):
        """
        Check if MOD_IP, DEMOD_IP, IPERF_CLIENT_IP, and IPERF_SERVER_IP are up.
        Runs all checks in parallel and returns a dict {ip: True/False}.
        Completes in ~5 seconds max.
        """
        hosts = [MOD_IP, DEMOD_IP, IPERF_CLIENT_IP, IPERF_SERVER_IP]
        tcp_ports = [22, 23, 88]
        udp_ports = [161, 162]
        results = {}
        threads = []

        def worker(ip):
            results[ip] = self._is_host_up(ip, tcp_ports, udp_ports)

        # Launch all host checks in parallel
        for ip in hosts:
            t = threading.Thread(target=worker, args=(ip,), daemon=True)
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join(timeout=IS_ALIVE_TIMEOUT + 2)

        return results