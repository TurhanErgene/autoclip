# autoclip

Turn a long gameplay recording or stream into short vertical clips, automatically.

autoclip watches the whole video, finds the moments worth posting (loud reactions, fast action, hype words), and cuts each one into a 9:16 clip for TikTok, Reels and Shorts, with an optional facecam-on-top layout and burned-in subtitles.

```
python autoclip.py my_gta_session.mp4 -n 8 --len 30
```

## Use it as a Claude Code skill

Copy this folder to `~/.claude/skills/autoclip/` (or your project's `.claude/skills/`). Then just ask Claude:

> "Make 8 clips from my_stream.mp4"

Claude finds the facecam, runs autoclip, checks the clips, and writes a hook title and caption for each one.

## Use it on its own

Needs Python 3.9+ and [ffmpeg](https://ffmpeg.org/download.html) on your PATH.

```
pip install -r requirements.txt
python autoclip.py video.mp4                         # 8 clips, blurred background
python autoclip.py video.mp4 -n 12 --len 25          # 12 clips of 25 s
python autoclip.py video.mp4 --cam 1500,40,380,280   # facecam box x,y,w,h: cam on top, game below
python autoclip.py video.mp4 --focus crashes         # look for crashes: loud + fast motion
python autoclip.py video.mp4 --at 12:30,1:05:10      # your timestamps, plus automatic finds
python autoclip.py video.mp4 --no-subs               # skip speech-to-text (faster)
```

Output goes to `video_clips/`: `clip_01.mp4`, `clip_02.mp4`, … and a `report.json` with each clip's time range, score and transcript.

Speech-to-text uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper). It runs on an NVIDIA GPU if CUDA is set up, and falls back to the CPU otherwise.

## How it picks moments

Every half second gets a score from three signals, each compared with the video's own local level:

- **Loudness spikes**: shouting, explosions, laughter
- **Motion**: how much the picture changes (chases, fights, crashes)
- **Hype words** in the transcript: "no way", "let's go", "oh my god", …

The top peaks become clips, spaced at least one clip apart, with each peak about 60% in so the build-up stays.

## Please clip responsibly

Only clip footage you own, or creators who allow it (many streamers run clipping programs and pay clippers). Always credit the creator.

## License

MIT
