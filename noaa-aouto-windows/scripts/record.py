"""
record.py
Startar rtl_fm och sox för att spela in en NOAA APT-pass till WAV.
Kan anropas med --start-time ISO för att vänta tills pass start.
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RECORD_DIR = BASE / "recordings" / "raw"
RECORD_DIR.mkdir(parents=True, exist_ok=True)

def iso_to_ts(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)

def build_command(freq_mhz, out_wav_path, gain=40, sample_rate=48000):
    """
    Command pipeline for Windows (rtl_fm piped to sox).
    Assumes rtl_fm.exe and sox.exe are on PATH.
    """
    # rtl_fm command produces raw 16-bit signed samples when using -r and -s
    # We'll use rtl_fm to demodulate FM into audio and pipe to sox to convert to WAV.
    # Note: Windows piping works with shell True.
    rtl_cmd = f'rtl_fm -f {freq_mhz}M -M fm -s {sample_rate} -g {gain} -'
    # sox reads signed 16-bit raw from stdin: -t raw -r 48000 -e signed -b 16 -c 1 -
    sox_cmd = f'sox -t raw -r {sample_rate} -e signed -b 16 -c 1 - {out_wav_path} rate 11025'
    # rate 11025 is common for APT processing later (wx-style tools expect ~5–11k)
    # Combine
    cmd = f'{rtl_cmd} | {sox_cmd}'
    return cmd

def run_record(freq, satname, start_time_iso=None, duration_override=None):
    ts = None
    if start_time_iso:
        ts = iso_to_ts(start_time_iso)
        now = datetime.utcnow().astimezone(timezone.utc)
        delta = (ts - now).total_seconds()
        if delta > 0:
            print(f"Sleeping {delta:.1f}s until start {ts.isoformat()}Z")
            time.sleep(delta)
    # assemble output filename
    tnow = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = RECORD_DIR / f"{satname}_{tnow}_{int(freq*1000)}kHz.wav"
    cmd = build_command(freq, str(out))
    print("Starting recording with command:")
    print(cmd)
    # Run command in shell so pipes work on Windows
    proc = subprocess.Popen(cmd, shell=True)
    try:
        # If duration_override is set, wait that many seconds then terminate
        if duration_override:
            time.sleep(duration_override)
            proc.terminate()
            print("Terminated recording after override duration.")
        else:
            # No duration given — this script is intended to be terminated externally (e.g. after LOS)
            print("Recording started (no duration given). Press Ctrl-C to stop.")
            proc.wait()
    except KeyboardInterrupt:
        print("KeyboardInterrupt — terminating recorder.")
        proc.terminate()
    print(f"Recording complete: {out}")
    return out

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sat", required=True)
    parser.add_argument("--freq", type=float, required=True)
    parser.add_argument("--start-time", default=None, help="ISO format UTC start time")
    parser.add_argument("--duration", type=int, default=None, help="seconds to record")
    args = parser.parse_args()
    run_record(args.freq, args.sat, args.start_time, args.duration)
