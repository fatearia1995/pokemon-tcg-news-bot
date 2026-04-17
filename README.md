# 寶可夢卡牌消息爬蟲

這是一個以 Python 撰寫的 CLI 工具，用來整理寶可夢集換式卡牌實體卡的最新消息，重點覆蓋：

- 繁體中文版：`asia.pokemon-card.com/tw`
- 國際版：The Pokémon Company 北美官方 press site
- 日文版：`pokemon-card.com`
- X：透過公開 Nitter RSS 抓取官方帳號最新貼文，再回填原始 `x.com` 連結

工具預設會把繁中以外來源的摘要翻譯成繁體中文，方便直接閱讀；原始標題與來源連結則保留不變。
Telegram 摘要目前會優先整理「預購／補貨／卡盒開賣」相關快訊，再附上少量官方補充消息，閱讀上會更偏向搶先通知。

## 安裝

```bash
python -m pip install -r requirements.txt
```

## 使用方式

輸出 Markdown 摘要：

```bash
python pokemon_news_tool.py
```

輸出 JSON：

```bash
python pokemon_news_tool.py --format json
```

只看官方網站，不抓 X：

```bash
python pokemon_news_tool.py --no-x
```

輸出後同步發送到 Telegram：

```bash
python pokemon_news_tool.py --telegram
```

若你是在 Windows 想直接用同一套使用者環境變數執行，也可以用：

```powershell
.\run_pokemon_news.ps1
```

## 自動化

目前保留兩條路：

- 本機 Codex 自動化：你現有那條會繼續保留
- GitHub Actions：新增雲端排程，不需要電腦開著

### GitHub Actions 設定

專案已新增 workflow：

- [.github/workflows/pokemon-news.yml](/C:/Users/jpart/Documents/New%20project/.github/workflows/pokemon-news.yml)

這條 workflow 會：

- 每天台灣時間上午 `08:30` 執行
- 執行 `python pokemon_news_tool.py --telegram`
- 把報告檔上傳成 GitHub Actions artifact

注意：GitHub Actions 的排程是用 UTC 表示，所以檔案裡設定的是 `00:30 UTC`，等於台灣時間 `08:30`。

### 你還需要做的事

1. 把這個專案推到 GitHub repository
2. 到該 repository 的 `Settings` -> `Secrets and variables` -> `Actions`
3. 新增兩個 repository secrets：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

4. 到 `Actions` 頁面手動跑一次 `Pokemon TCG News` workflow 測試

只要 GitHub 上的 secrets 設好，之後就算你的電腦關機，GitHub Actions 也會照常跑。

## 參數

- `--days`: 只保留最近幾天的消息，預設 `45`
- `--per-region`: 每個區域保留幾則，預設 `5`
- `--site-limit`: 每個官方網站來源最多抓幾則候選，預設 `8`
- `--x-limit`: 每個 X 帳號最多抓幾則候選，預設 `8`
- `--config`: 來源設定檔，預設 `sources.example.json`
- `--format`: `markdown` 或 `json`
- `--output`: 指定輸出檔案路徑
- `--no-x`: 跳過 X 來源
- `--no-translate`: 不翻譯非繁中摘要
- `--telegram`: 輸出完成後，把摘要與完整報告傳送到 Telegram

若未指定 `--output`，工具會自動輸出到：

- `outputs/pokemon_tcg_news_YYYY-MM-DD.md`
- 或 `outputs/pokemon_tcg_news_YYYY-MM-DD.json`

## Telegram 設定

若要自動傳送到 Telegram，請先準備：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### 1. 建立 Bot

在 Telegram 找 `@BotFather`，建立新的 bot，取得 token。

### 2. 讓你的帳號先跟 Bot 開始對話

先打開你建立的 bot，按下 `Start`，或傳一則訊息給它。

### 3. 取得你的 chat_id

把下面網址中的 `{BOT_TOKEN}` 換成你的 token 後打開：

```text
https://api.telegram.org/bot{BOT_TOKEN}/getUpdates
```

在回傳 JSON 中找到：

- `message.chat.id`

這個值就是 `TELEGRAM_CHAT_ID`。

### 4. 在本機設定環境變數

PowerShell：

```powershell
setx TELEGRAM_BOT_TOKEN "你的 bot token"
setx TELEGRAM_CHAT_ID "你的 chat id"
```

重新開啟終端後再執行：

```bash
python pokemon_news_tool.py --telegram
```

工具會先傳一則精簡摘要，再附上完整 Markdown 報告檔案。

## 來源設定

`sources.example.json` 目前內建：

- `PokemonTCG`
- `playpokemon`
- `PokemonCenterUS`
- `PokeAlerts_`
- `PokemonTCGDrops`
- `pokepullzhq`
- `PokemonRestocks`
- `PokemonDealsTCG`
- `PokeTCGAlerts`
- `PokecaCH`
- `pokemon_cojp`

你可以自行複製成別的 JSON 檔後再透過 `--config` 指定。

## 限制說明

- `pokemon.com` 與 `pokemoncenter.com` 某些頁面有防護機制，直接 requests 抓取不穩定，所以目前國際版以官方 press site 與官方 X 為主。
- 繁中卡牌目前沒有找到穩定、公開且可長期抓取的官方 X 帳號，因此繁中區塊暫時只整理官網消息。
- X 來源依賴 Nitter RSS；若日後某個 Nitter 節點失效，可改程式中的 RSS 來源或自行擴充備援。
- 新增的預購／補貨 X 帳號多半是民間快訊帳號，不是官方來源，可能帶有聯盟連結、Discord 導流或轉推內容；工具目前保留來源連結，方便你自行判斷與追蹤。
