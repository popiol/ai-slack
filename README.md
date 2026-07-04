# ai-slack
Receives messages from slack and executes tasks

Runs in the background, listens to one Slack channel, sends every message from
an allow-listed set of users to a local `claude` (Claude Code CLI) session,
and posts the reply back as a threaded Slack message. Each Slack thread maps
to its own resumable Claude Code session.

## Security note

By default this runs Claude Code with `--permission-mode bypassPermissions`,
which skips **all** tool-use approval prompts (arbitrary shell commands, file
edits, etc.). Anyone in `SLACK_ALLOWED_USERS` can therefore get arbitrary code
execution on this machine, scoped to `CLAUDE_WORKING_DIR` and
`CLAUDE_ADD_DIRS`. Keep the allow-list short and treat this like giving those
users a shell.

## Slack app setup

In your Slack app configuration (api.slack.com/apps):

1. **Socket Mode**: enable it (Settings > Socket Mode). Generate an
   app-level token with the `connections:write` scope -> this is
   `SLACK_APP_TOKEN` (starts with `xapp-`).
2. **OAuth & Permissions**: add bot token scopes:
   - `channels:history` (public channel messages) and/or `groups:history`
     (private channel messages)
   - `chat:write`
   - `reactions:write`
   Install/reinstall the app to your workspace, then copy the Bot User OAuth
   Token -> this is `SLACK_BOT_TOKEN` (starts with `xoxb-`).
3. **Event Subscriptions**: enable events and subscribe to the bot event
   `message.channels` (and `message.groups` for a private channel).
4. Invite the bot user to the target channel.

Find the channel ID via the channel name > View channel details > About >
Channel ID. Find a user ID via their profile > More > Copy member ID.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env: SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL_ID, SLACK_ALLOWED_USERS
```

## Run

From the repo root:

```bash
python -m src.main
```

This runs in the foreground; use your OS's usual tools to background it
(e.g. `nohup python -m src.main &`, a Windows scheduled task, `pm2`, or a
systemd unit) for a persistent background process.

## Notes

- Thread <-> Claude session mapping is in-memory only: restarting the process
  starts a fresh Claude session the next time a given thread gets a message.
- `CLAUDE_ADD_DIRS` (default `~/proj`) grants Claude Code file/tool access to
  everything under that path, so it can act on projects beyond this repo.
