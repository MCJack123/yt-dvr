FROM python:3.14-trixie

WORKDIR /usr/src/app

RUN apt-get update && apt-get upgrade -y && apt-get install -y ffmpeg nodejs npm && rm -rf /var/lib/apt/lists/*

# for EJS support
RUN npm install -g deno

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY templates templates
COPY ytdvr ytdvr

RUN mkdir files
VOLUME /usr/src/app/files

EXPOSE 6334
ENV YTDVR_DB=files/ytdvr.db
ENV YTDVR_CONFIG=files/ytdvr_config.json
CMD [ "python", "ytdvr/server.py" ]
