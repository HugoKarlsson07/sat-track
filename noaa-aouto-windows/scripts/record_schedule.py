"""
Optimized RTL-SDR satellite recorder
- Fully driven by config/satellites.yaml (TLEs embedded)
- Block-based streaming (avoids huge memory allocations)
- Thread-safe active recordings
- Watches config file for changes and reloads TLEs automatically
- Uses pyrtlsdr for RX (fallback to rtl_fm if desired)

Requirements:
  pip install pyrtlsdr scipy numpy pyyaml apscheduler skyfield

Usage:
  - Put your satellites.yaml in project/config/satellites.yaml
  - Ensure recordings/ and tle/ directories exist (script will create them)
  - Run: python optimized_rtlsdr_recorder.py

"""

from pathlib import Path
from datetime import datetime, timezone, timedelta
import threading
import time
import yaml
import logging
import wave
import struct

import numpy as np
from scipy.signal import decimate
from pyrtlsdr import RtlSdr
from apscheduler.schedulers.background import BackgroundScheduler
from skyfield.api import Loader, EarthSatellite, wgs84

# ----------------------
# CONFIG
# ----------------------
PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config" / "satellites.yaml"
TLE_DIR = PROJECT_ROOT / "tle"
RECORD_DIR = PROJECT_ROOT / "recordings" / "raw"
RECORD_DIR.mkdir(parents=True, exist_ok=True)
TLE_DIR.mkdir(parents=True, exist_ok=True)

# Receiver settings
USE_PY_RTLSDR = True
RTL_SAMPLE_RATE = 2_400_000  # 2.4 MS/s
CHUNK_SECONDS = 0.25         # seconds per processing chunk
CHUNK_SAMPLES = int(RTL_SAMPLE_RATE * CHUNK_SECONDS)

# Audio output
OUT_SAMPLE_RATE = 12000  # final audio sample rate
DECIMATE_STAGE1 = 50     # 2400000 / 50 = 48000
DECIMATE_STAGE2 = 4      # 48000 / 4 = 12000

# Location (Göteborg)
MY_LAT = 57.69
MY_LON = 11.97
MY_ELEV_M = 0

# Skyfield
loader = Loader(str(TLE_DIR))
ts = loader.timescale()

# State
active_recordings = set()
active_lock = threading.Lock()

# Config caching
_config_cache = None
_config_mtime = None
_sats_cache = None

# Logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
log = logging.getLogger('satrec')

# ----------------------
# UTIL: load config + build satellites
# ----------------------

def load_config(force=False):
    global _config_cache, _config_mtime, _sats_cache

    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")

    mtime = CONFIG_PATH.stat().st_mtime
    if not force and _config_cache is not None and mtime == _config_mtime:
        return _config_cache, _sats_cache

    log.info("Loading config and TLEs from %s", CONFIG_PATH)
    data = yaml.safe_load(CONFIG_PATH.read_text())
    sats = {}
    for entry in data.get('satellites', []):
        name = entry['name']
        tle1 = entry['tle1']
        tle2 = entry['tle2']
        sats[name] = EarthSatellite(tle1, tle2, name, ts)

    _config_cache = data
    _config_mtime = mtime
    _sats_cache = sats
    return data, sats


# ----------------------
# PASS PREDICTION
# ----------------------

def get_local_passes(sat, minutes_ahead=24*60, step_minutes=1, elev_mask_deg=10):
    t0 = datetime.utcnow().replace(tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=minutes_ahead)

    # generate skyfield times for evaluation
    times = ts.utc([t0 + timedelta(minutes=i * step_minutes) for i in range(int(minutes_ahead / step_minutes) + 1)])

    geoc = wgs84.latlon(MY_LAT, MY_LON, elevation_m=MY_ELEV_M)
    altaz = (sat - geoc).at(times).altaz()
    altitudes = altaz[0].degrees

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


# ----------------------
# FM demod + decimation helper
# ----------------------

def fm_demodulate(iq, prev_sample=None):
    # iq: complex64 array
    # return float array of instantaneous frequency (unscaled)
    if prev_sample is not None:
        iq = np.concatenate(([prev_sample], iq))
    ph = np.angle(iq[1:] * np.conj(iq[:-1]))
    return ph, iq[-1]


# ----------------------
# Recording: streaming & incremental WAV write
# ----------------------

def record_with_pyrtlsdr(freq_mhz, duration_s, outpath):
    """Stream from RTL-SDR in chunks, demodulate FM, decimate to OUT_SAMPLE_RATE and write WAV incrementally."""
    log.info("[PY-RTLSDR] Recording %ds at %.6f MHz → %s", duration_s, freq_mhz, outpath)

    sdr = RtlSdr()
    try:
        sdr.sample_rate = RTL_SAMPLE_RATE
        sdr.center_freq = freq_mhz * 1e6
        sdr.gain = 'auto'

        total_chunks = max(1, int(np.ceil(duration_s / CHUNK_SECONDS)))
        prev_sample = None

        # open wave file for streaming write
        wf = wave.open(outpath, 'wb')
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(OUT_SAMPLE_RATE)

        for i in range(total_chunks):
            # compute how many samples to read (last chunk shorter)
            remaining = duration_s - i * CHUNK_SECONDS
            cur_chunk_s = min(CHUNK_SECONDS, max(0.001, remaining))
            cur_samples = int(RTL_SAMPLE_RATE * cur_chunk_s)

            try:
                iq = sdr.read_samples(cur_samples)
            except Exception as e:
                log.exception("SDR read error: %s", e)
                break

            # FM demodulate (returns ph and last sample for continuity)
            ph, prev_sample = fm_demodulate(iq, prev_sample)

            # decimate 2400000 -> 48000
            try:
                audio_48k = decimate(ph, DECIMATE_STAGE1, ftype='fir', zero_phase=True)
                # decimate 48000 -> 12000
                audio_12k = decimate(audio_48k, DECIMATE_STAGE2, ftype='fir', zero_phase=True)
            except Exception:
                # fallback to crude downsample (less ideal) if decimate fails
                audio_12k = ph[::DECIMATE_STAGE1 * DECIMATE_STAGE2]

            # normalize small chunks separately to avoid clipping; keep global scale low
            if np.max(np.abs(audio_12k)) > 0:
                audio_12k = audio_12k / np.max(np.abs(audio_12k)) * 0.9

            # convert to int16 PCM
            pcm = np.int16(np.clip(audio_12k * 32767, -32768, 32767))
            wf.writeframes(pcm.tobytes())

        wf.close()
        log.info("[PY-RTLSDR] Saved WAV → %s", outpath)
    finally:
        sdr.close()


# ----------------------
# High-level recorder wrapper
# ----------------------

def run_record(freq_mhz, satname, start_time_utc=None, duration_override=None):
    log.info("Starting recording thread for %s @ %.6f MHz", satname, freq_mhz)

    # wait until start if specified
    if start_time_utc:
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        if isinstance(start_time_utc, str):
            start_ts = datetime.fromisoformat(start_time_utc)
        else:
            start_ts = start_time_utc
        delta = (start_ts - now).total_seconds()
        if delta > 0:
            log.info("Waiting %.1fs until start %s", delta, start_ts)
            time.sleep(delta)

    tnow = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out = RECORD_DIR / f"{satname.replace(' ', '_')}_{tnow}_{int(freq_mhz*1000)}kHz.wav"

    duration_s = duration_override or 60

    try:
        if USE_PY_RTLSDR:
            record_with_pyrtlsdr(freq_mhz, duration_s, str(out))
        else:
            # fallback to external rtl_fm + sox if desired (not implemented streaming here)
            raise RuntimeError("RTL_FM fallback not implemented in optimized script")
    except Exception:
        log.exception("Recording failed for %s", satname)
    finally:
        with active_lock:
            if satname in active_recordings:
                active_recordings.remove(satname)
        log.info("Recording finished for %s", satname)


# ----------------------
# Scheduler job: detect passes and start recordings
# ----------------------

def job_check_and_schedule():
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    log.info("Scheduler tick at %s", now.isoformat())

    try:
        config, sats = load_config()
    except Exception:
        log.exception("Failed loading config")
        return

    next_events = []

    for satcfg in config.get('satellites', []):
        name = satcfg['name']
        freq = float(satcfg['freq_mhz'])

        sat = sats.get(name)
        if not sat:
            continue

        passes = get_local_passes(sat, minutes_ahead=12*60)

        # find next future pass
        future = next((p for p in passes if p[0] > now), None)
        if future:
            next_events.append((name, future[0]))

        for aos_utc, los_utc in passes:
            start = aos_utc - timedelta(seconds=20)
            stop = los_utc + timedelta(seconds=20)
            duration = int((stop - start).total_seconds())

            if start <= now <= stop:
                with active_lock:
                    if name not in active_recordings:
                        active_recordings.add(name)
                        threading.Thread(target=run_record, args=(freq, name, None, duration), daemon=True).start()
                break

            elif start > now and (start - now) < timedelta(minutes=10):
                with active_lock:
                    if name not in active_recordings:
                        active_recordings.add(name)
                        threading.Thread(target=run_record, args=(freq, name, start, duration), daemon=True).start()
                break

    if next_events:
        log.info("Next passes:")
        for satname, aos in next_events:
            delta_min = (aos - now).total_seconds() / 60
            log.info("  %s: %s (in %.1f min)", satname, aos.isoformat(), delta_min)


# ----------------------
# MAIN
# ----------------------

def main():
    log.info("Starting optimized recorder")

    # initial load
    load_config(force=True)

    # start scheduler
    sched = BackgroundScheduler()
    sched.add_job(job_check_and_schedule, 'interval', seconds=60)
    sched.start()

    # run initial check immediately
    job_check_and_schedule()

    try:
        # keep main thread alive
        while True:
            time.sleep(1)
            # auto reload config if file changed
            try:
                load_config()
            except Exception:
                pass
    except KeyboardInterrupt:
        log.info('Stopping...')
        sched.shutdown()


if __name__ == '__main__':
    main()
