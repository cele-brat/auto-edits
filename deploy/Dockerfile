# Auto Edits server. Build from the project root:  docker build -f deploy/Dockerfile -t auto-edits .
FROM python:3.12-slim
WORKDIR /app
# The headless OpenCV build needs no display libraries. MediaPipe (masks, faces) is installed without its own
# OpenCV, sound and plotting extras, which the server never uses.
RUN pip install --no-cache-dir fastapi uvicorn python-multipart numpy scipy opencv-contrib-python-headless==4.11.0.86 \
        absl-py flatbuffers \
    && pip install --no-cache-dir --no-deps mediapipe==0.10.32
COPY engine ./engine
COPY models ./models
COPY data/presets.json data/lr_response.json data/params.json ./data/
COPY server ./server
RUN useradd --uid 10001 --no-create-home autoedits && mkdir /data && chown autoedits /data
USER autoedits
# one maths thread per request: more threads than the processor share allows makes rendering erratic
ENV AUTOEDITS_DATA=/data AUTOEDITS_WARM_UP=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
