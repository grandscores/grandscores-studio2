# GRANDSCORES Studio

GRANDSCORES Studio is a Python/Flask football results and league-table video renderer.

## External APIs

GRANDSCORES connects to these external services:

- Sportmonks Football API for live scores and league tables.
- ElevenLabs Text-to-Speech API for voiceover.

The application does **not** need different API URLs just because it is hosted on Railway. The API credentials are stored as Railway environment variables, while the application continues to call the official external API endpoints.

### Required Railway variables

Set these under the Railway service's **Variables** section:

```text
SPORTMONKS_API_TOKEN=your_sportmonks_token
ELEVENLABS_API_KEY=your_elevenlabs_key
ELEVENLABS_VOICE_ID=your_voice_id
ELEVENLABS_MODEL=eleven_multilingual_v2
```

Do not commit real API keys to GitHub.

## Railway deployment

1. Push the repository to GitHub.
2. In Railway, connect the GitHub repository to the existing GRANDSCORES service.
3. Railway will detect the `Dockerfile` and build the application.
4. Add the environment variables listed above.
5. Deploy the `main` branch.
6. Check `/health` after deployment. It should report `ffmpeg: true` and show `sportmonks`/`elevenlabs` as `true` when their variables are configured.

Railway supplies the `PORT` environment variable automatically. The Docker command binds Gunicorn to that port.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

For a production-style local run:

```bash
PORT=10000 gunicorn --bind 0.0.0.0:10000 --workers 1 --threads 8 --timeout 0 app:app
```

## Render compatibility

The same Dockerfile remains compatible with the existing Render deployment. The `render.yaml` file is retained for that reason.

## Security

- Never commit `.env` or real API keys.
- Use Railway/Render environment variables for secrets.
- Generated MP4 files are written to the temporary `outputs/` directory and are ignored by Git.

## Project layout

```text
app.py               Flask routes, Sportmonks/ElevenLabs calls and the video renderers
templates/index.html Page markup
static/app.css       Styling
static/app.js        Front-end logic (date picker, page routing, previews, render jobs)
assets/              Fonts, artwork, audio and the Mid-90s loop video
```

## Changelog

### v5.46
- Own goals are listed under the team they counted for, with "OG" after the name (e.g. "Penders OG" under Tottenham in Chelsea 0-2 Tottenham). Sportmonks own-goal events are checked against the final score to work out which side the goal belongs to.
- Manual results accept own goals typed as "Name og" or "Name (og)" in the scorers of the team the goal counted for.
- Test data now includes an own goal (Sunderland 0-2 Norwich City).

### v5.45
- Fixed Vidi Printer renders being killed (SIGKILL) on Railway for running out of memory. Identical frames (pauses, cursor blinks, holds) now reuse the file already written instead of saving a new copy, and still-background renders use JPEG frames. An 11-result render went from about 2 GB of frame files to about 0.2 GB, and renders about 3.5 times faster. The Mid-90s loop keeps transparent PNG frames.

### v5.44
- Background picker (with thumbnails) in step 3 of Results and step 2 of League table: Classic (two players), Striker, or the Mid-90s loop. Each tab remembers its choice in the browser.
- Results Video and Vidi Printer can now use the Striker image, and the League table can use Classic.
- Any image added to `assets/backgrounds/` appears in the picker automatically (scaled and centre-cropped to 1080×1920).
- New endpoints: `/api/backgrounds` and `/background-thumb/<key>`. The older `mid90sLoopBackground` flag still works.

### v5.43
- Removed the white glow around the Grandscores logo at the bottom of the Results Video (`assets/identity.png`). The logo keeps its position.

### v5.42
- Results Video and Vidi Printer voiceovers share one script line. The away score now runs straight on from the away team ("Wigan Athletic nill.") with no comma or pause, so it is read flat and falling rather than stressed.
- ElevenLabs requests send steadier voice settings: stability 0.70, similarity 0.75, style 0. Each can be changed with the optional Railway variables `ELEVENLABS_STABILITY`, `ELEVENLABS_SIMILARITY`, `ELEVENLABS_STYLE` and `ELEVENLABS_SPEED`.

### v5.41
- Vidi Printer splits a competition evenly across pages (11 results print as 4, 4, 3 rather than 5, 5, 1), still allowing for scorer lines.
- Vidi Printer preview has page buttons, so every page can be checked before rendering.
- Results Video: team names and scores are centred in the grey boxes on one shared baseline, instead of each row being placed by its own letter shapes (which left some names sitting on the bottom edge).

### v5.40
- Choose which day's results to load: Live (today, refreshing every minute), the previous six days, or a date range of up to seven days.
- New `/api/fixtures` endpoint (`/api/live-scores` still works) accepting `?date=YYYY-MM-DD` or `?start=…&end=…`. The Sportmonks `livescores` endpoint is only called when the range includes today.
- Sportmonks results are now paginated, so busy matchdays are no longer cut off at 50 fixtures.
- Results tab reorganised into three steps: Find results, Arrange pages, Make the video. Vidi Printer is now an output option rather than a separate tab.
- Results are routed to the page showing their competition; an empty page adopts the competition of the first result added.
- One "Test data" switch in the header replaces the separate test buttons. Manual results and random samples live under Testing tools.
- Fixes: fixture rows no longer wrap the Add button onto a second line; added results sort above empty rows; blank rows no longer start ticked as late; changing the league filter no longer re-requests Sportmonks; preview shows a message instead of an empty bar.
- Page HTML, CSS and JavaScript moved out of `app.py` into `templates/` and `static/`.

### v5.39
- League Table renders with the Mid-90s loop background limit FFmpeg threads and queues to avoid out-of-memory failures.

### v5.38
- Vidi Printer typing sound is a bundled asset (`assets/printer_typing.wav`).

### v5.29
- Mid-90s Loop Background option for all video types, composited natively at 1080×1920.
- Results Vidi Printer added, with grouped goalscorers and typewriter reveal.
- Live rows show the elapsed minute; editable, confirmable league-table voiceover.
