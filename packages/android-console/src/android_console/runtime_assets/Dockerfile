FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
RUN apt-get update \
 && apt-get install -y --no-install-recommends android-tools-adb ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY python-package ./python-package
RUN pip install --no-cache-dir ./python-package && mkdir -p /files
ENTRYPOINT ["android-console"]
CMD ["serve"]
