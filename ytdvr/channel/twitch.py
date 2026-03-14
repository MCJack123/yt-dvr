import datetime
import random
import re
import socket
import threading
from io import TextIOWrapper

from config import LOG

from . import ChatRecorder

CHANNEL_NAME_RE = re.compile(r"^https?://(www\.)?twitch\.tv/([\w_]+)")
CHAT_MESSAGE_RE = re.compile(r":(.+?)!.+?PRIVMSG #[\w_]+ :(.+)")


class TwitchChatRecorder(ChatRecorder):
    thread: threading.Thread
    conn: socket.socket
    file: TextIOWrapper
    running: bool
    start_time: datetime.datetime

    def __init__(self, url: str, filename: str):
        m = CHANNEL_NAME_RE.match(url)
        assert m, f"Invalid Twitch URL: {url}"
        name = m.group(2)
        self.running = True
        self.file = open(filename, "w")
        self.conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.start_time = datetime.datetime.now()
        self.thread = threading.Thread(
            target=self._worker, name=f"Twitch chat for {name}", args=[name]
        )
        self.thread.start()

    def _format_line(self, text: str) -> str:
        now = datetime.datetime.now()
        elapsed = (now - self.start_time).total_seconds()
        timestamp = now.isoformat(sep=" ", timespec="seconds")
        return f"[{timestamp}][{elapsed:.0f}] {text}"

    def _worker(self, name: str):
        LOG.debug(f"Connecting to chat for {name}")
        self.conn.connect(("irc.chat.twitch.tv", 6667))
        nick_num = random.randint(0, 9999)
        self.conn.sendall(
            f"CAP REQ :twitch.tv/commands\r\n"
            f"PASS BLANK\r\n"
            f"NICK justinfan{nick_num:04d}\r\n"
            f"JOIN #{name}\r\n".encode("utf-8")
        )
        reader = self.conn.makefile()
        while self.running and reader:
            line = reader.readline()
            if not line:
                break

            m = CHAT_MESSAGE_RE.match(line)
            if m:
                self.file.write(self._format_line(f"{m.group(1)}: {m.group(2)}") + "\n")
            elif "USERNOTICE" in line:
                self.file.write(self._format_line(line[line.find(" :") + 2 :]))
            elif "CLEARMSG" in line:
                self.file.write(
                    self._format_line(
                        f"<message deleted>: {line[line.find(' :') + 2 :]}"
                    )
                )
            elif "CLEARCHAT" in line:
                if " :" in line:
                    self.file.write(
                        self._format_line(f"Purged user {line[line.find(' :') + 2 :]}")
                    )
                else:
                    self.file.write(self._format_line("Purged chat") + "\n")
            else:
                if "PING" in line:
                    self.conn.send(line.replace("PING", "PONG").encode("utf-8"))
                continue

            self.file.flush()

            if "PING" in line:
                self.conn.send(line.replace("PING", "PONG").encode("utf-8"))

        self.conn.close()
        self.file.close()

    def stop(self):
        self.running = False
