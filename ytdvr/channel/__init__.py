from typing import Optional, cast, Callable, Any
from yt_dlp import YoutubeDL, utils
import asyncio
import ctypes
import datetime
import ffmpeg
import importlib
import logging
import os
import pathvalidate
import sys
import threading
sys.path.append("..")
from config import config, LOG, Retention

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

class ChatRecorder:
    """
    An abstract class representing a chat recorder for a platform.
    """

    def stop(self):
        """
        Stop a pending chat recording process.
        """
        raise NotImplementedError()

def get_chat_recorder(loop: asyncio.EventLoop, platform: str, url: str, filename: str, info: Optional[dict]) -> Optional[ChatRecorder]:
    """
    Returns a chat recorder for a platform, if available, and starts recording.

    :param platform: The platform to get for
    :param url: The URL to start recording
    :param filename: The file path to save at
    """
    if platform == "Twitch" or platform == "TwitchStream":
        return importlib.import_module(".twitch", "channel").TwitchChatRecorder(url, filename)
    elif platform == "Youtube":
        try: yt = importlib.import_module(".youtube", "channel")
        except: return None
        return yt.YoutubeChatRecorder(loop, info, filename)
    elif platform == "Kick":
        try: kick = importlib.import_module(".kick", "channel")
        except: return None
        return kick.KickChatRecorder(loop, url, filename)
    return None

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
    chat_filename: Optional[str]
    in_progress: bool

    _ytdlProcess: Optional[threading.Thread]
    _chatRecorder: Optional[ChatRecorder]
    _stop: bool
    _abort: bool

    def __init__(self, platform: str, channel: str, title: str, timestamp: int, url: str, filename: str, chat_filename: Optional[str], in_progress: bool):
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
        self.chat_filename = chat_filename
        self.in_progress = in_progress
        self._ytdlProcess = None
        self._chatRecorder = None
        self._stop = False
        self._abort = False

    @classmethod
    def _create_ytdl(cls, loop: asyncio.EventLoop, dl: YoutubeDL, info: dict, getChat: bool, platform: str, channel: str, title: str):
        """
        Internal - Creates a recording for a yt-dl session.

        :param loop: The event loop for the main thread
        :param dl: The yt-dl session that was initialized
        :param info: The info about the video
        :param getChat: Whether to create a chat recorder with the recording
        :param platform: The ID of the platform that started the recording
        :param channel: The ID of the channel that is being recorded
        :param title: The title of the video
        """
        self = RecordingInfo(
            platform, channel, title,
            int(datetime.datetime.now().timestamp()),
            cast(str, info["original_url"]),
            channel + "/" + pathvalidate.sanitize_filename(datetime.datetime.now().isoformat(sep=" ", timespec="seconds").replace(":", "-") + " - " + title + ".ts"),
            channel + "/" + pathvalidate.sanitize_filename(datetime.datetime.now().isoformat(sep=" ", timespec="seconds").replace(":", "-") + " - " + title + ".txt") if getChat is not None else None,
            True)
        try: os.makedirs(config.saveDir + "/" + channel)
        except FileExistsError: pass
        dl.params["outtmpl"] = {"default": config.saveDir + "/" + self.filename} # TODO: proper path and extension
        dl.params["hls_use_mpegts"] = True
        #dl.params["writesubtitles"] = True
        #dl.params["subtitleslangs"] = ["live_chat"]
        dl.params["wait_for_video"] = (2, 5)
        self._ytdlProcess = threading.Thread(target=self._ytdlMain, name=self.filename, args=[dl, loop]) # type: ignore
        self._ytdlProcess.start()
        if getChat: self._chatRecorder = get_chat_recorder(loop, platform, cast(str, info["original_url"]), config.saveDir + "/" + cast(str, self.chat_filename), info)
        loop.call_soon_threadsafe(self._insert_into_db)
        LOG.info(f"Starting recording process (TID {self._ytdlProcess.native_id})")
        return self
    
    def _insert_into_db(self):
        cur = config.db.cursor()
        cur.execute("INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.platform, self.channel, self.title, self.timestamp, self.url, self.filename, self.chat_filename, self.in_progress))
        config.db.commit()

    def stop(self):
        """
        Stops a pending recording if in progress, triggering a remux if necessary.
        """
        if self._ytdlProcess is not None:
            self._stop = True
            ctype_async_raise(self._ytdlProcess.ident, KeyboardInterrupt)
            self._ytdlProcess.join()
        if self._chatRecorder is not None: self._chatRecorder.stop()
    
    def abort(self):
        """
        Aborts a pending recording if in progress, skipping remux. This is used
        on server close.
        """
        if self._ytdlProcess is not None:
            self._abort = True
            self._stop = True
            ctype_async_raise(self._ytdlProcess.ident, KeyboardInterrupt)
        if self._chatRecorder is not None: self._chatRecorder.stop()

    def remux(self):
        """
        Remuxes the recording if necessary.
        """
        if self.filename.endswith("." + config.remuxFormat): return
        LOG.info("Remuxing container for " + self.title + " (" + self.filename + ")")
        newname = self.filename.removesuffix(".ts") + "." + config.remuxFormat
        try:
            input = config.saveDir + "/" + self.filename
            if not os.path.exists(input): input += ".part"
            (ffmpeg
                .input(filename=input)
                .output(filename=config.saveDir + "/" + newname, f=config.remuxFormat, codec="copy", extra_options={"movflags": "+faststart", "y": True, "loglevel": config.logLevel.lower(), "hide_banner": True})).run()
            try: os.remove(input)
            except: pass
            self.filename = newname
        except Exception as e:
            LOG.error(e)
    
    def update(self, platform: str | None = None, channel: str | None = None, timestamp: int | None = None):
        """
        Updates the recording status in the database, and remuxes if necessary.
        Provide the original platform/channel/timestamp values if they have
        changed, for identification in the database. (They are not required
        otherwise.)

        This must be called from the main thread.

        :param platform: The original platform for the recording
        :param channel: The original channel for the recording
        :param timestamp: The original timestamp for the recording
        """
        if platform is None: platform = self.platform
        if channel is None: channel = self.channel
        if timestamp is None: timestamp = self.timestamp
        cur = config.db.cursor()
        cur.execute("UPDATE videos SET platform = ?, channel = ?, title = ?, timestamp = ?, url = ?, filename = ?, chat_filename = ?, in_progress = ? WHERE platform = ? AND channel = ? AND timestamp = ?",
                    (self.platform, self.channel, self.title, self.timestamp, self.url, self.filename, self.chat_filename, self.in_progress, platform, channel, timestamp))
        config.db.commit()

    async def delete(self):
        """
        Deletes the recording from disk.

        This must be called from the main thread.
        """
        if self.in_progress: self.abort()
        cur = config.db.cursor()
        cur.execute("DELETE FROM videos WHERE platform = ? AND channel = ? AND timestamp = ?", (self.platform, self.channel, self.timestamp))
        config.db.commit()
        try:
            os.remove(config.saveDir + "/" + self.filename)
            if self.chat_filename is not None: os.remove(config.saveDir + "/" + self.chat_filename)
        except: pass

    def _dump(self):
        return {
            "platform": self.platform,
            "channel": self.channel,
            "title": self.title,
            "timestamp": self.timestamp,
            "original_url": self.url,
            "path": "/files/" + self.filename,
            "chat_path": "/files/" + self.chat_filename if self.chat_filename is not None else None,
            "in_progress": self.in_progress
        }

    def _ytdlProgress(self, _):
        if self._stop:
            self._stop = False
            raise KeyboardInterrupt()

    def _ytdlMain(self, dl: YoutubeDL, loop: asyncio.EventLoop):
        dl.add_progress_hook(self._ytdlProgress)
        try: dl.download(self.url)
        except: LOG.error("A download error occurred in " + self.title)
        finally:
            self.in_progress = False
            if not self._abort:
                if not self.in_progress and self.filename.endswith(".ts") and config.remuxRecordings:
                    self.remux()
                loop.call_soon_threadsafe(self.update)
            self._ytdlProcess = None

class Channel:
    """
    Contains information about a channel to monitor.
    """
    url: str
    getChat: bool
    platform: Optional[str]
    ytdlParams: Optional[dict]
    retention: Optional[Retention]
    quality: Optional[str]

    def __init__(self, obj: dict):
        self.url = obj["url"]
        self.getChat = obj["getChat"]
        if "platform" in obj: self.platform = obj["platform"]
        else: self.platform = None
        if "ytdlParams" in obj: self.ytdlParams = obj["ytdlParams"]
        else: self.ytdlParams = None
        if "retention" in obj and obj["retention"] is not None: self.retention = Retention(obj["retention"])
        else: self.retention = None
        if "quality" in obj: self.quality = obj["quality"]
        else: self.quality = None

    def _check_live(self, loop: asyncio.EventLoop, future: asyncio.Future):
        dl = YoutubeDL(self.ytdlParams) # type: ignore
        if not ("noprogress" in dl.params) and LOG.level > logging.DEBUG: dl.params["noprogress"] = True
        if not ("quiet" in dl.params) and LOG.level > logging.DEBUG: dl.params["quiet"] = True
        try:
            info = dl.extract_info(self.url, False)
        except utils.DownloadError:
            loop.call_soon_threadsafe(future.set_result, (False, None))
            return
        loop.call_soon_threadsafe(future.set_result, (True, (dl, info))) # type: ignore

    async def check_live(self) -> tuple[bool, Any]:
        """
        Checks if the channel is live, and if so, returns some internal metadata
        to pass to download.

        :returns: Whether the channel is live, and if so, an opaque value to pass to `download`
        """
        future = asyncio.Future()
        thread = threading.Thread(target=self._check_live, args=[asyncio.current_task().get_loop(), future]) # type: ignore
        thread.start()
        res = await future
        #print(res)
        thread.join()
        return res
    
    def _download(self, name: str, arg: tuple[YoutubeDL, dict], loop: asyncio.EventLoop, future: asyncio.Future):
        dl, info = arg
        dl.params["format"] = self.quality or "bestvideo+bestaudio"
        try:
            loop.call_soon_threadsafe(future.set_result, RecordingInfo._create_ytdl(loop, dl, info, self.getChat, self.platform or info["extractor_key"], name, info["description"] if info["title"].find("(live)") != -1 else info["title"])) # type: ignore
        except BaseException as e:
            loop.call_soon_threadsafe(future.set_exception, e)

    async def download(self, name: str, live_arg: Any) -> RecordingInfo:
        """
        Attempts to start a recording session for a channel after checking if
        the channel is live.

        :param name: The name of the channel
        :param live_arg: The second parameter returned by check_live
        :param completion: A completion handler to call when the recording finishes
        :returns: A new recording session
        """
        future = asyncio.Future()
        thread = threading.Thread(target=self._download, args=[name, live_arg, asyncio.current_task().get_loop(), future]) # type: ignore
        thread.start()
        res = await future
        #print(res)
        thread.join()
        return res

    def _dump(self) -> dict:
        return {
            "url": self.url,
            "getChat": self.getChat,
            "platform": self.platform,
            "quality": self.quality,
            "retention": self.retention._dump() if self.retention is not None else None,
            "ytdlParams": self.ytdlParams
        }

recordings: list[RecordingInfo] = []
