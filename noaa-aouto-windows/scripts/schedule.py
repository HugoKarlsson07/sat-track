
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from skyfield.api import Loader, EarthSatellite, wgs84
import yaml
from record_test import fake_record  # bara för att testa programmet utan antenn
# from record import run_record #avkomentera när programmet ska köras med antenn
import threading

BASE = Path(__file__).resolve().parents[1]
CONFIG = BASE / "config" / "satellites.yaml"

# kordinater för origovägen 4
#blir predict någon minut fel kan det vara så att tle filen behöver updateras.
MY_LAT = 57.69
MY_LON = 11.97
MY_ELEV_M = 0

loader = Loader(str(BASE / "tle"))
ts = loader.timescale()


def load_tles():
    """Läser in TLE-data från YAML-konfig."""
    data = yaml.safe_load(CONFIG.read_text())
    sats = {}
    for satcfg in data["satellites"]:
        name = satcfg["name"]
        tle1 = satcfg["tle1"]
        tle2 = satcfg["tle2"]
        sats[name] = EarthSatellite(tle1, tle2, name, ts)
    return sats


def get_local_passes(sat, minutes_ahead=24 * 60, step_minutes=1, elev_mask_deg=10):
    """Beräknar alla satellitpass över mottagarens position."""
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
            start = t0 + timedelta(minutes=i * step_minutes)
        if alt < elev_mask_deg and inpass:
            end = t0 + timedelta(minutes=i * step_minutes)
            passes.append((start, end))
            inpass = False

    if inpass:
        passes.append((start, t1))
    return passes


def job():
    """Körs varje minut: uppdaterar pass och startar fake_record vid behov."""
    print("Job tick:", datetime.utcnow().isoformat() + "Z")
    sats = load_tles()
    config = yaml.safe_load(CONFIG.read_text())
    next_events = []

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    for satcfg in config["satellites"]:
        name = satcfg["name"]
        freq = satcfg["freq_mhz"]

        if name not in sats:
            print(f"[WARN] Satellite {name} not found in YAML TLEs.")
            continue

        sat = sats[name]
        passes = get_local_passes(sat, minutes_ahead=12 * 60, step_minutes=1, elev_mask_deg=10)

        # Hitta nästa pass i framtiden
        future_pass = next((p for p in passes if p[0] > now), None)
        if future_pass:
            aos_utc, _los_utc = future_pass
            next_events.append((name, aos_utc))

        # Kolla om vi är inne i ett pass just nu, eller nära ett kommande
        for p in passes:
            aos_utc, los_utc = p
            start = aos_utc - timedelta(seconds=30)
            stop = los_utc + timedelta(seconds=30)

            if start <= now <= stop:
                duration = int((stop - now).total_seconds())
                print(f"[INFO] In-pass now for {name} — starting fake_record for {duration}s")
                fake_record(name, None, duration)

            elif start > now and (start - now) < timedelta(minutes=10):
                duration = int((stop - start).total_seconds())
                print(f"[INFO] Upcoming pass for {name} at {start.isoformat()}Z — scheduling fake_record")
                fake_record(name, start.isoformat(), duration)

    # Lista nästa pass
    print("---- NEXT PASSES ----")
    for satname, aos in next_events:
        delta_min = (aos - now).total_seconds() / 60
        print(f"{satname}: {aos.isoformat()}Z  (om {delta_min:.1f} min)")
    print("Job done.\n")


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", minutes=1, max_instances=3)
    print("Starting scheduler. Press Ctrl-C to exit.")
    job()  
    scheduler.start()
