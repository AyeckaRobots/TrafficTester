import json
import time
import csv
import os
import datetime

from restcore import restmod, restdemod
import noiseindex
from constants import RESTMOD_IP, RESTDEMOD_IP, ADMIN_USER, ADMIN_PASS

from demod.iface import Demodulator
from demod.adapters import RestDemodAdapter


class NoiseTester:
    def __init__(self, freq, symrate, power, pls, dut, timeouts=None, streak_goal_sec=None):
        self.freq = freq
        self.symrate = symrate
        self.power = power
        self.pls = pls
        self.streak_goal_sec = float(streak_goal_sec) if streak_goal_sec else None

        # Load PLS code thresholds
        with open("plscodes.json", "r") as f:
            self._plscodes = json.load(f)
        self.target_esno = self._get_min_esno()

        # DUTs and helpers
        self.mod = restmod.RestMod(RESTMOD_IP, ADMIN_USER, ADMIN_PASS)
        self.dut = dut or RestDemodAdapter(restdemod.RestDemod(RESTDEMOD_IP, ADMIN_USER, ADMIN_PASS))
        self.noise_index = noiseindex.NoiseIndex("sweep_results.csv")

        # Timeouts and control (seconds)
        self.timeouts = {
            "lock_wait": 60.0,
            "evaluation_window": 200.0,     # hard timeout for the whole evaluation
            "poll_interval": 1.0,
            "stabilize_after_reset": 3.0,   # wait after any counter reset/unlock
            "log_heartbeat": 5.0,           # periodic status logging cadence
        }
        if timeouts:
            self.timeouts.update(timeouts)

    def _wait_for_lock(self, timeout=None):
        timeout = timeout or self.timeouts["lock_wait"]
        start = time.time()
        while not self.dut.is_locked():
            if time.time() - start > timeout:
                raise TimeoutError(f"Lock not achieved in {timeout}s")
            time.sleep(0.5)
        print("🔒 Locked.")

    def _get_min_esno(self):
        for entry in self._plscodes:
            if entry["plscode"] == self.pls:
                return entry["min_esno"]
        raise ValueError(f"No entry for PLS {self.pls}")

    def execute_test(self):
        try:
            # Configure RF and baseband
            print(f"⏳ Initializing: freq={self.freq} Hz, symrate={self.symrate} sps, power={self.power} dBm, target ESNO={self.target_esno} dB")
            self.mod.set_all(self.freq, self.symrate, self.power, noise=0, pls=self.pls)
            self.dut.set_all(self.freq, self.symrate)
            self.dut.switch_rx1()

            print("⏳ Waiting for demodulator to lock...")
            self._wait_for_lock()

            # Calibrate noise to target ESNO
            adj = self.noise_index.adjust_noise(self.freq, self.symrate, self.power, self.target_esno)
            noise = adj["noise_dec"]
            print(f"🎯 Calibrated noise: {noise} (0x{noise:x})")

            # Evaluate best zero-error locked streak within evaluation window (or until goal is reached)
            best = self._evaluate()
            print(f"🏁 Best zero-error locked streak: {best['best_streak_sec']:.2f}s (good frames={best['best_streak_good']})")

            return {
                "traffic": best,          # contains best_streak_sec, best_streak_good
                "noise_dec": noise
            }

        except Exception as e:
            print(f"❌ Test failed: {e}")
            self._save_problematic("error", str(e))
            return None

    def _evaluate(self):
        """
        Goal:
          - Find the longest continuous period where:
              - demodulator stays locked
              - bad == 0 and missed == 0 throughout the period
          - If self.streak_goal_sec is set and a streak reaches that length, stop early and finish.
          - Total search time is limited by evaluation_window.
          - On any error (bad>0 or missed>0) or unlock, reset counters and stabilize before resuming.

        Output:
          - best_streak_sec: longest zero-error locked streak (seconds)
          - best_streak_good: number of good frames during the best streak
        """
        poll = float(self.timeouts.get("poll_interval", 1.0))
        window = float(self.timeouts.get("evaluation_window", 200.0))
        stabilize = float(self.timeouts.get("stabilize_after_reset", 3.0))
        heartbeat = float(self.timeouts.get("log_heartbeat", 5.0))
        goal = float(self.streak_goal_sec) if self.streak_goal_sec and self.streak_goal_sec > 0 else None

        # Ensure counters start clean and stabilize
        self.dut.reset_counters()
        self._stabilize_sleep(stabilize, time.time() + window)

        start_time = time.time()
        deadline = start_time + window

        in_streak = False
        streak_start_time = None
        streak_good_start = 0

        best_streak_sec = 0.0
        best_streak_good = 0

        last_lock_state = None
        last_best_reported = 0.0
        last_heartbeat = 0.0

        msg = f"🧪 Evaluating zero-error window for up to {int(window)}s"
        if goal:
            msg += f" (goal={int(goal)}s"
            msg += f", poll={poll}s)"
        else:
            msg += f" (poll={poll}s)"
        print(msg)

        while True:
            now = time.time()
            if now >= deadline:
                print("⏱️ Evaluation window reached.")
                break

            remaining = max(0.0, deadline - now)

            locked = self.dut.is_locked()
            if locked != last_lock_state:
                print(f"🔒 Lock state: {'LOCKED' if locked else 'UNLOCKED'}")
                last_lock_state = locked

            if not locked:
                if in_streak:
                    duration = time.time() - streak_start_time
                    print(f"⚠️ Unlock detected — streak broken at {duration:.2f}s. Resetting counters.")
                    in_streak = False
                self.dut.reset_counters()
                print(f"🧹 Counters reset; stabilizing for {stabilize:.1f}s...")
                self._stabilize_sleep(stabilize, deadline)
                time.sleep(min(poll, max(0.0, deadline - time.time())))
                continue

            # Locked: inspect counters
            counters = self.dut.get_packet_traffic()
            good = int(counters.get("good_frame_counter", 0))
            bad = int(counters.get("bad_frame_counter", 0))
            missed = int(counters.get("missed_frame_counter", 0))
            errors = bad + missed

            # Heartbeat logging
            if (now - last_heartbeat) >= heartbeat:
                print(
                    f"🫀 Status: locked={locked} in_streak={in_streak} "
                    f"good={good} bad={bad} missed={missed} "
                    f"best={best_streak_sec:.2f}s remaining={int(deadline - now)}s"
                )
                last_heartbeat = now

            if errors > 0:
                if in_streak:
                    duration = time.time() - streak_start_time
                    print(f"❌ Error detected (bad={bad}, missed={missed}) — streak broken at {duration:.2f}s. Resetting counters.")
                else:
                    print(f"❌ Error detected (bad={bad}, missed={missed}) — not in streak. Resetting counters.")
                self.dut.reset_counters()
                print(f"🧹 Counters reset; stabilizing for {stabilize:.1f}s...")
                self._stabilize_sleep(stabilize, deadline)
                in_streak = False
                time.sleep(min(poll, max(0.0, deadline - time.time())))
                continue

            # No errors and locked => candidate for streak
            if not in_streak:
                in_streak = True
                streak_start_time = time.time()
                streak_good_start = good
                print(f"🌱 New zero-error streak started (good_start={streak_good_start}).")

            # Update best if current streak beats previous best
            current_duration = time.time() - streak_start_time
            if current_duration > best_streak_sec:
                best_streak_sec = current_duration
                best_streak_good = max(0, good - streak_good_start)
                if best_streak_sec - last_best_reported >= 1.0:
                    print(f"📈 New best: {best_streak_sec:.2f}s zero-error (good_delta={best_streak_good}).")
                    last_best_reported = best_streak_sec

            # Early stop on goal reached
            if goal and current_duration >= goal:
                print(f"🎉 Goal reached: {current_duration:.2f}s zero-error. Ending evaluation early.")
                break

            time.sleep(min(poll, remaining))

        # If we ended while in a streak, ensure final update (best is already tracked continuously)
        if in_streak:
            final_duration = time.time() - streak_start_time
            if final_duration > best_streak_sec:
                best_streak_sec = final_duration
                best_streak_good = max(0, int(self.dut.get_packet_traffic().get("good_frame_counter", 0)) - streak_good_start)

        return {
            "best_streak_sec": round(best_streak_sec, 2),
            "best_streak_good": int(best_streak_good)
        }

    def _stabilize_sleep(self, stabilize, deadline):
        # Sleep up to 'stabilize' seconds, but do not overshoot the deadline
        remaining = max(0.0, deadline - time.time())
        sleep_time = min(stabilize, remaining)
        if sleep_time > 0:
            time.sleep(sleep_time)

    def _save_problematic(self, reason, details):
        filename = "problematic_tests.json"
        entry = {
            "freq": self.freq,
            "symrate": self.symrate,
            "power": self.power,
            "pls": self.pls,
            "reason": reason,
            "details": details,
            "timestamp": datetime.datetime.now().isoformat()
        }
        data = []
        if os.path.exists(filename):
            with open(filename, "r") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    data = []
        data.append(entry)
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        print(f"📂 Problematic test saved: {reason}")


def main():
    cfg = json.load(open("tests.json"))

    # Output CSV — keep existing schema for compatibility
    csv_file = "tests_results.csv"
    cols = [
        "device_name", "serial_number", "mdc", "bca", "web",
        "demodulator_fpga", "modulator_firmware",
        "modulator_software", "hw_version",
        "freq", "symrate", "power", "pls",
        "noise_dec", "waiting_time_sec",
        "interval_sec", "good", "bad", "missed"
    ]
    write_header = not os.path.isfile(csv_file) or os.path.getsize(csv_file) == 0
    out = open(csv_file, "a", newline="")
    writer = csv.DictWriter(out, fieldnames=cols)
    if write_header:
        writer.writeheader()

    raw_demod = restdemod.RestDemod(RESTDEMOD_IP, ADMIN_USER, ADMIN_PASS)

    # Merge timeouts; keep evaluation_window default 200 unless overridden
    timeouts = dict(cfg.get("timeouts", {}))
    if "evaluation_window" not in timeouts:
        timeouts["evaluation_window"] = 200.0
    if "poll_interval" not in timeouts:
        timeouts["poll_interval"] = 1.0
    if "stabilize_after_reset" not in timeouts:
        timeouts["stabilize_after_reset"] = 3.0
    if "log_heartbeat" not in timeouts:
        timeouts["log_heartbeat"] = 5.0

    def write_row(info, result, params, goal_sec):
        freq, symrate, power, pls, _goal = params
        best = result["traffic"]
        writer.writerow({
            **info,
            "freq": freq,
            "symrate": symrate,
            "power": power,
            "pls": pls,
            "noise_dec": result["noise_dec"],
            # Record the intended per-test limit if provided, otherwise the hard window
            "waiting_time_sec": goal_sec if goal_sec else timeouts.get("evaluation_window", 200.0),
            # Best zero-error locked streak actually achieved
            "interval_sec": best["best_streak_sec"],
            "good": best["best_streak_good"],
            "bad": 0,
            "missed": 0
        })

    for params in cfg["tests"]:
        # params: [freq, symrate, power, pls, limit_seconds]
        freq, symrate, power, pls, limit_seconds = params
        # Interpret the 5th param as per-test streak goal (seconds); 0/None means no early stop
        try:
            streak_goal_sec = float(limit_seconds) if limit_seconds not in (None, "", False) else None
            if streak_goal_sec is not None and streak_goal_sec <= 0:
                streak_goal_sec = None
        except (TypeError, ValueError):
            streak_goal_sec = None

        print("\n" + "=" * 80)
        print(f"🛰️ Starting test: freq={freq}, symrate={symrate}, power={power}, pls={pls} (goal={int(streak_goal_sec)}s)" if streak_goal_sec else
              f"🛰️ Starting test: freq={freq}, symrate={symrate}, power={power}, pls={pls}")
        tester = NoiseTester(freq, symrate, power, pls, raw_demod, timeouts, streak_goal_sec=streak_goal_sec)
        try:
            try:
                info = tester.dut.get_general_info()
            except Exception:
                info = {}
            result = tester.execute_test()
            if not result:
                print("⚠️ Test returned no result. Skipping write.")
                continue
            write_row(info, result, params, streak_goal_sec)
            print("✅ Test completed and recorded.")
        except Exception as e:
            print(f"❌ Unhandled test error: {e}")

    out.close()
    print("\n📄 Results saved to tests_results.csv")


if __name__ == "__main__":
    main()
