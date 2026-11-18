#!/usr/bin/env python3
"""autoclip: turn a long gameplay recording or stream VOD into short vertical clips.

It finds the moments worth watching (loud reactions, fast action, hype words), cuts each one into a
9:16 clip with optional facecam layout and burned-in subtitles, and writes a report of what it picked.

    python autoclip.py stream.mp4                      # 8 clips, blurred-background layout
    python autoclip.py stream.mp4 -n 12 --len 30       # 12 clips of ~30 s
    python autoclip.py stream.mp4 --cam 1500,40,380,280  # facecam box x,y,w,h → cam on top, game below
    python autoclip.py stream.mp4 --no-subs            # skip speech-to-text

Needs ffmpeg on PATH and numpy. Subtitles and hype-word detection use faster-whisper if installed.
Only clip footage you own or have permission to use (your own streams, or creators who allow clips).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np

HYPE = re.compile(
    r"\b(no way|let'?s go+|oh my god|omg|what+|wow|insane|crazy|bro|holy|yes+|run|clutch|dude|wait|hahaha+|lol)\b",
    re.I,
)
WIN = 0.5  # analysis window, seconds


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def has_audio(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout
    return bool(out.strip())


def loudness(path, dur):
    """RMS loudness per WIN-second window, in dB."""
    sr = 8000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = int(sr * WIN)
    k = len(x) // n
    if k == 0:
        return np.zeros(1)
    rms = np.sqrt(np.mean(x[: k * n].reshape(k, n) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms)


def motion(path, dur):
    """How much the picture changes per WIN window (mean frame-to-frame luma difference, 5 fps sample)."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-an", "-vf",
                        "fps=5,scale=320:-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-", "-f", "null", "-"],
                       capture_output=True, text=True)
    times = [float(t) for t in re.findall(r"pts_time:([0-9.]+)", p.stdout)]
    vals = [float(v) for v in re.findall(r"YDIF=([0-9.]+)", p.stdout)]
    n = int(np.ceil(dur / WIN))
    acc, cnt = np.zeros(n), np.zeros(n)
    for t, v in zip(times, vals):
        i = int(t / WIN)
        if i < n:
            acc[i] += v
            cnt[i] += 1
    return acc / np.maximum(cnt, 1)


def transcribe(path, log):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log("faster-whisper not installed: no subtitles or hype words (pip install faster-whisper)")
        return []
    def run_on(device, compute):
        model = WhisperModel("small", device=device, compute_type=compute)
        segments, _ = model.transcribe(path, word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:  # the work happens while iterating, so GPU errors show up here
            for w in seg.words or []:
                words.append({"t0": w.start, "t1": w.end, "w": w.word.strip()})
        return words

    try:
        words = run_on("cuda", "float16")
        log("speech-to-text: GPU")
        return words
    except Exception as e:  # no NVIDIA GPU, or CUDA libraries missing
        log(f"GPU not usable ({str(e)[:60]}), using the CPU (slower)")
    return run_on("cpu", "int8")


def zscore(a):
    s = a.std()
    return (a - a.mean()) / s if s > 1e-6 else a * 0


def smooth(a, k):
    if k <= 1:
        return a
    return np.convolve(a, np.ones(k) / k, mode="same")


def score_timeline(dur, loud, mov, words, w=(1.0, 0.5, 0.8)):
    n = int(np.ceil(dur / WIN))
    s_loud = np.zeros(n)
    m = min(n, len(loud))
    # loudness relative to the local level, so a quiet stream and a loud stream both work
    s_loud[:m] = zscore(loud[:m] - smooth(loud[:m], int(60 / WIN)))
    s_cut = np.zeros(n)
    m2 = min(n, len(mov))
    s_cut[:m2] = zscore(smooth(mov[:m2], int(3 / WIN)) - smooth(mov[:m2], int(60 / WIN)))  # action relative to the local pace
    s_hype = np.zeros(n)
    for w in words:
        if HYPE.search(w["w"]):
            i = int(w["t0"] / WIN)
            if i < n:
                s_hype[i] += 1
    s_hype = zscore(smooth(s_hype, int(4 / WIN)))
    total = w[0] * smooth(s_loud, int(3 / WIN)) + w[1] * s_cut + w[2] * s_hype
    return total, s_loud, s_cut, s_hype


def pick_moments(total, dur, count, length, at=()):
    """Highest-scoring peaks, at least one clip length apart; each clip puts its peak ~60% in."""
    order = np.argsort(total)[::-1]
    picked = [t for t in at if 0 <= t < dur]  # timestamps the user asked for always become clips
    for i in order:
        if len(picked) >= count:
            break
        t = i * WIN
        if any(abs(t - p) < length for p in picked):
            continue
        picked.append(t)
    clips = []
    for t in sorted(picked):
        start = max(0.0, min(dur - length, t - 0.6 * length))
        clips.append({"peak": round(t, 1), "start": round(start, 1), "end": round(min(dur, start + length), 1), "score": round(float(total[int(t / WIN)]), 2)})
    return clips


def srt_time(t):
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02}:{int(m):02}:{int(s):02},{int(round((s % 1) * 1000)):03}"


def write_srt(words, start, end, path):
    """Short subtitle chunks (max 4 words / 1.6 s), timed to the clip."""
    ws = [w for w in words if start <= w["t0"] < end]
    chunks, cur = [], []
    for w in ws:
        if cur and (len(cur) >= 4 or w["t1"] - cur[0]["t0"] > 1.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    with open(path, "w", encoding="utf-8") as f:
        for i, c in enumerate(chunks, 1):
            text = " ".join(w["w"] for w in c).upper()
            f.write(f"{i}\n{srt_time(c[0]['t0'] - start)} --> {srt_time(c[-1]['t1'] - start)}\n{text}\n\n")
    return bool(chunks)


def render(src, clip, out, cam, srt):
    start, length = clip["start"], clip["end"] - clip["start"]
    if cam:
        x, y, w, h = cam
        # facecam on top (1080x640), gameplay center-cropped below (1080x1280)
        vf = (f"[0:v]split=2[a][b];[a]crop={w}:{h}:{x}:{y},scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640[cam];"
              f"[b]scale=-2:1280,crop=1080:1280[game];[cam][game]vstack=inputs=2[v]")
    else:
        # gameplay fit to width, blurred copy of itself behind it
        vf = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3,eq=brightness=-0.12[bg];"
              "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    if srt:
        style = "Fontname=Arial,Fontsize=16,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=70"
        vf += f";[v]subtitles={os.path.basename(srt)}:force_style='{style}'[v2]"
        vmap = "[v2]"
    else:
        vmap = "[v]"
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(length), "-i", os.path.abspath(src),
           "-filter_complex", vf, "-map", vmap, "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", os.path.basename(out)]
    run(cmd, cwd=os.path.dirname(os.path.abspath(out)))  # relative subtitle path avoids Windows escaping issues


def main():
    ap = argparse.ArgumentParser(description="Turn a long recording into short vertical clips.")
    ap.add_argument("video")
    ap.add_argument("-n", "--count", type=int, default=8, help="number of clips (default 8)")
    ap.add_argument("--len", type=float, default=30, help="clip length in seconds (default 30)")
    ap.add_argument("--cam", help="facecam box as x,y,w,h in source pixels; puts the cam on top")
    ap.add_argument("--at", help="timestamps you want as clips, e.g. 12:30,1:05:10 (mm:ss or h:mm:ss)")
    ap.add_argument("--focus", choices=["crashes", "funny", "action", "talk"], help="what to look for: crashes = loud + motion, funny = speech + laughs, action = motion, talk = speech")
    ap.add_argument("--no-subs", action="store_true", help="skip speech-to-text and subtitles")
    ap.add_argument("-o", "--out", default=None, help="output folder (default: <video>_clips)")
    a = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found on PATH. Install it first: https://ffmpeg.org/download.html")
    src = a.video
    out = a.out or os.path.splitext(src)[0] + "_clips"
    os.makedirs(out, exist_ok=True)
    log = lambda m: print(f"[autoclip] {m}", flush=True)

    dur = duration(src)
    length = min(a.len, dur)
    log(f"{os.path.basename(src)}: {dur / 60:.1f} min")
    loud = loudness(src, dur) if has_audio(src) else np.zeros(1)
    log("loudness: done")
    mov = motion(src, dur)
    log("motion: done")
    words = [] if a.no_subs or not has_audio(src) else transcribe(src, log)
    if words:
        log(f"speech: {len(words)} words, {sum(bool(HYPE.search(w['w'])) for w in words)} hype words")

    weights = {"crashes": (1.6, 1.0, 0.3), "funny": (0.8, 0.1, 1.6), "action": (0.6, 1.6, 0.2), "talk": (0.3, 0.1, 2.0)}.get(a.focus, (1.0, 0.5, 0.8))
    at = []
    for t in (a.at or "").split(","):
        if t.strip():
            at.append(float(sum(float(x) * 60 ** i for i, x in enumerate(reversed(t.strip().split(":"))))))
    total, *_ = score_timeline(dur, loud, mov, words, weights)
    clips = pick_moments(total, dur, a.count, length, at)
    cam = tuple(int(v) for v in a.cam.split(",")) if a.cam else None

    for i, c in enumerate(clips, 1):
        name = os.path.join(out, f"clip_{i:02}.mp4")
        srt = os.path.join(out, f"clip_{i:02}.srt")
        use_srt = bool(words) and write_srt(words, c["start"], c["end"], srt)
        render(src, c, name, cam, srt if use_srt else None)
        c["file"] = os.path.basename(name)
        c["text"] = " ".join(w["w"] for w in words if c["start"] <= w["t0"] < c["end"])[:200]
        log(f"clip {i}/{len(clips)}: {c['start']:.0f}s-{c['end']:.0f}s (score {c['score']})")

    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"source": os.path.basename(src), "clips": clips}, f, indent=2, ensure_ascii=False)
    log(f"done: {len(clips)} clips in {out}")


if __name__ == "__main__":
    main()
