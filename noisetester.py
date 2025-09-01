import json
import time
import csv
import os
import datetime
import threading
import traceback
import logging
import sys
import re

from restcore import restmod, restdemod
from snmpcore import hw6demod
import noiseindex
from constants import *

from demod.iface import Demodulator
from demod.adapters import RestDemodAdapter
from demod.adapters import HW6DemodAdapter

logger = logging.getLogger("NoiseTester")
logger.setLevel(logging.DEBUG)

# track last log time (for wait suppression)
_last_lock = threading.Lock()
_last_log = time.time()
def _get_last_log():
    with _last_lock:
        return _last_log
def _set_last_log(ts):
    global _last_log
    with _last_lock:
        _last_log = ts

class UpdateLastHandler(logging.Handler):
    def emit(self, record):
        try:
            _set_last_log(getattr(record, "created", time.time()))
        except Exception:
            pass

# console handler (keeps emojis + debug info)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    "%Y-%m-%d %H:%M:%S"
))

# file handler (serious test report — no emojis, no waits, no internals)
fh = logging.FileHandler("noise_tester.log")
fh.setLevel(logging.INFO)

class TestReportFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        # filter wait explicitly
        if "wait" in msg.lower() or "waiting" in msg.lower():
            return ""
        # strip emojis
        clean = re.sub(r"[^\x00-\x7F]+", " ", msg).strip()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        return f"{ts} [{record.levelname}] {clean}"

class SkipWaitFilter(logging.Filter):
    def filter(self, record):
        return "wait" not in record.getMessage().lower() and "waiting" not in record.getMessage().lower()

fh.setFormatter(TestReportFormatter())
fh.addFilter(SkipWaitFilter())

# attach handlers
logger.handlers = []
logger.addHandler(UpdateLastHandler())
logger.addHandler(ch)
logger.addHandler(fh)


class WaitThread(threading.Thread):
    """Only logs a waiting message when interval seconds passed since the last *any* log."""
    def __init__(self, interval=3.0, stop_event: threading.Event = None):
        super().__init__(daemon=True)
        self.interval = float(interval)
        self._stop = stop_event or threading.Event()

    def run(self):
        while not self._stop.is_set():
            now = time.time()
            if now - _get_last_log() >= self.interval:
                # Send only to console handler, not file
                msg = "⏳ Waiting..."
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                ch.emit(logging.makeLogRecord({
                    "name": logger.name,
                    "levelno": logging.INFO,
                    "levelname": "INFO",
                    "msg": msg,
                    "created": now
                }))
                _set_last_log(now)  # update so it won't spam
            self._stop.wait(0.25)



def safe_call(obj, method_name, *args, fallback=None, **kwargs):
    """
    Call obj.method_name(*args, **kwargs) and catch/log any exception.
    Returns fallback on error.
    """
    try:
        method = getattr(obj, method_name)
        return method(*args, **kwargs)
    except Exception as e:
        logger.exception("Error calling %s.%s: %s", obj.__class__.__name__, method_name, e)
        return fallback


class NoiseTester:
    def __init__(self, freq, symrate, power, pls, dut: Demodulator):
        self.freq = freq
        self.symrate = symrate
        self.power = power
        self.pls = pls

        # Load PLS code thresholds
        try:
            with open("plscodes.json", "r") as f:
                self._plscodes = json.load(f)
        except Exception as e:
            logger.exception("Failed to load plscodes.json: %s", e)
            self._plscodes = []

        self.target_esno = self._get_min_esno()

        # DUTs and helpers
        try:
            self.mod = restmod.RestMod(MOD_IP, ADMIN_USER, ADMIN_PASS)
        except Exception as e:
            logger.exception("Failed to init RestMod: %s", e)
            self.mod = None

        self.dut: Demodulator = dut
        try:
            self.noise_index = noiseindex.NoiseIndex("sweep_results.csv")
        except Exception as e:
            logger.exception("Failed to init NoiseIndex: %s", e)
            self.noise_index = None

        # For wait control
        self._stop_event = threading.Event()
        self._wait_thread = WaitThread(interval=3.0, stop_event=self._stop_event)

    def _wait_for_lock(self):
        timeout = LOCK_TIMEOUT
        start = time.time()
        logger.info("Waiting for lock (timeout: %s s)...", timeout)
        while True:
            locked = safe_call(self.dut, "is_locked", fallback=False)
            if locked:
                logger.info("🔒 Locked.")
                return True
            if time.time() - start > timeout:
                logger.warning("Lock not achieved in %s s", timeout)
                return False
            # short sleep so waiting message continues separately
            time.sleep(0.5)

    def _get_min_esno(self):
        try:
            for entry in self._plscodes:
                if entry.get("plscode") == self.pls:
                    return entry.get("min_esno")
        except Exception:
            logger.exception("Error while searching PLS codes.")
        logger.warning("No entry for PLS %s — defaulting target_esno to 0.0", self.pls)
        return 0.0

    def start_waiting(self):
        if not self._wait_thread.is_alive():
            logger.debug("Starting wait thread.")
            self._wait_thread.start()

    def stop_waiting(self):
        logger.debug("Stopping wait thread.")
        self._stop_event.set()
        # No join here required — it's daemon and will stop on program exit. join optionally:
        try:
            self._wait_thread.join(timeout=1.0)
        except Exception:
            logger.debug("Wait thread join failed/timeout.")

    def _write_csv_result(self, noise_result, locked, esno_after, eval_result):
        try:
            csv_file = "test_results.csv"
            file_exists = os.path.isfile(csv_file)
            needs_header = True

            if file_exists:
                # Check if file is empty
                needs_header = os.path.getsize(csv_file) == 0

            with open(csv_file, "a", newline="") as f:
                writer = csv.writer(f)

                general_info = safe_call(self.dut, "get_general_info", fallback={})
                general_keys = list(general_info.keys())
                general_values = [general_info.get(k, "") for k in general_keys]

                if needs_header:
                    writer.writerow(
                        general_keys + [
                            "freq", "symrate", "power", "pls",
                            "noise", "locked", "esno_after", "packet_loss_percentage"
                        ]
                    )

                writer.writerow(
                    general_values + [
                        self.freq,
                        self.symrate,
                        self.power,
                        self.pls,
                        noise_result if noise_result is not None else "none",
                        "true" if locked else "false",
                        float(esno_after) if esno_after is not None else "none",
                        (
                            str(eval_result.get("packet_loss_percentage"))
                            if eval_result and eval_result.get("packet_loss_percentage") is not None
                            else "none"
                        )
                    ]
                )

            logger.info("Results written to %s", csv_file)

        except Exception:
            logger.exception("Failed to write results to CSV.")

    def execute_test(self):
        """
        Top-level logic with robust error handling and logging.
        Now applies noise before waiting for lock.
        Sequence: set freq/symrate/power/noise -> wait for lock -> if locked, wait for ESNO -> start test.
        """
        # Check demodulator type before setting PLS
        if isinstance(self.dut, RestDemodAdapter):
            self.mod.set_test_pattern_pls(self.pls)
        elif isinstance(self.dut, HW6DemodAdapter):
            self.mod.set_test_pattern_pls(5)
            self.mod.set_data_pls(self.pls)
        else:
            raise(Exception)

        logger.info(f"🎛️  Setting modulator: freq={self.freq} MHz, symrate={self.symrate} Msps, power={self.power} dBm, PLS={self.pls}")

        self.start_waiting()

        # Safely call mod and dut setup methods
        noise_result = None
        if self.noise_index:
            try:
                nearest = self.noise_index.get_closest_noise(
                    self.freq, self.symrate, self.power, self.target_esno+1
                )
                if isinstance(nearest, dict):
                    noise_result = nearest.get('noise_dec')
                logger.info("🔍 Closest noise: %s", noise_result)
            except Exception:
                logger.exception("Error while fetching closest noise.")
        else:
            logger.warning("Noise index not initialized; skipping noise lookup.")

        if self.mod:
            safe_call(self.mod, "set_all", self.freq, self.symrate, self.power, noise=noise_result if noise_result is not None else 0)
        else:
            logger.warning("Modulator object not initialized; skipping set_all.")

        safe_call(self.dut, "set_all", self.freq, self.symrate)
        time.sleep(1)

        logger.info("🔄 Resetting DUT counters...")
        safe_call(self.dut, "reset_counters")

        logger.info("🔒 Waiting for lock...")
        locked = self._wait_for_lock()
        esno_after = None  # Track ESNO for CSV

        if not locked:
            logger.error("Device did not lock. Skipping ESNO measurement and test.")
            self.stop_waiting()
            self._write_csv_result(
                noise_result=noise_result,
                locked=False,
                esno_after=None,
                eval_result=None
            )
            logger.info("Test finished (wait thread stopped).")
            return

        logger.info("🔒 Locked — proceeding to ESNO measurement after noise application.")
        if self.mod and noise_result is not None:
            logger.info(f"⏳ Waiting {ESNO_SYNC_TIMEOUT} seconds after noise application...")
            time.sleep(ESNO_SYNC_TIMEOUT)
            try:
                esno_after = safe_call(self.dut, "get_esno", fallback=None)
                if esno_after is not None:
                    logger.info("📡 ESNO after noise: %.1f dB", float(esno_after))
                    if float(esno_after) >= float(self.target_esno) + 1:
                        logger.info("✅ ESNO target achieved.")
                    else:
                        logger.warning("⚠️ ESNO target not achieved.")
                else:
                    logger.warning("Could not read ESNO after noise.")
            except Exception:
                logger.exception("Error while reading ESNO after noise.")
        else:
            logger.info("No noise applied (missing noise_result or modulator). ESNO measurement skipped.")

        # Run iperf test to measure bitrate and packet loss
        eval_result = None
        if hasattr(self.dut, "_d") and hasattr(self.dut._d, "run_iperf"):
            logger.info("🚀 Running iperf test to measure bitrate and packet loss for %s seconds...", TEST_TIME)
            safe_call(self.dut._d, "run_iperf")
            # After iperf, fetch packet loss
            try:
                packet_loss_raw = safe_call(self.dut, "get_packet_traffic", fallback=None)
                if packet_loss_raw is not None:
                    try:
                        packet_loss_percentage = float(packet_loss_raw)
                        eval_result = {"packet_loss_percentage": round(packet_loss_percentage, 4)}
                        logger.info("📊 Packet loss percentage: %.4f%%", packet_loss_percentage)
                    except (TypeError, ValueError):
                        logger.warning("get_packet_traffic returned non-numeric value: %r", packet_loss_raw)
                else:
                    logger.warning("get_packet_traffic returned None; cannot compute packet loss.")
            except Exception:
                logger.exception("Unexpected error during evaluation.")
        else:
            logger.warning("DUT does not support iperf test.")

        self.stop_waiting()  # <-- Always stop the wait thread after test

        logger.info("Test finished (wait thread stopped).")

        self._write_csv_result(
            noise_result=noise_result,
            locked=locked,
            esno_after=esno_after,
            eval_result=eval_result
        )

    def _evaluate(self):
        """
        Run the modulator for TEST_TIME seconds.
        At the end, check the packet loss percentage reported by the demodulator.
        Returns a dict or None on failure.
        """
        try:
            self.dut.reset_counters()
            start_time = time.time()

            while time.time() - start_time < TEST_TIME:
                # Short sleep; heartbeat thread will still print every 3 seconds.
                time.sleep(1)

            packet_loss_raw = safe_call(self.dut, "get_packet_traffic", fallback=None)
            if packet_loss_raw is None:
                logger.warning("get_packet_traffic returned None; cannot compute packet loss.")
                return None

            try:
                packet_loss_percentage = float(packet_loss_raw)
            except (TypeError, ValueError):
                logger.warning("get_packet_traffic returned non-numeric value: %r", packet_loss_raw)
                return None

            logger.info("✅ Evaluation complete: packet_loss_percentage=%.4f%%", packet_loss_percentage)

            return {
                "packet_loss_percentage": round(packet_loss_percentage, 4)
            }
        except Exception as e:
            logger.exception("Exception during evaluation: %s", e)
            return None


def main():
    try:
        # Example parameters (replace with actual values as needed)
        freq = 1200.0
        symrate = 12.0
        power = -30.0
        pls = 61

        # dut = RestDemodAdapter(restdemod.RestDemod(DEMOD_IP, "admin", "admin"))
        dut = HW6DemodAdapter(hw6demod.HW6Demod())

        safe_call(dut, "switch_rx1")
        tester = NoiseTester(freq, symrate, power, pls, dut)
        tester.execute_test()
    except Exception:
        logger.exception("Unhandled exception in main — program will exit but exception was logged.")


if __name__ == "__main__":
    main()
