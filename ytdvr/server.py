import app
import config
import time
import channel as channels
import asyncio
import logging
import os
import sys
import signal
import sqlite3
import multiprocessing

LOG = logging.getLogger("yt-dvr")

db: sqlite3.Connection

def updateRecording(info: channels.RecordingInfo, platform: str | None = None, channel: str | None = None, timestamp: int | None = None):
    global db
    if platform is None: platform = info.platform
    if channel is None: channel = info.channel
    if timestamp is None: timestamp = info.timestamp
    cur = db.cursor()
    cur.execute("UPDATE videos SET platform = ?, channel = ?, title = ?, timestamp = ?, url = ?, filename = ?, in_progress = ? WHERE platform = ? AND channel = ? AND timestamp = ?",
                (info.platform, info.channel, info.title, info.timestamp, info.url, info.filename, info.in_progress, platform, channel, timestamp))
    db.commit()

async def main():
    global db
    LOG.info("Starting yt-dvr")
    asyncio.get_event_loop().add_signal_handler(signal.SIGINT, asyncio.current_task().cancel) # type: ignore
    config.config.load(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    db = sqlite3.connect(os.getenv("YTDVR_DB") or "./ytdvr.db")
    cur = db.cursor()
    res = cur.execute("SELECT platform, channel, title, timestamp, url, filename, in_progress FROM videos")
    for platform, channel, title, timestamp, url, filename, in_progress in res.fetchall():
        if in_progress != 0:
            # TODO: remux
            LOG.warning(f"Detected partial video at {filename}, remuxing")
        channels.recordings.append(channels.RecordingInfo(platform, channel, title, timestamp, url, filename, False))
    asyncio.create_task(app.run())
    signal.signal(signal.SIGINT, signal.default_int_handler)
    multiprocessing.set_start_method("spawn")
    try:
        while True:
            LOG.info("Checking channels for liveness")
            for channel in config.config.channels:
                LOG.debug(f"Checking channel {channel.name}")
                try:
                    next(r for r in channels.recordings if r.channel == channel.name and r.in_progress)
                except StopIteration:
                    LOG.info(f"Starting recording for channel {channel.name}")
                    rec = channel.download(updateRecording)
                    if rec is not None:
                        channels.recordings.append(rec)
                        cur = db.cursor()
                        cur.execute("INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (rec.platform, rec.channel, rec.title, rec.timestamp, rec.url, rec.filename, rec.in_progress))
                        db.commit()
            LOG.info("Done checking")
            await asyncio.sleep(config.config.pollInterval)
    except KeyboardInterrupt:
        LOG.warning("Caught interrupt, exiting")
        for r in channels.recordings: r.stop()
        config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        return
    except BaseException as e:
        LOG.warning("Caught exception, exiting")
        for r in channels.recordings: r.abort()
        config.config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        raise e

if __name__ == "__main__":
    LOG.setLevel(logging.DEBUG)
    #LOG.addHandler(logging.StreamHandler(sys.stdout))
    asyncio.run(main())
    