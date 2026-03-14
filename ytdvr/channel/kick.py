import asyncio
import datetime
import os
import re
from io import TextIOWrapper

import kickpython
from dateutil import parser as dateparser

from . import ChatRecorder

CHANNEL_NAME_RE = re.compile(r"^https?://(www\.)?kick\.com/([\w_]+)")


class KickChatRecorder(ChatRecorder):
    running: bool
    conn: kickpython.KickAPI
    file: TextIOWrapper
    start_time: datetime.datetime

    def __init__(self, loop: asyncio.AbstractEventLoop, url: str, filename: str):
        m = CHANNEL_NAME_RE.match(url)
        assert m, f"Invalid Kick URL: {url}"
        name = m.group(2)
        self.running = True
        self.file = open(filename, "w")
        self.conn = kickpython.KickAPI(db_path=os.getenv("YTDVR_DB") or "./ytdvr.db")
        self.conn.add_message_handler(self.onmessage)
        loop.create_task(self.conn.connect_to_chatroom(name))
        self.start_time = datetime.datetime.now(datetime.timezone.utc)

    async def onmessage(self, message: dict):
        d = dateparser.parse(message["created_at"])
        elapsed = (d - self.start_time).total_seconds()
        timestamp = d.isoformat(sep=" ", timespec="seconds")
        self.file.write(
            f"[{timestamp}][{elapsed:.0f}] "
            f"{message['sender_username']}: {message['content']}\n"
        )
        self.file.flush()

    def stop(self):
        self.running = False
        asyncio.ensure_future(self.conn.close())
        self.file.close()
