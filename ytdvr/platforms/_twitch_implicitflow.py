# from https://github.com/Domi-G/pyTwitchAPI/blob/e370427f67d86da0fd61ad3eebfcb1527776ab61/twitchAPI/oauth.py
# MIT

from twitchAPI.twitch import Twitch
from twitchAPI.helper import build_url, build_scope, get_uuid, TWITCH_AUTH_BASE_URL, fields_to_enum
from twitchAPI.type import AuthScope
import webbrowser
from aiohttp import web
import asyncio
from threading import Thread
from concurrent.futures import CancelledError
from logging import getLogger, Logger
from typing import List, Union, Optional, Callable, Awaitable

class ImplicitFlow:
    """Basic implementation of the Implicit User Authentication.

    Example use:

    .. code-block:: python

        APP_ID = "my_app_id"
        USER_SCOPES = [AuthScope.BITS_READ, AuthScope.BITS_WRITE]

        twitch = await Twitch(APP_ID, authenticate_app=False)
        auth = ImplicitFlow(twitch, USER_SCOPES)
        token = await auth.authenticate()
        await twitch.set_user_authentication(token, USER_SCOPES)
    """

    def __init__(self,
                 twitch: 'Twitch',
                 scopes: List[AuthScope],
                 force_verify: bool = False,
                 url: str = 'http://localhost:17563',
                 host: str = '0.0.0.0',
                 port: int = 17563,
                 auth_base_url: str = TWITCH_AUTH_BASE_URL):
        """

        :param twitch: A twitch instance
        :param scopes: List of the desired Auth scopes
        :param force_verify: If this is true, the user will always be prompted for authorization by twitch |default| :code:`False`
        :param url: The reachable URL that will be opened in the browser. |default| :code:`http://localhost:17563`
        :param host: The host the webserver will bind to. |default| :code:`0.0.0.0`
        :param port: The port that will be used for the webserver. |default| :code:`17653`
        :param auth_base_url: The URL to the Twitch API auth server |default| :const:`~twitchAPI.helper.TWITCH_AUTH_BASE_URL`
        """
        self._twitch: 'Twitch' = twitch
        self._client_id: str = twitch.app_id
        self.scopes: List[AuthScope] = scopes
        self.force_verify: bool = force_verify
        self.logger: Logger = getLogger('twitchAPI.oauth.implicit_flow')
        """The logger used for OAuth related log messages"""
        self.url = url
        self.auth_base_url: str = auth_base_url
        self.document: str = """<!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <title>pyTwitchAPI OAuth</title>
        </head>
        <body>
            <script>
                window.onload = function() {
                    // get the URI fragment and remove the '#' symbol
                    const hash = document.location.hash.substring(1);
                    if (hash) {
                        const params = new URLSearchParams(hash);
                        // send the token and associated data to the server
                        fetch(`/auth?${params.toString()}`, {
                            method: 'GET'
                        })
                        .catch(error => console.error('Error:', error));
                    }
                };
            </script>
            <h1>Thanks for Authenticating with pyTwitchAPI!</h1>
        You may now close this page.
        </body>
        </html>"""
        """The document that will be rendered at the end of the flow"""
        self.port: int = port
        """The port that will be used for the webserver. |default| :code:`17653`"""
        self.host: str = host
        """The host the webserver will bind to. |default| :code:`0.0.0.0`"""
        self.state: str = str(get_uuid())
        """The state to be used for identification, |default| a random UUID"""
        self._callback_func = None
        self._server_running: bool = False
        self._loop: Union[asyncio.AbstractEventLoop, None] = None
        self._runner: Union[web.AppRunner, None] = None
        self._thread: Union[Thread, None] = None
        self._access_token: Union[str, None] = None
        self._can_close: bool = False
        self._is_closed = False

    def _build_auth_url(self):
        params = {
            'client_id': self._twitch.app_id,
            'redirect_uri': self.url,
            'response_type': 'token',
            'scope': build_scope(self.scopes),
            'force_verify': str(self.force_verify).lower(),
            'state': self.state
        }
        return build_url(self.auth_base_url + 'authorize', params)

    def _build_runner(self):
        app = web.Application()
        # / will be used as the redirect site
        app.add_routes([web.get('/', self._handle_callback)])
        # /auth recives the actual data extracted via javascript from the fragment portion of the URI
        app.add_routes([web.get('/auth', self._handle_auth_callback)])
        return web.AppRunner(app)

    async def _run_check(self):
        while not self._can_close:
            await asyncio.sleep(0.1)
        await self._runner.shutdown() # type: ignore
        await self._runner.cleanup() # type: ignore
        self.logger.info('shutting down oauth Webserver')
        self._is_closed = True

    def _run(self, runner: web.AppRunner):
        self._runner = runner
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, self.host, self.port)
        self._loop.run_until_complete(site.start())
        self._server_running = True
        self.logger.info('running oauth Webserver')
        try:
            self._loop.run_until_complete(self._run_check())
        except (CancelledError, asyncio.CancelledError):
            pass

    def _start(self):
        self._thread = Thread(target=self._run, args=(self._build_runner(),))
        self._thread.start()

    def stop(self):
        """Manually stop the flow

        :rtype: None
        """
        self._can_close = True

    async def _handle_auth_callback(self, request: web.Request):
        val = request.rel_url.query.get('state')
        self.logger.debug(f'got callback with state {val}')
        # invalid state!
        if val != self.state:
            return web.Response(status=401)
        self._access_token = request.rel_url.query.get('access_token')
        if self._access_token is None:
            # must provide code
            return web.Response(status=400)
        if self._callback_func is not None:
            self._callback_func(self._access_token)
        return web.Response(text='', content_type='text/html')

    async def _handle_callback(self, _):
        return web.Response(text=self.document, content_type='text/html')

    def return_auth_url(self):
        """Returns the URL that will authenticate the app, used for headless server environments."""
        return self._build_auth_url()

    async def authenticate(self,
                           callback_func: Optional[Callable[[str], None]] = None,
                           browser_name: Optional[str] = None,
                           browser_new: int = 2,
                           use_browser: bool = True,
                           auth_url_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
        """Start the implicit authentication flow\n
        If callback_func is not set, authenticate will wait till the authentication process finished and then return
        the access_token

        :param callback_func: Function to call once the authentication finished.
        :param browser_name: The browser that should be used, None means that the system default is used.
                            See `the webbrowser documentation <https://docs.python.org/3/library/webbrowser.html#webbrowser.register>`__ for more info
                            |default|:code:`None`
        :param browser_new: controls in which way the link will be opened in the browser.
                            See `the webbrowser documentation <https://docs.python.org/3/library/webbrowser.html#webbrowser.open>`__ for more info
                            |default|:code:`2`
        :param use_browser: controls if a browser should be opened.
                            If set to :const:`False`, the browser will not be opened and the URL to be opened will either be printed to the info log or
                            send to the specified callback function (controlled by :const:`~twitchAPI.oauth.UserAuthenticator.authenticate.params.auth_url_callback`)
                            |default|:code:`True`
        :param auth_url_callback: a async callback that will be called with the url to be used for the authentication flow should
                            :const:`~twitchAPI.oauth.UserAuthenticator.authenticate.params.use_browser` be :const:`False`.
                            If left as None, the URL will instead be printed to the info log
                            |default|:code:`None`
        :return: None if callback_func is set, otherwise access_token and refresh_token
        :raises ~twitchAPI.type.TwitchAPIException: if authentication fails
        :rtype: None or str
        """
        self._callback_func = callback_func
        self._can_close = False
        self._access_token = None
        self._is_closed = False

        # the implicit flow does not use renewal tokens
        self._twitch.auto_refresh_auth = False

        self._start()
        # wait for the server to start up
        while not self._server_running:
            await asyncio.sleep(0.01)
        if use_browser:
            # open in browser
            browser = webbrowser.get(browser_name)
            browser.open(self._build_auth_url(), new=browser_new)
        else:
            if auth_url_callback is not None:
                await auth_url_callback(self._build_auth_url())
            else:
                self.logger.info(f"To authenticate open: {self._build_auth_url()}")
        while self._access_token is None:
            await asyncio.sleep(0.01)

        self.stop()

        if callback_func is None:
            while not self._is_closed:
                await asyncio.sleep(0.1)
            return self._access_token
        elif self._access_token is not None:
            self._callback_func(self._access_token) # type: ignore
        return self._access_token