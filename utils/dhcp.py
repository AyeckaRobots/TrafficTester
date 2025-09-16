"""
A minimal DHCP server using scapy. Only used and tested on HW6.
"""

from scapy.all import sniff, sendp, Ether, IP, UDP, BOOTP, DHCP, conf
import socket, struct, fcntl, time

SIOCGIFADDR = 0x8915  # ioctl to get iface IP on Linux

class DHCPServer:
    def __init__(self,
                 offered_ip="192.168.10.200",
                 subnet_mask="255.255.255.0",
                 lease_seconds=86400,
                 server_ip=None,      # if None, auto-fill from iface
                 iface="eth1",
                 listen_timeout=60,
                 request_timeout=15,
                 sniff_timeout=1,      # timeout (seconds) passed to each sniff() call
                 sniff_count=1):       # count passed to sniff() (defaults to 1 like before)
        self.offered_ip = offered_ip
        self.subnet_mask = subnet_mask
        self.lease_seconds = int(lease_seconds)
        self.server_ip = server_ip
        self.iface = iface
        self.listen_timeout = listen_timeout
        self.request_timeout = request_timeout
        self.sniff_timeout = sniff_timeout
        self.sniff_count = sniff_count

    # ----------------- helpers -----------------
    def _get_ip_for_iface(self, iface):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ifr = struct.pack('256s', iface[:15].encode())
            res = fcntl.ioctl(s.fileno(), SIOCGIFADDR, ifr)
            return socket.inet_ntoa(res[20:24])
        finally:
            s.close()

    def _dhcp_msg_type(self, pkt):
        if DHCP not in pkt:
            return None
        for opt in pkt[DHCP].options:
            if isinstance(opt, tuple) and opt[0] == "message-type":
                return opt[1]
        return None

    def _requested_ip(self, pkt):
        if DHCP not in pkt:
            return None
        for opt in pkt[DHCP].options:
            if isinstance(opt, tuple) and opt[0] == "requested_addr":
                return opt[1]
        return None

    def _make_pkt(self, msg_type_str, yiaddr, xid, chaddr):
        opts = [
            ("message-type", msg_type_str),
            ("subnet_mask", self.subnet_mask),
            ("router", self.server_ip),
            ("server_id", self.server_ip),
            ("lease_time", self.lease_seconds),
            "end"
        ]
        pkt = ( Ether(dst="ff:ff:ff:ff:ff:ff") /
                IP(src=self.server_ip or "0.0.0.0", dst="255.255.255.255") /
                UDP(sport=67, dport=68) /
                BOOTP(op=2, yiaddr=yiaddr, siaddr=self.server_ip or "0.0.0.0", chaddr=chaddr, xid=xid) /
                DHCP(options=opts) )
        return pkt

    def _send(self, pkt, dest_mac=None, dest_ip=None, broadcast=False):
        if broadcast or not dest_ip or dest_ip == "0.0.0.0":
            sendp(pkt, iface=self.iface, verbose=False)
        else:
            pkt_uc = pkt.copy()
            pkt_uc[Ether].dst = dest_mac
            pkt_uc[IP].dst = dest_ip
            sendp(pkt_uc, iface=self.iface, verbose=False)

    # ----------------- main serve -----------------
    def serve(self):
        conf.iface = self.iface

        # auto-fill server_ip if needed
        if not self.server_ip:
            try:
                self.server_ip = self._get_ip_for_iface(self.iface)
                print(f"[*] Auto-filled server_ip from {self.iface}: {self.server_ip}")
            except Exception as e:
                self.server_ip = "0.0.0.0"
                print(f"[!] Could not auto-fill server_ip for {self.iface}: {e}; using 0.0.0.0")

        print(f"[+] DHCPServer listening on iface {self.iface}; offering {self.offered_ip} (server id {self.server_ip})")
        start = time.time()

        # wait for DISCOVER
        while time.time() - start < self.listen_timeout:
            pkts = sniff(filter="udp and (port 67 or port 68)",
                         iface=self.iface,
                         timeout=self.sniff_timeout,
                         count=self.sniff_count,
                         store=1)
            if not pkts:
                continue
            pkt = pkts[0]
            if self._dhcp_msg_type(pkt) != 1:  # only DISCOVER
                continue

            xid = pkt[BOOTP].xid
            chaddr = pkt[BOOTP].chaddr
            client_mac = pkt[Ether].src
            ciaddr = pkt[BOOTP].ciaddr
            src_ip = pkt[IP].src if IP in pkt else "0.0.0.0"
            flags = pkt[BOOTP].flags if BOOTP in pkt else 0
            wants_broadcast = bool(flags & 0x8000)

            print(f"[+] DISCOVER from {client_mac} xid={hex(xid)} ciaddr={ciaddr} src={src_ip}")

            # immediate ACK if client already claims offered IP
            if ciaddr == self.offered_ip or src_ip == self.offered_ip:
                print("[*] Client claims offered IP -> sending immediate ACK")
                ack = self._make_pkt("ack", self.offered_ip, xid, chaddr)
                if wants_broadcast or src_ip == "0.0.0.0":
                    self._send(ack, broadcast=True)
                else:
                    self._send(ack, dest_mac=client_mac, dest_ip=src_ip, broadcast=False)
                print("[+] Sent ACK; done")
                return True

            # otherwise send OFFER (broadcast)
            offer = self._make_pkt("offer", self.offered_ip, xid, chaddr)
            print("[*] Sending DHCPOFFER (broadcast)")
            self._send(offer, broadcast=True)
            print(f"[+] Sent DHCPOFFER for {self.offered_ip}")

            # wait for REQUEST from same MAC
            req_start = time.time()
            while time.time() - req_start < self.request_timeout:
                pkts2 = sniff(filter="udp and (port 67 or port 68)",
                              iface=self.iface,
                              timeout=self.sniff_timeout,
                              count=self.sniff_count,
                              store=1)
                if not pkts2:
                    continue
                p2 = pkts2[0]
                if self._dhcp_msg_type(p2) != 3:  # REQUEST
                    continue
                if p2[Ether].src != client_mac:
                    continue
                req_ip = self._requested_ip(p2)
                if req_ip and req_ip != self.offered_ip:
                    print(f"[-] Client requested different IP {req_ip}, ignoring")
                    continue

                xid2 = p2[BOOTP].xid
                flags2 = p2[BOOTP].flags if BOOTP in p2 else 0
                wants_bcast2 = bool(flags2 & 0x8000)
                src_ip2 = p2[IP].src if IP in p2 else "0.0.0.0"

                ack = self._make_pkt("ack", self.offered_ip, xid2, p2[BOOTP].chaddr)
                if wants_bcast2 or src_ip2 == "0.0.0.0":
                    self._send(ack, broadcast=True)
                else:
                    self._send(ack, dest_mac=client_mac, dest_ip=src_ip2, broadcast=False)
                print("[+] Sent ACK; done")
                return True

            print("[-] Timed out waiting for REQUEST after OFFER; continuing to listen for DISCOVER")

        print(f"[-] Timed out waiting for DISCOVER (waited {self.listen_timeout}s). Exiting.")
        return False

# ----------------- example usage -----------------
if __name__ == "__main__":
    # Example: make the per-sniff timeout explicit (defaults maintain previous behavior)
    srv = DHCPServer(iface="eth1",
                     offered_ip="192.168.10.200",
                     listen_timeout=60,
                     request_timeout=15,
                     sniff_timeout=1,
                     sniff_count=1)
    ok = srv.serve()
    print("[+] Success" if ok else "[-] Failure")
