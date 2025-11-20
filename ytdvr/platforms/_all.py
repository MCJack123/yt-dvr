from typing import Optional
from . import *
from . import twitch

async def initPlatform(obj: Optional[dict] = None, name: Optional[str] = None, url: Optional[str] = None, platformOptions: Optional[dict] = None) -> Platform:
    if obj is not None:
        name = obj["name"]
        platformOptions = obj["platformOptions"]
    elif url is not None:
        pass # TODO
    retval: Platform
    if name == "Twitch":
        if platformOptions is None: raise TypeError("Twitch platform requires platformOptions with client_id")
        retval = twitch.TwitchPlatform(platformOptions["client_id"], platformOptions["token"] if "token" in platformOptions else None)
        await retval.connect()
    else: raise ValueError(f"Unknown platform name {name}")
    if obj is not None and "ytdlParams" in obj: retval.ytdlParams = obj["ytdlParams"]
    return retval
