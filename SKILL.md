---
name: autoclip
description: Turn a long gameplay recording or stream VOD into short vertical clips for TikTok, Reels and Shorts. Finds the best moments (loud reactions, fast action, hype words), cuts 9:16 clips with an optional facecam layout and burned-in subtitles, and writes titles and captions for each. Use when the user wants clips, highlights or shorts from a long video.
---

# autoclip

Turns one long video into ready-to-post vertical clips with `autoclip.py` (in this skill's folder).

## Before you start

1. **Check permission.** Only clip footage the user owns, or creators who allow clipping (many streamers run clipping programs). If the footage is someone else's and permission is unclear, say so before continuing.
2. **Check the tools.** Run `ffmpeg -version`. If it's missing, tell the user how to install it (Windows: `winget install ffmpeg`, macOS: `brew install ffmpeg`, Linux: the package manager).
3. **Install the Python packages** once: `pip install -r requirements.txt` (numpy and faster-whisper). faster-whisper is optional; without it there are no subtitles or hype-word detection.

## Steps

1. **Find the facecam (if there is one).** Grab a frame and look at it:
   `ffmpeg -ss 60 -i "<video>" -frames:v 1 frame.png`
   If a facecam is visible, estimate its box in source pixels as `x,y,w,h` (top-left corner, width, height). Use the video's real resolution (`ffprobe -v error -show_entries stream=width,height -of csv=p=0 "<video>"`).
2. **Run autoclip:**
   `python autoclip.py "<video>" -n 8 --len 30` and add `--cam x,y,w,h` if there is a facecam.
   - `-n`: how many clips (default 8). `--len`: clip length in seconds (default 30; 20–40 works best).
   - `--focus crashes|funny|action|talk`: what to look for (e.g. crashes = loud + fast motion). Use it when the user says what kind of moments they want.
   - `--at 12:30,1:05:10`: timestamps the user wants; they always become clips, and the rest are found automatically.
   - `--no-subs`: skip speech-to-text (much faster on long videos without a GPU).
   - Clips land in `<video>_clips/` with a `report.json` (time range, score and transcript for each clip).
3. **Check the result.** Grab one frame from a clip or two and look at it: the facecam should be on top and the game below, with readable subtitles. If the cam box is off, fix `--cam` and run again.
4. **Write the posting text.** For each clip in `report.json`, use its transcript to write:
   - a short hook title (under 8 words; the most surprising thing that happens),
   - a caption of one or two lines plus 3–5 relevant hashtags,
   - the creator credit, if it's someone else's footage.

   Save them as `<video>_clips/captions.md`.
5. **Tell the user** where the clips are, which clip is strongest (highest score), and remind them to credit the creator when it isn't their own footage.

## How it picks moments

Every half second gets a score from three signals, each compared with the video's own local level, so quiet and loud streams both work:
- **loudness spikes** (shouting, explosions, laughter),
- **motion**: how much the picture changes (chases, fights, crashes),
- **hype words** from the transcript ("no way", "let's go", "oh my god", …).

The highest peaks become clips, at least one clip length apart, with each peak about 60% of the way in so the build-up is kept.
