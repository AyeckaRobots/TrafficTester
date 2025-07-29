import json
import time

from restcore import restmod, restdemod
import novelsatdemod
import findnoise
from constants import RESTMOD_IP, RESTDEMOD_IP, ADMIN_USER, ADMIN_PASS

class NoiseTester:
    def __init__(self, freq, symrate, power, pls, target_esno=None):
        self.freq = freq
        self.symrate = symrate
        self.power = power
        self.pls = pls

        # load PLS codes once
        self._plscodes = json.load(open("plscodes.json"))
        self.target_esno = target_esno if target_esno is not None else self._get_min_esno()

        self.mod = restmod.RestMod(RESTMOD_IP, ADMIN_USER, ADMIN_PASS)
        self.dut = restdemod.RestDemod(RESTDEMOD_IP, ADMIN_USER, ADMIN_PASS)
        self.nsdemod = novelsatdemod.NovelsatDemod()
        self.noise_index = findnoise.NoiseIndex("sweep_results.csv")

    def _get_min_esno(self):
        for entry in self._plscodes:
            if entry["plscode"] == self.pls:
                return entry["min_esno"]
        raise ValueError(f"No entry for PLS {self.pls}")

    def execute_test(self, intervals):
        self.mod.set_all(self.freq, self.symrate, self.power, pls=self.pls)
        self.dut.set_all(self.freq, self.symrate)

        print("Waiting for demodulator to lock...")
        while not self.dut.is_locked():
            time.sleep(2)

        print("Demodulator locked.")

        noise = self.noise_index.adjust_noise(
            self.freq, self.symrate, self.power, self.target_esno
        )["noise_dec"]
        print(f"Calibrated noise: {noise} (0x{noise:x})")

        self._monitor(intervals)
        return noise


    def _monitor(self, intervals, retry=2):
        print(f"🔍 Syncing ESNO (target ≥{self.target_esno} dB)…")
        self._sync(retry)
        print("✅ ESNO synced. Starting traffic checks.")
        self.dut.reset_advanced_status()

        for idx, sec in enumerate(intervals, start=1):
            time.sleep(sec)
            print(f"\nInterval #{idx}:")

            # 1. Check lock status
            print(" ⏳ Waiting for demodulator to lock…")
            while not self.dut.is_locked():
                time.sleep(1)
            print(" 🔒 Demodulator locked.")

            # 2. Check ESNO alignment
            print(" 🔄 Verifying ESNO alignment…")
            self._sync(retry)
            print(" ✅ ESNO aligned.")

            # 3. Packet traffic
            traffic = self.dut.get_packet_traffic()
            print(f" 📈 Traffic #{idx}: {traffic}")


    def _sync(self, retry):
        while True:
            rest_esno = self.dut.get_esno()
            ns_esno = self.nsdemod.measure_esno()
            delta = abs(rest_esno - ns_esno)

            print(f"🔧 REST ESNO: {rest_esno:.2f} dB, "
                f"Novelsat ESNO: {ns_esno:.2f} dB, "
                f"Δ: {delta:.2f} dB")

            if delta <= 0.5 and min(rest_esno, ns_esno) >= self.target_esno:
                return

            time.sleep(retry)


if __name__ == "__main__":
    tester = NoiseTester(freq=1050, symrate=6, power=-5, pls=101)
    tester.execute_test([10, 15, 20, 20])
