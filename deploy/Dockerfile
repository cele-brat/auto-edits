# Auto Edits server. Build from the project root:  docker build -f deploy/Dockerfile -t auto-edits .
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn python-multipart numpy scipy opencv-python-headless==4.11.0.86
COPY engine ./engine
COPY data/presets.json data/lr_response.json data/params.json ./data/
COPY server ./server
RUN useradd --uid 10001 --no-create-home autoedits && mkdir /data && chown autoedits /data
USER autoedits
ENV AUTOEDITS_DATA=/data AUTOEDITS_WARM_UP=1
EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
