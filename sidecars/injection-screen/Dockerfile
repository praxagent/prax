# Prompt-injection screen sidecar. Run it capped: it holds a ~740 MB model and
# peaks near 1.8 GB while scoring (measured).
#   docker build -t prax-injection-screen .
#   docker run -d --name injection-screen --memory 2g --memory-swap 2g \
#     -p 127.0.0.1:8795:8795 -e INJECTION_SCREEN_HOST=0.0.0.0 prax-injection-screen
FROM python:3.13-slim
RUN pip install --no-cache-dir onnxruntime tokenizers numpy && apt-get update \
    && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY server.py fetch_model.sh ./
RUN sh fetch_model.sh /models/prompt-injection
ENV INJECTION_SCREEN_MODEL_DIR=/models/prompt-injection
USER nobody
EXPOSE 8795
CMD ["python", "server.py"]
