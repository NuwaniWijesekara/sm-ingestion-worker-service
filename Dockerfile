FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV and InsightFace
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download InsightFace model
RUN python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l')" || true

COPY . .

# No HTTP port - this is a background worker

CMD ["python", "-m", "app.worker"]