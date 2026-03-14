import asyncio
import ctypes
import datetime
import importlib
import logging
import os
import pathlib
import threading
from typing import Any, Optional, cast

import pathvalidate
from config import LOG, Retention, config
from yt_dlp import YoutubeDL, utils


def ctype_async_raise(target_tid: int, exception: type):
    ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_long(target_tid), ctypes.py_object(exception)
    )
    if ret == 0:
        raise ValueError("Invalid thread ID")
    elif ret > 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(target_tid, ctypes.c_void_p(0))
        raise SystemError("PyThreadState_SetAsyncExc failed")


class ChatRecorder:
    def stop(self):
        raise NotImplementedError()


def get_chat_recorder(
    loop: asyncio.AbstractEventLoop,
    platform: str,
    url: str,
    filename: str,
    info: Optional[dict],
) -> Optional[ChatRecorder]:
    if platform in ("Twitch", "TwitchStream"):
        return importlib.import_module(".twitch", "channel").TwitchChatRecorder(
            url, filename
        )
    elif platform == "Youtube":
        try:
            yt = importlib.import_module(".youtube", "channel")
        except ImportError:
            return None
        return yt.YoutubeChatRecorder(loop, info, filename)
    elif platform == "Kick":
        try:
            kick = importlib.import_module(".kick", "channel")
        except ImportError:
            return None
        return kick.KickChatRecorder(loop, url, filename)
    return None


def _make_hls_path(file_path: str) -> str:
    p = pathlib.PurePosixPath(file_path)
    return str(p.with_suffix(".m3u8"))


def _make_thumbnail_path(file_path: str) -> str:
    """Convert a file path to its thumbnail .jpg equivalent."""
    p = pathlib.PurePosixPath(file_path)
    return str(p.with_suffix(".thumb.jpg"))


def generate_thumbnail(
    input_path: str, output_path: str, seek_seconds: int = 5
) -> bool:
    """
    Generate a thumbnail from a video file using ffmpeg.
    Returns True on success, False on failure.
    """
    try:
        import ffmpeg

        (
            ffmpeg.input(input_path, ss=seek_seconds)
            .output(
                output_path,
                vframes=1,
                format="image2",
                vcodec="mjpeg",
                s="480x270",
            )
            .overwrite_output()
            .run(quiet=True, capture_stderr=True)
        )
        return os.path.isfile(output_path)
    except Exception as e:
        LOG.debug(f"Thumbnail generation failed for {input_path}: {e}")
        return False


class RecordingInfo:
    platform: str
    channel: str
    title: str
    timestamp: int
    url: str
    filename: str
    chat_filename: Optional[str]
    thumbnail_filename: Optional[str]
    in_progress: bool

    _ytdlProcess: Optional[threading.Thread]
    _chatRecorder: Optional[ChatRecorder]
    _stop: bool
    _abort: bool

    def __init__(
        self,
        platform: str,
        channel: str,
        title: str,
        timestamp: int,
        url: str,
        filename: str,
        chat_filename: Optional[str],
        in_progress: bool,
        thumbnail_filename: Optional[str] = None,
    ):
        self.platform = platform
        self.channel = channel
        self.title = title
        self.timestamp = timestamp
        self.url = url
        self.filename = filename
        self.chat_filename = chat_filename
        self.thumbnail_filename = thumbnail_filename
        self.in_progress = in_progress
        self._ytdlProcess = None
        self._chatRecorder = None
        self._stop = False
        self._abort = False

    @classmethod
    def _create_ytdl(
        cls,
        loop: asyncio.AbstractEventLoop,
        dl: YoutubeDL,
        info: dict,
        get_chat: bool,
        platform: str,
        channel: str,
        title: str,
    ) -> "RecordingInfo":
        now = datetime.datetime.now()
        timestamp_str = now.isoformat(sep=" ", timespec="seconds").replace(":", "-")
        base_name = pathvalidate.sanitize_filename(f"{timestamp_str} - {title}")

        self = RecordingInfo(
            platform=platform,
            channel=channel,
            title=title,
            timestamp=int(now.timestamp()),
            url=cast(str, info["original_url"]),
            filename=f"{channel}/{base_name}.ts",
            chat_filename=f"{channel}/{base_name}.txt" if get_chat else None,
            thumbnail_filename=None,
            in_progress=True,
        )

        os.makedirs(os.path.join(config.saveDir, channel), exist_ok=True)

        dl.params["outtmpl"] = {"default": os.path.join(config.saveDir, self.filename)}
        dl.params["hls_use_mpegts"] = True
        dl.params["wait_for_video"] = (2, 5)

        self._ytdlProcess = threading.Thread(
            target=self._ytdlMain, name=self.filename, args=[dl, loop]
        )
        self._ytdlProcess.start()

        if get_chat:
            self._chatRecorder = get_chat_recorder(
                loop,
                platform,
                cast(str, info["original_url"]),
                os.path.join(config.saveDir, cast(str, self.chat_filename)),
                info,
            )

        loop.call_soon_threadsafe(self._insert_into_db)
        LOG.info(f"Starting recording process (TID {self._ytdlProcess.native_id})")
        return self

    def _insert_into_db(self):
        cur = config.db.cursor()
        cur.execute(
            "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.platform,
                self.channel,
                self.title,
                self.timestamp,
                self.url,
                self.filename,
                self.chat_filename,
                self.thumbnail_filename,
                self.in_progress,
            ),
        )
        config.db.commit()

    def stop(self):
        if self._ytdlProcess is not None:
            self._stop = True
            ctype_async_raise(self._ytdlProcess.ident, KeyboardInterrupt)
            self._ytdlProcess.join()
        if self._chatRecorder is not None:
            self._chatRecorder.stop()

    def abort(self):
        if self._ytdlProcess is not None:
            self._abort = True
            self._stop = True
            ctype_async_raise(self._ytdlProcess.ident, KeyboardInterrupt)
        if self._chatRecorder is not None:
            self._chatRecorder.stop()

    def remux(self):
        if self.filename.endswith("." + config.remuxFormat):
            return

        LOG.info(f"Remuxing container for {self.title} ({self.filename})")
        newname = pathlib.PurePosixPath(self.filename).with_suffix(
            "." + config.remuxFormat
        )
        input_path = os.path.join(config.saveDir, self.filename)
        if not os.path.exists(input_path):
            input_path += ".part"
        if not os.path.exists(input_path):
            LOG.error(f"Cannot remux: source file not found for {self.filename}")
            return

        output_path = os.path.join(config.saveDir, str(newname))
        try:
            import ffmpeg

            (
                ffmpeg.input(input_path)
                .output(
                    output_path,
                    format=config.remuxFormat,
                    codec="copy",
                    movflags="+faststart",
                )
                .overwrite_output()
                .run(quiet=config.logLevel != "DEBUG")
            )
            try:
                os.remove(input_path)
            except OSError:
                pass
            self.filename = str(newname)
        except Exception as e:
            LOG.error(f"Remux failed: {e}")

    def generate_thumbnail(self):
        """Generate thumbnail after recording finishes. Runs in recording thread."""
        base = pathlib.PurePosixPath(self.filename)
        thumb_rel = str(base.with_suffix(".thumb.jpg"))
        thumb_abs = os.path.join(config.saveDir, thumb_rel)

        input_path = os.path.join(config.saveDir, self.filename)
        if not os.path.isfile(input_path):
            input_path = input_path + ".part"
        if not os.path.isfile(input_path):
            LOG.debug(
                f"Cannot generate thumbnail: source not found for {self.filename}"
            )
            return

        LOG.info(f"Generating thumbnail for {self.title}")
        if generate_thumbnail(input_path, thumb_abs):
            self.thumbnail_filename = thumb_rel
        else:
            LOG.debug(f"Thumbnail generation skipped for {self.title}")

    def update(
        self,
        platform: Optional[str] = None,
        channel: Optional[str] = None,
        timestamp: Optional[int] = None,
    ):
        orig_platform = platform or self.platform
        orig_channel = channel or self.channel
        orig_timestamp = timestamp or self.timestamp

        cur = config.db.cursor()
        cur.execute(
            """UPDATE videos
               SET platform = ?, channel = ?, title = ?, timestamp = ?,
                   url = ?, filename = ?, chat_filename = ?,
                   thumbnail_filename = ?, in_progress = ?
               WHERE platform = ? AND channel = ? AND timestamp = ?""",
            (
                self.platform,
                self.channel,
                self.title,
                self.timestamp,
                self.url,
                self.filename,
                self.chat_filename,
                self.thumbnail_filename,
                self.in_progress,
                orig_platform,
                orig_channel,
                orig_timestamp,
            ),
        )
        config.db.commit()

    async def delete(self):
        if self.in_progress:
            self.abort()
        cur = config.db.cursor()
        cur.execute(
            "DELETE FROM videos WHERE platform = ? AND channel = ? AND timestamp = ?",
            (self.platform, self.channel, self.timestamp),
        )
        config.db.commit()
        for attr in ("filename", "chat_filename", "thumbnail_filename"):
            val = getattr(self, attr, None)
            if val:
                try:
                    os.remove(os.path.join(config.saveDir, val))
                except OSError:
                    pass

    def _dump(self) -> dict:
        file_path = "/files/" + self.filename
        return {
            "platform": self.platform,
            "channel": self.channel,
            "title": self.title,
            "timestamp": self.timestamp,
            "original_url": self.url,
            "path": file_path,
            "hls_path": _make_hls_path(file_path),
            "chat_path": (
                "/files/" + self.chat_filename
                if self.chat_filename is not None
                else None
            ),
            "thumbnail_path": (
                "/files/" + self.thumbnail_filename
                if self.thumbnail_filename is not None
                else None
            ),
            "in_progress": self.in_progress,
        }

    def _ytdlProgress(self, _):
        if self._stop:
            self._stop = False
            raise KeyboardInterrupt()

    def _ytdlMain(self, dl: YoutubeDL, loop: asyncio.AbstractEventLoop):
        dl.add_progress_hook(self._ytdlProgress)
        try:
            dl.download(self.url)
        except Exception:
            LOG.error(f"A download error occurred in {self.title}")
        finally:
            self.in_progress = False
            if not self._abort:
                if self.filename.endswith(".ts") and config.remuxRecordings:
                    self.remux()

                self.generate_thumbnail()
                loop.call_soon_threadsafe(self.update)
            self._ytdlProcess = None


class Channel:
    url: str
    getChat: bool
    platform: Optional[str]
    ytdlParams: Optional[dict]
    retention: Optional[Retention]
    quality: Optional[str]

    def __init__(self, obj: dict):
        self.url = obj["url"]
        self.getChat = obj.get("getChat", False)
        self.platform = obj.get("platform")
        self.ytdlParams = obj.get("ytdlParams")
        self.quality = obj.get("quality")
        retention_data = obj.get("retention")
        self.retention = (
            Retention(retention_data) if retention_data is not None else None
        )

    def _check_live(self, loop: asyncio.AbstractEventLoop, future: asyncio.Future):
        params = dict(self.ytdlParams) if self.ytdlParams else {}
        dl = YoutubeDL(params)
        if LOG.level > logging.DEBUG:
            dl.params.setdefault("noprogress", True)
            dl.params.setdefault("quiet", True)
        try:
            info = dl.extract_info(self.url, False)
        except utils.DownloadError:
            loop.call_soon_threadsafe(future.set_result, (False, None))
            return
        loop.call_soon_threadsafe(future.set_result, (True, (dl, info)))

    async def check_live(self) -> tuple[bool, Any]:
        future: asyncio.Future = asyncio.Future()
        loop = asyncio.get_running_loop()
        thread = threading.Thread(target=self._check_live, args=[loop, future])
        thread.start()
        res = await future
        thread.join()
        return res

    def _download(
        self,
        name: str,
        arg: tuple[YoutubeDL, dict],
        loop: asyncio.AbstractEventLoop,
        future: asyncio.Future,
    ):
        dl, info = arg
        dl.params["format"] = self.quality or "bestvideo+bestaudio"
        try:
            title = (
                info["description"]
                if "(live)" in info.get("title", "")
                else info["title"]
            )
            result = RecordingInfo._create_ytdl(
                loop,
                dl,
                info,
                self.getChat,
                self.platform or info["extractor_key"],
                name,
                title,
            )
            loop.call_soon_threadsafe(future.set_result, result)
        except BaseException as e:
            loop.call_soon_threadsafe(future.set_exception, e)

    async def download(self, name: str, live_arg: Any) -> RecordingInfo:
        future: asyncio.Future = asyncio.Future()
        loop = asyncio.get_running_loop()
        thread = threading.Thread(
            target=self._download,
            args=[name, live_arg, loop, future],
        )
        thread.start()
        res = await future
        thread.join()
        return res

    def _dump(self) -> dict:
        return {
            "url": self.url,
            "getChat": self.getChat,
            "platform": self.platform,
            "quality": self.quality,
            "retention": self.retention._dump() if self.retention is not None else None,
            "ytdlParams": self.ytdlParams,
        }


recordings: list[RecordingInfo] = []
