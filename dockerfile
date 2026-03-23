FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install chromium && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        xvfb sudo x11-utils xserver-xorg-video-dummy && \
    rm -rf /var/lib/apt/lists/*

# даём sudo pwuser
RUN echo "pwuser ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

COPY . .

RUN mkdir -p data/photos data/logs

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

USER root

CMD ["./entrypoint.sh"]
