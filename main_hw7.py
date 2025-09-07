# --- FILE: main_hw7.py ---
"""
Entrypoint for HW7 / REST-based tests.
Usage: python main_hw7.py
"""
from traffictester import TrafficTester
from demod.adapters import RestDemodAdapter
from restcore import restdemod
from utils.helpers import safe_call

from constants import *

def main():
    # Example parameters — replace with real values or make these CLI args if you like.
    freq = 1200.0
    symrate = 12.0
    power = -30.0
    pls = 61

    # REST demodulator typically needs the DEMOD_IP constant defined in constants.py
    dut = RestDemodAdapter(restdemod.RestDemod(DEMOD_IP, ADMIN_USER, ADMIN_PASS))
    safe_call(dut, "switch_rx1")

    tester = TrafficTester(freq, symrate, power, pls, dut)
    tester.execute_test()

if __name__ == "__main__":
    main()