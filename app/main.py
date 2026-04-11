import os
import uuid
import httpx
import subprocess
import tempfile
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import cloudinary
import cloudinary.uploader

app = FastAPI(title="Yield Lab Renderer")

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "doe8gfoak"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")

# In-memory job store
jobs = {}

class RenderRequest(BaseModel):
    audio_url: str
    script: str
    title: str
    orientation: str = "landscape"

class JobStatus(BaseModel):
    job_id: str
    status: str
    video_url: str = ""
    error: str = ""

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/render", response_model=JobStatus)
async def render_video(req: RenderRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "pending", "video_url": None, "error": None}
    background_tasks.add_task(process_render, job_id, req)
    return JobStatus(job_id=job_id, status="pending")

@app.get("/status/{job_id}", response_model=JobStatus)
def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = jobs[job_id]
    return JobStatus(
        job_id=job_id,
        status=job["status"],
        video_url=job.get("video_url") or "",
        error=job.get("error") or ""
    )

async def process_render(job_id: str, req: RenderRequest):
    jobs[job_id]["status"] = "processing"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            print(f"[{job_id}] Downloading audio...")
            audio_path = tmpdir / "audio.mp3"
            await download_file(req.audio_url, audio_path)
            duration = get_audio_duration(audio_path)
            print(f"[{job_id}] Duration: {duration}s")
            print(f"[{job_id}] Fetching stock footage...")
            video_path = tmpdir / "footage.mp4"
            await fetch_pexels_video(req.title, video_path)
            srt_path = tmpdir / "subs.srt"
            generate_srt(req.script, duration, srt_path)
            print(f"[{job_id}] Rendering...")
            output_path = tmpdir / f"output_{job_id}.mp4"
            render_ffmpeg(video_path, audio_path, srt_path, output_path, duration, req.orientation)
            print(f"[{job_id}] Uploading...")
            result = cloudinary.uploader.upload(
                str(output_path),
                resource_type="video",
                public_id=f"yield-lab/{job_id}",
                folder="yield-lab"
            )
            jobs[job_id]["status"] = "done"
            jobs[job_id]["video_url"] = result["secure_url"]
            print(f"[{job_id}] Done! {result['secure_url']}")
    except Exception as e:
        print(f"[{job_id}] Error: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)

async def download_file(url: str, dest: Path):
    async with httpx.AsyncClient(timeout=120) as client:
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

async def fetch_pexels_video(query: str, dest: Path):
    keywords = query.lower().replace("-", " ").split()[:3]
    search_query = " ".join(keywords) if keywords else "technology business"
    fallback_queries = [search_query, "technology business", "laptop office", "finance money"]
    video_url = None
    async with httpx.AsyncClient(timeout=30) as client:
        for q in fallback_queries:
            try:
                r = await client.get(
                    "https://api.pexels.com/videos/search",
                    headers={"Authorization": PEXELS_API_KEY},
                    params={"query": q, "per_page": 5, "orientation": "landscape", "size": "large"}
                )
                data = r.json()
                videos = data.get("videos", [])
                for video in videos:
                    files = sorted(video.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True)
                    for f in files:
                        if f.get("width", 0) >= 1280:
                            video_url = f["link"]
                            break
                    if video_url:
                        break
                if video_url:
                    break
            except Exception as e:
                print(f"Pexels '{q}' failed: {e}")
                continue
    if not video_url:
        raise Exception("Could not find stock footage")
    await download_file(video_url, dest)

def generate_srt(script: str, duration: float, srt_path: Path):
    import re
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        srt_path.write_text("")
        return
    time_per_sentence = duration / len(sentences)
    srt_content = ""
    for i, sentence in enumerate(sentences):
        start = i * time_per_sentence
        end = (i + 1) * time_per_sentence
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

def render_ffmpeg(video_path, audio_path, srt_path, output_path, duration, orientation):
    if orientation == "landscape":
        width, height = 1920, 1080
    else:
        width, height = 1080, 1920
    subtitle_style = (
        "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=60"
    )
    filter_complex = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[v];"
        f"[v]subtitles={str(srt_path)}:force_style='{subtitle_style}'[vout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", str(duration),
        "-movflags", "+faststart",
        str(output_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise Exception(f"FFmpeg error: {result.stderr[-500:]}")
