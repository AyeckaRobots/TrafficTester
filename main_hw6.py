# --- FILE: main_hw6.py ---
"""
Entrypoint for HW6-based tests.
Usage: python main_hw6.py
"""
from traffictester import TrafficTester
from demod.adapters import HW6DemodAdapter
from snmpcore import hw6demod
from utils.helpers import safe_call

from constants import *

def main():
    # Example parameters — replace with real values or make these CLI args if you like.
    freq = 1200.0
    symrate = 12.0
    power = -30.0
    pls = 61

    # Create HW6 DUT and ensure RX1 is selected (safe_call swallows errors and logs them)
    dut = HW6DemodAdapter(hw6demod.HW6Demod())
    safe_call(dut, "switch_rx1")

    tester = TrafficTester(freq, symrate, power, pls, dut)
    tester.execute_test()

if __name__ == "__main__":
    main()