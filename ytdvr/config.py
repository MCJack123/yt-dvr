from typing import Optional, cast
from platforms import Platform
from platforms._all import initPlatform
import json
import os

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
    
class Channel:
    platform: Platform
    id: str
    retention: Optional[Retention]
    quality: Optional[str]

    def __init__(self, obj: Optional[dict] = None, platform: Optional[Platform] = None, id: Optional[str] = None):
        if obj is not None:
            try:
                self.platform = next(platform for platform in config.platforms if platform.name == obj["platform"])
            except:
                raise ValueError(f"Unknown platform {obj["platform"]} used in channel {obj["id"]}")
            self.id = obj["id"]
            if "retention" in obj and type(obj["retention"]) == dict:
                self.retention = Retention(obj["retention"])
            else: self.retention = None
            if "quality" in obj and type(obj["quality"]) == str:
                self.quality = obj["quality"]
            else: self.quality = None
        elif platform is not None and id is not None:
            self.platform = platform
            self.id = id
            self.retention = None
            self.quality = None
        else: raise TypeError("One of (obj=) or (platform=, id=) must be specified")

    def _dump(self) -> dict:
        return {
            "platform": self.platform.name,
            "id": self.id,
            "retention": self.retention._dump() if self.retention is not None else None,
            "quality": self.quality
        }

class Config:
    saveDir: str
    defaultRetention: Retention
    channels: list[Channel]
    pollInterval: int
    saveFormat: str
    platforms: list[Platform]

    def __init__(self):
        self.saveDir = "files/"
        self.defaultRetention = Retention()
        self.channels = []
        self.pollInterval = 60
        self.saveFormat = "mp4"
        self.platforms = []

    async def load(self, path: str):
        try:
            dict = {}
            with open(path, "r") as file:
                dict = json.load(file)
            self.saveDir = dict["saveDir"]
            self.defaultRetention = Retention(dict["defaultRetention"])
            self.platforms = [await initPlatform(obj=o) for o in dict["platforms"]]
            self.channels = [Channel(obj=c) for c in dict["channels"]]
            self.pollInterval = dict["pollInterval"]
            self.saveFormat = dict["saveFormat"]
        except FileNotFoundError: pass

    def _dump(self, partial: bool = False) -> dict:
        if partial:
            return {
                "saveDir": self.saveDir,
                "defaultRetention": self.defaultRetention._dump(),
                "pollInterval": self.pollInterval,
                "saveFormat": self.saveFormat
            }
        return {
            "saveDir": self.saveDir,
            "defaultRetention": self.defaultRetention._dump(),
            "channels": [channel._dump() for channel in self.channels],
            "pollInterval": self.pollInterval,
            "saveFormat": self.saveFormat,
            "platforms": [platform._dump() for platform in self.platforms]
        }

    def dumps(self) -> str:
        return json.dumps(self._dump(), indent=4)

    def save(self, path: str):
        with open(path, "w") as file: file.write(self.dumps())

config = Config()
