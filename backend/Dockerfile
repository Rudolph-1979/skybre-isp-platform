FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for psycopg2, general build tooling, correct local
# timestamps in logs (tzdata — slim images don't include it by default),
# and iputils-ping (radiusauth.pingcheck -- powers the online/offline
# status on Networking -> RADIUS Clients; needs the suid-root system
# ping binary since raw ICMP sockets aren't available to this container)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl tzdata iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
