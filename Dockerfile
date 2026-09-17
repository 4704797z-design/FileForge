FROM python:3.12-slim

RUN apt-get update -o Acquire::Retries=5 && \
    apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
      poppler-utils djvulibre-bin pdf2djvu ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "app.main:APP", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
