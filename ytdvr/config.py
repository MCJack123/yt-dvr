import json
import logging
import sqlite3
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from channel import Channel
else:
    Channel = object

LOG = logging.getLogger("yt-dvr")


class Retention:
    count: Optional[int]
    time: Optional[int]
    size: Optional[int]

    def __init__(self, obj: Optional[dict] = None):
        self.count = None
        self.time = None
        self.size = None
        if obj is not None:
            if isinstance(obj.get("count"), int):
                self.count = obj["count"]
            if isinstance(obj.get("time"), int):
                self.time = obj["time"]
            if isinstance(obj.get("size"), int):
                self.size = obj["size"]

    def _dump(self) -> dict:
        return {
            "count": self.count,
            "time": self.time,
            "size": self.size,
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

    db: sqlite3.Connection

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

    def load(self, path: str):
        try:
            with open(path, "r") as file:
                data = json.load(file)
            self.saveDir = data.get("saveDir", self.saveDir)
            self.serverPort = data.get("serverPort", self.serverPort)
            self.defaultRetention = Retention(data.get("defaultRetention"))
            self.globalRetention = Retention(data.get("globalRetention"))
            self.pollInterval = data.get("pollInterval", self.pollInterval)
            self.remuxRecordings = data.get("remuxRecordings", self.remuxRecordings)
            self.remuxFormat = data.get("remuxFormat", self.remuxFormat)
            self.logLevel = data.get("logLevel", "INFO")

            import importlib

            channel_module = importlib.import_module("channel")
            self.channels = {
                k: channel_module.Channel(obj=c)
                for k, c in data.get("channels", {}).items()
            }
        except FileNotFoundError:
            pass

    def _dump(self, partial: bool = False) -> dict:
        result = {
            "saveDir": self.saveDir,
            "serverPort": self.serverPort,
            "defaultRetention": self.defaultRetention._dump(),
            "globalRetention": self.globalRetention._dump(),
            "pollInterval": self.pollInterval,
            "remuxRecordings": self.remuxRecordings,
            "remuxFormat": self.remuxFormat,
            "logLevel": self.logLevel,
        }
        if not partial:
            result["channels"] = {
                k: channel._dump() for k, channel in self.channels.items()
            }
        return result

    def dumps(self) -> str:
        return json.dumps(self._dump(), indent=4)

    def save(self, path: str):
        with open(path, "w") as file:
            file.write(self.dumps())


config = Config()
