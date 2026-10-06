FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tests/requirements.txt
RUN pip install --no-cache-dir -r /tests/requirements.txt \
    && python3 -c 'from rewardkit.agents import _ensure_claude_installed; _ensure_claude_installed("2.1.282")'
ENV PYTHONPATH=/tests
WORKDIR /work
COPY . /tests/
RUN chmod +x /tests/test.sh
