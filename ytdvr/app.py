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
def file(subpath: str):
    if os.path.isfile(config.config.saveDir + "/" + subpath):
        if subpath.endswith(".part"): return send_file(config.config.saveDir + "/" + subpath, max_age=0, mimetype="video/mpeg-ts")
        else: return send_file(config.config.saveDir + "/" + subpath, max_age=86400, mimetype="video/mpeg-ts" if subpath.endswith(".ts") else None)
    else: return (render_template("404.html", message="The requested file does not exist."), 404)

@app.route("/files/<channel>/<file>.m3u8")
def file_m3u8(channel, file):
    path = ""
    if os.path.isfile(config.config.saveDir + "/" + channel + "/" + file + ".ts"):
        path = file + ".ts"
    elif os.path.isfile(config.config.saveDir + "/" + channel + "/" + file + ".ts.part"):
        path = file + ".ts.part"
    elif os.path.isfile(config.config.saveDir + "/" + channel + "/" + file + ".mp4"):
        path = file + ".mp4"
    elif os.path.isfile(config.config.saveDir + "/" + channel + "/" + file + ".mp4.part"):
        path = file + ".mp4.part"
    else: return (render_template("404.html", message="The requested file does not exist."), 404)
    if path.endswith(".part"): return "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-VERSION:3\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:9.97667\n" + path + "\n"
    else: return "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-VERSION:3\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:9.97667\n" + path + "\n#EXT-X-ENDLIST\n"

@app.route("/settings")
def settings():
    return render_template("settings.html", settings=config.config._dump(True))

@app.route("/channels")
def channels_():
    return render_template("channels.html", channels=[(k, c._dump()) for k, c in config.config.channels.items()])

@app.route("/channels/<channel>")
def channel_(channel):
    try:
        c = config.config.channels[channel]
        videos = [info._dump() for info in channels.recordings if info.channel == channel]
        videos.sort(key=lambda info: info["timestamp"], reverse=True)
        return render_template("channel.html", channel=channel, contents=c._dump(), videos=videos, formatdate=formatdate)
    except KeyError:
        return (render_template("404.html", message="The channel requested was not found."), 404)

@app.route("/channels/<channel>/<int:timestamp>")
def video(channel, timestamp):
    for info in channels.recordings:
        if info.channel == channel and info.timestamp == timestamp:
            return render_template("video.html", info=info._dump(), formattime=formattime)
    return (render_template("404.html", message="The recording requested was not found."), 404)

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
        data = request.json
        if "saveDir" in data:
            if type(data["saveDir"]) != str: return ({"error": "'saveDir' not a string"}, 400)
            config.config.saveDir = data["saveDir"]
        if "pollInterval" in data:
            if type(data["pollInterval"]) != int: return ({"error": "'pollInterval' not an integer"}, 400)
            config.config.pollInterval = data["pollInterval"]
        if "remuxRecordings" in data:
            if type(data["remuxRecordings"]) != bool: return ({"error": "'remuxRecordings' not a boolean"}, 400)
            config.config.remuxRecordings = data["remuxRecordings"]
        if "remuxFormat" in data:
            if type(data["remuxFormat"]) != str: return ({"error": "'remuxFormat' not a string"}, 400)
            config.config.remuxFormat = data["remuxFormat"]
        if "defaultRetention" in data:
            if type(data["defaultRetention"]) != dict: return ({"error": "'defaultRetention' not an object"}, 400)
            if "count" in data["defaultRetention"] and type(data["defaultRetention"]["count"]) != int: return ({"error": "'defaultRetention.count' not an integer"}, 400)
            if "time" in data["defaultRetention"] and type(data["defaultRetention"]["time"]) != int: return ({"error": "'defaultRetention.time' not an integer"}, 400)
            if "size" in data["defaultRetention"] and type(data["defaultRetention"]["size"]) != int: return ({"error": "'defaultRetention.size' not an integer"}, 400)
            config.config.defaultRetention = data["defaultRetention"]
        return (config.config._dump(True), 200)
    else: return ({"error": "Invalid request method"}, 405)

@app.route("/api/channels", methods=["GET", "POST"])
def api_channels():
    if request.method == "GET":
        return {k: c._dump() for k, c in config.config.channels.items()}
    elif request.method == "POST":
        data = request.json
        if not ("name" in data) or type(data["name"]) != str: return ({"error": "'name' not a string"}, 400)
        if not ("url" in data) or type(data["url"]) != str: return ({"error": "'url' not a string"}, 400)
        # TODO: check URL
        if "getChat" in data:
            if type(data["getChat"]) != bool: return ({"error": "'getChat' not a bool"}, 400)
        else: data["getChat"] = False
        if "platform" in data and type(data["platform"]) != str: return ({"error": "'platform' not a string"}, 400)
        if "quality" in data and type(data["quality"]) != str: return ({"error": "'quality' not a string"}, 400)
        if "retention" in data:
            if type(data["retention"]) != dict: return ({"error": "'retention' not an object"}, 400)
            if "count" in data["retention"] and type(data["retention"]["count"]) != int: return ({"error": "'retention.count' not an integer"}, 400)
            if "time" in data["retention"] and type(data["retention"]["time"]) != int: return ({"error": "'retention.time' not an integer"}, 400)
            if "size" in data["retention"] and type(data["retention"]["size"]) != int: return ({"error": "'retention.size' not an integer"}, 400)
        if "ytdlParams" in data and type(data["ytdlParams"]) != dict: return ({"error": "'ytdlParams' not an object"}, 400)
        if data["name"] in config.config.channels: return ({"error": "Channel already exists"}, 400)
        config.config.channels[data["name"]] = channels.Channel(obj=data)
        config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        return (config.config.channels[data["name"]]._dump(), 201)
    else: return ({"error": "Invalid request method"}, 405)

@app.route("/api/channels/<channel>", methods=["GET", "PUT", "DELETE"])
def api_channel(channel):
    if request.method == "GET":
        try:
            return config.config.channels[channel]._dump()
        except KeyError:
            return ({"error": "No such channel"}, 404)
    elif request.method == "PUT":
        data = request.json
        c = None
        try:
            c = config.config.channels[channel]
        except KeyError:
            return ({"error": "No such channel"}, 404)
        if "url" in data:
            if type(data["url"]) != str: return ({"error": "'url' not a string"}, 400)
            # TODO: check URL
            c.url = data["url"]
        if "getChat" in data:
            if type(data["getChat"]) != bool: return ({"error": "'getChat' not a bool"}, 400)
            c.getChat = data["getChat"]
        if "platform" in data:
            if type(data["platform"]) != str: return ({"error": "'platform' not a string"}, 400)
            c.platform = data["platform"]
        if "quality" in data:
            if type(data["quality"]) != str: return ({"error": "'quality' not a string"}, 400)
            c.quality = data["quality"]
        if "retention" in data:
            if type(data["retention"]) != dict: return ({"error": "'retention' not an object"}, 400)
            if "count" in data["retention"] and type(data["retention"]["count"]) != int: return ({"error": "'retention.count' not an integer"}, 400)
            if "time" in data["retention"] and type(data["retention"]["time"]) != int: return ({"error": "'retention.time' not an integer"}, 400)
            if "size" in data["retention"] and type(data["retention"]["size"]) != int: return ({"error": "'retention.size' not an integer"}, 400)
            c.retention = data["retention"]
        if "ytdlParams" in data:
            if type(data["ytdlParams"]) != dict: return ({"error": "'ytdlParams' not an object"}, 400)
            c.ytdlParams = data["ytdlParams"]
        config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        return (c._dump(), 200)
    elif request.method == "DELETE":
        del config.config.channels[channel]
        config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        return ("", 204)
    else: return ({"error": "Invalid request method"}, 405)

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
        for i, info in enumerate(channels.recordings):
            if info.channel == channel and info.timestamp == timestamp:
                if info.in_progress: info.stop()
                try:
                    try:
                        os.remove(config.config.saveDir + "/" + info.filename)
                    except:
                        os.remove(config.config.saveDir + "/" + info.filename + ".part")
                    if info.chat_filename is not None: os.remove(config.config.saveDir + "/" + info.chat_filename)
                except: pass
                channels.recordings.pop(i)
                # TODO: delete from database
                return ("", 204)
        return ({"error": "Video not found"}, 404)
    else: return ({"error": "Invalid request method"}, 405)

@app.route("/api/videos")
def api_videos():
    retval = [info._dump() for info in channels.recordings]
    retval.sort(key=lambda info: info["timestamp"], reverse=True)
    return retval

def run(port: int | None = None):
    config = hypercorn_config.Config()
    if port is not None: config.bind = "0.0.0.0:" + str(port)
    LOG.info("Starting yt-dvr web interface")
    return serve(WsgiToAsgi(app), config)
