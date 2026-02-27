from . import ChatRecorder
from config import LOG
from io import TextIOWrapper
import datetime
import random
import re
import socket
import threading

channel_name_regex = re.compile("^https?://(www\\.)?twitch\\.tv/([\\w_]+)")
chat_message_regex = re.compile(":(.+?)!.+?PRIVMSG #[\\w_]+ :(.+)")

class TwitchChatRecorder(ChatRecorder):
    thread: threading.Thread
    conn: socket.socket
    file: TextIOWrapper
    running: bool
    start_time: datetime.datetime

    def __init__(self, url: str, filename: str):
        m = channel_name_regex.match(url)
        assert m
        name = m.group(2)
        self.running = True
        self.file = open(filename, "w")
        self.conn = socket.socket(socket.AddressFamily.AF_INET, socket.SocketKind.SOCK_STREAM)
        self.thread = threading.Thread(target=self._worker, name="Twitch chat for " + name, args=[name])
        self.thread.start()
        self.start_time = datetime.datetime.now()

    def _worker(self, name: str):
        LOG.debug("Connecting to chat for " + name)
        self.conn.connect(("irc.chat.twitch.tv", 6667))
        self.conn.sendall(b"CAP REQ :twitch.tv/commands\r\nPASS BLANK\r\nNICK justinfan" + (b"%04d" % random.randint(0, 9999)) + b"\r\nJOIN #" + bytes(name, "utf8") + b"\r\n")
        reader = self.conn.makefile()
        while self.running and reader:
            line = reader.readline()
            if line != "":
                m = chat_message_regex.match(line)
                if m:
                    self.file.write("[%s][%d] %s: %s\n" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), (datetime.datetime.now() - self.start_time).total_seconds(), m.group(1), m.group(2)))
                    self.file.flush()
                elif "USERNOTICE" in line:
                    self.file.write("[%s][%d] %s" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), (datetime.datetime.now() - self.start_time).total_seconds(), line[line.find(" :") + 2:]))
                    self.file.flush()
                elif "CLEARMSG" in line:
                    self.file.write("[%s][%d] <message deleted>: %s" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), (datetime.datetime.now() - self.start_time).total_seconds(), line[line.find(" :") + 2:]))
                    self.file.flush()
                elif "CLEARCHAT" in line:
                    if " :" in line:
                        self.file.write("[%s][%d] Purged user %s" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), (datetime.datetime.now() - self.start_time).total_seconds(), line[line.find(" :") + 2:]))
                        self.file.flush()
                    else:
                        self.file.write("[%s][%d] Purged chat\n" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), (datetime.datetime.now() - self.start_time).total_seconds()))
                        self.file.flush()
                if line.find("PING") != -1: self.conn.send(bytes(line.replace("PING", "PONG"), "utf8"))
        self.conn.close()
        self.file.close()

    def stop(self):
        self.running = False
