# clipper

Turns a long anime story video into back-to-back vertical clips (1080x1920) for Shorts, Reels and TikTok.
The 9:16 window follows the main character's face, jumps on scene cuts and moves smoothly in between.

You watch the video and write down where each clip should **end**. clipper does the rest.

## Straight to your phone (the easy way)

Your phone and the server share a folder called `Clipper` through [Syncthing](https://syncthing.net).
You put a text file with a link and end times in `Clipper/jobs`. The server downloads the video, makes the
clips, and they appear in `Clipper/<video title>/` on your phone. There's no SSH and no copying.

On the server, once:

```bash
git clone https://github.com/eeejaymedia100/clipper.git
cd clipper
./setup.sh
```

It installs everything and starts two background services, `syncthing@$USER` and `clipper`. At the end it
prints the server's Syncthing ID and a QR code. Then:

1. On your phone, install **Syncthing-Fork** (F-Droid or its GitHub page) and add a device by scanning that QR code.
2. Copy your phone's own device ID from Syncthing-Fork and run `./setup.sh pair <phone ID>` on the server.
3. Accept the server and the `Clipper` folder on your phone, and choose where the folder goes.

To make clips, create a text file such as `Clipper/jobs/ep5.txt`:

```text
https://www.youtube.com/watch?v=XXXX
start 1:30
4:12
9:30
end
```

The link must be on the first line. `start` is optional. After that, list the end time of each clip.
Any site yt-dlp supports works, including YouTube, TikTok, Facebook, X and Instagram.
- If something is wrong, `jobs/ep5.status.txt` says what. Fix `ep5.txt` and save it again.
- Finished jobs move to `jobs/done/`.
- The downloaded video is deleted from the server once its clips are made.
- Half-made clips are never synced, so your phone only downloads finished clips. If you're on mobile data, set Syncthing-Fork to sync on Wi-Fi only.

To see what the server is doing, run `sudo journalctl -u clipper -f`.
To get updates, run `git pull && ./setup.sh`.

## One-time setup (command line)

On the server (Ubuntu/Debian, CPU only):

```bash
sudo apt install ffmpeg python3-venv nodejs      # nodejs (or deno) lets yt-dlp get 1080p from YouTube
cd clipper
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -e .
```

Then either run `source .venv/bin/activate` once per terminal, or call `.venv/bin/clipper` directly.
The first run downloads the anime face model, which is about 12 MB.

## Making clips

1. Watch the video and write the end time of every clip in `cuts.txt`, one per line (copy `cuts.example.txt`):

   ```text
   # Episode 3. Anything after the time is a note, and lines starting with # are skipped
   4:12   meets the villain
   9:30   the fight
   15:02
   end    to the very end
   ```

   Clip 1 runs from the start to 4:12, clip 2 from 4:12 to 9:30, and so on.
   Times can be written as `90` (seconds), `4:12`, `1:02:30`, `4:12.5` or `end`.

2. Run:

   ```bash
   clipper "https://www.youtube.com/watch?v=XXXX"
   ```

   It reads `cuts.txt` from the folder you run it in.

Clips land in `clips/<video title>/<title>_part01.mp4`, `_part02.mp4` and so on, next to a
`manifest.json` that lists each part with its start, end, length and file name.

### Options

| Option | What it does |
| --- | --- |
| `--times "4:12, 9:30, end"` | Give the times on the command line instead of `cuts.txt` |
| `--cuts other.txt` | Use a different cuts file |
| `--start 1:30` | Skip an intro: clip 1 starts here instead of at 0:00 |
| `--max-height 720` | Download at lower quality (default 1080) |
| `--force` | Remake clips that are already done |
| `--preset veryfast` | Encode faster but make bigger files (default `faster`) |
| `--detector opencv` | Use the simpler OpenCV face finder |
| `-o folder` | Put the clips somewhere other than `clips/` |

A local file works too: `clipper "my episode.mp4" --times "4:12, end"`.

### Running it again

Downloads are kept in `downloads/`, and finished clips are skipped. If a run stops
(Ctrl+C, a lost SSH session), run the same command again and it carries on.
If you change a time in `cuts.txt`, only the clips whose start or end moved are remade.

## How the framing works

- **Faces.** It uses an anime-trained face detector (imgutils, running ONNX on the CPU) about 4 times a second.
  If imgutils won't install or load, it falls back to OpenCV's `lbpcascade_animeface`.
- **Scene cuts.** These are found by comparing each frame's colour histogram with the previous frame's.
  On a cut, the crop moves to its new place on that exact frame.
- **Following one character.** Within a shot it locks onto the biggest face and stays on that same
  character, even if someone else comes closer. It only switches when that face has been gone for 1.5 seconds.
- **Smooth movement.** Moves smaller than 5% of the frame width are ignored. The path is smoothed
  with a filter that looks both ways in time (zero-phase), so the crop doesn't trail behind a moving face.
- **When the face is missing.** If it disappears mid-shot, the crop holds its last position. A shot with no face at all is centred.
- **Vertical sources.** If the video is already vertical, it is scaled to fit and padded instead of cropped.

Burned-in subtitles in a wide video will be partly cut off by the 9:16 window.
That can't be avoided when cropping, so add your own captions afterwards if you need them.

Cuts are frame-accurate. Every boundary is snapped to a frame, and each clip starts on exactly
the frame where the previous one ended, so there are no gaps or overlaps.
The audio is the original track, re-encoded to AAC so it can be cut at those exact points.
Video is H.264 with `faststart`.

## Speed

Everything runs on the CPU. On a 4-vCPU e2 machine, a 1080p source takes roughly **1.5–3× the clip's length**:
face checks take about 0.2 s each, and encoding to 1080x1920 takes the rest.
`--preset veryfast` cuts the encoding time at the cost of bigger files.

## When something is wrong

clipper explains problems in plain English and stops. For example:

- `"4:7x" is not a time. Write it like 4:12, 1:02:30, 90 or end.`
- `cuts.txt line 3: "8:3o" is not a time ...`
- `Cut 5 (12:40.000) is past the end of the video (11:58.000). Use "end" for the last clip.`
- `Cut 3 (9:00.000) must come after the previous cut (9:30.000).`
- `ffmpeg is not installed. Install it with:  sudo apt install ffmpeg`
- `Couldn't download the video: ...` (a private or deleted video, or no internet)

## Tests

```bash
.venv/bin/python -m pytest -q
```

The tests generate a synthetic video: a white box (standing in for a face) moving across the
screen, with one hard cut where the background and the box jump. They then run the real
pipeline and check the output to prove that:

- the box stays near the centre of every output frame;
- the crop snaps on the exact frame of the cut;
- every clip has exactly the frame count, and video and audio lengths, that the cut times give;
- finished clips are skipped, changed times remake only the affected clips, and `--force` remakes all;
- error messages are plain English.
