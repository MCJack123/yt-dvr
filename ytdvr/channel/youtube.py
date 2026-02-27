from . import ChatRecorder
from config import LOG
from dateutil import parser as dateparser
from io import TextIOWrapper
from pytchat.processors.default.processor import Chatdata
import asyncio
import datetime
import pytchat

class YoutubeChatRecorder(ChatRecorder):
    running: bool
    conn: pytchat.LiveChatAsync
    file: TextIOWrapper
    start_time: datetime.datetime

    def __init__(self, loop: asyncio.EventLoop, info: dict, filename: str):
        self.running = True
        self.file = open(filename, "w")
        asyncio.run_coroutine_threadsafe(self._start(info["id"]), loop)
        self.start_time = datetime.datetime.now()

    async def _start(self, id: str):
        async def cb(chatdata): return await self.callback(chatdata)
        self.conn = pytchat.LiveChatAsync(id, callback=cb, interruptable=False, logger=LOG)
        LOG.debug("Started logging YT chat for " + id)

    async def callback(self, chatdata: Chatdata):
        if not self.running: return
        async for c in chatdata.async_items():
            d = dateparser.parse(c.datetime)
            self.file.write("[%s][%d] %s: %s\n" % (d.isoformat(sep=" ", timespec="seconds"), (d - self.start_time).total_seconds(), c.author.name, c.message))
        self.file.flush()

    def stop(self):
        self.running = False
        self.conn.terminate()
        self.file.close()
