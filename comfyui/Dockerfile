# ComfyUI with NVIDIA GPU support, used as the default image backend.
FROM pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Pin a ComfyUI release with: docker compose build --build-arg COMFYUI_REF=v0.x.y comfyui
ARG COMFYUI_REF=v0.42.1
RUN git clone https://github.com/comfyanonymous/ComfyUI /opt/ComfyUI \
    && cd /opt/ComfyUI && git checkout "$COMFYUI_REF" \
    && pip install -r requirements.txt \
    && git clone https://github.com/city96/ComfyUI-GGUF /opt/ComfyUI/custom_nodes/ComfyUI-GGUF \
    && pip install -r /opt/ComfyUI/custom_nodes/ComfyUI-GGUF/requirements.txt

WORKDIR /opt/ComfyUI
EXPOSE 8188
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
