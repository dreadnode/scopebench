FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates git jq dnsutils netcat-openbsd iproute2 procps sudo openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash agent \
    && mkdir /work && chown agent:agent /work
# No challenge source, solutions, verifier, Docker socket, or host mounts.
WORKDIR /work
CMD ["sleep", "infinity"]
