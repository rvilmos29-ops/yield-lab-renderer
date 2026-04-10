import os
import uuid
import httpx
import subprocess
import tempfile
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import cloudinary
import cloudinary.uploader

app = FastAPI(title="Yield Lab Renderer")

# Cloudinary config (same as your existing setup)
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "doe8gfoak"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

class RenderRequest(BaseModel):
    audio_url: str          # Fish Audio / ElevenLabs output URL
    script: str             # Full script text (used for subtitles)
    title: str              # Video title (shown as intro text)
    duration_seconds: int = 600  # ~10 min default
    orientation: str = "landscape"  # landscape = 1920x1080

class RenderResponse(BaseModel):
    video_url: str
    duration: float
    job_id: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/render", response_model=RenderResponse)
async def render_video(req: RenderRequest):
    job_id = str(uuid.uuid4())[:8]
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # 1. Download audio
        audio_path = tmpdir / "audio.mp3"
        await download_file(req.audio_url, audio_path)
        
        # 2. Get audio duration
        duration = get_audio_duration(audio_path)
        
        # 3. Fetch stock footage from Pexels
        video_path = tmpdir / "footage.mp4"
        await fetch_pexels_video(req.title, video_path, duration)
        
        # 4. Generate subtitle file (SRT)
        srt_path = tmpdir / "subs.srt"
        generate_srt(req.script, duration, srt_path)
        
        # 5. Render with FFmpeg
        output_path = tmpdir / f"output_{job_id}.mp4"
        render_ffmpeg(
            video_path=video_path,
            audio_path=audio_path,
            srt_path=srt_path,
            title=req.title,
            output_path=output_path,
            duration=duration,
            orientation=req.orientation
        )
        
        # 6. Upload to Cloudinary
        result = cloudinary.uploader.upload(
            str(output_path),
            resource_type="video",
            public_id=f"yield-lab/{job_id}",
            folder="yield-lab"
        )
        
        return RenderResponse(
            video_url=result["secure_url"],
            duration=duration,
            job_id=job_id
        )

async def download_file(url: str, dest: Path):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)

def get_audio_duration(audio_path: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ], capture_output=True, text=True)
    return float(result.stdout.strip())

async def fetch_pexels_video(query: str, dest: Path, min_duration: float):
    """Fetch a relevant stock video from Pexels"""
    # Extract keywords from title for better search
    keywords = query.lower().replace("-", " ").split()[:3]
    search_query = " ".join(keywords) if keywords else "technology business"
    
    # Finance/AI relevant fallback queries
    fallback_queries = [search_query, "technology money", "business finance", "laptop office"]
    
    video_url = None
    async with httpx.AsyncClient(timeout=30) as client:
        for q in fallback_queries:
            r = await client.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": PEXELS_API_KEY},
                params={"query": q, "per_page": 10, "min_duration": int(min_duration), "orientation": "landscape"}
            )
            data = r.json()
            videos = data.get("videos", [])
            
            # Pick best quality video file (1080p preferred)
            for video in videos:
                files = sorted(video.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True)
                for f in files:
                    if f.get("width", 0) >= 1920:
                        video_url = f["link"]
                        break
                if video_url:
                    break
            
            # Fallback to any HD video
            if not video_url and videos:
                files = videos[0].get("video_files", [])
                if files:
                    video_url = sorted(files, key=lambda x: x.get("width", 0), reverse=True)[0]["link"]
            
            if video_url:
                break
    
    if not video_url:
        raise HTTPException(status_code=500, detail="Could not find suitable stock footage")
    
    await download_file(video_url, dest)

def generate_srt(script: str, duration: float, srt_path: Path):
    """Split script into timed subtitle chunks"""
    # Split into sentences
    import re
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if not sentences:
        srt_path.write_text("")
        return
    
    # Distribute time evenly across sentences
    time_per_sentence = duration / len(sentences)
    
    srt_content = ""
    for i, sentence in enumerate(sentences):
        start = i * time_per_sentence
        end = (i + 1) * time_per_sentence
        
        # Chunk long sentences into ~8 word lines
        words = sentence.split()
        chunks = [" ".join(words[j:j+8]) for j in range(0, len(words), 8)]
        chunk_duration = (end - start) / len(chunks)
        
        for k, chunk in enumerate(chunks):
            idx = i * 100 + k + 1
            cs = start + k * chunk_duration
            ce = start + (k + 1) * chunk_duration
            srt_content += f"{idx}\n{format_time(cs)} --> {format_time(ce)}\n{chunk}\n\n"
    
    srt_path.write_text(srt_content)

def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def render_ffmpeg(video_path, audio_path, srt_path, title, output_path, duration, orientation):
    """Assemble final video with FFmpeg"""
    
    if orientation == "landscape":
        width, height = 1920, 1080
    else:
        width, height = 1080, 1920
    
    # FFmpeg filter: scale footage, overlay audio, burn subtitles, add title
    subtitle_style = (
        "FontName=Arial,"
        "FontSize=18,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "Outline=2,"
        "Alignment=2,"
        "MarginV=60"
    )
    
    filter_complex = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[v];"
        f"[v]subtitles={str(srt_path)}:force_style='{subtitle_style}'[vout]"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",          # Loop footage if shorter than audio
        "-i", str(video_path),          # Input footage
        "-i", str(audio_path),          # Input audio
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",                   # High quality (lower = better)
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(duration),            # Trim to audio length
        "-movflags", "+faststart",      # Web optimized
        str(output_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {result.stderr[-500:]}")
