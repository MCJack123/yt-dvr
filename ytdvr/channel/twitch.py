from . import ChatRecorder
import socket
import threading
import re
import random
from io import TextIOWrapper
import datetime

channel_name_regex = re.compile("^https?://(www\\.)?twitch\\.tv/([\\w_]+)")
chat_message_regex = re.compile(":(.+?)!.+?PRIVMSG #[\\w_]+ :(.+)")

class TwitchChatRecorder(ChatRecorder):
    thread: threading.Thread
    conn: socket.socket
    file: TextIOWrapper
    running: bool

    def __init__(self, url: str, filename: str):
        m = channel_name_regex.match(url)
        assert m
        name = m.group(2)
        self.running = True
        self.file = open(filename, "w")
        self.conn = socket.socket(socket.AddressFamily.AF_INET, socket.SocketKind.SOCK_STREAM)
        self.thread = threading.Thread(target=self._worker, name="Twitch chat for " + name, args=[name])
        self.thread.start()

    def _worker(self, name: str):
        print("Connecting to chat for " + name)
        self.conn.connect(("irc.chat.twitch.tv", 6667))
        self.conn.sendall(b"CAP REQ :twitch.tv/commands\r\nPASS BLANK\r\nNICK justinfan" + (b"%04d" % random.randint(0, 9999)) + b"\r\nJOIN #" + bytes(name, "utf8") + b"\r\n")
        reader = self.conn.makefile()
        while self.running and reader:
            line = reader.readline()
            if line != "":
                m = chat_message_regex.match(line)
                if m:
                    self.file.write("[%s] %s: %s\n" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), m.group(1), m.group(2)))
                    self.file.flush()
                elif "USERNOTICE" in line:
                    self.file.write("[%s] %s" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), line[line.find(" :") + 2:]))
                    self.file.flush()
                elif "CLEARMSG" in line:
                    self.file.write("[%s] <message deleted>: %s" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), line[line.find(" :") + 2:]))
                    self.file.flush()
                elif "CLEARCHAT" in line:
                    if " :" in line:
                        self.file.write("[%s] Purged user %s" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), line[line.find(" :") + 2:]))
                        self.file.flush()
                    else:
                        self.file.write("[%s] Purged chat\n" % (datetime.datetime.now().isoformat(sep=" ", timespec="seconds")))
                        self.file.flush()
                if line.find("PING") != -1: self.conn.send(bytes(line.replace("PING", "PONG"), "utf8"))
        self.conn.close()
        self.file.close()

    def stop(self):
        self.running = False
