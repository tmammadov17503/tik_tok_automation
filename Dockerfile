FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV VIDEO_AGENT_HOST=0.0.0.0
ENV VIDEO_AGENT_PORT=8765

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8765

CMD ["python", "app.py"]
