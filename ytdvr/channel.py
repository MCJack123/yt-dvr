import datetime
import os
import threading
import logging
from typing import Optional, cast, Callable
from yt_dlp import YoutubeDL, utils
import sys
import asyncio
import ctypes
sys.path.append("..")
import config

LOG = logging.getLogger("yt-dvr")

def ctype_async_raise(target_tid, exception):
    ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(target_tid), ctypes.py_object(exception))
    # ref: http://docs.python.org/c-api/init.html#PyThreadState_SetAsyncExc
    if ret == 0:
        raise ValueError("Invalid thread ID")
    elif ret > 1:
        # Huh? Why would we notify more than one threads?
        # Because we punch a hole into C level interpreter.
        # So it is better to clean up the mess.
        ctypes.pythonapi.PyThreadState_SetAsyncExc(target_tid, ctypes.c_void_p(0))
        raise SystemError("PyThreadState_SetAsyncExc failed")

async def task_wrapper(fn, arg):
    return fn(arg)

class RecordingInfo:
    """
    Information about a live stream. This is derived by platforms to implement
    a recording session.
    """
    platform: str
    channel: str
    title: str
    timestamp: int
    url: str
    filename: str
    in_progress: bool

    _ytdlProcess: Optional[threading.Thread]
    _stop: bool

    def __init__(self, platform: str, channel: str, title: str, timestamp: int, url: str, filename: str, in_progress: bool):
        """
        Creates a base recording object.

        :param platform: The ID of the platform that started the recording
        :param channel: The ID of the channel that is being recorded
        :param title: The title of the video
        :param timestamp: The time the recording started
        :param url: The original URL of the video
        :param filename: The path of the file on disk, relative to saveDir
        :param in_progress: Whether the recording is ongoing
        """
        self.platform = platform
        self.channel = channel
        self.title = title
        self.timestamp = timestamp
        self.url = url
        self.filename = filename
        self.in_progress = in_progress
        self._ytdlProcess = None
        self._stop = False

    @classmethod
    def _create_ytdl(cls, dl: YoutubeDL, info: dict, platform: str, channel: str, title: str, completion: Callable):
        """
        Internal - Creates a recording for a yt-dl session.

        :param dl: The yt-dl session that was initialized
        :param info: The info about the video
        :param platform: The ID of the platform that started the recording
        :param channel: The ID of the channel that is being recorded
        :param title: The title of the video
        """
        self = RecordingInfo(platform, channel, title, cast(int, info["timestamp"]), cast(str, info["original_url"]), platform + "/" + channel + "/" + datetime.datetime.now().isoformat(sep=" ", timespec="seconds").replace(":", "-") + " - " + title + ".mp4", True)
        try: os.makedirs(config.config.saveDir + "/" + platform + "/" + channel)
        except FileExistsError: pass
        dl.params["outtmpl"] = {"default": config.config.saveDir + "/" + self.filename} # TODO: proper path and extension
        dl.params["hls_use_mpegts"] = True
        #dl.params["writesubtitles"] = True
        #dl.params["subtitleslangs"] = ["live_chat"]
        dl.params["wait_for_video"] = (2, 5)
        self._ytdlProcess = threading.Thread(target=self._ytdlMain, name=self.filename, args=[dl, completion, asyncio.current_task().get_loop()]) # type: ignore
        self._ytdlProcess.start()
        LOG.info(f"Starting recording process (TID {self._ytdlProcess.native_id})")
        return self

    def stop(self):
        """
        Stops a pending recording if in progress, triggering a remux if necessary.
        """
        if self._ytdlProcess is not None:
            self._stop = True
            ctype_async_raise(self._ytdlProcess.ident, KeyboardInterrupt)
            self._ytdlProcess.join()
    
    def abort(self):
        """
        Aborts a pending recording if in progress, skipping remux. This is used
        on server close.
        """
        # TODO
        if self._ytdlProcess is not None:
            self._stop = True
            ctype_async_raise(self._ytdlProcess.ident, KeyboardInterrupt)

    def _dump(self):
        return {
            "platform": self.platform,
            "channel": self.channel,
            "title": self.title,
            "timestamp": self.timestamp,
            "original_url": self.url,
            "path": "/files/" + self.filename,
            "in_progress": self.in_progress
        }

    def _ytdlProgress(self, _):
        if self._stop:
            self._stop = False
            raise KeyboardInterrupt()

    def _ytdlMain(self, dl: YoutubeDL, completion: Callable, loop: asyncio.EventLoop):
        dl.add_progress_hook(self._ytdlProgress)
        try:
            dl.download(self.url)
        except KeyboardInterrupt: pass
        finally:
            self.in_progress = False
            asyncio.run_coroutine_threadsafe(task_wrapper(completion, self), loop)
            self._ytdlProcess = None

class Channel:
    """
    Contains information about a channel to monitor.
    """
    name: str
    url: str
    ytdlParams: Optional[dict]
    retention: Optional[config.Retention]
    quality: Optional[str]

    def __init__(self, obj: dict):
        self.name = obj["name"]
        self.url = obj["url"]
        if "ytdlParams" in obj: self.ytdlParams = obj["ytdlParams"]
        else: self.ytdlParams = None
        if "retention" in obj and obj["retention"] is not None: self.retention = config.Retention(obj["retention"])
        else: self.retention = None
        if "quality" in obj: self.quality = obj["quality"]
        else: self.quality = None
    
    def download(self, completion: Callable[[RecordingInfo], None]) -> Optional[RecordingInfo]:
        """
        Attempts to start a recording session for a channel and returns the info
        handle if live.

        :returns: A new recording session, or None if the channel isn't live
        """
        dl = YoutubeDL(self.ytdlParams) # type: ignore
        try:
            info = dl.extract_info(self.url, False)
        except utils.DownloadError:
            LOG.info(f"Stream {self.name} is not live")
            return None
        dl.params["format"] = self.quality or "bestvideo+bestaudio"
        return RecordingInfo._create_ytdl(dl, info, info["extractor_key"], self.name, info["description"] if info["title"].find("(live)") != -1 else info["title"], completion) # type: ignore

    def _dump(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "quality": self.quality,
            "retention": self.retention._dump() if self.retention is not None else None,
            "ytdlParams": self.ytdlParams
        }

recordings: list[RecordingInfo] = []
