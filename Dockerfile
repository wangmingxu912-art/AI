FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY web /app/web

ENV APP_WEB_DIR=/app/web
ENV APP_DATA_DIR=/app/data

# cloud platforms (Render/Railway/Fly) typically inject PORT
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

