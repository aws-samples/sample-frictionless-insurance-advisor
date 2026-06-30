# ARM64 Python base (required by AgentCore Runtime). Pinned by digest for reproducible builds.
# nosemgrep: avoid-platform-with-from -- AgentCore Runtime requires linux/arm64; pin is intentional
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim@sha256:e544a7fcbdf8555eceda66bf86cafb006c736339f76141918bcb812f3174c00a

# PyAudio is pulled in transitively by strands-agents[bidi-all] and needs
# portaudio headers even though we never open a mic/speaker in the container.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libasound-dev \
        libportaudio2 \
        libportaudiocpp0 \
        portaudio19-dev \
        gcc \
        python3-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV CONTAINER_ENV=true
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping').read()"]

CMD ["python", "app.py"]
