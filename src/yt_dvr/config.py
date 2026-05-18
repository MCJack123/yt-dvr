from typing import Optional, cast, TYPE_CHECKING
import datetime
import importlib
import json
import logging
import sqlite3
if TYPE_CHECKING: from yt_dvr.channel import Channel
else: Channel = object

class Retention:
    count: Optional[int]
    time: Optional[int]
    size: Optional[int]

    def __init__(self, obj: Optional[dict] = None):
        self.count = None
        self.time = None
        self.size = None
        if obj is not None:
            if "count" in obj and type(obj["count"]) == int: self.count = obj["count"]
            if "time" in obj and type(obj["time"]) == int: self.time = obj["time"]
            if "size" in obj and type(obj["size"]) == int: self.size = obj["size"]

    def _dump(self) -> dict:
        return {
            "count": self.count,
            "time": self.time,
            "size": self.size
        }

class Config:
    saveDir: str
    serverPort: int
    defaultRetention: Retention
    globalRetention: Retention
    channels: dict[str, Channel]
    pollInterval: int
    remuxRecordings: bool
    remuxFormat: str
    logLevel: str
    ffmpegPath: Optional[str]

    db: sqlite3.Connection
    lastScanTime: datetime.datetime

    def __init__(self):
        self.saveDir = "files"
        self.serverPort = 6334
        self.defaultRetention = Retention()
        self.globalRetention = Retention()
        self.channels = {}
        self.pollInterval = 60
        self.remuxRecordings = True
        self.remuxFormat = "mp4"
        self.logLevel = "INFO"
        self.ffmpegPath = None

    def load(self, path: str):
        try:
            dict = {}
            with open(path, "r") as file:
                dict = json.load(file)
            self.saveDir = dict["saveDir"]
            self.serverPort = dict["serverPort"]
            self.defaultRetention = Retention(dict["defaultRetention"])
            self.globalRetention = Retention(dict["globalRetention"])
            channel = importlib.import_module("yt_dvr.channel")
            self.channels = {k: channel.createChannel(obj=c) for k, c in dict["channels"].items()}
            self.pollInterval = dict["pollInterval"]
            self.remuxRecordings = dict["remuxRecordings"]
            self.remuxFormat = dict["remuxFormat"]
            self.logLevel = dict["logLevel"] if "logLevel" in dict else "INFO"
            self.ffmpegPath = dict["ffmpegPath"] if "ffmpegPath" in dict else None
        except FileNotFoundError: pass

    def _dump(self, partial: bool = False) -> dict:
        if partial:
            return {
                "saveDir": self.saveDir,
                "serverPort": self.serverPort,
                "defaultRetention": self.defaultRetention._dump(),
                "globalRetention": self.globalRetention._dump(),
                "pollInterval": self.pollInterval,
                "remuxRecordings": self.remuxRecordings,
                "remuxFormat": self.remuxFormat,
                "logLevel": self.logLevel,
                "ffmpegPath": self.ffmpegPath,
            }
        return {
            "saveDir": self.saveDir,
            "serverPort": self.serverPort,
            "defaultRetention": self.defaultRetention._dump(),
            "globalRetention": self.globalRetention._dump(),
            "channels": {k: channel._dump() for k, channel in self.channels.items()},
            "pollInterval": self.pollInterval,
            "remuxRecordings": self.remuxRecordings,
            "remuxFormat": self.remuxFormat,
            "logLevel": self.logLevel,
            "ffmpegPath": self.ffmpegPath,
        }

    def dumps(self) -> str:
        return json.dumps(self._dump(), indent=4)

    def save(self, path: str):
        with open(path, "w") as file: file.write(self.dumps())

config = Config()
LOG = logging.getLogger("yt-dvr")
