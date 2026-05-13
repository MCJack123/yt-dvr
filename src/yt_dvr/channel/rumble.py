from typing import cast

from copy import copy
from io import TextIOWrapper
from yt_dlp import YoutubeDL, utils
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dvr.channel import ChatRecorder, Channel
from yt_dvr.config import LOG, config
import asyncio
import datetime
import httpx
import json
import ld_eventsource
import logging
import re
import sys
import threading

channel_name_regex = re.compile("https?://[^/]*rumble\\.com/c?/?([^/]+)")
base36 = "0123456789abcdefghijklmnopqrstuvwxyz"

class RumbleChatRecorder(ChatRecorder):
    thread: threading.Thread
    conn: ld_eventsource.SSEClient
    file: TextIOWrapper
    running: bool
    start_time: datetime.datetime
    known_user_ids: dict[int, str]

    def __init__(self, info: dict, filename: str):
        # ID exposed in info is in base36, decode it
        id_num = 0
        for c in info["id"][1:]: id_num = id_num * 36 + base36.find(c)
        self.running = True
        self.file = open(filename, "w")
        self.conn = ld_eventsource.SSEClient(f"https://web7.rumble.com/chat/api/chat/{id_num}/stream")
        self.thread = threading.Thread(target=self._worker, name="Rumble chat for " + info["channel"], args=[info["channel"]])
        self.thread.start()
        self.start_time = datetime.datetime.now(datetime.UTC)
        self.known_user_ids = {}

    def _worker(self, name: str):
        LOG.debug("Connecting to chat for " + name)
        for event in self.conn.events:
            if not self.running: break
            if event.event != "message": continue
            try:
                data = json.loads(event.data)
                if data["type"] == "messages" or data["type"] == "init":
                    for msg in data["data"]["messages"]:
                        if msg["user_id"] in self.known_user_ids:
                            username = self.known_user_ids[msg["user_id"]]
                        else:
                            username = "<" + str(msg["user_id"]) + ">"
                            for user in data["data"]["users"]:
                                if user["id"] == msg["user_id"]:
                                    username = user["username"]
                                    self.known_user_ids[msg["user_id"]] = username
                                    break
                        self.file.write("[%s][%d] %s: %s\n" % (msg["time"], (datetime.datetime.fromisoformat(msg["time"]) - self.start_time).total_seconds(), username, msg["text"]))
                elif data["type"] == "mute_users":
                    for id in data["data"]["user_id"]:
                        if id in self.known_user_ids:
                            username = self.known_user_ids[id]
                        else:
                            username = "<" + str(id) + ">"
                        self.file.write("[%s][%d] %s has been muted.\n" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), (datetime.datetime.now(datetime.UTC) - self.start_time).total_seconds(), username))
                else:
                    self.file.write(event.data + "\n")
                self.file.flush()
            except BaseException as e:
                LOG.debug(event)
                LOG.error(e)
        self.conn.close()
        self.file.close()

    def stop(self):
        self.running = False

class RumbleChannel(Channel):
    channel_id: str

    def __init__(self, obj: dict):
        super().__init__(obj)
        m = channel_name_regex.match(self.url)
        if not m: raise ValueError(f"URL {self.url} is not a Rumble URL")
        channel_name = m.group(1)
        with httpx.Client() as client:
            response = client.get(f"https://rumble.com/service.php?name=search&query={channel_name}&offset=0&limit=6&api=7")
            data = response.json()
            self.channel_id = next(filter(lambda it: it["name"] == channel_name, cast(list, data["data"]["channel"]["items"])))["id"]

    def _check_live(self, loop: asyncio.EventLoop, future: asyncio.Future):
        with httpx.Client() as client:
            response = client.get(f"https://rumble.com/service.php?id={self.channel_id}&offset=0&name=video_collection.videos&options=video.full&content_type=long-form&sort=&limit=6&api=7")
            data = response.json()
        try:
            video = data["data"]["items"][0]
            if video["live"]:
                dl = YoutubeDL(copy(self.ytdlParams)) # type: ignore
                if not ("noprogress" in dl.params) and LOG.level > logging.DEBUG: dl.params["noprogress"] = True
                if not ("quiet" in dl.params) and LOG.level > logging.DEBUG: dl.params["quiet"] = True
                if not ("impersonate" in dl.params): dl.params["impersonate"] = ImpersonateTarget(client='chrome', version=None, os=None, os_version=None)
                if hasattr(sys, "_MEIPASS"):
                    dl.params["ffmpeg_location"] = sys._MEIPASS + "/ffmpeg.exe" # type: ignore
                    dl.params["js_runtimes"] = {"deno": {"path": sys._MEIPASS + "/deno.exe"}} # type: ignore
                elif config.ffmpegPath is not None: dl.params["ffmpeg_location"] = config.ffmpegPath
                try:
                    info = dl.extract_info(video["url"], False)
                except utils.DownloadError:
                    dl.close()
                    loop.call_soon_threadsafe(future.set_result, (False, None))
                    return
                loop.call_soon_threadsafe(future.set_result, (True, (dl, info))) # type: ignore
            else:
                loop.call_soon_threadsafe(future.set_result, (False, None))
        except KeyError as e:
            print(e)
            loop.call_soon_threadsafe(future.set_result, (False, None))
