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

# track last log time (for heartbeat suppression)
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

# file handler (serious test report — no emojis, no heartbeats, no internals)
fh = logging.FileHandler("noise_tester.log")
fh.setLevel(logging.INFO)

class TestReportFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        # filter heartbeat explicitly
        if "heartbeat" in msg.lower():
            return ""
        # strip emojis
        clean = re.sub(r"[^\x00-\x7F]+", " ", msg).strip()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        return f"{ts} [{record.levelname}] {clean}"

class SkipHeartbeatFilter(logging.Filter):
    def filter(self, record):
        return "heartbeat" not in record.getMessage().lower()

fh.setFormatter(TestReportFormatter())
fh.addFilter(SkipHeartbeatFilter())

# attach handlers
logger.handlers = []
logger.addHandler(UpdateLastHandler())
logger.addHandler(ch)
logger.addHandler(fh)


class HeartbeatThread(threading.Thread):
    """Only logs a heartbeat when interval seconds passed since the last *any* log."""
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
            self.mod = restmod.RestMod(RESTMOD_IP, ADMIN_USER, ADMIN_PASS)
        except Exception as e:
            logger.exception("Failed to init RestMod: %s", e)
            self.mod = None

        self.dut: Demodulator = dut
        try:
            self.noise_index = noiseindex.NoiseIndex("sweep_results.csv")
        except Exception as e:
            logger.exception("Failed to init NoiseIndex: %s", e)
            self.noise_index = None

        # For heartbeat control
        self._stop_event = threading.Event()
        self._heartbeat = HeartbeatThread(interval=3.0, stop_event=self._stop_event)

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
            # short sleep so heartbeat continues separately
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

    def start_heartbeat(self):
        if not self._heartbeat.is_alive():
            logger.debug("Starting heartbeat thread.")
            self._heartbeat.start()

    def stop_heartbeat(self):
        logger.debug("Stopping heartbeat thread.")
        self._stop_event.set()
        # No join here required — it's daemon and will stop on program exit. join optionally:
        try:
            self._heartbeat.join(timeout=1.0)
        except Exception:
            logger.debug("Heartbeat thread join failed/timeout.")

    def execute_test(self):
        """
        Top-level logic with robust error handling and logging.
        Any recoverable error will be logged; fatal errors will be logged and the method will return.
        """
        logger.info("🎛️  Setting modulator: freq=%s MHz, symrate=%s Msps, power=%s dBm, PLS=%s",
                    self.freq, self.symrate, self.power, self.pls)

        # Start heartbeat so we always print something every 3 seconds
        self.start_heartbeat()

        # Safely call mod and dut setup methods
        if self.mod:
            safe_call(self.mod, "set_all", self.freq, self.symrate, self.power, noise=0, pls=self.pls)
        else:
            logger.warning("Modulator object not initialized; skipping set_all.")

        safe_call(self.dut, "set_all", self.freq, self.symrate)
        safe_call(self.dut, "switch_rx1")
        time.sleep(1)

        logger.info("🔄 Resetting DUT counters...")
        safe_call(self.dut, "reset_counters")

        logger.info("🔒 Waiting for lock...")
        locked = self._wait_for_lock()
        if not locked:
            logger.error("Device did not lock. Proceeding but results may be invalid.")

        logger.info("🎯 Target ESNO: %s dB", self.target_esno)

        # Wait for ESNO target, but do not raise — log and continue on timeout/errors
        timeout = ESNO_SYNC_TIMEOUT
        start = time.time()
        while True:
            esno = safe_call(self.dut, "get_esno", fallback=None)
            if esno is None:
                logger.warning("get_esno returned None; will retry until timeout.")
            else:
                try:
                    logger.info("📡 Current ESNO: %.1f dB", float(esno))
                except Exception:
                    logger.exception("Invalid ESNO value: %s", esno)

                try:
                    if float(esno) >= float(self.target_esno) + 0.5:
                        logger.info("✅ ESNO target achieved.")
                        break
                except Exception:
                    logger.debug("Skipping ESNO comparison due to invalid values.")

            if time.time() - start > timeout:
                logger.warning("⏱️ ESNO target not achieved in %s s — proceeding anyway.", timeout)
                break
            time.sleep(1)

        # Attempt to get noise result
        noise_result = None
        if self.noise_index:
            try:
                nearest = self.noise_index.get_closest_noise(self.freq, self.symrate, self.power, self.target_esno)
                if isinstance(nearest, dict):
                    noise_result = nearest.get('noise_dec')
                logger.info("🔍 Closest noise: %s", noise_result)
            except Exception:
                logger.exception("Error while fetching closest noise.")
        else:
            logger.warning("Noise index not initialized; skipping noise lookup.")

        # Apply noise to modulator here (safely)
        if noise_result is not None and self.mod:
            safe_call(self.mod, "set_noise", noise_result)
        else:
            logger.info("No noise applied (modulator or noise_result missing).")

        logger.info("🧪 Running test for %s seconds...", TEST_TIME)

        # Run evaluation and print result
        try:
            eval_result = self._evaluate()
            if eval_result is not None:
                logger.info("📊 Packet loss percentage: %.4f%%", eval_result.get("packet_loss_percentage", 0.0))
            else:
                logger.warning("Evaluation returned no result.")
        except Exception:
            # _evaluate should handle exceptions itself, but catch anything unexpected
            logger.exception("Unexpected error during evaluation.")

        # Stop heartbeat
        self.stop_heartbeat()
        logger.info("Test finished (heartbeat stopped).")

    def _evaluate(self):
        """
        Run the modulator for TEST_TIME seconds.
        At the end, check the packet loss percentage reported by the demodulator.
        Returns a dict or None on failure.
        """
        try:
            TEST_TIME = 60  # seconds
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
            except Exception:
                logger.exception("Failed to cast packet traffic to float: %s", packet_loss_raw)
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
        freq = 1550.0
        symrate = 6.0
        power = -30.0
        pls = 61

        # Choose and initialize your demodulator adapter here
        # For example, using RestDemodAdapter:
        # dut = RestDemodAdapter(restdemod.RestDemod("192.168.10.200", "admin", "admin"))
        dut = HW6DemodAdapter(hw6demod.HW6Demod())

        safe_call(dut, "switch_rx1")
        tester = NoiseTester(freq, symrate, power, pls, dut)
        tester.execute_test()
    except Exception:
        logger.exception("Unhandled exception in main — program will exit but exception was logged.")


if __name__ == "__main__":
    main()
