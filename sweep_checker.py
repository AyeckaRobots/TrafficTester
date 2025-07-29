import os
import csv
import time
import logging
import traceback

from restcore import restmod
import novelsatdemod
from constants import RESTMOD_IP, ADMIN_USER, ADMIN_PASS


class SweepRunner:
    TOKEN_REFRESH_INTERVAL = 300  # seconds
    BASE_NOISE = 0x1C000

    def __init__(
        self,
        output_csv="sweep_results.csv",
        log_file="sweep_errors.log",
    ):
        self.output_csv = output_csv
        self.log_file = log_file

        # Sweep steps
        self.freq_step  = 100
        self.symb_step  = 5
        self.power_step = 5
        self.noise_step = 4

        self.mod       = None
        self.demod     = None
        self.last_params = {}
        self.resume      = False
        self.token_ts    = 0

        self._setup_logging()

    def _setup_logging(self):
        logging.basicConfig(
            filename=self.log_file,
            level=logging.ERROR,
            format="%(asctime)s %(levelname)s %(message)s"
        )
        self.logger = logging.getLogger()

    def _refresh_token_if_needed(self):
        now = time.time()
        if now - self.token_ts > self.TOKEN_REFRESH_INTERVAL:
            try:
                # Uses BaseRestClient.refresh_token under the hood
                self.mod.refresh_token(ADMIN_USER, ADMIN_PASS)
                self.token_ts = now
                print(f"🔄 Modulator token refreshed at {time.strftime('%X')}")
            except Exception:
                self.logger.error("Failed to refresh modulator token", exc_info=True)

    def initialize_hardware(self) -> bool:
        try:
            # Pass IP, user & pass into the constructor
            self.mod   = restmod.RestMod(RESTMOD_IP, ADMIN_USER, ADMIN_PASS)
            self.demod = novelsatdemod.NovelsatDemod()
            self.token_ts = time.time()
            print(f"\n🔐 Authenticated with modulator at {RESTMOD_IP}.\n")
            return True
        except Exception:
            self.logger.error("Initialization failed", exc_info=True)
            return False

    def detect_resume(self):
        if not os.path.exists(self.output_csv) or os.stat(self.output_csv).st_size == 0:
            return

        with open(self.output_csv, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return

        last = rows[-1]
        self.last_params = {
            "freq":  int(last["frequency_mhz"]),
            "symb":  int(last["symbol_rate_msps"]),
            "power": int(last["power_dbm"]),
            "noise": int(last["noise_dec"]),
        }
        self.resume = True

    def run(self):
        self.detect_resume()

        with open(self.output_csv, "a", newline="") as csvfile:
            fieldnames = [
                "frequency_mhz", "symbol_rate_msps",
                "power_dbm", "noise_hex", "noise_dec",
                "locked", "esno_db"
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if os.stat(self.output_csv).st_size == 0:
                writer.writeheader()

            if not self.initialize_hardware():
                return

            for freq in range(950, 2150, self.freq_step):
                if self.resume and freq < self.last_params["freq"]:
                    continue
                at_freq_resume = self.resume and freq == self.last_params["freq"]

                print(f"📡 Tuning frequency: {freq} MHz")
                self._refresh_token_if_needed()
                try:
                    self.mod.set_freq(freq)
                    self.demod.set_freq(freq)
                except Exception:
                    self.logger.error(f"Failed to set freq={freq}", exc_info=True)
                    continue

                for symb in range(1, 60, self.symb_step):
                    if at_freq_resume and symb < self.last_params["symb"]:
                        continue
                    at_symb_resume = at_freq_resume and symb == self.last_params["symb"]

                    print(f"🌀 Setting symbol rate: {symb} MSps")
                    self._refresh_token_if_needed()
                    try:
                        self.mod.set_symrate(symb)
                        self.demod.set_symrate(symb)
                    except Exception:
                        self.logger.error(
                            f"Failed to set symrate={symb} at freq={freq}",
                            exc_info=True
                        )
                        continue

                    for pwr in range(-30, 0, self.power_step):
                        if at_symb_resume and pwr < self.last_params["power"]:
                            continue
                        # only skip power once
                        at_symb_resume = False

                        print(f"⚡ Applying power level: {pwr} dBm")
                        self._refresh_token_if_needed()
                        try:
                            self.mod.set_power(pwr)
                            time.sleep(2)
                        except Exception:
                            self.logger.error(
                                f"Failed to set power={pwr} at f={freq}, s={symb}",
                                exc_info=True
                            )
                            continue

                        self._sweep_noise(freq, symb, pwr, writer)

    def _sweep_noise(self, freq, symb, pwr, writer):
        lock_fail = esno_na = 0
        max_fail = 3

        resume_noise = self.last_params.get("noise") if self.resume else None

        for offset in range(0, 0x1000, self.noise_step):
            noise_val = self.BASE_NOISE + offset
            if resume_noise is not None and noise_val <= resume_noise:
                continue
            # resume only applies to the first matching run
            self.resume = False

            self._refresh_token_if_needed()
            try:
                self.mod.set_noise(noise_val)
            except Exception:
                self.logger.error(
                    f"Failed to write noise={noise_val:#06X} at f={freq}, s={symb}, p={pwr}",
                    exc_info=True
                )
                break

            time.sleep(5)  # let hardware settle

            try:
                locked = self.demod.is_locked()
                esno = self.demod.measure_esno() if locked else None
            except Exception:
                self.logger.error(
                    f"Demod measurement error at f={freq}, s={symb}, p={pwr}, n={noise_val:#06X}",
                    exc_info=True
                )
                break

            if not locked:
                lock_fail += 1
            else:
                lock_fail = 0

            if esno is None:
                esno_na += 1
            else:
                esno_na = 0

            if (esno is not None and esno < -2.2) or lock_fail >= max_fail or esno_na >= max_fail:
                print("⚠️ Too many failures, moving on")
                break

            print(
                f"✅ LOCKED f={freq}MHz s={symb}MSps p={pwr}dBm "
                f"n={noise_val:#06X} ESNO={esno:.2f}dB"
            )

            writer.writerow({
                "frequency_mhz":    freq,
                "symbol_rate_msps": symb,
                "power_dbm":        pwr,
                "noise_hex":        f"{noise_val:#06X}",
                "noise_dec":        noise_val,
                "locked":           locked,
                "esno_db":          esno
            })

            txt_line = (
                f"{freq},{symb},{pwr},"
                f"{noise_val:#06X},{noise_val},"
                f"{locked},{esno:.2f}\n"
            )
            with open("sweep_results.csv", "a") as txtfile:
                txtfile.write(txt_line)


if __name__ == "__main__":
    runner = SweepRunner(
        output_csv="sweep_results.csv",
        log_file="sweep_errors.log"
    )
    print("▶️  Starting sweep…")
    runner.run()
    print("✅  Sweep finished.")

