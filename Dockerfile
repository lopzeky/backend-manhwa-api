# Usamos Python ligero
FROM python:3.9-slim

# 1. Instalar Tesseract (OCR) y sus idiomas (Coreano y Español)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    tesseract-ocr-kor \
    tesseract-ocr-spa \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2. Configurar carpeta
WORKDIR /app

# 3. Instalar librerías
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copiar código
COPY . .

# 5. Encender servidor
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
