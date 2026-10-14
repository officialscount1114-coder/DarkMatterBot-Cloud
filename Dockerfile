# DarkMatter Traffic Bot - Production Cloud & Railway Dockerfile
FROM python:3.11-slim

# Prevent Python from writing pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure resources directory exists
RUN mkdir -p resources

# Expose default port (Railway dynamically injects $PORT)
EXPOSE 8765 8080 80

# Default command: run Relay server (auto-detects $PORT and binds 0.0.0.0)
CMD ["python", "relay.py"]
