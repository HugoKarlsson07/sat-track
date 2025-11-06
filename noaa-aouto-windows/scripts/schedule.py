"""
schedule.py
Huvudloop: räknar pass med skyfield, startar record.py vid AOS.
Ingen fetch längre – TLE hämtas från satellites.yaml
"""
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from skyfield.api import Loader, EarthSatellite, wgs84, N, E
import yaml

BASE = Path(__file__).resolve().parents[1]
CONFIG = BASE / "config" / "satellites.yaml"

# Your receiver location - EDIT THIS to your approximate coords!
MY_LAT = 59.3293
MY_LON = 18.0686
MY_ELEV_M = 0

loader = Loader(str(BASE / "tle"))
ts = loader.timescale()

def load_tles():
    data = yaml.safe_load(CONFIG.read_text())
    sats = {}
    for satcfg in data["satellites"]:
        name = satcfg["name"]
        tle1 = satcfg["tle1"]
        tle2 = satcfg["tle2"]
        sats[name] = EarthSatellite(tle1, tle2, name, ts)
    return sats

def get_local_passes(sat, minutes_ahead=24*60, step_minutes=1, elev_mask_deg=10):
    t0 = datetime.utcnow().replace(tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=minutes_ahead)
    times = ts.utc([t0 + timedelta(minutes=i) for i in range(0, minutes_ahead, step_minutes)])
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

def job():
    
    print("Job tick:", datetime.utcnow().isoformat()+"Z")
    sats = load_tles()
    config = yaml.safe_load(CONFIG.read_text())

    for satcfg in config["satellites"]:
        name = satcfg["name"]
        freq = satcfg["freq_mhz"]
        if name not in sats:
            print(f"Satellite {name} not found in YAML TLEs.")
            continue
        sat = sats[name]
        passes = get_local_passes(sat, minutes_ahead=12*60, step_minutes=1, elev_mask_deg=10)
        for p in passes:
            aos_utc, los_utc = p
            start = aos_utc - timedelta(seconds=30)
            stop = los_utc + timedelta(seconds=30)
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
            if start <= now <= stop:
                print(f"In-pass now for {name} — starting record.")
                subprocess.Popen([sys.executable, str(BASE / "scripts" / "record.py"),
                                  "--sat", name, "--freq", str(freq)])
            elif start > now and (start - now) < timedelta(minutes=10):
                print(f"Upcoming pass for {name} at {start.isoformat()}Z — starting recorder thread.")
                subprocess.Popen([sys.executable, str(BASE / "scripts" / "record.py"),
                                  "--sat", name, "--freq", str(freq), "--start-time", start.isoformat()])
            else:
                print(f"No immediate pass for {name} within 10 minutes.")
    print("Job done.")
    print(name, aos_utc.isoformat(), los_utc.isoformat())


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(job, 'interval', minutes=5)
    print("Starting scheduler. Press Ctrl-C to exit.")
    job()
    scheduler.start()
