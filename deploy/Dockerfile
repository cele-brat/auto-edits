# Auto Edits server. Build from the project root:  docker build -f deploy/Dockerfile -t auto-edits .
FROM python:3.12-slim
WORKDIR /app
# The headless OpenCV build needs no display libraries. MediaPipe (masks, faces) is installed without its own
# OpenCV and sound extras, which the server never uses.
# Every version is pinned (the same as requirements.txt), so a rebuild never picks up a surprise upgrade.
RUN pip install --no-cache-dir fastapi==0.146.0 starlette==1.6.0 pydantic==2.13.5 uvicorn==0.53.0 python-multipart==0.0.32 \
        numpy==2.5.3 scipy==1.18.1 opencv-contrib-python-headless==4.11.0.86 absl-py==2.5.0 flatbuffers==25.12.19 \
        matplotlib==3.11.2 boto3==1.43.114 pillow==12.3.0 segno==1.6.6 defusedxml==0.7.1 \
    && pip install --no-cache-dir --no-deps mediapipe==0.10.32
COPY engine ./engine
COPY models ./models
COPY data/presets.json data/lr_response.json data/params.json ./data/
COPY server ./server
# the Lightroom plug-in, zipped on request at /download/lightroom-plugin
COPY plugin/AutoEdits.lrplugin ./plugin/AutoEdits.lrplugin
RUN useradd --uid 10001 --no-create-home autoedits && mkdir /data && chown autoedits /data
USER autoedits
# one maths thread per request: more threads than the processor share allows makes rendering erratic
ENV AUTOEDITS_DATA=/data AUTOEDITS_WARM_UP=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLCONFIGDIR=/tmp/matplotlib
EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
