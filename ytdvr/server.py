import app
import config
import time
import platforms
import asyncio
import logging
import os
import sys
import signal
import sqlite3

LOG = logging.getLogger("yt-dvr")

db: sqlite3.Connection

def updateRecording(info: platforms.RecordingInfo, platform: str | None = None, channel: str | None = None, timestamp: int | None = None):
    if platform is None: platform = info.platform
    if channel is None: channel = info.channel
    if timestamp is None: timestamp = info.timestamp
    cur = db.cursor()
    cur.execute("UPDATE videos SET platform = ?, channel = ?, title = ?, timestamp = ?, url = ?, filename = ?, in_progress = ? WHERE platform = ? AND channel = ? AND timestamp = ?",
                (info.platform, info.channel, info.title, info.timestamp, info.url, info.filename, info.in_progress, platform, channel, timestamp))

async def main():
    global db
    global recordings
    LOG.info("Starting yt-dvr")
    asyncio.get_event_loop().add_signal_handler(signal.SIGINT, asyncio.current_task().cancel) # type: ignore
    await config.config.load(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    db = sqlite3.connect(os.getenv("YTDVR_DB") or "./ytdvr.db")
    cur = db.cursor()
    res = cur.execute("SELECT platform, channel, title, timestamp, url, filename, in_progress FROM videos")
    for platform, channel, title, timestamp, url, filename, in_progress in res.fetchall():
        if in_progress:
            # TODO: remux
            pass
        platforms.recordings.append(platforms.RecordingInfo(platform, channel, title, timestamp, url, filename, False))
    asyncio.create_task(app.run())
    try:
        while True:
            LOG.info("Checking channels for liveness")
            for channel in config.config.channels:
                LOG.debug(f"Checking channel {channel.id}")
                if await channel.platform.poll(channel.id):
                    LOG.debug(f"Channel {channel.id} is online")
                    try:
                        next(r for r in platforms.recordings if r.platform == channel.platform.name and r.channel == channel.id and r.in_progress)
                    except StopIteration:
                        LOG.info(f"Starting recording for channel {channel.id}")
                        rec = await channel.platform.download(channel.id)
                        if rec is not None: platforms.recordings.append(rec)
                else:
                    LOG.debug(f"Channel {channel.id} is offline")
                    try:
                        rec = next(r for r in platforms.recordings if r.platform == channel.platform.name and r.channel == channel.id and r.in_progress)
                        LOG.info(f"Stopping recording for channel {channel.id}")
                        rec.stop()
                    except StopIteration: pass
            LOG.info("Done checking")
            await asyncio.sleep(config.config.pollInterval)
    except KeyboardInterrupt:
        LOG.warning("Caught interrupt, exiting")
        for r in platforms.recordings: r.stop()
        config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        return
    except BaseException as e:
        LOG.warning("Caught exception, exiting")
        for r in platforms.recordings: r.abort()
        config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        raise e

if __name__ == "__main__":
    LOG.setLevel(logging.DEBUG)
    #LOG.addHandler(logging.StreamHandler(sys.stdout))
    asyncio.run(main())
    