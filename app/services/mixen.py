import base64
import logging
import os
import time

import requests

log = logging.getLogger("fileforge.mixen")

API_BASE = os.getenv("MIXEN_API_BASE_URL", "https://api.mixen.ai/v1").rstrip("/")
MODEL = os.getenv("MIXEN_VIDEO_MODEL", "alibaba/wan-3.0")
IMAGE_MODEL = os.getenv("MIXEN_IMAGE_MODEL", "gemini-3.1-flash-lite-image-preview")

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


def _api_error(response: requests.Response) -> str:
    """Extract a useful, bounded error message from a Mixen response."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:1000]
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        message = err.get("message") or err.get("detail") or err.get("code")
        if message:
            return str(message)[:1000]
    if isinstance(err, str):
        return err[:1000]
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("detail")
        if message:
            return str(message)[:1000]
    return str(payload)[:1000]


def _job_id(payload: dict) -> str | None:
    """Read a job identifier from the response shapes used by video APIs."""
    if not isinstance(payload, dict):
        return None
    for key in ("id", "request_id", "job_id"):
        if payload.get(key):
            return str(payload[key])
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("id", "request_id", "job_id"):
            if data.get(key):
                return str(data[key])
    return None


def _video_url(payload: dict, request_id: str) -> str:
    if isinstance(payload, dict):
        for key in ("url", "video_url", "output_url"):
            if payload.get(key):
                return str(payload[key])
        output = payload.get("output")
        if isinstance(output, dict):
            for key in ("url", "video_url"):
                if output.get(key):
                    return str(output[key])
        data = payload.get("data")
        if isinstance(data, dict):
            return _video_url(data, request_id)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return _video_url(data[0], request_id)
    return f"{API_BASE}/videos/{request_id}/content"


def _post_with_retry(url: str, **kwargs) -> requests.Response:
    """POST with retries on transient network failures."""
    last_exc = None
    for attempt in range(3):
        try:
            return requests.post(url, timeout=120, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            log.warning("[AI_REQUEST] transient network error (attempt %d): %s", attempt + 1, type(e).__name__)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise last_exc


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
        "seconds": str(int(duration)) if duration != "auto" else "2",
        "size": size_for(resolution, aspect_ratio),
        "input_reference": {"image_url": data_uri(data, content_type)},
    }

    # Wan 3.0's documented image-to-video API does not expose an audio flag,
    # so the UI option is currently ignored rather than sending an unsupported field.
    log.info("[AI_REQUEST] POST %s/videos model=%s seconds=%s size=%s image_bytes=%d",
             API_BASE, MODEL, payload["seconds"], payload["size"], len(data))
    response = _post_with_retry(f"{API_BASE}/videos", headers=_headers(), json=payload)
    if response.status_code >= 400:
        detail = _api_error(response)
        log.error("[AI_RESPONSE] video submit failed %d: %s", response.status_code, detail)
        raise RuntimeError(f"Mixen API {response.status_code}: {detail}")
    log.info("[AI_RESPONSE] video submit ok")

    job = response.json()
    job_id = _job_id(job)
    if not job_id:
        log.error("[AI_RESPONSE] video submit returned no job id: %s", str(job)[:1000])
        raise RuntimeError("Mixen API returned no video job id")
    return job_id


def submit_text(
    prompt: str,
    duration: str,
    resolution: str,
    aspect_ratio: str,
    generate_audio: bool,
) -> str:
    """Text-to-video: create a video job from a prompt only (no input image)."""
    if not configured():
        raise RuntimeError("Mixen provider is not configured")

    validate_options(prompt, duration, resolution, aspect_ratio)

    payload = {
        "model": MODEL,
        "prompt": prompt.strip(),
        "seconds": str(int(duration)) if duration != "auto" else "2",
        "size": size_for(resolution, aspect_ratio),
    }

    log.info("[AI_REQUEST] POST %s/videos (text) model=%s seconds=%s size=%s",
             API_BASE, MODEL, payload["seconds"], payload["size"])
    response = _post_with_retry(f"{API_BASE}/videos", headers=_headers(), json=payload)
    if response.status_code >= 400:
        detail = _api_error(response)
        log.error("[AI_RESPONSE] text-video submit failed %d: %s", response.status_code, detail)
        raise RuntimeError(f"Mixen API {response.status_code}: {detail}")
    log.info("[AI_RESPONSE] text-video submit ok")

    job = response.json()
    job_id = _job_id(job)
    if not job_id:
        log.error("[AI_RESPONSE] text-video submit returned no job id: %s", str(job)[:1000])
        raise RuntimeError("Mixen API returned no video job id")
    return job_id


def status(request_id: str, resolution: str) -> dict:
    try:
        response = requests.get(
            f"{API_BASE}/videos/{request_id}",
            headers={"Authorization": f"Bearer {os.getenv('MIXEN_API_KEY', '')}"},
            timeout=60,
        )
    except requests.RequestException as e:
        log.warning("[VIDEO_STATUS] transient network error: %s", type(e).__name__)
        return {"status": "queued"}
    if response.status_code >= 400:
        detail = _api_error(response)
        log.error("[VIDEO_STATUS] poll failed %d: %s", response.status_code, detail)
        return {"status": "failed", "error": f"Mixen API {response.status_code}: {detail}"}

    job = response.json()
    state = str(job.get("status", job.get("state", "queued"))).lower()
    log.info("[VIDEO_STATUS] job=%s state=%s progress=%s", request_id, state, job.get("progress"))
    if state in {"queued", "pending", "in_progress", "processing", "submitting", "running"}:
        result = {"status": "processing" if state in {"in_progress", "processing", "running"} else "queued"}
        if job.get("progress") is not None:
            result["progress"] = job.get("progress")
        return result
    if state in {"failed", "error", "cancelled", "canceled"}:
        err = job.get("error")
        if isinstance(err, dict):
            err = err.get("message") or err.get("detail") or str(err)
        return {"status": "failed", "error": err or job.get("message") or "Mixen generation failed"}
    if state in {"completed", "succeeded", "success"}:
        return {
            "status": "completed",
            "video": {"url": _video_url(job, request_id)},
            "seed": job.get("seed"),
        }
    return {"status": "queued"}


def generate_image(prompt: str, size: str = "1024x1024") -> bytes:
    """Generate an image via Mixen OpenAI-compatible /images/generations."""
    if not configured():
        raise RuntimeError("Mixen provider is not configured")
    log.info("[AI_REQUEST] POST %s/images/generations model=%s size=%s", API_BASE, IMAGE_MODEL, size)
    response = _post_with_retry(
        f"{API_BASE}/images/generations",
        headers=_headers(),
        json={"model": IMAGE_MODEL, "prompt": prompt, "n": 1, "size": size},
    )
    if response.status_code >= 400:
        detail = _api_error(response)
        log.error("[AI_RESPONSE] image generation failed %d: %s", response.status_code, detail)
        raise RuntimeError(f"Mixen API {response.status_code}: {detail}")
    b64 = response.json()["data"][0].get("b64_json")
    if not b64:
        raise RuntimeError("Mixen image response has no image data")
    log.info("[AI_RESPONSE] image generation ok (%d bytes)", len(b64))
    return base64.b64decode(b64)


def edit_image(data: bytes, content_type: str | None, prompt: str) -> bytes:
    """Edit an image via Mixen OpenAI-compatible /images/edits (multipart)."""
    if not configured():
        raise RuntimeError("Mixen provider is not configured")
    log.info("[AI_REQUEST] POST %s/images/edits model=%s image_bytes=%d", API_BASE, IMAGE_MODEL, len(data))
    response = _post_with_retry(
        f"{API_BASE}/images/edits",
        headers={"Authorization": f"Bearer {os.getenv('MIXEN_API_KEY', '')}"},
        files={"image": ("input.png", data, content_type or "image/png")},
        data={"model": IMAGE_MODEL, "prompt": prompt, "n": "1"},
    )
    if response.status_code >= 400:
        detail = _api_error(response)
        log.error("[AI_RESPONSE] image edit failed %d: %s", response.status_code, detail)
        raise RuntimeError(f"Mixen API {response.status_code}: {detail}")
    b64 = response.json()["data"][0].get("b64_json")
    if not b64:
        raise RuntimeError("Mixen image response has no image data")
    log.info("[AI_RESPONSE] image edit ok (%d bytes)", len(b64))
    return base64.b64decode(b64)
