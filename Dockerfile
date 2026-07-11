FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG CACHEBUST=1
COPY app_fastapi_v3.py .
COPY index.html .
COPY system_prompt.md .
COPY shanyuan_corpus.csv .
COPY public/ public/

EXPOSE 8080

CMD uvicorn app_fastapi_v3:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
