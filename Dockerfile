FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No HTTP port - this is a background worker

CMD ["python", "-m", "app.worker"]