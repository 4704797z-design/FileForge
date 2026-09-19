import os
import tempfile

import fal_client

MODEL_STANDARD = "bytedance/seedance-2.0/image-to-video"
MODEL_FAST = "bytedance/seedance-2.0/fast/image-to-video"

RESOLUTIONS = {"480p", "720p", "1080p"}
DURATIONS = {"auto", *{str(i) for i in range(4, 16)}}
ASPECT_RATIOS = {"auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}


def configured() -> bool:
    return bool(os.getenv("FAL_KEY"))


def validate_options(prompt: str, duration: str, resolution: str, aspect_ratio: str) -> None:
    if not prompt or len(prompt.strip()) < 3:
        raise ValueError("Нужно описание движения для видео")
    if len(prompt) > 2000:
        raise ValueError("Описание движения слишком длинное")
    if duration not in DURATIONS:
        raise ValueError("Недопустимая длительность")
    if resolution not in RESOLUTIONS:
        raise ValueError("Недопустимое разрешение")
    if aspect_ratio not in ASPECT_RATIOS:
        raise ValueError("Недопустимое соотношение сторон")
    if resolution == "1080p":
        return


def model_for(resolution: str) -> str:
    # fal's Fast tier currently supports up to 720p; 1080p uses standard.
    return MODEL_STANDARD if resolution == "1080p" else MODEL_FAST


def submit(data: bytes, content_type: str | None, prompt: str, duration: str,
           resolution: str, aspect_ratio: str, generate_audio: bool) -> str:
    if not configured():
        raise RuntimeError("Seedance provider is not configured")
    validate_options(prompt, duration, resolution, aspect_ratio)
    suffix = ".jpg" if content_type == "image/jpeg" else ".png" if content_type == "image/png" else ".webp"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        image_url = fal_client.upload_file(tmp.name)
    arguments = {
        "prompt": prompt.strip(),
        "image_url": image_url,
        "resolution": resolution,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
    }
    handle = fal_client.submit(model_for(resolution), arguments=arguments)
    return handle.request_id


def status(request_id: str, resolution: str) -> dict:
    model = model_for(resolution)
    s = fal_client.status(model, request_id, with_logs=False)
    name = s.__class__.__name__.lower()
    result = {"status": "processing" if name == "inprogress" else "queued"}
    if name == "completed":
        if getattr(s, "error", None):
            return {"status": "failed", "error": str(s.error)}
        result["status"] = "completed"
        output = fal_client.result(model, request_id)
        video = output.get("video") if isinstance(output, dict) else None
        if not video or not video.get("url"):
            raise RuntimeError("Seedance returned no video")
        result["video"] = video
        result["seed"] = output.get("seed")
    elif getattr(s, "error", None):
        result["status"] = "failed"
        result["error"] = str(s.error)
    return result
