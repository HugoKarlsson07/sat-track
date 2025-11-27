import sys
import threading
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from skyfield.api import Loader, EarthSatellite, wgs84

# ---------------------------------------------
# GLOBAL PATHS
# ---------------------------------------------
BASE = Path(__file__).resolve().parents[1]
CONFIG = BASE / "config" / "satellites.yaml"
TLE_DIR = BASE / "tle"
RECORD_DIR = BASE / "recordings" / "raw"
RECORD_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------
# POSITION FOR GÖTEBORG (Origo, Origovägen 4)
# ---------------------------------------------
MY_LAT = 57.69
MY_LON = 11.97
MY_ELEV_M = 0

# ---------------------------------------------
# SKYFIELD SETUP
# ---------------------------------------------
loader = Loader(str(TLE_DIR))
ts = loader.timescale()

# ---------------------------------------------
# STATES: spårar aktiva inspelningar
# ---------------------------------------------
active_recordings = {}

# ---------------------------------------------
# RECORDING FUNCTIONS (INTEGRERAT)
# ---------------------------------------------

def iso_to_ts(s):
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def build_command(freq_mhz, out_wav_path, gain=40, sample_rate=48000):
    """
    Windows pipeline: rtl_fm → sox → WAV
    """
    rtl_cmd = f'rtl_fm -f {freq_mhz}M -M fm -s {sample_rate} -g {gain} -'
    sox_cmd = f'sox -t raw -r {sample_rate} -e signed -b 16 -c 1 - {out_wav_path} rate 11025'
    return f"{rtl_cmd} | {sox_cmd}"


def run_record(freq, satname, start_time_iso=None, duration_override=None):
    """
    Startar en inspelning, eventuellt efter en fördröjning.
    """
    print(f"[REC] Launching recorder for {satname} FREQ={freq} MHz")

    # vänta tills starttid
    if start_time_iso:
        ts = iso_to_ts(start_time_iso)
        now = datetime.utcnow().astimezone(timezone.utc)
        delta = (ts - now).total_seconds()
        if delta > 0:
            print(f"[REC] Waiting {delta:.1f}s until start at {ts.isoformat()}Z")
            time.sleep(delta)

    # skapa filnamn
    tnow = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = RECORD_DIR / f"{satname}_{tnow}_{int(freq*1000)}kHz.wav"

    cmd = build_command(freq, str(out))
    print("[REC] Command:")
    print(cmd)

    proc = subprocess.Popen(cmd, shell=True)

    try:
        if duration_override:
            time.sleep(duration_override)
            proc.terminate()
            print(f"[REC] Stopped {satname} recording after {duration_override}s")
        else:
            proc.wait()

    except KeyboardInterrupt:
        proc.terminate()

    # Ta bort från aktiva inspelningar
    active_recordings.pop(satname, None)

    print(f"[REC] File saved: {out}")


# ---------------------------------------------
# LOAD TLE DATA
# ---------------------------------------------

def load_tles():
    data = yaml.safe_load(CONFIG.read_text())
    sats = {}
    for satcfg in data["satellites"]:
        name = satcfg["name"]
        tle1 = satcfg["tle1"]
        tle2 = satcfg["tle2"]
        sats[name] = EarthSatellite(tle1, tle2, name, ts)
    return sats


# ---------------------------------------------
# PASSPREDIKTION
# ---------------------------------------------

def get_local_passes(sat, minutes_ahead=24*60, step_minutes=1, elev_mask_deg=10):
    t0 = datetime.utcnow().replace(tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=minutes_ahead)

    times = ts.utc([
        t0 + timedelta(minutes=i) for i in range(0, minutes_ahead, step_minutes)
    ])

    geoc = wgs84.latlon(MY_LAT, MY_LON, elevation_m=MY_ELEV_M)
    altitudes = (sat - geoc).at(times).altaz()[0].degrees

    passes = []
    inpass = False
    start = None

    for i, alt in enumerate(altitudes):
        if alt >= elev_mask_deg and not inpass:
            inpass = True
            start = t0 + timedelta(minutes=i*step_minutes)

        if alt < elev_mask_deg and inpass:
            end = t0 + timedelta(minutes=i*step_minutes)
            passes.append((start, end))
            inpass = False

    if inpass:
        passes.append((start, t1))

    return passes


# ---------------------------------------------
# JOB FUNCTION (KÖRS VARJE MINUT)
# ---------------------------------------------

def job():
    print("Job tick:", datetime.utcnow().isoformat() + "Z")

    sats = load_tles()
    config = yaml.safe_load(CONFIG.read_text())

    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    next_events = []

    for satcfg in config["satellites"]:
        name = satcfg["name"]
        freq = satcfg["freq_mhz"]

        if name not in sats:
            print(f"[WARN] No TLE for {name}")
            continue

        sat = sats[name]
        passes = get_local_passes(sat, minutes_ahead=12*60)

        # hitta nästa pass
        future = next((p for p in passes if p[0] > now), None)
        if future:
            next_events.append((name, future[0]))

        # kolla om vi ska starta inspelning
        for aos_utc, los_utc in passes:

            start = aos_utc - timedelta(seconds=20)
            stop = los_utc + timedelta(seconds=20)
            duration = int((stop - start).total_seconds())

            # Vi är i passet nu
            if start <= now <= stop:

                if name in active_recordings:
                    break  # redan spelar in

                print(f"[INFO] Now in pass for {name} — recording {duration}s")

                active_recordings[name] = True
                threading.Thread(
                    target=run_record,
                    args=(freq, name, None, duration),
                    daemon=True
                ).start()
                break 

            # Pass inom 10 min
            elif start > now and (start - now) < timedelta(minutes=10):

                if name in active_recordings:
                    break

                start_iso = start.isoformat()
                print(f"[INFO] Upcoming pass for {name} at {start_iso}Z — scheduling recording")

                active_recordings[name] = True
                threading.Thread(
                    target=run_record,
                    args=(freq, name, start_iso, duration),
                    daemon=True
                ).start()
                break

    
    print("---- NEXT PASSES ----")
    for satname, aos in next_events:
        delta_min = (aos - now).total_seconds() / 60
        print(f"{satname}: {aos.isoformat()}Z  (om {delta_min:.1f} min)")
    print("Job done.\n")



if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", minutes=1, max_instances=3)

    print("Starting scheduler. Press Ctrl-C to exit.")

    job()  # kör en gång direkt
    scheduler.start()
