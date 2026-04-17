import os
import uuid
import httpx
import subprocess
import tempfile
import random
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
    workdir = Path(f"/tmp/render_{job_id}")
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        print(f"[{job_id}] Downloading audio...")
        audio_path = workdir / "audio.mp3"
        await download_file(req.audio_url, audio_path)
        duration = get_audio_duration(audio_path)
        print(f"[{job_id}] Duration: {duration}s")

        print(f"[{job_id}] Fetching stock footage...")
        video_path = workdir / "footage.mp4"
        await fetch_pexels_video(req.title, video_path, duration)

        print(f"[{job_id}] Generating subtitles...")
        srt_path = workdir / "subs.srt"
        ass_path = workdir / "subs.ass"
        generate_srt(req.script, duration, srt_path)
        convert_srt_to_ass(srt_path, ass_path)

        print(f"[{job_id}] Rendering final video...")
        output_path = workdir / f"output_{job_id}.mp4"
        render_ffmpeg(video_path, audio_path, ass_path, output_path, duration, req.orientation)

        print(f"[{job_id}] Uploading to Cloudinary...")
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
        import traceback
        print(f"[{job_id}] Error: {e}")
        print(traceback.format_exc())
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

async def download_file(url: str, dest: Path):
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
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

async def fetch_pexels_video(query: str, dest: Path, total_duration: float):
    """
    Fetch clips from Pexels and build a dynamic edit:
    - First 10s: clips of 2-3s (hook, high energy)
    - 10s-60s: clips of 4-5s
    - After 60s: clips of 6-8s
    """
    # Finance/money themed search queries
    search_queries = [
        "stock market trading",
        "money cash wealth",
        "entrepreneur business success",
        "city skyline financial district",
        "laptop working from home",
        "luxury lifestyle",
        "investment portfolio",
        "startup office team",
        "data analytics dashboard",
        "real estate property",
        "cryptocurrency bitcoin",
        "successful businessman",
        "passive income online",
        "financial freedom travel",
        "banking finance",
    ]
    random.shuffle(search_queries)

    all_clips = []
    async with httpx.AsyncClient(timeout=60) as client:
        for q in search_queries:
            try:
                r = await client.get(
                    "https://api.pexels.com/videos/search",
                    headers={"Authorization": PEXELS_API_KEY},
                    params={"query": q, "per_page": 5, "orientation": "landscape", "size": "medium"}
                )
                data = r.json()
                videos = data.get("videos", [])
                for video in videos[:2]:
                    files = sorted(video.get("video_files", []), key=lambda x: x.get("width", 0), reverse=True)
                    for f in files:
                        if f.get("width", 0) >= 1920:
                            all_clips.append(f["link"])
                            break
                    else:
                        # fallback to 1280
                        for f in files:
                            if f.get("width", 0) >= 1280:
                                all_clips.append(f["link"])
                                break
            except Exception as e:
                print(f"Pexels '{q}' failed: {e}")
                continue

    if not all_clips:
        raise Exception("Could not find stock footage from Pexels")

    random.shuffle(all_clips)
    print(f"Found {len(all_clips)} clips total")

    # Build clip duration schedule based on timeline position
    # We'll generate enough clips to cover total_duration
    clip_schedule = []
    t = 0.0
    idx = 0
    while t < total_duration:
        if t < 10:
            clip_dur = random.uniform(2, 3)
        elif t < 60:
            clip_dur = random.uniform(4, 5)
        else:
            clip_dur = random.uniform(6, 8)
        clip_schedule.append((idx % len(all_clips), clip_dur))
        t += clip_dur
        idx += 1

    print(f"Need {len(clip_schedule)} clip slots for {total_duration:.1f}s video")

    # Download unique clips needed
    unique_indices = list(set(i for i, _ in clip_schedule))
    clip_dir = dest.parent / "clips"
    clip_dir.mkdir(exist_ok=True)
    downloaded = {}

    async with httpx.AsyncClient(timeout=60) as client:
        for i in unique_indices:
            url = all_clips[i]
            raw_path = clip_dir / f"raw_{i:03d}.mp4"
            clip_path = clip_dir / f"clip_{i:03d}.mp4"
            try:
                r = await client.get(url, timeout=45)
                if r.status_code == 200:
                    raw_path.write_bytes(r.content)
                    # Normalize to 1920x1080, no audio
                    trim = subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(raw_path),
                        "-vf", (
                            "scale=1920:1080:force_original_aspect_ratio=increase,"
                            "crop=1920:1080,setsar=1,fps=30"
                        ),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                        "-an",
                        str(clip_path)
                    ], capture_output=True, text=True, timeout=120)
                    raw_path.unlink(missing_ok=True)
                    if trim.returncode == 0 and clip_path.exists():
                        downloaded[i] = clip_path
                        print(f"  Processed clip {len(downloaded)}/{len(unique_indices)}")
                    else:
                        print(f"  Clip {i} encode failed: {trim.stderr[-200:]}")
            except Exception as e:
                print(f"  Clip {i} failed: {e}")
                if raw_path.exists():
                    raw_path.unlink(missing_ok=True)

    if not downloaded:
        raise Exception("No clips downloaded successfully")

    print(f"Downloaded {len(downloaded)} unique clips, building edit...")

    # Build final clip list with exact durations
    trimmed_dir = dest.parent / "trimmed"
    trimmed_dir.mkdir(exist_ok=True)
    concat_parts = []

    for slot, (clip_idx, clip_dur) in enumerate(clip_schedule):
        src = downloaded.get(clip_idx)
        if src is None:
            # fallback to any available clip
            src = list(downloaded.values())[slot % len(downloaded)]

        out = trimmed_dir / f"seg_{slot:04d}.mp4"

        # Get clip duration
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(src)
        ], capture_output=True, text=True)
        try:
            src_dur = float(probe.stdout.strip())
        except:
            src_dur = 30.0

        # Random start offset within clip
        max_start = max(0, src_dur - clip_dur - 0.5)
        start_offset = random.uniform(0, max_start) if max_start > 0 else 0

        trim = subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(start_offset),
            "-i", str(src),
            "-t", str(clip_dur),
            "-c", "copy",
            str(out)
        ], capture_output=True, text=True, timeout=30)

        if trim.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            concat_parts.append(out)

    if not concat_parts:
        raise Exception("No trimmed segments created")

    print(f"Built {len(concat_parts)} segments, concatenating...")

    concat_file = dest.parent / "concat.txt"
    with open(concat_file, "w") as f:
        for cp in concat_parts:
            f.write(f"file '{cp}'\n")

    result = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-an",
        str(dest)
    ], capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        raise Exception(f"Concat failed: {result.stderr[-500:]}")

    print(f"Final footage size: {dest.stat().st_size / 1024 / 1024:.1f}MB")


def generate_srt(script: str, duration: float, srt_path: Path):
    import re
    # Split into natural phrases/sentences
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        srt_path.write_text("")
        return

    # Calculate words per second based on typical TTS speed
    total_words = sum(len(s.split()) for s in sentences)
    wps = total_words / duration  # words per second

    srt_content = ""
    idx = 1
    current_time = 0.0

    for sentence in sentences:
        words = sentence.split()
        # Split into chunks of max 6 words for readability
        chunks = [" ".join(words[j:j+6]) for j in range(0, len(words), 6)]
        for chunk in chunks:
            chunk_words = len(chunk.split())
            chunk_dur = chunk_words / wps
            chunk_dur = max(0.8, min(chunk_dur, 4.0))  # clamp between 0.8s and 4s
            end_time = current_time + chunk_dur
            srt_content += f"{idx}\n{format_time(current_time)} --> {format_time(end_time)}\n{chunk}\n\n"
            current_time = end_time
            idx += 1

    srt_path.write_text(srt_content, encoding="utf-8")


def convert_srt_to_ass(srt_path: Path, ass_path: Path):
    """Convert SRT to ASS with professional styling"""
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(srt_path),
        str(ass_path)
    ], capture_output=True, text=True)

    if result.returncode != 0 or not ass_path.exists():
        # Fallback: manual ASS generation
        ass_path.write_text(generate_ass_from_srt(srt_path))
        return

    # Override styles in the ASS file for professional look
    content = ass_path.read_text(encoding="utf-8")

    # Replace the Style line with our custom style
    import re
    style_line = (
        "Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,"
        "1,0,0,0,100,100,0,0,1,3,2,2,10,10,50,1"
    )
    content = re.sub(r"Style: Default.*", style_line, content)
    ass_path.write_text(content, encoding="utf-8")


def generate_ass_from_srt(srt_path: Path) -> str:
    """Manual ASS generation as fallback"""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,3,2,2,10,10,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = ""
    content = srt_path.read_text(encoding="utf-8")
    import re
    pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+?)(?=\n\n|\Z)', re.DOTALL)
    for m in pattern.finditer(content):
        start = m.group(2).replace(",", ".").replace(":", "\\:")
        end = m.group(3).replace(",", ".").replace(":", "\\:")
        # Proper ASS time format: H:MM:SS.cs
        def srt_time_to_ass(t):
            t = t.replace("\\:", ":")
            h, rest = t.split(":", 1)
            m2, s = rest.rsplit(":", 1)
            s, ms = s.split(".")
            cs = int(ms) // 10
            return f"{h}:{m2}:{s}.{cs:02d}"
        start_ass = srt_time_to_ass(m.group(2).replace(",", "."))
        end_ass = srt_time_to_ass(m.group(3).replace(",", "."))
        text = m.group(4).strip().replace("\n", "\\N")
        events += f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}\n"
    return header + events


def format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_ffmpeg(video_path, audio_path, ass_path, output_path, duration, orientation):
    width, height = 1280, 720  # 720p - faster render, still good for YouTube

    if not video_path.exists() or video_path.stat().st_size < 1000:
        raise Exception(f"Video file missing or too small: {video_path}")
    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        raise Exception(f"Audio file missing or too small: {audio_path}")

    print(f"Video: {video_path.stat().st_size/1024/1024:.1f}MB | Audio: {audio_path.stat().st_size/1024/1024:.1f}MB")

    # Build subtitle filter
    has_subs = ass_path.exists() and ass_path.stat().st_size > 100
    if has_subs:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,"
            f"ass={ass_path}"
        )
    else:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1"
        )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1",
        "-t", str(duration),
        "-i", str(video_path),
        "-t", str(duration),
        "-i", str(audio_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "26",
        "-threads", "2",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-f", "mp4",
        str(output_path)
    ]

    print(f"Rendering {duration:.1f}s at {width}x{height}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    print(f"FFmpeg exit: {result.returncode}")
    if result.returncode != 0:
        print(f"FFmpeg stderr: {result.stderr[-1000:]}")
        raise Exception(f"FFmpeg error: {result.stderr[-800:]}")

    if not output_path.exists():
        raise Exception(f"Output file not created: {output_path}")

    print(f"Output: {output_path.stat().st_size/1024/1024:.1f}MB")
