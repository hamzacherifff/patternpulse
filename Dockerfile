FROM python:3.12-slim

WORKDIR /app

# libgomp1 is required by lightgbm's OpenMP-based training/inference on Linux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data_store logs

EXPOSE 8501
