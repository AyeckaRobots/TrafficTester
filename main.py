from snmpcore.hw6demod import HW6Demod
from snmpcore import base
from demod import adapters
from restcore import restmod
import time

if __name__ == "__main__":
    """demod = HW6Demod(
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

    adapter = adapters.HW6DemodAdapter(demod)
    print(adapter.get_general_info())"""

    mod = restmod.RestMod("192.168.15.132", "admin", "admin")
    mod.set_data_pls(61)