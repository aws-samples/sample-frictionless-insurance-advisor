FROM python:3.13-alpine@sha256:bb1f2fdb1065c85468775c9d680dcd344f6442a2d1181ef7916b60a623f11d40

# Install system dependencies
RUN apk add --no-cache gcc musl-dev

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

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
