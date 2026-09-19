import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "fileforge-test.db"
    monkeypatch.setenv("DATABASE", str(db_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-please-use-a-real-secret-in-production")
    monkeypatch.setenv("EMAIL_VERIFICATION_ENABLED", "false")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "false")
    monkeypatch.setenv("FREE_VIDEO_TRIAL_SECONDS", "5")
    monkeypatch.setenv("PREMIUM_VIDEO_SECONDS", "30")
    monkeypatch.setenv("ANON_DAILY_LIMIT", "50")
    monkeypatch.setenv("USER_DAILY_LIMIT", "50")
    monkeypatch.setenv("PREMIUM_DAILY_LIMIT", "200")
    import importlib
    import app.main as main
    importlib.reload(main)
    return TestClient(main.APP)

def png_bytes():
    img = Image.new("RGB", (16, 12), "white")
    buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()

def pdf_bytes():
    writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO(); writer.write(buf); return buf.getvalue()

def test_health_and_home(client):
    assert client.get("/health").json()["status"] == "ok"
    assert "FileForge" in client.get("/").text
    assert client.get("/api/config").status_code == 200

def test_register_login_session(client):
    r = client.post("/api/auth/register", data={"email":"test@example.com","password":"password123","password_confirm":"password123","accept_terms":"true"})
    assert r.status_code == 200
    assert client.get("/api/me").json()["authenticated"] is True
    client.post("/api/auth/logout")
    assert client.get("/api/me").json()["authenticated"] is False
    r = client.post("/api/auth/login", data={"email":"test@example.com","password":"password123"})
    assert r.status_code == 200
    assert client.get("/api/me").json()["authenticated"] is True

def test_image_operations(client):
    payload = {"file": ("test.png", png_bytes(), "image/png")}
    r = client.post("/api/image/convert", files=payload, data={"fmt":"webp"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/webp")
    r = client.post("/api/image/compress", files=payload, data={"quality":"80"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/jpeg")
    r = client.post("/api/image/upscale", files=payload, data={"scale":"2"})
    assert r.status_code == 200
    with Image.open(io.BytesIO(r.content)) as out: assert out.size == (32, 24)

def test_pdf_operations(client):
    pdf = pdf_bytes(); payload = {"file": ("test.pdf", pdf, "application/pdf")}
    r = client.post("/api/pdf/merge", files=[("files", payload["file"])]); assert r.status_code == 200
    r = client.post("/api/pdf/to-images", files=payload, data={"fmt":"png"}); assert r.status_code == 200
    r = client.post("/api/images/to-pdf", files=[("files", ("page.png", png_bytes(), "image/png"))]); assert r.status_code == 200
    r = client.post("/api/pdf/to-djvu", files=payload); assert r.status_code == 200
    djvu = r.content; assert djvu.startswith(b"AT&TFORM")
    r = client.post("/api/djvu/to-pdf", files={"file":("test.djvu", djvu, "image/vnd.djvu")}); assert r.status_code == 200

def test_invalid_image_is_rejected(client):
    r = client.post("/api/image/convert", files={"file":("bad.txt",b"not an image","text/plain")}, data={"fmt":"png"})
    assert r.status_code == 400

def test_animation_requires_provider(client, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    client.post("/api/auth/register", data={"email":"anim-provider@example.com","password":"password123","password_confirm":"password123","accept_terms":"true"})
    r = client.post("/api/photo/animate", files={"file":("test.png", png_bytes(), "image/png")}, data={"prompt":"slow camera movement"})
    assert r.status_code == 503

def test_animation_queue_and_status(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    import app.main as main
    monkeypatch.setattr(main.seedance, "configured", lambda: True)
    monkeypatch.setattr(main.seedance, "submit", lambda *args, **kwargs: "req-test-123")
    states = [{"status":"processing"}, {"status":"completed","video":{"url":"https://example.invalid/video.mp4","content_type":"video/mp4"},"seed":7}]
    monkeypatch.setattr(main.seedance, "status", lambda *args, **kwargs: states.pop(0))
    client.post("/api/auth/register", data={"email":"anim-queue@example.com","password":"password123","password_confirm":"password123","accept_terms":"true"})
    payload = {"file":("test.png", png_bytes(), "image/png")}
    r = client.post("/api/photo/animate", files=payload, data={"prompt":"slow camera movement","duration":"5","resolution":"720p","aspect_ratio":"auto","generate_audio":"true"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert client.get(f"/api/photo/animate/{token}").json()["status"] == "processing"
    result = client.get(f"/api/photo/animate/{token}").json()
    assert result["status"] == "completed"
    assert result["video"]["url"].startswith("https://")

def test_animation_rejects_bad_options(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    import app.main as main
    monkeypatch.setattr(main.seedance, "configured", lambda: True)
    client.post("/api/auth/register", data={"email":"anim-options@example.com","password":"password123","password_confirm":"password123","accept_terms":"true"})
    r = client.post("/api/photo/animate", files={"file":("test.png", png_bytes(), "image/png")}, data={"prompt":"x","duration":"99"})
    assert r.status_code == 400

def test_yookassa_payment_flow_is_verified_and_idempotent(client, monkeypatch):
    monkeypatch.setenv("PAYMENT_PROVIDER", "yookassa")
    import app.main as main
    monkeypatch.setattr(main.yookassa, "configured", lambda: True)
    monkeypatch.setattr(main.yookassa, "create_payment", lambda order_id, amount: {"id":"pay-test-1","status":"pending","checkout_url":"https://pay.example/1"})
    monkeypatch.setattr(main.yookassa, "get_payment", lambda payment_id: {"id":payment_id,"status":"succeeded","amount":{"value":"999.00","currency":"RUB"},"metadata":{"order_id":"1"}})
    client.post("/api/auth/register", data={"email":"pay@example.com","password":"password123","password_confirm":"password123","accept_terms":"true"})
    r = client.post("/api/premium/create")
    assert r.status_code == 200
    assert r.json()["checkout_url"] == "https://pay.example/1"
    r = client.post("/api/payment/webhook", json={"event":"payment.succeeded","object":{"id":"pay-test-1"}})
    assert r.status_code == 200
    assert client.get("/api/me").json()["premium"] is True
    assert client.get("/api/me").json()["video_seconds_remaining"] == 30
    r = client.post("/api/payment/webhook", json={"event":"payment.succeeded","object":{"id":"pay-test-1"}})
    assert r.status_code == 200
    assert r.json()["idempotent"] is True

def test_seedance_submit_uses_fal_upload(monkeypatch):
    import app.services.seedance as seedance
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setattr(seedance.fal_client, "upload_file", lambda path: "https://fal.example/input.png")
    class Handle:
        request_id = "req-upload-test"
    seen = {}
    def fake_submit(model, arguments):
        seen["model"] = model
        seen["arguments"] = arguments
        return Handle()
    monkeypatch.setattr(seedance.fal_client, "submit", fake_submit)
    request_id = seedance.submit(png_bytes(), "image/png", "slow camera movement", "5", "720p", "auto", True)
    assert request_id == "req-upload-test"
    assert seen["model"] == "bytedance/seedance-2.0/fast/image-to-video"
    assert seen["arguments"]["image_url"] == "https://fal.example/input.png"

def test_registration_requires_password_confirmation_and_terms(client):
    r = client.post("/api/auth/register", data={"email":"bad@example.com","password":"password123","password_confirm":"nope","accept_terms":"false"})
    assert r.status_code == 400
    assert "Пароли не совпадают" in r.json()["detail"]

def test_free_video_trial_is_limited_and_account_required(client, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    import app.main as main
    monkeypatch.setattr(main.seedance, "configured", lambda: True)
    monkeypatch.setattr(main.seedance, "submit", lambda *args, **kwargs: "req-free")
    client.post("/api/auth/register", data={"email":"free@example.com","password":"password123","password_confirm":"password123","accept_terms":"true"})
    r = client.post("/api/photo/animate", files={"file":("test.png",png_bytes(),"image/png")}, data={"prompt":"slow camera movement","duration":"5","resolution":"720p","aspect_ratio":"auto","generate_audio":"false"})
    assert r.status_code == 200
    r = client.post("/api/photo/animate", files={"file":("test.png",png_bytes(),"image/png")}, data={"prompt":"slow camera movement","duration":"5","resolution":"720p","aspect_ratio":"auto","generate_audio":"false"})
    assert r.status_code == 402

def test_verify_email_flow(client, monkeypatch):
    monkeypatch.setenv("EMAIL_VERIFICATION_ENABLED", "true")
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    import app.main as main
    sent = {}
    monkeypatch.setattr(main, "send_verification_email", lambda email, token: sent.update({"email":email,"token":token}) or True)
    r = client.post("/api/auth/register", data={"email":"verify@example.com","password":"password123","password_confirm":"password123","accept_terms":"true"})
    assert r.status_code == 200
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", data={"email":"verify@example.com","password":"password123"})
    assert r.status_code == 403
    r = client.get("/api/auth/verify", params={"token":sent["token"]})
    assert r.status_code == 200
    r = client.post("/api/auth/login", data={"email":"verify@example.com","password":"password123"})
    assert r.status_code == 200
