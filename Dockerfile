# Usamos una versión ligera de Python
FROM python:3.9-slim

# 1. Instalar Tesseract (OCR) y sus idiomas + dependencias del sistema
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    tesseract-ocr-kor \
    tesseract-ocr-spa \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2. Crear carpeta de trabajo
WORKDIR /app

# 3. Copiar las librerías de python e instalarlas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copiar el resto del código (main.py)
COPY . .

# 5. El comando para encender el servidor en el puerto 10000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
