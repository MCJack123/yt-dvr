import asyncio
import datetime
import logging
import multiprocessing
import os
import signal
import sqlite3
from typing import Any

import app
import channel as channels
from config import LOG, config

CONFIG_PATH = os.getenv("YTDVR_CONFIG") or "ytdvr_config.json"
DB_PATH = os.getenv("YTDVR_DB") or "./ytdvr.db"

shutdown_event = asyncio.Event()
check_now_event = asyncio.Event()


def _signal_handler(*_: Any) -> None:
    shutdown_event.set()


def _migrate_db(cur: sqlite3.Cursor):
    """Add thumbnail_filename column if it doesn't exist yet."""
    cols = [row[1] for row in cur.execute("PRAGMA table_info(videos)").fetchall()]
    if "thumbnail_filename" not in cols:
        LOG.info("Migrating DB: adding thumbnail_filename column")
        cur.execute("ALTER TABLE videos ADD COLUMN thumbnail_filename TEXT")


async def _enforce_retention(retention, video_list: list, all_recordings: list):
    if retention.count is not None:
        while len(video_list) > retention.count:
            v = video_list.pop(0)
            LOG.info(f"Removing recording {v.title} (count)")
            all_recordings.remove(v)
            await v.delete()

    if retention.size is not None:
        total_size = 0
        for v in video_list:
            try:
                fpath = os.path.join(config.saveDir, v.filename)
                try:
                    total_size += os.path.getsize(fpath)
                except OSError:
                    total_size += os.path.getsize(fpath + ".part")
                if v.chat_filename is not None:
                    total_size += os.path.getsize(
                        os.path.join(config.saveDir, v.chat_filename)
                    )
            except OSError:
                pass
        while video_list and total_size > retention.size * 1_000_000:
            v = video_list.pop(0)
            LOG.info(f"Removing recording {v.title} (size)")
            all_recordings.remove(v)
            await v.delete()

    if retention.time is not None:
        now = int(datetime.datetime.now().timestamp())
        cutoff = now - retention.time * 86400
        while video_list and video_list[0].timestamp < cutoff:
            v = video_list.pop(0)
            LOG.info(f"Removing recording {v.title} (time)")
            all_recordings.remove(v)
            await v.delete()


async def retention_watcher():
    while not shutdown_event.is_set():
        LOG.info("Scanning retention for all channels")

        for name, channel in config.channels.items():
            retention = channel.retention or config.defaultRetention
            if (
                retention.count is not None
                or retention.size is not None
                or retention.time is not None
            ):
                videos = sorted(
                    [v for v in channels.recordings if v.channel == name],
                    key=lambda v: v.timestamp,
                )
                await _enforce_retention(retention, videos, channels.recordings)

        retention = config.globalRetention
        if (
            retention.count is not None
            or retention.size is not None
            or retention.time is not None
        ):
            videos = sorted(list(channels.recordings), key=lambda v: v.timestamp)
            await _enforce_retention(retention, videos, channels.recordings)

        await asyncio.sleep(config.pollInterval)


async def main():
    LOG.info("Starting yt-dvr")
    config.load(CONFIG_PATH)
    config.save(CONFIG_PATH)
    config.db = sqlite3.connect(DB_PATH)
    LOG.setLevel(config.logLevel)

    cur = config.db.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS videos ("
        "platform TEXT, channel TEXT, title TEXT, timestamp INTEGER, "
        "url TEXT, filename TEXT, chat_filename TEXT, "
        "thumbnail_filename TEXT, in_progress INTEGER)"
    )

    _migrate_db(cur)
    config.db.commit()

    rows = cur.execute(
        "SELECT platform, channel, title, timestamp, url, filename, "
        "chat_filename, thumbnail_filename, in_progress FROM videos"
    ).fetchall()

    for (
        platform,
        channel,
        title,
        timestamp,
        url,
        filename,
        chat_filename,
        thumbnail_filename,
        in_progress,
    ) in rows:
        r = channels.RecordingInfo(
            platform,
            channel,
            title,
            timestamp,
            url,
            filename,
            chat_filename,
            False,
            thumbnail_filename=thumbnail_filename,
        )
        if in_progress != 0:
            LOG.warning(f"Detected partial video at {filename}, remuxing")
            r.remux()

            r.generate_thumbnail()
            r.update()
        channels.recordings.append(r)

    app.set_check_now_event(check_now_event)
    asyncio.create_task(app.run(config.serverPort, shutdown_event.wait))
    asyncio.create_task(retention_watcher())

    signal.signal(signal.SIGINT, _signal_handler)
    multiprocessing.set_start_method("spawn")

    try:
        while not shutdown_event.is_set():
            check_now_event.clear()
            LOG.info("Checking channels for liveness")
            for name, channel in config.channels.items():
                LOG.debug(f"Checking channel {name}")
                already_recording = any(
                    r
                    for r in channels.recordings
                    if r.channel == name and r.in_progress
                )
                if already_recording:
                    continue

                ok, arg = await channel.check_live()
                if ok:
                    LOG.info(f"Starting recording for channel {name}")
                    rec = await channel.download(name, arg)
                    channels.recordings.append(rec)
                else:
                    LOG.debug(f"Stream {name} is not live")

            LOG.debug("Done checking")
            try:
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(shutdown_event.wait()),
                        asyncio.create_task(check_now_event.wait()),
                        asyncio.create_task(asyncio.sleep(config.pollInterval)),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            except Exception:
                pass
    except KeyboardInterrupt:
        LOG.warning("Caught interrupt, exiting")
        for r in channels.recordings:
            r.stop()
        return
    except BaseException:
        LOG.warning("Caught exception, exiting")
        for r in channels.recordings:
            r.abort()
        config.save(CONFIG_PATH)
        raise

    LOG.warning("Shutdown requested, exiting")
    for r in channels.recordings:
        r.stop()


def main_cli():
    asyncio.run(main())


if __name__ == "__main__":
    LOG.setLevel(logging.DEBUG)
    asyncio.run(main())
    config.save(CONFIG_PATH)
    config.db.close()
