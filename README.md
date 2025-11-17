# WakaTime/Hackatime Blender Integration

Automatic time tracking for Blender projects via [WakaTime](https://wakatime.com). This add-on captures editing activity, saves heartbeats to your WakaTime dashboard, and keeps local timeline logs so you can review what happened in each session.

## Features
- Tracks active Blender editing time and project context automatically once a `.blend` file is saved.
- Displays live tracking and sync state inside the Text Editor sidebar and Blender status bar.
- Downloads and wraps the legacy WakaTime Python CLI runtime so it works on modern Blender distributions.
- Persists daily totals locally for quick reference and writes human-readable timeline logs to `~/.wakatime/timeline/`.
- Supports offline queueing with automatic background sync when connectivity returns.

## Requirements
- Blender 4.5.4 LTS, idk if it works for other versions.
- A WakaTime/Hackatime account and API key.
- Internet access the first time you run **Force Sync** to download the WakaTime CLI runtime.

## Installation
1. Download this repository as a `.zip` archive or clone it locally:
	 ```powershell
	 git clone https://github.com/Luckmuc/WakatimeBlender.git
	 ```
2. In Blender, open `Edit > Preferences > Get Extensions `, click the top right button, click Install from   disk.
3. Select the downloaded zip and press **Install...**.
4. A new section should pop up down right, click on it, check if all credentials are right and then press Force sync. Wait till it finished, give your file a name and now the time should get tracked.

## Troubleshooting
- **Nothing working** Check the console window and the output and give it to ChatGPT or somth like that, idk ts is vibecoded 

## Disclaimer
This is vibecoded because i needed a working hackatime extension for Blender, nothing more to say, if you got issues make make one on github