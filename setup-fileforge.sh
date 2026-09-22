#!/bin/bash
# FileForge server setup — run on the VPS: bash setup-fileforge.sh
set -e

cd /root/FileForge

echo "=== 1/4 Writing .env ==="
cat > .env << 'ENVEOF'
SECRET_KEY=hDx8Ox_riqZ6L09xrkLZwhk-NqIVIrVRN0FDRRMts7W6Jt8GsNAljPpnSkL7UGkK
DATABASE=/data/fileforge.db
ANON_DAILY_LIMIT=5
USER_DAILY_LIMIT=20
PREMIUM_DAILY_LIMIT=200
MAX_UPLOAD_MB=50
PREMIUM_PRICE_RUB=999
COOKIE_SECURE=false
MIXEN_API_BASE_URL=https://api.mixen.ai/v1
MIXEN_API_KEY=PASTE_YOUR_MIXEN_KEY_HERE
MIXEN_VIDEO_MODEL=alibaba/wan-3.0
PAYMENT_PROVIDER=yookassa
ENVEOF
echo ".env written"

echo "=== 2/4 Building image (3-10 min, be patient) ==="
docker compose build

echo "=== 3/4 Starting container ==="
docker compose up -d

echo "=== 4/4 Waiting for health ==="
for i in $(seq 1 30); do
  if curl -sf -m 3 http://localhost:8000/health > /dev/null 2>&1; then
    echo ""
    echo "SUCCESS: FileForge is running"
    curl -s http://localhost:8000/health
    echo ""
    echo "Public URL: http://194.87.99.30:8000"
    exit 0
  fi
  sleep 2
done

echo ""
echo "Health check failed. Recent logs:"
docker compose logs --tail=40 fileforge
