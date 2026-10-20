# kakao-cli control client — OS-independent (runs anywhere Docker runs).
# It only needs adb + Python; it drives the Android (redroid) instance over adb TCP.
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends android-tools-adb ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md NOTICE ./
COPY src ./src
COPY scripts/compose-boot.sh ./scripts/compose-boot.sh
RUN python -m venv /app/.venv \
 && /app/.venv/bin/pip install --no-cache-dir .
ENV PATH="/app/.venv/bin:$PATH"
RUN mkdir -p /files /root/.cache/kakao-cli

# adb must reach the redroid host (e.g. the Waydroid/redroid VM) over the network.
# Example: docker run --rm kakao-cli doctor --connect 192.168.0.30:5555
ENTRYPOINT ["kakao"]
CMD ["--help"]
