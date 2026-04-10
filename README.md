# Yield Lab Renderer

FFmpeg-based video render service for the Yield Lab YouTube automation pipeline.

## What it does
1. Takes an audio URL (Fish Audio TTS output) + script text
2. Fetches relevant stock footage from Pexels automatically
3. Assembles 1080p video with burned-in subtitles using FFmpeg
4. Uploads to Cloudinary and returns the video URL

## Deploy to Railway
1. Push this repo to GitHub
2. Create new Railway service → Deploy from GitHub repo
3. Add environment variables (see .env.example)
4. Railway will auto-detect Dockerfile and deploy

## Environment Variables
| Variable | Description |
|---|---|
| CLOUDINARY_CLOUD_NAME | Your Cloudinary cloud name (doe8gfoak) |
| CLOUDINARY_API_KEY | Cloudinary API key |
| CLOUDINARY_API_SECRET | Cloudinary API secret |
| PEXELS_API_KEY | Pexels API key (free at pexels.com/api) |

## n8n Integration
Add an HTTP Request node after your Fish Audio TTS node:

- Method: POST
- URL: https://your-railway-url.railway.app/render
- Body (JSON):
```json
{
  "audio_url": "{{ $json.audio_url }}",
  "script": "{{ $json.script }}",
  "title": "{{ $json.title }}",
  "duration_seconds": 600
}
```

Response will contain:
```json
{
  "video_url": "https://res.cloudinary.com/...",
  "duration": 598.4,
  "job_id": "a1b2c3d4"
}
```

Then pass `video_url` directly to your YouTube upload node.

## API Endpoints
- `GET /health` - Health check
- `POST /render` - Render a video
