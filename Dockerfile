FROM python:3.11-slim

# Instalar ffmpeg e dependências
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    libffi-dev \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Evita problemas de buffer e locale
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

COPY requirements.txt .

# Atualiza pip antes de instalar
RUN pip install --no-cache-dir --upgrade pip

RUN pip install --no-cache-dir -r requirements.txt

# Garante yt-dlp atualizado (muito importante)
RUN pip install --no-cache-dir -U yt-dlp

COPY . .

CMD ["python", "main.py"]
