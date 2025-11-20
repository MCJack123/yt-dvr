import datetime
import os
import threading
import logging
from typing import Optional, cast
from yt_dlp import YoutubeDL, utils
import sys
sys.path.append("..")
import config

LOG = logging.getLogger("yt-dvr")

class ChatRecorder:
    """
    Holds information about a chat recording session for a platform.
    """
    id: str
    recording: bool

    def __enter__(self):
        return self
    
    def __exit__(self):
        self.close()

    def startRecording(self, path: str):
        """
        Starts recording the chat to a file.

        :param path: The path to record to.
        """
        raise NotImplementedError()
    
    def stopRecording(self):
        """
        Stops a previously started recording.
        """
        raise NotImplementedError()
    
    def close(self):
        """
        Closes the chat connection after stopping any recording.
        """
        if self.recording:
            self.stopRecording()

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
    def _create_ytdl(cls, dl: YoutubeDL, info: dict, platform: str, channel: str, title: str):
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
        self._ytdlProcess = threading.Thread(target=self._ytdlMain, name=self.filename, args=[dl])
        self._ytdlProcess.start()
        LOG.info(f"Starting recording process (TID {self._ytdlProcess.native_id})")
        return self

    def stop(self):
        """
        Stops a pending recording if in progress, triggering a remux if necessary.
        """
        if self._ytdlProcess is not None:
            self._stop = True
            self._ytdlProcess.join()
    
    def abort(self):
        """
        Aborts a pending recording if in progress, skipping remux. This is used
        on server close.
        """
        # TODO
        if self._ytdlProcess is not None:
            self._stop = True

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

    def _ytdlProgress(self):
        if self._stop:
            self._stop = False
            raise KeyboardInterrupt()

    def _ytdlMain(self, dl: YoutubeDL):
        dl.add_progress_hook(self._ytdlProgress)
        dl.download(self.url)
        self.in_progress = False
        import server
        server.updateRecording(self)
        self._ytdlProcess = None

class Platform:
    """
    The handle to a certain platform connection instance.
    """
    name: str
    url: str
    headers: dict
    platformOptions: dict
    ytdlParams: Optional[dict]
    _ytdlTitleKey: str

    async def poll(self, id: str) -> bool:
        """
        Polls a channel for live status.

        :param id: The ID of the channel to check
        :returns: Whether the channel is live
        """
        raise NotImplementedError()
    
    async def download(self, id: str, quality: Optional[str] = None) -> Optional[RecordingInfo]:
        """
        Starts a recording session for a channel and returns the info handle.

        :param id: The ID of the channel to record
        :param quality: The quality of the video to get, if desired
        :returns: A new recording session, or None if the channel isn't live
        """
        raise NotImplementedError()
    
    async def connectChat(self, id: str) -> Optional[ChatRecorder]:
        """
        Connects to a chat channel (if available) and returns a recorder instance.

        :param id: The channel to connect to
        :returns: The chat recorder instance, 
        """
        return None
    
    async def _ytdlDownload(self, id: str, url: str, quality: Optional[str]) -> Optional[RecordingInfo]:
        """
        Internal - starts a generic yt-dlp-based download session.

        :param url: The URL of the channel to record
        :param quality: The quality of the video to get, if desired
        :returns: A new recording session, or None if the channel isn't live
        """
        dl = YoutubeDL(self.ytdlParams) # type: ignore
        try:
            info = dl.extract_info(url, False)
        except utils.UserNotLive:
            LOG.error(f"Stream {url} is not live")
            return None
        dl.params["format"] = quality or "bestvideo+bestaudio"
        return RecordingInfo._create_ytdl(dl, info, self.name, id, info[self._ytdlTitleKey]) # type: ignore
        """
        if not ("formats" in info): return None
        formats = info["formats"]
        if formats is None: return None
        selected_format = None
        if quality is not None:
            for format in formats:
                if cast(str, format["format_id"]).find(quality) != -1:
                    selected_format = cast(str, format["format_id"])
                    break
        if selected_format is None:
            best_audio = None
            best_audio_rate = 0
            best_video = None
            best_video_rate = 0
            best_video_dim = 0
            best_video_has_audio = False
            for format in formats:
                if format["vcodec"] != 'none':
                    video_dim = cast(int, format["width"]) * cast(int, format["height"])
                    video_rate = cast(int | None, format["vbr"])
                    if video_rate is None: video_rate = cast(int, format["tbr"])
                    if best_video is None or video_dim > best_video_dim or (video_dim == best_video_dim and video_rate > best_video_rate):
                        best_video = cast(str, format["format_id"])
                        best_video_rate = video_rate
                        best_video_dim = video_dim
                        best_video_has_audio = format["acodec"] != 'none'
                elif format["acodec"] != 'none' and not best_video_has_audio:
                    audio_rate = cast(int | None, format["abr"])
                    if audio_rate is None: audio_rate = cast(int, format["tbr"])
                    if best_audio is None or audio_rate > best_audio_rate:
                        best_audio = cast(str, format["format_id"])
                        best_audio_rate = audio_rate
            if best_video is None:
                if best_audio is None:
                    return None # no formats found somehow
                selected_format = best_audio
            else:
                if best_video_has_audio or best_audio is None: selected_format = best_video
                else: selected_format = best_video + "+" + best_audio
        """

    def _dump(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "platformOptions": self.platformOptions,
            "ytdlParams": self.ytdlParams
        }

recordings: list[RecordingInfo] = []
