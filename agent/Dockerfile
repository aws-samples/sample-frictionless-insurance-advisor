# Python 3.13.18 on Alpine 3.24, pinned by tag and digest for reproducible builds.
FROM python:3.13.18-alpine3.24@sha256:9ef1a599f863231c3d91e941389bfdc4a2d6070557243d54df1a4e57015b960e

# Install system dependencies
RUN apk add --no-cache gcc musl-dev

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Copy application code
COPY app.py form_tools.py form_rules.py sources.py note_guard.py language_reminder.py write_guard.py identity_guard.py guardrail_trace.py container_cache.py memory_prefetch.py customer_access.py verified_advisor.py input_mask.py limits.py ./

# Create non-root user and switch to it
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

# Expose port 8080 for AgentCore Runtime
EXPOSE 8080

# Healthcheck using TCP check on port 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import socket; s=socket.create_connection(('localhost',8080),2); s.close()"]

# Set the command to run the AgentCore app with OpenTelemetry instrumentation
CMD ["opentelemetry-instrument", "python", "app.py"]
