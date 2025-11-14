"""
record_test.py
Dummy-version av record.py för test utan SDR/antenner.
Skapar inga riktiga WAV-filer, men simulerar timing och filnamn.
"""

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RECORD_DIR = BASE / "recordings" / "raw"
RECORD_DIR.mkdir(parents=True, exist_ok=True)


def fake_record(satname, start_time_iso=None, duration_override=None, freq_mhz=None):
    """
    Simulerar inspelning. Returnerar sökväg till 'fake' fil.
    Väntar tills start_time om det anges.
    """
    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    # Vänta tills start_time om det finns
    if start_time_iso:
        start_ts = datetime.fromisoformat(start_time_iso).astimezone(timezone.utc)
        delta = (start_ts - now).total_seconds()
        if delta > 0:
            print(f"[FAKE] Sleeping {delta:.1f}s until start {start_ts.isoformat()}Z")
            time.sleep(delta)
        now = datetime.utcnow().replace(tzinfo=timezone.utc)

    tnow = now.strftime("%Y%m%d_%H%M%S")
    fake_file = RECORD_DIR / f"{satname}_{tnow}_FAKE.wav"

    # Simulera inspelning
    if duration_override:
        print(f"[FAKE] Recording {satname} for {duration_override}s "
              f"{'(freq: '+str(freq_mhz)+' MHz)' if freq_mhz else ''} -> {fake_file}")
        time.sleep(duration_override)
    else:
        print(f"[FAKE] Recording {satname} (no duration override) "
              f"{'(freq: '+str(freq_mhz)+' MHz)' if freq_mhz else ''} -> {fake_file}")
        print("[FAKE] Simulating ongoing recording for test purposes...")

    print(f"[FAKE] Recording complete: {fake_file}")
    return fake_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sat", required=True)
    parser.add_argument("--start-time", default=None, help="ISO format UTC start time")
    parser.add_argument("--duration", type=int, default=5, help="seconds to simulate recording")
    parser.add_argument("--freq", type=float, default=None, help="Downlink frequency in MHz")
    args = parser.parse_args()

    fake_record(args.sat, args.start_time, args.duration, args.freq)
