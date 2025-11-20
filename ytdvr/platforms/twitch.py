from . import ChatRecorder, Platform, RecordingInfo
from twitchAPI import twitch
from twitchAPI.oauth import UserAuthenticator
from twitchAPI.type import AuthScope
from ._twitch_implicitflow import ImplicitFlow
from yt_dlp import YoutubeDL, utils
from typing import cast

#class TwitchChatRecorder(ChatRecorder):
#    connection: 

class TwitchPlatform(Platform):
    """
    Platform instance for Twitch.
    """
    connection: twitch.Twitch

    def __init__(self, client_id: str, token: str | None = None):
        """
        Creates a new Twitch platform instance.
        """
        self.name = "Twitch"
        self.url = "https?://www.twitch.tv/([A-Za-z0-9_]+)"
        self.headers = {}
        self.platformOptions = {"client_id": client_id, "token": token}
        self.ytdlParams = None
        self._ytdlTitleKey = "description"
    
    async def connect(self):
        self.connection = await twitch.Twitch(self.platformOptions["client_id"], authenticate_app=False)
        self.connection.auto_refresh_auth = False
        target_scope = [AuthScope.CHAT_READ]
        token = self.platformOptions["token"]
        if token is None:
            auth = ImplicitFlow(self.connection, target_scope, force_verify=False)
            # this will open your default browser and prompt you with the twitch verification website
            token = await auth.authenticate()
            self.platformOptions["token"] = token
        # add User authentication
        await self.connection.set_user_authentication(token, target_scope)

    async def poll(self, id: str) -> bool:
        streams = [item async for item in self.connection.get_streams(user_login=[id], stream_type="live")]
        if len(streams) == 0: return False
        return streams[0].type == "live"
    
    async def download(self, id: str, quality: str | None = None) -> RecordingInfo | None:
        return await self._ytdlDownload(id, "https://www.twitch.tv/" + id, quality)

    #async def connectChat(self, id: str) -> ChatRecorder | None:
        
