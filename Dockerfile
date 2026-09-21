FROM python:3.12-slim

RUN set -eux; \
    for i in 1 2 3 4 5; do \
      apt-get update -o Acquire::Retries=10 && \
      apt-get install -y --no-install-recommends -o Acquire::Retries=10 \
        poppler-utils djvulibre-bin pdf2djvu ca-certificates && \
      rm -rf /var/lib/apt/lists/* && \
      exit 0; \
      echo "apt attempt $i failed; retrying..." >&2; \
      rm -rf /var/lib/apt/lists/*; \
      sleep $((i * 5)); \
    done; \
    exit 1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:APP", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
