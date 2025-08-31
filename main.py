from snmpcore.hw6demod import HW6Demod
import time

if __name__ == "__main__":
    demo = HW6Demod(
        server_ip="192.168.115.11",
        client_ip="192.168.115.36",
        server_username= "user",
        server_password= "user",
        client_username= "user",
        client_password= "user",
        multicast="225.1.1.1",
        server_gw="192.168.9.11",
        client_gw="192.168.9.1",
        runtime=10000,
        bitrate="25M"
    )
    demo.switch_rx1()
    demo.set_freq(1200)
    demo.set_symrate(12)

    #demo.switch_rx1()
    #demo.set_freq(1200)
    #demo.set_symrate(12)

    #demo._snmp_set("1.3.6.1.4.1.27928.107.1.3.2.0", "i", 1)