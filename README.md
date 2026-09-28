# Instagram Posts Downloader Bot

A Telegram bot project for handling Instagram post, Reel, and carousel links. This starter version validates submitted links and explains that downloading is not yet implemented.

## Features
- `/start` and `/help` commands
- Instagram URL validation
- Clear handling for unsupported links
- No login bypass, private-account access, or access-control circumvention

## Requirements
- Python 3.10+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Run locally or in GitHub Codespaces

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set the `BOT_TOKEN` environment variable. Never commit your token.
3. Start the bot:
   ```bash
   python bot.py
   ```

Example environment variable:
```bash
export BOT_TOKEN="paste-your-token-here"
```

## Important limitation
This starter does not fetch or download Instagram media yet. Add a lawful, authorized media source/API before implementing downloads. Only download content you own or have permission to save, and respect platform terms and copyright.

## License
No license has been selected yet. Add a license file before allowing reuse or redistribution.