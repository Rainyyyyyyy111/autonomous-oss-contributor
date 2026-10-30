FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir 'pytest>=8,<9' \
    && useradd --uid 10001 --create-home worker \
    && mkdir /work && chown -R 10001:10001 /work /opt/venv
COPY bridge.py /usr/local/lib/oss_bridge.py
COPY proxy.py /usr/local/lib/oss_proxy.py
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
USER 10001:10001
WORKDIR /work
CMD ["sleep", "infinity"]
