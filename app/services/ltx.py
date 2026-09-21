import base64
import mimetypes
import os

import fal_client

MODEL = "fal-ai/ltx-2.3-22b/distilled/image-to-video"

DURATIONS = {"auto", *{str(i) for i in range(4, 16)}}
RESOLUTIONS = {"480p", "720p", "1080p"}
ASPECT_RATIOS = {"auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}

RESOLUTION_MAP = {
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
}


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


def dimensions(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    short = RESOLUTION_MAP[resolution]
    ratio = aspect_ratio if aspect_ratio != "auto" else "16:9"

    if ratio == "16:9":
        h = short
        w = round(h * 16 / 9)
    elif ratio == "21:9":
        h = short
        w = round(h * 21 / 9)
    elif ratio == "4:3":
        h = short
        w = round(h * 4 / 3)
    elif ratio == "3:4":
        w = short
        h = round(w * 4 / 3)
    elif ratio == "9:16":
        w = short
        h = round(w * 16 / 9)
    else:
        w = h = short

    # Keep dimensions aligned for video encoders.
    w -= w % 8
    h -= h % 8
    return max(256, w), max(256, h)


def data_uri(data: bytes, content_type: str | None) -> str:
    mime = content_type or mimetypes.guess_type("image.jpg")[0] or "image/jpeg"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def frame_count(duration: str) -> int:
    if duration == "auto":
        return 121
    return int(duration) * 24 + 1


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
        raise RuntimeError("LTX provider is not configured")

    validate_options(prompt, duration, resolution, aspect_ratio)
    width, height = dimensions(resolution, aspect_ratio)

    arguments = {
        "prompt": prompt.strip(),
        # Use a base64 data URI so the request does not depend on fal Storage.
        "image_url": data_uri(data, content_type),
        "num_frames": frame_count(duration),
        "video_size": {"width": width, "height": height},
        "generate_audio": generate_audio,
        "use_multiscale": True,
        "fps": 24,
        "scheduler": "ltx2",
        "acceleration": "none",
        "video_output_type": "X264 (.mp4)",
        "video_quality": "high",
        "video_write_mode": "balanced",
        "enable_prompt_expansion": True,
        "enable_safety_checker": True,
        "image_strength": 1,
        "end_image_strength": 1,
    }

    handle = fal_client.submit(MODEL, arguments=arguments)
    return handle.request_id


def status(request_id: str, resolution: str) -> dict:
    s = fal_client.status(MODEL, request_id, with_logs=False)
    name = s.__class__.__name__.lower()

    result = {"status": "processing" if name == "inprogress" else "queued"}

    if name == "completed":
        if getattr(s, "error", None):
            return {"status": "failed", "error": str(s.error)}

        result["status"] = "completed"
        output = fal_client.result(MODEL, request_id)
        video = output.get("video") if isinstance(output, dict) else None
        if not video or not video.get("url"):
            raise RuntimeError("LTX returned no video")

        result["video"] = video
        result["seed"] = output.get("seed")
    elif getattr(s, "error", None):
        result["status"] = "failed"
        result["error"] = str(s.error)

    return result
