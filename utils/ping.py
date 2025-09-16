import socket
import threading

from constants import IS_ALIVE_TIMEOUT, MOD_IP, DUT_IP, IPERF_CLIENT_IP, IPERF_SERVER_IP

class CheckAlive:
    def __init__(self):
        pass

    def is_host_up(self, host, tcp_ports=None, udp_ports=None, timeout=None):
        """
        Strict host check: requires a real TCP connect or UDP reply.
        Runs TCP and UDP checks in parallel, returns True if ANY succeed.

        Args:
            host (str): IP or hostname to check.
            tcp_ports (list[int], optional): TCP ports to test.
            udp_ports (list[int], optional): UDP ports to test.
            timeout (float, optional): Seconds to wait per socket operation.
                                        Defaults to IS_ALIVE_TIMEOUT.
        Returns:
            bool: True if any probe succeeds.
        """
        timeout = timeout if timeout is not None else IS_ALIVE_TIMEOUT
        tcp_ports = tcp_ports or []
        udp_ports = udp_ports or []
        results = []
        threads = []

        def check_tcp(port):
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    results.append(True)
            except Exception:
                pass

        def check_udp(port):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(timeout)
                try:
                    s.sendto(b"", (host, port))
                    try:
                        s.recvfrom(1024)
                        results.append(True)
                    except socket.timeout:
                        pass
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
            t.join(timeout + 0.5)

        return any(results)

    def check_all_hosts(self, timeout=None):
        """
        Check if MOD_IP, DUT_IP, IPERF_CLIENT_IP, and IPERF_SERVER_IP are up.
        Runs all checks in parallel and returns a dict {ip: True/False}.
        You can override the per-host timeout here as well.
        """
        hosts = [MOD_IP, DUT_IP, IPERF_CLIENT_IP, IPERF_SERVER_IP]
        tcp_ports = [22, 23, 88]
        udp_ports = [161, 162]
        results = {}
        threads = []

        def worker(ip):
            results[ip] = self.is_host_up(ip, tcp_ports, udp_ports, timeout=timeout)

        for ip in hosts:
            t = threading.Thread(target=worker, args=(ip,), daemon=True)
            threads.append(t)
            t.start()

        # Allow a bit more time for all threads to finish
        join_timeout = (timeout if timeout is not None else IS_ALIVE_TIMEOUT) + 2
        for t in threads:
            t.join(join_timeout)

        return results
