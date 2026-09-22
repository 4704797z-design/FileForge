import base64
import os

import requests

API_BASE = os.getenv("MIXEN_API_BASE_URL", "https://api.mixen.ai/v1").rstrip("/")
MODEL = os.getenv("MIXEN_VIDEO_MODEL", "alibaba/wan-3.0")

DURATIONS = {"auto", *{str(i) for i in range(2, 31)}}
RESOLUTION_HEIGHTS = {"480p": 480, "720p": 720, "1080p": 1080}
ASPECT_SIZES = {
    "16:9": {"480p": "852x480", "720p": "1280x720", "1080p": "1920x1080"},
    "9:16": {"480p": "480x852", "720p": "720x1280", "1080p": "1080x1920"},
    "1:1": {"480p": "480x480", "720p": "720x720", "1080p": "1080x1080"},
    "4:3": {"480p": "640x480", "720p": "960x720", "1080p": "1440x1080"},
    "3:4": {"480p": "480x640", "720p": "720x960", "1080p": "1080x1440"},
}


def configured() -> bool:
    return bool(os.getenv("MIXEN_API_KEY"))


def _headers() -> dict:
    key = os.getenv("MIXEN_API_KEY", "")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def validate_options(prompt: str, duration: str, resolution: str, aspect_ratio: str) -> None:
    if not prompt or len(prompt.strip()) < 3:
        raise ValueError("Нужно описание движения для видео")
    if len(prompt) > 30000:
        raise ValueError("Описание движения слишком длинное")
    if duration not in DURATIONS:
        raise ValueError("Недопустимая длительность")
    if resolution not in RESOLUTION_HEIGHTS:
        raise ValueError("Недопустимое разрешение")
    if aspect_ratio not in {"auto", *ASPECT_SIZES.keys()}:
        raise ValueError("Недопустимое соотношение сторон")


def data_uri(data: bytes, content_type: str | None) -> str:
    mime = content_type or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def size_for(resolution: str, aspect_ratio: str) -> str:
    ratio = aspect_ratio if aspect_ratio != "auto" else "16:9"
    return ASPECT_SIZES[ratio][resolution]


def submit(
    data: bytes,
    content_type: str | None,
    prompt: str,
    duration: str,
    resolution: str,
    aspect_ratio: str,
    generate_audio: bool,
) -> str:
    if not configured():
        raise RuntimeError("Mixen provider is not configured")

    validate_options(prompt, duration, resolution, aspect_ratio)

    payload = {
        "model": MODEL,
        "prompt": prompt.strip(),
        "seconds": int(duration) if duration != "auto" else 2,
        "size": size_for(resolution, aspect_ratio),
        "input_reference": {"image_url": data_uri(data, content_type)},
    }

    # Wan 3.0's documented image-to-video API does not expose an audio flag,
    # so the UI option is currently ignored rather than sending an unsupported field.
    response = requests.post(
        f"{API_BASE}/videos",
        headers=_headers(),
        json=payload,
        timeout=60,
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:1000]
        raise RuntimeError(f"Mixen API {response.status_code}: {detail}")

    job = response.json()
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError("Mixen API returned no video job id")
    return str(job_id)


def status(request_id: str, resolution: str) -> dict:
    response = requests.get(
        f"{API_BASE}/videos/{request_id}",
        headers={"Authorization": f"Bearer {os.getenv('MIXEN_API_KEY', '')}"},
        timeout=60,
    )
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:1000]
        return {"status": "failed", "error": f"Mixen API {response.status_code}: {detail}"}

    job = response.json()
    state = job.get("status", "queued")
    if state in {"queued", "in_progress", "processing", "submitting"}:
        return {"status": "processing" if state in {"in_progress", "processing"} else "queued"}
    if state == "failed":
        return {"status": "failed", "error": job.get("error") or job.get("message") or "Mixen generation failed"}
    if state == "completed":
        return {
            "status": "completed",
            "video": {"url": f"{API_BASE}/videos/{request_id}/content"},
            "seed": job.get("seed"),
        }
    return {"status": "queued"}
