from typing import Any
from config import LOG, config
import app
import asyncio
import channel as channels
import datetime
import logging
import multiprocessing
import os
import signal
import sqlite3

shutdown_event = asyncio.Event()

def _signal_handler(*_: Any) -> None:
    shutdown_event.set()

async def retention_watcher():
    while not shutdown_event.is_set():
        LOG.info("Scanning retention for all channels")
        # TODO: should in progress videos be exempt? would complicate code structure
        for name, channel in config.channels.items():
            retention = channel.retention or config.defaultRetention
            if retention.count is not None or retention.size is not None or retention.time is not None:
                videos = [v for v in channels.recordings if v.channel == name]
                videos.sort(key=lambda v: v.timestamp)
                if retention.count is not None:
                    while len(videos) > retention.count:
                        LOG.info("Removing recording " + videos[0].title + " (count)")
                        channels.recordings.remove(videos[0])
                        await videos[0].delete()
                        videos.pop(0)
                if retention.size is not None:
                    total_size = 0
                    for v in videos:
                        try:
                            try: total_size = total_size + os.path.getsize(config.saveDir + "/" + v.filename)
                            except: total_size = total_size + os.path.getsize(config.saveDir + "/" + v.filename + ".part")
                            if v.chat_filename is not None: total_size = total_size + os.path.getsize(config.saveDir + "/" + v.chat_filename)
                        except: pass
                    while len(videos) > 0 and total_size > retention.size * 1000000:
                        LOG.info("Removing recording " + videos[0].title + " (size)")
                        channels.recordings.remove(videos[0])
                        await videos[0].delete()
                        videos.pop(0)
                if retention.time is not None:
                    now = int(datetime.datetime.now().timestamp())
                    cutoff = now - retention.time * 86400
                    while len(videos) > 0 and videos[0].timestamp < cutoff:
                        LOG.info("Removing recording " + videos[0].title + " (time)")
                        channels.recordings.remove(videos[0])
                        await videos[0].delete()
                        videos.pop(0)
        retention = config.globalRetention
        if retention.count is not None or retention.size is not None or retention.time is not None:
            videos = [v for v in channels.recordings]
            videos.sort(key=lambda v: v.timestamp)
            if retention.count is not None:
                while len(videos) > retention.count:
                    LOG.info("Removing recording " + videos[0].title + " (count)")
                    channels.recordings.remove(videos[0])
                    await videos[0].delete()
                    videos.pop(0)
            if retention.size is not None:
                total_size = 0
                for v in videos:
                    try:
                        total_size = total_size + os.path.getsize(config.saveDir + "/" + v.filename)
                        if v.chat_filename is not None: total_size = total_size + os.path.getsize(config.saveDir + "/" + v.chat_filename)
                    except: pass
                while len(videos) > 0 and total_size > retention.size * 1000000:
                    LOG.info("Removing recording " + videos[0].title + " (size)")
                    channels.recordings.remove(videos[0])
                    await videos[0].delete()
                    videos.pop(0)
            if retention.time is not None:
                now = int(datetime.datetime.now().timestamp())
                cutoff = now - retention.time * 86400
                while len(videos) > 0 and videos[0].timestamp < cutoff:
                    LOG.info("Removing recording " + videos[0].title + " (time)")
                    channels.recordings.remove(videos[0])
                    await videos[0].delete()
                    videos.pop(0)
        await asyncio.sleep(config.pollInterval)

async def main():
    LOG.info("Starting yt-dvr")
    config.load(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    config.db = sqlite3.connect(os.getenv("YTDVR_DB") or "./ytdvr.db")
    LOG.setLevel(config.logLevel)
    cur = config.db.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS videos (platform TEXT, channel TEXT, title TEXT, timestamp INTEGER, url TEXT, filename TEXT, chat_filename TEXT, in_progress INTEGER)")
    res = cur.execute("SELECT platform, channel, title, timestamp, url, filename, chat_filename, in_progress FROM videos")
    for platform, channel, title, timestamp, url, filename, chat_filename, in_progress in res.fetchall():
        r = channels.RecordingInfo(platform, channel, title, timestamp, url, filename, chat_filename, False)
        if in_progress != 0:
            LOG.warning(f"Detected partial video at {filename}, remuxing")
            r.remux()
            r.update()
        channels.recordings.append(r)
    asyncio.create_task(app.run(config.serverPort, shutdown_event.wait))
    asyncio.create_task(retention_watcher())
    signal.signal(signal.SIGINT, _signal_handler)
    multiprocessing.set_start_method("spawn")
    try:
        while not shutdown_event.is_set():
            LOG.info("Checking channels for liveness")
            for name, channel in config.channels.items():
                LOG.debug(f"Checking channel {name}")
                try:
                    next(r for r in channels.recordings if r.channel == name and r.in_progress)
                except StopIteration:
                    ok, arg = await channel.check_live()
                    if ok:
                        LOG.info(f"Starting recording for channel {name}")
                        rec = await channel.download(name, arg)
                        channels.recordings.append(rec)
                    else:
                        LOG.debug(f"Stream {name} is not live")
            LOG.debug("Done checking")
            try: await asyncio.wait_for(shutdown_event.wait(), timeout=config.pollInterval)
            except TimeoutError: pass
    except KeyboardInterrupt:
        LOG.warning("Caught interrupt, exiting")
        for r in channels.recordings: r.stop()
        return
    except BaseException as e:
        LOG.warning("Caught exception, exiting")
        for r in channels.recordings: r.abort()
        config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
        raise e
    LOG.warning("Caught interrupt, exiting")
    for r in channels.recordings: r.stop()
    return

def main_cli():
    asyncio.run(main())

if __name__ == "__main__":
    LOG.setLevel(logging.DEBUG)
    #LOG.addHandler(logging.StreamHandler(sys.stdout))
    asyncio.run(main())
    config.save(os.getenv("YTDVR_CONFIG") or "ytdvr_config.json")
    config.db.close()
    