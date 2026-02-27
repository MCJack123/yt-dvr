from . import ChatRecorder
from dateutil import parser as dateparser
from io import TextIOWrapper
import asyncio
import datetime
import kickpython
import os
import re

channel_name_regex = re.compile("^https?://(www\\.)?kick\\.com/([\\w_]+)")

class KickChatRecorder(ChatRecorder):
    running: bool
    conn: kickpython.KickAPI
    task: asyncio.Task
    file: TextIOWrapper
    start_time: datetime.datetime

    def __init__(self, loop: asyncio.EventLoop, url: str, filename: str):
        m = channel_name_regex.match(url)
        assert m
        name = m.group(2)
        self.running = True
        self.file = open(filename, "w")
        self.conn = kickpython.KickAPI(db_path=os.getenv("YTDVR_DB") or "./ytdvr.db")
        self.conn.add_message_handler(self.onmessage)
        loop.create_task(self.conn.connect_to_chatroom(name))
        self.start_time = datetime.datetime.now(datetime.UTC)

    async def onmessage(self, message: dict):
        d = dateparser.parse(message["created_at"])
        self.file.write("[%s][%d] %s: %s\n" % (d.isoformat(sep=" ", timespec="seconds"), (d - self.start_time).total_seconds(), message["sender_username"], message["content"]))
        self.file.flush()

    async def _stop(self):
        await self.conn.close()
        self.file.close()

    def stop(self):
        self.running = False
        asyncio.create_task(self.conn.close())
