# Add to compose.yml for healthcheck:
#   healthcheck:
#     test: curl -f http://localhost:6334/api/healthcheck || exit 1
#     interval: 30s
#     timeout: 10s
#     retries: 5
#     start_period: 30s

FROM python:3.14-trixie

WORKDIR /usr/src/app

RUN apt-get update && apt-get upgrade -y && apt-get install -y ffmpeg nodejs npm && rm -rf /var/lib/apt/lists/*

# for EJS support
RUN npm install -g deno

COPY pyproject.toml .
COPY src src
COPY templates templates
RUN pip install --no-cache-dir .[kick,youtube,rumble]

RUN mkdir files
VOLUME /usr/src/app/files

EXPOSE 6334
ENV YTDVR_DB=files/ytdvr.db
ENV YTDVR_CONFIG=files/ytdvr_config.json
CMD [ "python", "-m", "yt_dvr" ]
