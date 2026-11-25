FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
ENV TAHDEER_FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/data/examgrader.db

VOLUME ["/data"]
CMD ["python", "bot.py"]
