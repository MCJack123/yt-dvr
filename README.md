# yt-dvr
A service to automatically record livestreams from various platforms, backed by [yt-dlp](https://github.com/yt-dlp/yt-dlp).

Channels are automatically pinged at a specified frequency, and recorded if they are live. Recordings are handled by yt-dlp, supporting hundreds of platforms, with specific support for certain platform features (e.g. chat).

A small web interface is served via Quart, for basic video playback and service configuration. An external media server is recommended if media organization is important - the interface is only meant for limited usage and is not optimized for multiple users, sorting, etc.

## Install
Requires Python (any recent version will do, no idea how old). You will also need a working copy of FFmpeg installed.

Install requirements from `requirements.txt`: `pip install -r requirements.txt`.

## Configuration
yt-dvr is configured with a JSON file at `$YTDVR_CONFIG` (default `ytdvr_config.json`). The config file contains the following keys:

- `saveDir`: The directory to store recordings in.
- `serverPort`: The port to host the internal server on.
- `defaultRetention`: An object containing keys indicating the maximum amounts to keep of a certain channel by default. Files will be deleted when any of the retention counts are hit, starting with the oldest. Null values mean the category is ignored/infinity - if all are null, files will never be deleted.
  - `count`: The maximum number of recordings to keep.
  - `time`: The maximum age of a recording, in days.
  - `size`: The maximum cumulative file size, in megabytes.
- `globalRetention`: Similar to `defaultRetention`, but applies to all videos altogether.
- `pollInterval`: The number of seconds to wait between live checks.
- `remuxRecordings`: Whether to remux recordings after finishing. (Recordings are saved as MPEG-TS for streaming.)
- `remuxFormat`: If remuxing is enabled, the (FFmpeg) format to remux to.
- `logLevel`: The logging level as defined by [Python `logging`](https://docs.python.org/3/library/logging.html#logging-levels) (string)
- `channels`: An object containing channel names and options to record, with the following channel options (optional unless otherwise specified):
  - `url`: The URL to record (required)
    - For YouTube channels, this should be in the format `https://www.youtube.com/@<channel>/live`
  - `getChat`: Whether to download chat automatically (only supported on some platforms) (required, default false)
  - `platform`: An override for platform support (TODO: is this necessary?)
  - `quality`: The yt-dlp quality format to record at (default `bestaudio+bestvideo`)
  - `retention`: An alternate retention configuration for this channel only - if set it overrides the defaults completely
  - `ytdlParams`: An object containing parameters to pass to yt-dlp, in API format (see https://github.com/yt-dlp/yt-dlp/blob/master/devscripts/cli_to_api.py)

These settings can be configured through the web interface.

The video database is stored in a SQLite database stored at `$YTDVR_DB`, default `ytdvr.db`.

## Running
Run `python ytdvr/server.py`.

The web interface is hosted at `http://localhost:6334` by default. The URL will be printed to the console.

## License
yt-dvr is licensed under the GNU Affero General Public License v3.0. You are allowed to host, modify and redistribute this code at will, as long as source code is always provided, including by public server hosts.
