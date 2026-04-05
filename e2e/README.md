# E2E Testing Harness

End-to-end tests for the flint-discord bot. Runs the bot against a real Discord server and a live Flint server, verifying all major bot features work correctly.

## Prerequisites

1. A dedicated Discord server for E2E testing
2. A Discord bot application with intents enabled: Message Content, Guild Messages, Guild Members
3. The bot invited to the test server with permissions: Send Messages, Create Public Threads, Manage Channels, Use Slash Commands, Embed Links, Attach Files
4. A running Flint server instance

## Setup

Set these environment variables (or add them to a `.env` file in the project root):

```bash
export DISCORD_BOT_TOKEN="your-bot-token"
export DISCORD_GUILD_ID="your-test-server-guild-id"
export FLINT_SERVER_URL="http://localhost:7433"
```

Optional tuning variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `E2E_RESPONSE_TIMEOUT` | `60` | Seconds to wait for bot responses |
| `E2E_API_DELAY` | `2.0` | Seconds between Discord API calls (rate limit safety) |

## Running

From the project root:

```bash
# Using the project venv
.venv/bin/python -m e2e

# Or with uv
uv run python -m e2e
```

The harness will:

1. Verify the Flint server is reachable
2. Connect to Discord using the bot token
3. Create a fresh channel (`e2e-test-<timestamp>`) in the test guild
4. Run all six test scenarios sequentially
5. Print a summary of pass/fail results
6. Exit with code 0 (all pass) or 1 (any failures)

## Test Scenarios

| Scenario | What it tests |
|----------|---------------|
| **Session Lifecycle** | Message -> thread creation -> status embeds -> session completion -> result posted |
| **Question / Answer** | Blocking question -> bot surfaces it -> user replies -> session resumes |
| **Slash Commands** | Task CRUD via Flint server API (list, create, view, update) |
| **Live Dashboard** | Dashboard embed exists in #flint-dashboard, updates on session changes |
| **Pagination** | Long output triggers PaginatorView with navigation buttons |
| **Image Extraction** | `discord-image` fences in output -> bot uploads file attachments |

## Important Notes

- **Channels persist** after test runs for human review. Delete old `e2e-test-*` channels manually.
- **Rate limits**: The harness includes delays between API calls. If you hit rate limits, increase `E2E_API_DELAY`.
- The Flint server **must be running** before launching the harness.
- The bot **must already be running** and connected to the test server for interaction-based tests (lifecycle, Q&A, pagination, image extraction).
- Slash commands are tested via the backing API, not via Discord's slash command dispatch.
