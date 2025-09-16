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
import socket

from restcore import restmod, restdemod
from snmpcore import hw6demod
import noiseindex
from constants import *

from demod.iface import Demodulator
from demod.adapters import RestDemodAdapter
from demod.adapters import HW6DemodAdapter

from utils.logging_setup import logger
from utils.wait import WaitThread
from utils.helpers import safe_call
from utils.ping import CheckAlive

class TrafficTester:
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
            logger.exception("Failed to create RestMod: %s", e)
            self.mod = None

        self.dut: Demodulator = dut
        try:
            self.noise_index = noiseindex.NoiseIndex("sweep_results.csv")
        except Exception as e:
            logger.exception("Failed to init NoiseIndex: %s", e)
            self.noise_index = None

        # thread / stop event for wait/heartbeat
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


    def _write_csv_result(self, noise_result, locked, esno, eval_result):
        try:
            # Select CSV file based on DUT type
            if isinstance(self.dut, HW6DemodAdapter):
                csv_file = "hw6_test_results.csv"
            elif isinstance(self.dut, RestDemodAdapter):
                csv_file = "hw7_test_results.csv"

            file_exists = os.path.isfile(csv_file)
            needs_header = True

            if file_exists:
                needs_header = os.path.getsize(csv_file) == 0

            with open(csv_file, "a", newline="") as f:
                writer = csv.writer(f)

                general_info = safe_call(self.dut, "get_general_info", fallback={})
                general_keys = list(general_info.keys())
                general_values = [general_info.get(k, "") for k in general_keys]

                # Add date and time
                current_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                general_keys += ["date", "time"]
                general_values += [current_datetime.split()[0], current_datetime.split()[1]]

                header = (
                    general_keys + [
                        "freq", "symrate", "power", "pls",
                        "noise", "locked", "esno", "packet_loss_percentage"
                    ]
                )

                if needs_header:
                    writer.writerow(header)

                writer.writerow(
                    general_values + [
                        self.freq,
                        self.symrate,
                        self.power,
                        self.pls,
                        noise_result if noise_result is not None else "none",
                        "true" if locked else "false",
                        float(esno) if esno is not None else "none",
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

    def _is_host_up(self, host, tcp_ports=None, udp_ports=None):
        """
        Check if a host is alive by testing multiple TCP and/or UDP ports in parallel.
        Returns True if ANY port check succeeds.
        """
        tcp_ports = tcp_ports or []
        udp_ports = udp_ports or []
        results = []

        def check_tcp(port):
            try:
                with socket.create_connection((host, port), timeout=IS_ALIVE_TIMEOUT):
                    results.append(True)
            except Exception:
                pass

        def check_udp(port):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(IS_ALIVE_TIMEOUT)
                try:
                    s.sendto(b"", (host, port))
                    try:
                        s.recvfrom(1024)  # some services may reply
                        results.append(True)
                    except socket.timeout:
                        # No reply but host accepted datagram → still considered alive
                        results.append(True)
                finally:
                    s.close()
            except Exception:
                pass

        threads = []

        for p in tcp_ports:
            t = threading.Thread(target=check_tcp, args=(p,), daemon=True)
            threads.append(t)
            t.start()

        for p in udp_ports:
            t = threading.Thread(target=check_udp, args=(p,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(IS_ALIVE_TIMEOUT + 0.5)

        return any(results)

    def _check_connectivity(self):
        """
        Check both modulator and DUT reachability using their known ports.
        """
        results = {}

        def check_mod():
            # REST modulator (usually HTTP/80 or HTTPS/443)
            results["mod"] = self._is_host_up(MOD_IP, tcp_ports=[80, 443])

        def check_dut():
            # DUT (SNMP/161, Telnet/23, maybe HTTP/88)
            results["dut"] = self._is_host_up(DUT_IP, tcp_ports=[23, 88], udp_ports=[161, 162])

        t1 = threading.Thread(target=check_mod, daemon=True)
        t2 = threading.Thread(target=check_dut, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

        if not results.get("mod"):
            logger.error(f"❌ Modulator {MOD_IP} unreachable.")
            return False
        if not results.get("dut"):
            logger.error(f"❌ DUT {DUT_IP} unreachable.")
            return False

        logger.info("✅ Connectivity check passed (modulator + DUT).")
        return True

    def execute_test(self):
        """
        Runs a full test sequence: checks connectivity, configures signal parameters, applies noise, and verifies lock. 
        If locked, measures ESNO and evaluates packet loss. Logs results and writes them to CSV.
        """

        checker = CheckAlive()
        results = checker.check_all_hosts()

        for ip, alive in results.items():
            if alive:
                logger.info(f"✔ {ip} is reachable")
            else:
                logger.error(f"✖️  {ip} is unreachable")

        if not all(results.values()):
            logger.error("❌ Connectivity check failed. Aborting test.")
            return

        logger.info("✅ Connectivity check passed (all devices reachable).")


        # Check demodulator type before setting PLS (in test pattern or data)
        if isinstance(self.dut, RestDemodAdapter):
            self.mod.set_test_pattern_pls(self.pls)
        elif isinstance(self.dut, HW6DemodAdapter):
            self.mod.set_test_pattern_pls(5)
            self.mod.set_data_pls(self.pls)
        else:
            raise(Exception)

        self.start_waiting()

        # Safely call mod and dut setup methods
        noise_result = None
        if self.noise_index:
            try:
                nearest = self.noise_index.get_closest_noise(
                    self.freq, self.symrate, self.power, self.target_esno+0.5
                )
                if isinstance(nearest, dict):
                    noise_result = nearest.get('noise_dec')
                logger.info("🔍 Closest noise: %s", noise_result)
            except Exception:
                logger.exception("Error while fetching closest noise.")
        else:
            logger.warning("Noise index not initialized; skipping noise lookup.")

        logger.info(f"🎛️  Setting modulator: freq={self.freq} MHz, symrate={self.symrate} Msps, power={self.power} dBm, PLS={self.pls}")

        if self.mod:
            safe_call(self.mod, "set_all", self.freq, self.symrate, self.power, noise=noise_result if noise_result is not None else 0)
        else:
            logger.warning("Modulator object not initialized; skipping set_all.")

        logger.info(f"🎛️  Setting demodulator: freq={self.freq} MHz, symrate={self.symrate} Msps")

        safe_call(self.dut, "set_all", self.freq, self.symrate)
        time.sleep(1)

        logger.info("🔒 Waiting for lock...")
        locked = self._wait_for_lock()
        esno = None  # Track ESNO for CSV

        if not locked:
            logger.error("Device did not lock. Skipping ESNO measurement and test.")
            self.stop_waiting()
            self._write_csv_result(
                noise_result=noise_result,
                locked=False,
                esno=None,
                eval_result=None
            )
            logger.info("Test finished (wait thread stopped).")
            return

        logger.info("🔒 Locked — proceeding to ESNO measurement after noise application.")
        if self.mod and noise_result is not None:
            logger.info(f"⏳ Waiting {ESNO_SYNC_TIMEOUT} seconds after noise application...")
            time.sleep(ESNO_SYNC_TIMEOUT)
            try:
                esno = safe_call(self.dut, "get_esno", fallback=None)
                if esno is not None:
                    logger.info("📡 ESNO after noise: %.1f dB", float(esno))
                    if float(esno) >= float(self.target_esno) + 1:
                        logger.info("✅ ESNO target achieved.")
                    else:
                        logger.warning("⚠️ ESNO target not achieved.")
                else:
                    logger.warning("Could not read ESNO after noise.")
            except Exception:
                logger.exception("Error while reading ESNO after noise.")
        else:
            logger.info("No noise applied (missing noise_result or modulator). ESNO measurement skipped.")

        logger.info("🔄 Resetting DUT counters...")
        safe_call(self.dut, "reset_counters")

        # Run iperf test to measure bitrate and packet loss
        eval_result = None
        # Use adapter method instead of direct _d access
        if hasattr(self.dut, "run_packet_traffic"):
            logger.info("🚀 Running packet traffic test to measure bitrate and packet loss for %s seconds...", TEST_TIME)
            safe_call(self.dut, "run_packet_traffic")
            # After test, fetch packet loss
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
            logger.warning("DUT does not support packet traffic test.")

        self.stop_waiting()  # <-- Always stop the wait thread after test

        logger.info("Test finished (wait thread stopped).")

        self._write_csv_result(
            noise_result=noise_result,
            locked=locked,
            esno=esno,
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
        