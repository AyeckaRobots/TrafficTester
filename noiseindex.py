import time
import pandas as pd
import bisect
import random

from restcore import restmod
from snmpcore import novelsatdemod
from constants import *

class NoiseIndex:
    def __init__(self, csv_path):
        self.index = self._build_index(csv_path)
        self.mod = restmod.RestMod(RESTMOD_IP, ADMIN_USER, ADMIN_PASS)
        self.demod = novelsatdemod.NovelsatDemod()

    def _build_index(self, csv_path):
        df = pd.read_csv(csv_path)
        groups = {}
        for (f, r, p), sub in df.groupby(['frequency_mhz',
                                           'symbol_rate_msps',
                                           'power_dbm']):
            sorted_sub = sub.sort_values('esno_db')
            esnos = sorted_sub['esno_db'].tolist()
            noises = list(zip(sorted_sub['noise_hex'],
                              sorted_sub['noise_dec']))
            groups[(f, r, p)] = (esnos, noises)
        return groups

    def get_closest_noise(self, freq, symrate, power, target_esno):
        key = (freq, symrate, power)
        if key not in self.index:
            raise ValueError(f"No data for freq={freq}, symrate={symrate}, power={power}")

        esno_list, noise_list = self.index[key]
        i = bisect.bisect_left(esno_list, target_esno)

        if i == 0:
            best_idx = 0
        elif i >= len(esno_list):
            best_idx = len(esno_list) - 1
        else:
            best_idx = i  # next‐higher ESNO

        closest_esno = esno_list[best_idx]
        noise_hex, noise_dec = noise_list[best_idx]

        return {
            'requested_esno': target_esno,
            'closest_esno': closest_esno,
            'noise_hex': noise_hex,
            'noise_dec': int(noise_dec)
        }



    def adjust_noise(self, freq, symrate, power, target_esno, buffer=0.3):
        """
        Uses get_noise to measure the closest ESNO, and if that ESNO
        is more than `buffer` dB above the requested_esno, applies
        extra noise gradually until within the buffer.
        """

        print(f"⏳ Initializing: freq={freq} MHz, symrate={symrate} Msps, power={power} dBm, target ESNO={target_esno} dB")

        # initial measurement
        result = self.get_closest_noise(freq, symrate, power, target_esno)
        requested = result['requested_esno']
        closest = result['closest_esno']
        noise_dec = result['noise_dec']
        noise_hex = result['noise_hex']

        self.mod.set_all(frequency=freq, symrate=symrate, power=power, noise=noise_dec)
        self.demod.set_freq(freq)
        self.demod.set_symrate(symrate)
        time.sleep(3)

        closest = self.demod.measure_esno()
        print(f"🔍 Initial ESNO={closest} dB (noise={noise_hex}/{noise_dec})")

        # loop until the measured ESNO is within buffer dB above requested
        while closest > requested + buffer:
            noise_dec += 1
            self.mod.set_all(noise=noise_dec)
            time.sleep(5)

            closest = self.demod.measure_esno()
            print(f"➕ Noise++ => dec={noise_dec}, measured ESNO={closest:.2f} dB")

        print(f"✅ Final ESNO={closest:.2f} dB (target was {requested} dB +{buffer})")

        noise_hex = hex(noise_dec)

        return {
            'requested_esno': requested,
            'closest_esno': closest,
            'noise_hex': noise_hex,
            'noise_dec': noise_dec
        }

def main():
    csv_path = "sweep_results.csv"
    noise_index = NoiseIndex(csv_path)

    freqs = [950, 1050, 1150, 1250, 1350]
    symbol_rates = [1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56]
    powers = [-30, -25, -20, -15, -10, -5]

    freq = random.choice(freqs)
    symrate = random.choice(symbol_rates)
    power = random.choice(powers)
    esno = round(random.uniform(5, 15), 2)

    print(f"freq={freq} MHz, symrate={symrate} Msps, power={power} dBm, target ESNO={esno} dB")

    result = noise_index.get_closest_noise(freq, symrate, power, esno)

    print(result)

if __name__ == "__main__":
    main()
