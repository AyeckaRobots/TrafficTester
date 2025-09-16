"""
DHCP server for giving the DUT an IP address.

RUN ONLY AS ROOT (with sudo)
"""
import socket
import struct
import time
import select

# for interface IP lookup
import fcntl
import errno

class DHCPServer:
    def __init__(self,
                 offered_ip="192.168.10.200",
                 subnet_mask="255.255.255.0",
                 lease_seconds=86400,
                 server_ip=None,           # if None, we'll try to auto-fill from iface
                 listen_timeout=60,
                 iface=None):              # e.g. "eth1"
        self.offered_ip = offered_ip
        self.subnet_mask = subnet_mask
        self.lease_seconds = lease_seconds
        self.server_ip = server_ip
        self.listen_timeout = listen_timeout
        self.server_port = 67
        self.client_port = 68
        self.iface = iface

    def _get_ip_for_iface(self, iface):
        """Return IPv4 address assigned to iface (Linux). Raises on failure."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            ifname = iface[:15].encode('utf-8')
            # SIOCGIFADDR = 0x8915
            res = fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', ifname))
            ip = socket.inet_ntoa(res[20:24])
            return ip
        finally:
            s.close()

    def _try_bind_to_device(self, sock, iface):
        """Try SO_BINDTODEVICE. Returns True if successful, False otherwise."""
        # SO_BINDTODEVICE is Linux-specific; some Python builds expose it as socket.SO_BINDTODEVICE.
        optname = getattr(socket, 'SO_BINDTODEVICE', 25)  # 25 is typical value on Linux
        try:
            sock.setsockopt(socket.SOL_SOCKET, optname, iface.encode('utf-8') + b'\x00')
            return True
        except PermissionError:
            # need root
            raise
        except OSError as e:
            # e.g. Operation not supported
            return False

    def _find_magic_cookie(self, data):
        cookie = b'\x63\x82\x53\x63'
        idx = data.find(cookie)
        if idx >= 0:
            return idx + 4
        return 240

    def _parse_options(self, data):
        opts = {}
        i = self._find_magic_cookie(data)
        length = len(data)
        while i < length:
            code = data[i]
            i += 1
            if code == 0:
                continue
            if code == 255:
                break
            if i >= length:
                break
            optlen = data[i]
            i += 1
            val = data[i:i + optlen]
            i += optlen
            opts[code] = val
        return opts

    def _build_bootp_header(self, op, htype, hlen, hops, xid, secs, flags,
                           ciaddr, yiaddr, siaddr, giaddr, chaddr):
        header = struct.pack('!BBBBIHH',
                             op, htype, hlen, hops, xid, secs, flags)
        header += socket.inet_aton(ciaddr)
        header += socket.inet_aton(yiaddr)
        header += socket.inet_aton(siaddr)
        header += socket.inet_aton(giaddr)
        ch = chaddr
        if len(ch) > 16:
            ch = ch[:16]
        ch = ch + b'\x00' * (16 - len(ch))
        header += ch
        header += b'\x00' * 64  # sname
        header += b'\x00' * 128  # file
        return header

    def _build_options(self, msg_type_code):
        # msg_type_code: 2=OFFER, 5=ACK
        opts = b''
        opts += struct.pack('BB', 53, 1) + struct.pack('B', msg_type_code)
        # Subnet mask (1)
        opts += struct.pack('BB', 1, 4) + socket.inet_aton(self.subnet_mask)
        # Router (3) -> server IP (common for tiny networks)
        opts += struct.pack('BB', 3, 4) + socket.inet_aton(self.server_ip)
        # Server identifier (54)
        opts += struct.pack('BB', 54, 4) + socket.inet_aton(self.server_ip)
        # Lease time (51)
        opts += struct.pack('BBI', 51, 4, int(self.lease_seconds))
        # End
        opts += struct.pack('B', 255)
        return b'\x63\x82\x53\x63' + opts

    def _build_offer_or_ack(self, req, msg_type_code):
        xid = req['xid']
        chaddr = req['chaddr']
        flags = req.get('flags', 0)
        header = self._build_bootp_header(
            op=2,  # BOOTREPLY
            htype=1,
            hlen=6,
            hops=0,
            xid=xid,
            secs=0,
            flags=flags,
            ciaddr="0.0.0.0",
            yiaddr=self.offered_ip,
            siaddr=self.server_ip,
            giaddr="0.0.0.0",
            chaddr=chaddr
        )
        opts = self._build_options(msg_type_code)
        return header + opts

    def _parse_dhcp_packet(self, data):
        if len(data) < 240:
            raise ValueError("Packet too short to be DHCP")
        op, htype, hlen, hops = struct.unpack('!BBBB', data[0:4])
        xid = struct.unpack('!I', data[4:8])[0]
        secs, flags = struct.unpack('!HH', data[8:12])
        ciaddr = socket.inet_ntoa(data[12:16])
        yiaddr = socket.inet_ntoa(data[16:20])
        siaddr = socket.inet_ntoa(data[20:24])
        giaddr = socket.inet_ntoa(data[24:28])
        chaddr = data[28:28 + 16]
        mac = chaddr[:6]
        opts = self._parse_options(data)
        msg_type = None
        if 53 in opts and len(opts[53]) >= 1:
            msg_type = opts[53][0]
        return {
            'op': op,
            'htype': htype,
            'hlen': hlen,
            'hops': hops,
            'xid': xid,
            'secs': secs,
            'flags': flags,
            'ciaddr': ciaddr,
            'yiaddr': yiaddr,
            'siaddr': siaddr,
            'giaddr': giaddr,
            'chaddr': mac,
            'options': opts,
            'msg_type': msg_type
        }

    def serve(self):
        print(f"[+] Starting SimpleDHCPServer: offering {self.offered_ip} (server id {self.server_ip or '(auto)'})")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # If iface provided, try to force socket to use it
        bound_to_device = False
        if self.iface:
            try:
                bound_to_device = self._try_bind_to_device(s, self.iface)
                if bound_to_device:
                    print(f"[*] Socket SO_BINDTODEVICE -> bound to device {self.iface}")
                else:
                    # fallback: lookup IP and bind to that IP
                    try:
                        iface_ip = self._get_ip_for_iface(self.iface)
                        print(f"[*] SO_BINDTODEVICE not available/supported; binding to iface IP {iface_ip}")
                        s.bind((iface_ip, self.server_port))
                    except Exception as e:
                        print(f"[-] Failed to bind to iface IP for {self.iface}: {e}")
                        s.close()
                        return False
            except PermissionError:
                print("[-] SO_BINDTODEVICE requires root. Run with sudo.")
                s.close()
                return False
        else:
            # no iface specified: default to bind all addresses
            s.bind(('', self.server_port))

        # If SO_BINDTODEVICE succeeded, still bind to port on all addrs (device forces interface)
        if bound_to_device:
            try:
                s.bind(('', self.server_port))
            except OSError as e:
                # If bind fails because address in use, it's probably already bound; that's okay.
                if e.errno != errno.EADDRINUSE:
                    print(f"[-] Failed to bind socket after SO_BINDTODEVICE: {e}")
                    s.close()
                    return False

        # If server_ip wasn't set, try auto-fill from iface (if provided)
        if not self.server_ip and self.iface:
            try:
                self.server_ip = self._get_ip_for_iface(self.iface)
                print(f"[*] Auto-filled server_ip from iface {self.iface}: {self.server_ip}")
            except Exception:
                # leave server_ip as None — but options building requires a valid IP,
                # so fall back to the offered_ip's network if possible:
                self.server_ip = '0.0.0.0'
                print("[!] Couldn't auto-fill server_ip; using 0.0.0.0 in options (not ideal)")

        s.setblocking(False)

        start = time.time()
        print(f"[+] Waiting for DHCPDISCOVER (timeout {self.listen_timeout}s)...")
        while True:
            if time.time() - start > self.listen_timeout:
                print("[-] Timeout waiting for DHCPDISCOVER")
                s.close()
                return False
            r, _, _ = select.select([s], [], [], 1)
            if not r:
                continue
            data, addr = s.recvfrom(2048)
            try:
                info = self._parse_dhcp_packet(data)
            except Exception:
                continue
            if info.get('msg_type') == 1:  # DHCPDISCOVER
                src_ip = addr[0]
                print(f"[+] Received DHCPDISCOVER from {src_ip} xid={hex(info['xid'])} ciaddr={info['ciaddr']}")
                # If client already claims the offered IP (ciaddr or packet source), send ACK immediately.
                client_claims_offered = (info['ciaddr'] == self.offered_ip) or (src_ip == self.offered_ip)
                if client_claims_offered:
                    print("[*] Client claims the offered IP -> sending DHCPACK immediately (no OFFER).")
                    ack = self._build_offer_or_ack(info, msg_type_code=5)  # ACK
                    # Send unicast to client IP if broadcast flag is not set; otherwise broadcast.
                    if info.get('flags', 0) & 0x8000:
                        dest = ('<broadcast>', self.client_port)
                        print("[*] Broadcast flag set in request -> broadcasting ACK")
                    else:
                        dest = (src_ip, self.client_port)
                        print(f"[*] Sending ACK unicast to {dest[0]}:{dest[1]}")
                    try:
                        s.sendto(ack, dest)
                        print(f"[+] Sent DHCPACK for {self.offered_ip} to {dest}")
                        s.close()
                        return True
                    except Exception as e:
                        print(f"[-] Failed to send ACK: {e}")
                        s.close()
                        return False

                # Otherwise follow normal simple OFFER -> wait REQUEST -> ACK flow
                print("[*] Client did not claim offered IP; sending DHCPOFFER and waiting for DHCPREQUEST.")
                offer = self._build_offer_or_ack(info, msg_type_code=2)
                s.sendto(offer, ('<broadcast>', self.client_port))
                print(f"[+] Sent DHCPOFFER for {self.offered_ip} to broadcast")

                req_start = time.time()
                req_timeout = 15
                while True:
                    if time.time() - req_start > req_timeout:
                        print("[-] Timeout waiting for DHCPREQUEST")
                        s.close()
                        return False
                    r2, _, _ = select.select([s], [], [], 1)
                    if not r2:
                        continue
                    data2, addr2 = s.recvfrom(2048)
                    try:
                        info2 = self._parse_dhcp_packet(data2)
                    except Exception:
                        continue
                    if info2.get('msg_type') == 3:  # DHCPREQUEST
                        # basic sanity checks
                        req_mac = info2['chaddr']
                        if req_mac != info['chaddr']:
                            continue
                        requested_ip = None
                        if 50 in info2['options']:
                            try:
                                requested_ip = socket.inet_ntoa(info2['options'][50])
                            except Exception:
                                requested_ip = None
                        if requested_ip and requested_ip != self.offered_ip:
                            print(f"[-] Client requested different IP {requested_ip}, ignoring")
                            continue
                        print(f"[+] Received DHCPREQUEST from {addr2[0]} xid={hex(info2['xid'])}")
                        ack = self._build_offer_or_ack(info2, msg_type_code=5)
                        # decide unicast vs broadcast based on flags
                        if info2.get('flags', 0) & 0x8000:
                            dest = ('<broadcast>', self.client_port)
                        else:
                            dest = (addr2[0], self.client_port)
                        s.sendto(ack, dest)
                        print(f"[+] Sent DHCPACK for {self.offered_ip} to {dest}")
                        s.close()
                        return True

        # unreachable

if __name__ == "__main__":
    server = DHCPServer(
        offered_ip="192.168.10.200",
        subnet_mask="255.255.255.0",
        lease_seconds=86400,
        server_ip=None,   # will auto-fill from iface
        listen_timeout=6000,
        iface="eth1"
    )
    ok = server.serve()
    if ok:
        print("[+] DHCP transaction completed successfully. Exiting.")
    else:
        print("[-] DHCP transaction failed or timed out. Exiting.")

