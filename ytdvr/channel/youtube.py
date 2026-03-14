import datetime
import json
import logging
import os
import shutil
from typing import Any, Awaitable, Callable

import config as config_module
from config import config
from quart import Quart, render_template, request, send_file

import channel as channels

LOG = logging.getLogger("yt-dvr")

app = Quart("yt-dvr")
app.logger.setLevel(logging.DEBUG)
app.config["TEMPLATES_AUTO_RELOAD"] = True

CONFIG_PATH = os.getenv("YTDVR_CONFIG") or "ytdvr_config.json"


_check_now_event = None


def set_check_now_event(event):
    """Set the asyncio event used for triggering immediate checks."""
    global _check_now_event
    _check_now_event = event


def formattime(timestamp: int) -> str:
    return datetime.datetime.fromtimestamp(timestamp).strftime("%c")


def formatdate(timestamp: int) -> str:
    return datetime.date.fromtimestamp(timestamp).strftime("%x")


def _format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"


def _get_file_size(recording) -> int:
    """Get total file size for a recording."""
    total = 0
    try:
        fpath = os.path.join(config.saveDir, recording.filename)
        if os.path.isfile(fpath):
            total += os.path.getsize(fpath)
        elif os.path.isfile(fpath + ".part"):
            total += os.path.getsize(fpath + ".part")
    except OSError:
        pass
    if recording.chat_filename:
        try:
            total += os.path.getsize(
                os.path.join(config.saveDir, recording.chat_filename)
            )
        except OSError:
            pass
    return total


def _enrich_video_dump(dump: dict, recording=None) -> dict:
    """Add extra fields to a video dump for templates."""
    if recording:
        file_size = _get_file_size(recording)
        dump["file_size"] = file_size
        dump["file_size_formatted"] = _format_size(file_size) if file_size > 0 else None

        dump["thumbnail"] = None
        dump["duration"] = None
        dump["duration_formatted"] = None
    return dump


def _save_config():
    config.save(CONFIG_PATH)


def _validate_type(data: dict, key: str, expected_type: type, label: str = ""):
    """Return an error tuple if data[key] exists and is not the expected type."""
    if key in data and data[key] is not None:
        if not isinstance(data[key], expected_type):
            field = label or key
            return {"error": f"'{field}' not a {expected_type.__name__}"}, 400
    return None


def _validate_retention(data: dict, prefix: str = ""):
    """Validate a retention dict. Returns error tuple or None."""
    label = f"{prefix}." if prefix else ""
    for field in ("count", "time", "size"):
        val = data.get(field)
        if val is not None and not isinstance(val, int):
            return {"error": f"'{label}{field}' not an integer"}, 400
    return None


def _get_disk_stats():
    """Get disk usage statistics for the save directory."""
    try:
        usage = shutil.disk_usage(config.saveDir)
        return {
            "disk_total": usage.total,
            "disk_used": usage.used,
            "disk_free": usage.free,
            "disk_total_formatted": _format_size(usage.total),
            "disk_used_formatted": _format_size(usage.used),
            "disk_free_formatted": _format_size(usage.free),
            "disk_percent": round(usage.used / usage.total * 100, 1)
            if usage.total > 0
            else 0,
        }
    except OSError:
        return {
            "disk_total": 0,
            "disk_used": 0,
            "disk_free": 0,
            "disk_total_formatted": "N/A",
            "disk_used_formatted": "N/A",
            "disk_free_formatted": "N/A",
            "disk_percent": 0,
        }


@app.route("/")
async def home():
    videos = []
    for info in channels.recordings:
        dump = info._dump()
        _enrich_video_dump(dump, info)
        videos.append(dump)
    videos.sort(key=lambda v: v["timestamp"], reverse=True)

    channel_names = sorted(config.channels.keys())

    return await render_template(
        "index.html",
        videos=videos,
        formatdate=formatdate,
        channel_names=channel_names,
    )


@app.route("/search")
async def search():
    query = request.args.get("q", "").strip().lower()
    results = []
    if query:
        for info in channels.recordings:
            if (
                query in info.title.lower()
                or query in info.channel.lower()
                or query in info.platform.lower()
            ):
                dump = info._dump()
                _enrich_video_dump(dump, info)
                results.append(dump)
    results.sort(key=lambda v: v["timestamp"], reverse=True)
    return await render_template(
        "search.html",
        query=request.args.get("q", ""),
        results=results,
        formatdate=formatdate,
    )


@app.route("/dashboard")
async def dashboard():
    active_recs = [r for r in channels.recordings if r.in_progress]
    total_size = sum(_get_file_size(r) for r in channels.recordings)

    disk_stats = _get_disk_stats()

    stats = {
        "total_channels": len(config.channels),
        "total_videos": len(channels.recordings),
        "active_recordings": len(active_recs),
        "total_size": total_size,
        "total_size_formatted": _format_size(total_size),
        **disk_stats,
    }

    active_recordings = []
    for r in active_recs:
        active_recordings.append(
            {
                "channel": r.channel,
                "title": r.title,
                "timestamp": r.timestamp,
                "platform": r.platform,
            }
        )

    recent = sorted(channels.recordings, key=lambda v: v.timestamp, reverse=True)[:10]
    recent_videos = []
    for r in recent:
        fs = _get_file_size(r)
        recent_videos.append(
            {
                "channel": r.channel,
                "title": r.title,
                "timestamp": r.timestamp,
                "platform": r.platform,
                "file_size_formatted": _format_size(fs) if fs > 0 else None,
            }
        )

    channel_stats = []
    for name, ch in config.channels.items():
        ch_videos = [r for r in channels.recordings if r.channel == name]
        ch_size = sum(_get_file_size(r) for r in ch_videos)
        is_recording = any(r.in_progress for r in ch_videos)
        channel_stats.append(
            {
                "name": name,
                "platform": ch.platform,
                "video_count": len(ch_videos),
                "total_size": ch_size,
                "total_size_formatted": _format_size(ch_size),
                "is_recording": is_recording,
            }
        )
    channel_stats.sort(key=lambda x: x["name"])

    return await render_template(
        "dashboard.html",
        stats=stats,
        active_recordings=active_recordings,
        recent_videos=recent_videos,
        channel_stats=channel_stats,
        formattime=formattime,
    )


@app.route("/assets/<path:subpath>")
async def assets(subpath):
    return await send_file("templates/assets/" + subpath, cache_timeout=30)


@app.route("/files/<path:subpath>")
async def file_serve(subpath: str):
    full_path = os.path.join(config.saveDir, subpath)
    if os.path.isfile(full_path):
        is_partial = subpath.endswith(".part")
        mime = "video/mpeg-ts" if (subpath.endswith(".ts") or is_partial) else None
        cache = 0 if is_partial else 86400
        return await send_file(
            full_path, cache_timeout=cache, mimetype=mime, conditional=True
        )
    return (
        await render_template("404.html", message="The requested file does not exist."),
        404,
    )


@app.route("/files/<channel>/<file>.m3u8")
async def file_m3u8(channel, file):
    base = os.path.join(config.saveDir, channel)
    path = None
    for ext in (".ts", ".ts.part", ".mp4", ".mp4.part"):
        candidate = os.path.join(base, file + ext)
        if os.path.isfile(candidate):
            path = file + ext
            break

    if path is None:
        return (
            await render_template(
                "404.html", message="The requested file does not exist."
            ),
            404,
        )

    header = (
        "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXT-X-VERSION:3\n#EXT-X-MEDIA-SEQUENCE:0\n"
    )
    if path.endswith(".part"):
        return header + f"#EXTINF:9.97667\n{path}\n"
    return (
        header + f"#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:9.97667\n{path}\n#EXT-X-ENDLIST\n"
    )


@app.route("/settings")
async def settings():
    dump = config._dump(True)
    return await render_template(
        "settings.html",
        settings=dump,
        log_levels=["DEBUG", "INFO", "WARNING", "ERROR"],
        current_log_level=dump["logLevel"],
    )


@app.route("/channels")
async def channels_page():
    channel_list = []
    for k, c in config.channels.items():
        dump = c._dump()

        ch_videos = [r for r in channels.recordings if r.channel == k]
        dump["video_count"] = len(ch_videos)
        dump["is_recording"] = any(r.in_progress for r in ch_videos)
        channel_list.append((k, dump))

    return await render_template("channels.html", channels=channel_list)


@app.route("/channels/<channel>")
async def channel_page(channel):
    c = config.channels.get(channel)
    if c is None:
        return (
            await render_template(
                "404.html", message="The channel requested was not found."
            ),
            404,
        )
    videos = []
    is_recording = False
    for info in channels.recordings:
        if info.channel == channel:
            dump = info._dump()
            _enrich_video_dump(dump, info)
            videos.append(dump)
            if info.in_progress:
                is_recording = True
    videos.sort(key=lambda v: v["timestamp"], reverse=True)
    return await render_template(
        "channel.html",
        channel=channel,
        contents=c._dump(),
        ytdlParams=json.dumps(c.ytdlParams) if c.ytdlParams is not None else "",
        videos=videos,
        formatdate=formatdate,
        is_recording=is_recording,
    )


@app.route("/channels/<channel>/<int:timestamp>")
async def video_page(channel, timestamp):
    for info in channels.recordings:
        if info.channel == channel and info.timestamp == timestamp:
            dump = info._dump()
            _enrich_video_dump(dump, info)
            return await render_template("video.html", info=dump, formattime=formattime)
    return (
        await render_template(
            "404.html", message="The recording requested was not found."
        ),
        404,
    )


@app.route("/stop")
async def stop():
    for r in channels.recordings:
        r.stop()
    _save_config()
    os._exit(0)


@app.route("/api")
async def api():
    return {"data": "Hello World!"}


@app.route("/api/status")
async def api_status():
    """Returns server status for navbar polling."""
    active = sum(1 for r in channels.recordings if r.in_progress)
    total_size = sum(_get_file_size(r) for r in channels.recordings)
    return {
        "active_recordings": active,
        "total_videos": len(channels.recordings),
        "total_channels": len(config.channels),
        "total_size": total_size,
        "total_size_formatted": _format_size(total_size),
    }


@app.route("/api/check-now", methods=["POST"])
async def api_check_now():
    """Trigger an immediate channel liveness check."""
    if _check_now_event is not None:
        _check_now_event.set()
    return {"status": "ok"}, 200


@app.route("/api/channels/<channel>/check", methods=["POST"])
async def api_channel_check(channel):
    """Trigger a check for a specific channel."""
    if channel not in config.channels:
        return {"error": "No such channel"}, 404

    if _check_now_event is not None:
        _check_now_event.set()
    return {"status": "ok"}, 200


@app.route("/api/run-retention", methods=["POST"])
async def api_run_retention():
    """Trigger an immediate retention cleanup."""
    if _check_now_event is not None:
        _check_now_event.set()
    return {"status": "ok"}, 200


@app.route("/api/export-config")
async def api_export_config():
    """Export the full configuration as JSON."""
    from quart import Response

    data = config._dump(False)
    return Response(
        json.dumps(data, indent=4),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=yt-dvr-config.json"},
    )


@app.route("/api/import-config", methods=["POST"])
async def api_import_config():
    """Import a configuration from JSON."""
    data = await request.json
    if data is None:
        return {"error": "Invalid JSON"}, 400

    try:
        if "saveDir" in data:
            config.saveDir = data["saveDir"]
        if "serverPort" in data:
            config.serverPort = data["serverPort"]
        if "pollInterval" in data:
            config.pollInterval = data["pollInterval"]
        if "remuxRecordings" in data:
            config.remuxRecordings = data["remuxRecordings"]
        if "remuxFormat" in data:
            config.remuxFormat = data["remuxFormat"]
        if "logLevel" in data:
            config.logLevel = data["logLevel"]

        for rk in ("defaultRetention", "globalRetention"):
            if rk in data and isinstance(data[rk], dict):
                setattr(config, rk, config_module.Retention(data[rk]))

        if "channels" in data and isinstance(data["channels"], dict):
            for name, ch_data in data["channels"].items():
                if name not in config.channels:
                    config.channels[name] = channels.Channel(obj=ch_data)

        _save_config()
        return {"status": "ok"}, 200
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/api/settings/reset", methods=["POST"])
async def api_settings_reset():
    """Reset settings to defaults (preserves channels)."""
    config.saveDir = "files"
    config.serverPort = 6334
    config.pollInterval = 60
    config.remuxRecordings = True
    config.remuxFormat = "mp4"
    config.logLevel = "INFO"
    config.defaultRetention = config_module.Retention()
    config.globalRetention = config_module.Retention()
    _save_config()
    return config._dump(True), 200


@app.route("/api/settings", methods=["GET", "PUT"])
async def api_settings():
    if request.method == "GET":
        return config._dump(True)

    data = await request.json
    if data is None:
        return {"error": "Invalid JSON"}, 400

    type_checks = [
        ("saveDir", str),
        ("serverPort", int),
        ("pollInterval", int),
        ("remuxRecordings", bool),
        ("remuxFormat", str),
        ("logLevel", str),
    ]
    for key, typ in type_checks:
        err = _validate_type(data, key, typ)
        if err:
            return err

    if "saveDir" in data:
        config.saveDir = data["saveDir"]
    if "serverPort" in data:
        config.serverPort = data["serverPort"]
    if "pollInterval" in data:
        config.pollInterval = data["pollInterval"]
    if "remuxRecordings" in data:
        config.remuxRecordings = data["remuxRecordings"]
    if "remuxFormat" in data:
        config.remuxFormat = data["remuxFormat"]
    if "logLevel" in data:
        config.logLevel = data["logLevel"]

    for retention_key in ("defaultRetention", "globalRetention"):
        if retention_key in data:
            if not isinstance(data[retention_key], dict):
                return {"error": f"'{retention_key}' not an object"}, 400
            err = _validate_retention(data[retention_key], retention_key)
            if err:
                return err
            setattr(
                config,
                retention_key,
                config_module.Retention(data[retention_key]),
            )

    _save_config()
    return config._dump(True), 200


@app.route("/api/channels", methods=["GET", "POST"])
async def api_channels():
    if request.method == "GET":
        return {k: c._dump() for k, c in config.channels.items()}

    data = await request.json
    if data is None:
        return {"error": "Invalid JSON"}, 400

    if not isinstance(data.get("name"), str):
        return {"error": "'name' not a string"}, 400
    if not isinstance(data.get("url"), str):
        return {"error": "'url' not a string"}, 400

    for key, typ, label in [
        ("getChat", bool, "'getChat' not a bool"),
        ("platform", str, "'platform' not a string"),
        ("quality", str, "'quality' not a string"),
    ]:
        val = data.get(key)
        if val is not None and not isinstance(val, typ):
            return {"error": label}, 400

    data.setdefault("getChat", False)

    if data.get("retention") is not None:
        if not isinstance(data["retention"], dict):
            return {"error": "'retention' not an object"}, 400
        err = _validate_retention(data["retention"], "retention")
        if err:
            return err

    if data.get("ytdlParams") is not None and not isinstance(data["ytdlParams"], dict):
        return {"error": "'ytdlParams' not an object"}, 400

    if data["name"] in config.channels:
        return {"error": "Channel already exists"}, 400

    config.channels[data["name"]] = channels.Channel(obj=data)
    _save_config()
    return config.channels[data["name"]]._dump(), 201


@app.route("/api/channels/<channel>", methods=["GET", "PUT", "DELETE"])
async def api_channel(channel):
    if request.method == "GET":
        c = config.channels.get(channel)
        if c is None:
            return {"error": "No such channel"}, 404
        return c._dump()

    elif request.method == "PUT":
        data = await request.json
        if data is None:
            return {"error": "Invalid JSON"}, 400

        c = config.channels.get(channel)
        if c is None:
            return {"error": "No such channel"}, 404

        if "url" in data:
            if not isinstance(data["url"], str):
                return {"error": "'url' not a string"}, 400
            c.url = data["url"]
        if "getChat" in data:
            if not isinstance(data["getChat"], bool):
                return {"error": "'getChat' not a bool"}, 400
            c.getChat = data["getChat"]
        if "platform" in data:
            if data["platform"] is not None and not isinstance(data["platform"], str):
                return {"error": "'platform' not a string"}, 400
            c.platform = data["platform"]
        if "quality" in data:
            if data["quality"] is not None and not isinstance(data["quality"], str):
                return {"error": "'quality' not a string"}, 400
            c.quality = data["quality"]
        if "retention" in data:
            if isinstance(data["retention"], dict):
                err = _validate_retention(data["retention"], "retention")
                if err:
                    return err
                c.retention = config_module.Retention(data["retention"])
            elif data["retention"] is None:
                c.retention = None
            else:
                return {"error": "'retention' not an object"}, 400
        if "ytdlParams" in data:
            if data["ytdlParams"] is not None and not isinstance(
                data["ytdlParams"], dict
            ):
                return {"error": "'ytdlParams' not an object"}, 400
            c.ytdlParams = data["ytdlParams"]

        _save_config()
        return c._dump(), 200

    elif request.method == "DELETE":
        if channel not in config.channels:
            return {"error": "No such channel"}, 404
        del config.channels[channel]
        _save_config()
        return "", 204


@app.route("/api/channels/<channel>/videos")
async def api_channel_videos(channel):
    return [info._dump() for info in channels.recordings if info.channel == channel]


@app.route("/api/channels/<channel>/<int:timestamp>", methods=["GET", "DELETE"])
async def api_video(channel, timestamp):
    if request.method == "GET":
        for info in channels.recordings:
            if info.channel == channel and info.timestamp == timestamp:
                dump = info._dump()
                _enrich_video_dump(dump, info)
                return dump
        return {"error": "Video not found"}, 404

    elif request.method == "DELETE":
        for i, info in enumerate(channels.recordings):
            if info.channel == channel and info.timestamp == timestamp:
                channels.recordings.pop(i)
                await info.delete()
                return "", 204
        return {"error": "Video not found"}, 404


@app.route("/api/videos")
async def api_videos():
    result = []
    for info in channels.recordings:
        dump = info._dump()
        _enrich_video_dump(dump, info)
        result.append(dump)
    result.sort(key=lambda v: v["timestamp"], reverse=True)
    return result


@app.route("/api/search")
async def api_search():
    """Search videos by query string."""
    query = request.args.get("q", "").strip().lower()
    if not query:
        return []
    results = []
    for info in channels.recordings:
        if (
            query in info.title.lower()
            or query in info.channel.lower()
            or query in info.platform.lower()
        ):
            dump = info._dump()
            _enrich_video_dump(dump, info)
            results.append(dump)
    results.sort(key=lambda v: v["timestamp"], reverse=True)
    return results


@app.route("/api/dashboard")
async def api_dashboard():
    """Returns dashboard data as JSON."""
    active = [r for r in channels.recordings if r.in_progress]
    total_size = sum(_get_file_size(r) for r in channels.recordings)
    disk = _get_disk_stats()

    return {
        "total_channels": len(config.channels),
        "total_videos": len(channels.recordings),
        "active_recordings": len(active),
        "total_size": total_size,
        "total_size_formatted": _format_size(total_size),
        **disk,
    }


def run(
    port: int | None = None,
    shutdown: Callable[..., Awaitable[Any | None]] | None = None,
):
    LOG.info("Starting yt-dvr web interface")
    return app.run_task(
        port=port if port is not None else 6334, shutdown_trigger=shutdown
    )
