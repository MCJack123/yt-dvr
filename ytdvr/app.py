from flask import Flask, request, send_file
from asgiref.wsgi import WsgiToAsgi
import config
from hypercorn import config as hypercorn_config
from hypercorn.asyncio import serve
import logging
import platforms

LOG = logging.getLogger("yt-dvr")

app = Flask("yt-dvr")
app.logger.setLevel(logging.DEBUG)

@app.route("/")
def home():
    return "<p>Hello, World!</p>"

@app.route("/files/<path:subpath>")
def file(subpath):
    return send_file(config.config.saveDir + subpath, max_age=86400)

@app.route("/settings")
def settings():
    return "<p>Hello, World!</p>"

@app.route("/platforms")
def platforms_():
    return "<p>Hello, World!</p>"

@app.route("/platforms/<platform>")
def platform(platform):
    return "<p>Hello, World!</p>"

@app.route("/platforms/<platform>/<channel>")
def channel():
    return "<p>Hello, World!</p>"

@app.route("/platforms/<platform>/<channel>/<timestamp>")
def video(platform, channel, timestamp):
    return "<p>Hello, World!</p>"

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

@app.route("/api/platforms", methods=["GET", "POST"])
def api_platforms():
    if request.method == "GET":
        return [p._dump() for p in config.config.platforms]
    elif request.method == "POST":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/platforms/<platform>", methods=["GET", "POST", "PUT", "DELETE"])
def api_platform(platform):
    if request.method == "GET":
        for p in config.config.platforms:
            if p.name == platform:
                retval = p._dump()
                retval["channels"] = [channel.id for channel in config.config.channels if channel.platform is p]
                return retval
        return ({"error": "No such platform"}, 404)
    elif request.method == "POST":
        return ("", 204)
    elif request.method == "PUT":
        return ("", 204)
    elif request.method == "DELETE":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/platforms/<platform>/<channel>", methods=["GET", "PUT", "DELETE"])
def api_channel(platform, channel):
    if request.method == "GET":
        for c in config.config.channels:
            if c.platform.name == platform and c.id == channel:
                return c._dump()
        return ({"error": "No such platform"}, 404)
    elif request.method == "PUT":
        return ("", 204)
    elif request.method == "DELETE":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/platforms/<platform>/<channel>/videos")
def api_channel_videos(platform, channel):
    return [info._dump() for info in platforms.recordings if info.platform == platform and info.channel == channel]

@app.route("/api/platforms/<platform>/<channel>/<int:timestamp>", methods=["GET", "DELETE"])
def api_video(platform, channel, timestamp):
    if request.method == "GET":
        #try:
        #    timestamp = int(timestamp)
        #except ValueError:
        #    return ({"error": "Invalid timestamp"}, 400)
        for info in platforms.recordings:
            if info.platform == platform and info.channel == channel and info.timestamp == timestamp:
                return info._dump()
        return ({"error": "Video not found"}, 404)
    elif request.method == "DELETE":
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 400)

@app.route("/api/videos")
def api_videos():
    retval = [info._dump() for info in platforms.recordings]
    retval.sort(key=lambda info: info["timestamp"], reverse=True)
    return retval

def run(port: int | None = None):
    LOG.info("Starting yt-dvr web interface")
    return serve(WsgiToAsgi(app), hypercorn_config.Config())
