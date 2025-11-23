from flask import Flask, request, send_file, render_template
from asgiref.wsgi import WsgiToAsgi
import config
from hypercorn import config as hypercorn_config
from hypercorn.asyncio import serve
import logging
import os
import datetime
import channel as channels

LOG = logging.getLogger("yt-dvr")

app = Flask("yt-dvr")
app.logger.setLevel(logging.DEBUG)
app.config['TEMPLATES_AUTO_RELOAD'] = True

def formattime(timestamp) -> str: return datetime.datetime.fromtimestamp(timestamp).strftime("%c")
def formatdate(timestamp) -> str: return datetime.date.fromtimestamp(timestamp).strftime("%x")

@app.route("/")
def home():
    videos = [info._dump() for info in channels.recordings]
    videos.sort(key=lambda info: info["timestamp"], reverse=True)
    return render_template("index.html", videos=videos, formatdate=formatdate)

@app.route("/assets/<path:subpath>")
def assets(subpath):
    return send_file("templates/assets/" + subpath, max_age=30)

@app.route("/files/<path:subpath>")
def file(subpath):
    return send_file(config.config.saveDir + subpath, max_age=86400)

@app.route("/settings")
def settings():
    return render_template("settings.html", settings=config.config._dump(True))

@app.route("/channels")
def channels_():
    return render_template("channels.html", channels=[c._dump() for c in config.config.channels])

@app.route("/channels/<channel>")
def channel_(channel):
    for c in config.config.channels:
        if c.name == channel:
            videos = [info._dump() for info in channels.recordings if info.channel == channel]
            videos.sort(key=lambda info: info["timestamp"], reverse=True)
            return render_template("channel.html", channel=channel, contents=c._dump(), videos=videos, formatdate=formatdate)
    return render_template("404.html", message="The channel requested was not found.")

@app.route("/channels/<channel>/<int:timestamp>")
def video(channel, timestamp):
    for info in channels.recordings:
        if info.channel == channel and info.timestamp == timestamp:
            return render_template("video.html", info=info._dump(), formattime=formattime)
    return render_template("404.html", message="The recording requested was not found.")

@app.route("/stop")
def stop():
    for r in channels.recordings: r.stop()
    config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    os._exit(0)

@app.route("/api")
def api():
    return {"data": "Hello World!"}

@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    if request.method == "GET":
        return config.config._dump(True)
    elif request.method == "PUT":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/channels", methods=["GET", "POST"])
def api_channels():
    if request.method == "GET":
        return [c._dump() for c in config.config.channels]
    elif request.method == "POST":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/channels/<channel>", methods=["GET", "PUT", "DELETE"])
def api_channel(channel):
    if request.method == "GET":
        for c in config.config.channels:
            if c.name == channel:
                return c._dump()
        return ({"error": "No such platform"}, 404)
    elif request.method == "PUT":
        return ("", 204)
    elif request.method == "DELETE":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/channels/<channel>/videos")
def api_channel_videos(channel):
    return [info._dump() for info in channels.recordings if info.channel == channel]

@app.route("/api/channels/<channel>/<int:timestamp>", methods=["GET", "DELETE"])
def api_video(channel, timestamp):
    if request.method == "GET":
        #try:
        #    timestamp = int(timestamp)
        #except ValueError:
        #    return ({"error": "Invalid timestamp"}, 400)
        for info in channels.recordings:
            if info.channel == channel and info.timestamp == timestamp:
                return info._dump()
        return ({"error": "Video not found"}, 404)
    elif request.method == "DELETE":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/videos")
def api_videos():
    retval = [info._dump() for info in channels.recordings]
    retval.sort(key=lambda info: info["timestamp"], reverse=True)
    return retval

def run(port: int | None = None):
    LOG.info("Starting yt-dvr web interface")
    return serve(WsgiToAsgi(app), hypercorn_config.Config())
