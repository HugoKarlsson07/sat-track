"""
decode.py
Enkel wrapper: försöker använda `satdump` om installerat, annars kör en
minimal python-baserad APT-dekoder (very basic).
Input: WAV file eller recordings/raw/*
Output: bilder i decoded/
"""
import subprocess
import sys
from pathlib import Path
import wave
import numpy as np
from scipy.signal import decimate
from datetime import datetime

BASE = Path(__file__).resolve().parents[1]
DECODE_DIR = BASE / "decoded"
DECODE_DIR.mkdir(parents=True, exist_ok=True)

def run_satdump(wavpath):
    # satdump decode apt <file> <outdir>
    try:
        subprocess.run(["satdump", "decode", "apt", str(wavpath), str(DECODE_DIR)], check=True)
        return True
    except FileNotFoundError:
        return False
    except subprocess.CalledProcessError:
        print("satdump failed.")
        return False

def simple_python_apt_decode(wavpath):
    # This is a very small & not-perfect APT decoder: it will produce a grayscale
    # image by demodulating the audio and stacking lines. Useful as fallback.
    with wave.open(str(wavpath),'rb') as wf:
        sr = wf.getframerate()
        nframes = wf.getnframes()
        audio = wf.readframes(nframes)
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
    # normalize
    samples -= samples.mean()
    samples /= (np.abs(samples).max()+1e-9)
    # downsample to ~5k
    target_sr = 5512
    factor = int(sr / target_sr) if sr > target_sr else 1
    if factor > 1:
        samples = decimate(samples, factor)
        sr = int(sr / factor)
    # NOAA APT produces 2080 samples per line at ~2.4 kHz audio rate -> approximate lines
    line_length = 2080
    nlines = len(samples) // line_length
    img = samples[:nlines*line_length].reshape((nlines, line_length))
    # map to 0-255
    img = ((img - img.min()) / (img.max()-img.min()+1e-9) * 255).astype(np.uint8)
    # save as PNG via pillow
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed; pip install pillow to save images.")
        return False
    outname = DECODE_DIR / (wavpath.stem + ".png")
    im = Image.fromarray(img)
    im.save(outname)
    print("Saved fallback decoded image to", outname)
    return True

def decode_file(wavpath):
    print("Decoding", wavpath)
    if run_satdump(wavpath):
        print("Decoded via satdump.")
    else:
        print("satdump not found or failed — using python fallback.")
        simple_python_apt_decode(wavpath)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: decode.py <wavfile1> [<wavfile2> ...]")
        sys.exit(1)
    for a in sys.argv[1:]:
        decode_file(Path(a))
