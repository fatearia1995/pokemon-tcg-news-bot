$ErrorActionPreference = "Stop"

$env:TELEGRAM_BOT_TOKEN = [Environment]::GetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "User")
$env:TELEGRAM_CHAT_ID = [Environment]::GetEnvironmentVariable("TELEGRAM_CHAT_ID", "User")

python pokemon_news_tool.py --telegram
