"""
tle_fetch.py
Hämtar senaste TLE från celestrak (Public) och sparar i tle/noaa_tle.txt
Körs av schedule.py innan beräkning.
"""
import requests
from pathlib import Path
from datetime import datetime

TLE_URL = "https://celestrak.com/NORAD/elements/noaa.txt"
OUT = Path(__file__).resolve().parents[1] / "tle" / "noaa_tle.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)

def fetch():
    print("Fetching TLE...")
    r = requests.get(TLE_URL, timeout=20)
    r.raise_for_status()
    OUT.write_text(r.text)
    print(f"TLE saved to {OUT} at {datetime.utcnow().isoformat()}Z")

if __name__ == "__main__":
    fetch()
