# FileForge

FileForge is a self-hosted web service for common image and document operations. It combines a FastAPI backend, a responsive browser UI, SQLite persistence, Docker deployment, and GitHub Actions CI/CD.

## Current status

**Current status:** development / self-hosted MVP. Core local file operations are implemented. Production payment processing requires real provider credentials. AI features (image generation, image editing, photo animation) are integrated with Mixen (`https://api.mixen.ai/v1`) and become live when `MIXEN_API_KEY` is configured.

## Features

### Image tools
- **AI-генерация изображений** по текстовому описанию (Gemini Image через Mixen, `/api/image/generate`).
- **AI-редактирование изображений** по текстовому описанию (Mixen `/images/edits`, `/api/image/ai-edit`).
- **Оживление фото (Image → Video)** — Wan 3.0 через Mixen: асинхронный job, polling статусов, скачивание MP4 (`/api/photo/animate`).
- Smart Upscale ×2 / ×4. With an external AI adapter it forwards the image to the configured provider; otherwise it uses Pillow resize + sharpening. The fallback is **not generative AI**.
- JPG / JPEG / PNG / WEBP conversion.
- JPEG compression with configurable quality.

### PDF / DJVU
- PDF → PNG / JPG; multi-page results are returned as ZIP.
- Images → PDF.
- Merge multiple PDFs.
- DJVU → PDF via `ddjvu`.
- PDF → DJVU via `pdf2djvu`.

### Accounts
- Email/password registration and login.
- Password confirmation and Terms acceptance during registration.
- Optional email verification flow with one-time 24-hour token and SMTP delivery.
- Signed HTTP-only session cookie.
- SQLite persistence.
- Daily limits for guests, users and Premium users.

### Premium / integrations
- Premium order creation endpoint.
- Payment webhook with optional HMAC verification.
- Premium default price: 999 ₽ / 30 days.
- Premium includes 30 weighted AI-video seconds; 720p consumes 1 second per generated second, while 1080p consumes 3.
- Free AI-video trial is limited to 5 seconds per account.
- Optional AI upscale adapter.
- Seedance 2.0 photo animation through fal.ai queue API.

The application never fabricates a successful payment or AI result. Seedance reports a configuration error until `FAL_KEY` is configured.

## Architecture

```text
Browser
   │
   ▼
FastAPI / Uvicorn
   ├── HTML / CSS / JS frontend
   ├── Pillow
   ├── pypdf
   ├── pdf2image → Poppler
   ├── ddjvu / pdf2djvu
   └── SQLite → /data/fileforge.db
```

## Repository layout

```text
.
├── .github/workflows/
│   ├── ci.yml                    # tests, static checks, Docker build
│   └── cd.yml                    # publishes tagged Docker images to GHCR
├── app/
│   ├── main.py                   # FastAPI application and API endpoints
│   └── static/                   # frontend
├── deploy/
│   ├── nginx.conf
│   └── systemd-fileforge.service
├── tests/test_app.py
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── .dockerignore
├── requirements.txt
└── README.md
```

## Quick start — Windows PowerShell

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Open `http://localhost:8000`.

For background mode:

```powershell
docker compose up -d --build
docker compose logs -f
```

## Local Python development

Python 3.12 is the reference runtime.

```powershell
python -m venv .venv
.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:APP --reload --host 127.0.0.1 --port 8000
```

PDF/DJVU operations also require Poppler, DjVuLibre and `pdf2djvu` on the host. Docker provides the complete runtime.

## Configuration and secret setup

Copy `.env.example` to `.env`. The example file contains a detailed setup guide because the application requires several server-side credentials.

### 1. Application secret — `SECRET_KEY`

This signs the application's login/session cookies.

Generate it locally instead of inventing one:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the output into:

```text
SECRET_KEY=...
```

Use a different value for every real deployment.

### 2. fal.ai / Seedance — `FAL_KEY`

Seedance 2.0 is available through fal.ai. The official Seedance API uses the model endpoint `bytedance/seedance-2.0/image-to-video`, and fal documents `FAL_KEY` as the server-side API credential. The image-to-video API accepts an image URL plus a motion prompt and supports configurable duration/resolution. 

To obtain the key:

1. Create or sign in to a fal.ai account.
2. Open the developer/API key dashboard.
3. Create a new API key.
4. Copy the key.
5. Put it into the server-side `.env` as `FAL_KEY=...`.
6. Never expose the key to browser JavaScript or commit it to Git.

fal's documentation explicitly recommends keeping `FAL_KEY` in the runtime environment and not exposing it client-side. 

The current generic `ANIMATION_API_URL` / `ANIMATION_API_TOKEN` adapter is **not automatically compatible with fal's Seedance API**: fal's Seedance integration has its own upload/queue contract. The dedicated Seedance adapter must use that contract rather than pretending a fal key is a generic multipart-provider token. 

### 3. AI upscale credentials

If an external AI-upscale provider is selected:

- `AI_UPSCALE_URL` = the exact API endpoint from that provider's developer documentation.
- `AI_UPSCALE_TOKEN` = the provider's secret API token.

The token belongs only on the backend. Do not put it into `app/static/`, HTML, or browser JavaScript.

### 4. Photo-animation provider credentials

Seedance 2.0 is now the built-in photo-animation provider. The backend submits a queued job through fal.ai, stores the request ID in SQLite, and exposes a status endpoint until the video is ready. The browser never receives `FAL_KEY`. fal recommends the queue approach for long-running generations and documents the Seedance image-to-video endpoint and Python client. 

The UI sends:

- the source JPEG/PNG/WEBP image;
- a motion prompt;
- duration from 4–15 seconds;
- resolution 480p/720p/1080p;
- aspect ratio;
- optional synchronized audio.

The current implementation uses the Fast Seedance endpoint for 480p/720p and the standard endpoint for 1080p. The provider currently documents separate per-second pricing for these tiers, so the application should not hard-code a single universal generation cost. 

For the old generic adapter, `ANIMATION_API_URL` / `ANIMATION_API_TOKEN` remain in the configuration only for compatibility. They are not used by the built-in Seedance path.

The source image is uploaded through fal-client to obtain a temporary provider-side image URL; the browser never receives FAL_KEY. Seedance accepts JPEG, PNG and WebP starting images up to 30 MB.  

### 5. Payment credentials

The repository now includes a server-side **YooKassa payment adapter**. This is the merchant checkout path; YooKassa's payment page can offer the payment methods enabled for the shop, and its documentation includes YooMoney as a supported payment method in test mode. 

Set:

- `PAYMENT_PROVIDER=yookassa`
- `YOOKASSA_SHOP_ID` = Shop ID from the YooKassa merchant cabinet.
- `YOOKASSA_SECRET_KEY` = secret API key from the merchant cabinet.
- `PUBLIC_BASE_URL` = the public HTTPS origin of FileForge.

The backend creates a payment with an idempotency key, stores the YooKassa payment ID, redirects the customer to the returned `confirmation_url`, and grants Premium only after the server verifies a `payment.succeeded` webhook by querying YooKassa's API. It also checks the paid RUB amount against the local order and ignores duplicate paid orders. YooKassa documents the server-side API, idempotency key, redirect confirmation and `succeeded` status flow. 

Configure the YooKassa notification endpoint as:

```text
https://YOUR-DOMAIN/api/payment/webhook
```

Do not put the Shop ID/secret key in browser JavaScript.

The old `PAYMENT_API_URL` / `PAYMENT_API_TOKEN` / `PAYMENT_WEBHOOK_SECRET` variables remain only as a compatibility path for a custom payment adapter.

### 6. Database and limits

These normally do not require secrets:

| Variable | Purpose |
|---|---|
| `DATABASE` | SQLite database path |
| `ANON_DAILY_LIMIT` | Guest operations/day |
| `USER_DAILY_LIMIT` | Registered-user operations/day |
| `PREMIUM_DAILY_LIMIT` | Premium operations/day |
| `MAX_UPLOAD_MB` | Upload limit |
| `PREMIUM_PRICE_RUB` | Premium price |
| `PREMIUM_VIDEO_SECONDS` | Included weighted AI-video seconds per paid 30-day period |
| `FREE_VIDEO_TRIAL_SECONDS` | One-time free AI-video trial per account |
| `EMAIL_VERIFICATION_ENABLED` | Send email verification links via SMTP |
| `REQUIRE_EMAIL_VERIFICATION` | Block login/AI-video until email is verified |
| `RESEND_API_KEY` | Server-side Resend API key used only for transactional email sending |\n| `RESEND_FROM_EMAIL` | Verified sender address configured in Resend |
| `COOKIE_SECURE` | Secure-cookie flag; use `true` behind HTTPS |

### Important `.env` rule

The repository intentionally ignores the real `.env`.

The workflow is:

```text
.env.example
     │
     ├── copy
     ▼
   .env
     │
     ├── insert real secrets locally/server-side
     ├── test
     └── NEVER commit/upload
```

**After all keys have been entered, delete the instructional comments from the real `.env`.**

Keep `.env.example` in Git with empty placeholders so another developer can understand the configuration without receiving any secrets.

Never send API keys, payment credentials, passwords, or private tokens through chat, issues, pull requests, screenshots, or source code.

## Configuration reference

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs session cookies |
| `DATABASE` | SQLite path |
| `ANON_DAILY_LIMIT` | Guest operations/day |
| `USER_DAILY_LIMIT` | Registered-user operations/day |
| `PREMIUM_DAILY_LIMIT` | Premium operations/day |
| `MAX_UPLOAD_MB` | Upload limit |
| `PREMIUM_PRICE_RUB` | Premium price |
| `COOKIE_SECURE` | Secure cookie flag |
| `AI_UPSCALE_URL` / `AI_UPSCALE_TOKEN` | AI upscale provider |
| `ANIMATION_API_URL` / `ANIMATION_API_TOKEN` | Legacy generic animation adapter; not used by built-in Seedance |\n| `FAL_KEY` | Seedance 2.0 / fal.ai server-side API key |
| `FAL_KEY` | Seedance 2.0 / fal.ai server-side API key |
| `PAYMENT_PROVIDER` | Payment adapter; use `yookassa` for the built-in checkout |\n| `YOOKASSA_SHOP_ID` | YooKassa merchant Shop ID |\n| `YOOKASSA_SECRET_KEY` | YooKassa server-side secret key |\n| `PUBLIC_BASE_URL` | Public HTTPS origin used as payment return URL |
| `PAYMENT_API_URL` / `PAYMENT_API_TOKEN` | Payment adapter credentials |
| `PAYMENT_WEBHOOK_SECRET` | Payment webhook HMAC secret |

## Premium economics

The MVP no longer treats AI-video as an unlimited operation count. Video usage is metered in weighted seconds because the provider bills by generated video duration.

- **Free:** one 5-second AI-video trial per account, 720p.
- **Premium:** **999 ₽ / 30 days**, with **30 weighted AI-video seconds** included.
- 720p consumes 1 AI-second per generated second.
- 1080p consumes 3 AI-seconds per generated second.
- When the AI-video balance reaches zero, new AI-video jobs are rejected instead of silently creating provider costs.
- Ordinary file operations keep their separate daily limits.

These defaults are configurable with PREMIUM_PRICE_RUB, PREMIUM_VIDEO_SECONDS and FREE_VIDEO_TRIAL_SECONDS.

## Account security

Registration now requires password confirmation and Terms acceptance. The backend sends a real one-time email-verification link through the Resend API. Enable EMAIL_VERIFICATION_ENABLED=true and REQUIRE_EMAIL_VERIFICATION=true, then configure RESEND_API_KEY, RESEND_FROM_EMAIL and PUBLIC_BASE_URL. The account page also has a 'send again' action for users who did not receive the message.

## API

```text
GET  /health
GET  /api/config
GET  /api/me
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
POST /api/image/upscale
POST /api/image/convert
POST /api/image/compress
POST /api/image/generate
POST /api/image/ai-edit
POST /api/pdf/to-images
POST /api/images/to-pdf
POST /api/pdf/merge
POST /api/djvu/to-pdf
POST /api/pdf/to-djvu
POST /api/photo/animate
GET  /api/photo/animate/{token}
GET  /api/photo/animate/{token}/download
GET  /api/photo/history
POST /api/premium/create
POST /api/payment/webhook
```

## CI/CD

### CI

GitHub Actions runs on pushes to `main` and pull requests. It installs Python 3.12 plus Poppler/DJVU tools, runs Ruff, Python compilation, JavaScript syntax validation, pytest, and a Docker image build.

### CD

Pushing a tag matching `v*` publishes a Docker image to GitHub Container Registry:

```text
ghcr.io/<github-owner>/fileforge:<tag>
ghcr.io/<github-owner>/fileforge:latest
```

Example:

```bash
git tag v6.0.0
git push origin v6.0.0
```

No registry password is stored in the repository; GitHub's workflow token is used.

## Testing

```bash
pip install -r requirements.txt
pip install pytest ruff
pytest -q
ruff check app tests --select F
python -m compileall -q app tests
node --check app/static/app.js
```

The test suite covers health/home, registration/login sessions, image operations, PDF operations, DJVU/PDF conversion, invalid image handling, Seedance configuration failures, Seedance queue/status flow with mocked provider calls, and invalid generation options.

## Deployment

On a Linux VPS (Debian/Ubuntu, amd64 or arm64):

```bash
# 1. Install Docker (if not installed)
curl -fsSL https://get.docker.com | sh

# 2. Clone the repo and configure
git clone https://github.com/4704797z-design/FileForge.git /opt/fileforge
cd /opt/fileforge
cp .env.example .env

# 3. Generate a strong session secret
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# paste the value into SECRET_KEY in .env

# 4. Set MIXEN_API_KEY in .env (AI features)
# 5. Start
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 fileforge
```

The service listens on port 8000. Check `http://<server-ip>:8000/health` — it must return `{"status":"ok",...}`.

Adapt `deploy/nginx.conf` for the real domain and TLS. `deploy/systemd-fileforge.service` is an example Docker Compose service wrapper.

For production, keep the real `.env` only on the server and make sure it is not part of the Git working tree being committed.

## Production checklist

- Generate a strong random `SECRET_KEY`.
- Set `COOKIE_SECURE=true` behind HTTPS.
- Put the service behind TLS and a reverse proxy.
- Add rate limiting and abuse protection.
- Back up `/data/fileforge.db`.
- Configure a real payment provider and verify its webhook schema, signature and replay/idempotency behavior.
- Configure a real AI provider before marketing the Pillow fallback as AI.
- Configure a real photo-animation provider before enabling that paid feature.
- Replace privacy/terms templates with documents matching the actual operator, jurisdiction, retention policy and processors.
- Add monitoring, alerting and log rotation.

## License

No open-source license is declared yet. Until a license is added, normal copyright applies.
