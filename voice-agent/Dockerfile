# ARM64 Python base (required by AgentCore Runtime). Pinned by digest for reproducible builds.
# nosemgrep: avoid-platform-with-from -- AgentCore Runtime requires linux/arm64; pin is intentional
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim@sha256:e544a7fcbdf8555eceda66bf86cafb006c736339f76141918bcb812f3174c00a

WORKDIR /app

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

COPY app.py consent.py transcripts.py wire.py roleplay.py identity_guard.py ./

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV CONTAINER_ENV=true
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping').read()"]

# Start under OpenTelemetry auto-instrumentation, as the text agent does. Span attributes that would carry
# the conversation are redacted (see OTEL_SEMCONV_STABILITY_OPT_IN in cdk/voice_stack.py).
CMD ["opentelemetry-instrument", "python", "app.py"]
