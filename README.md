# 0050 成分股新聞雷達

這是一個獨立的台股新聞摘要 MVP，用來追蹤 0050 / 台灣50 成分股相關的重要新聞，並透過 GitHub Actions 在台北時間盤前、盤中、盤後自動執行與寄信。

## 功能

- 追蹤 0050 成分股新聞、公告、籌碼與大盤因子。
- 依照三種模式套用不同評分規則：
  - `premarket`：盤前新聞，重點看美股、Nasdaq、費半、台積電 ADR、美債、美元、台幣匯率與海外產業新聞。
  - `intraday`：盤中新聞，重點看 0050 成分股爆量、急漲急跌、類股轉強轉弱與權值股影響。
  - `postmarket`：盤後新聞，重點看三大法人、融資融券、注意股、處置股、重大訊息、月營收與法說會。
- 預設挑出前 10 則重要新聞。
- SMTP 設定完成時寄信；沒有 SMTP 時會印出摘要，方便本機測試。
- GitHub Actions 於台北時間每個交易日常規時段自動執行。

## 專案結構

```text
taiwan-0050-news-radar/
  .github/workflows/0050-news-radar.yml
  config/constituents_0050.json
  src/tw0050_radar/radar.py
  .env.example
  pyproject.toml
  requirements.txt
```

## 本機試跑

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

如果還沒設定寄信資料，也可以先直接看輸出：

```bash
PYTHONPATH=src python -m tw0050_radar.radar --mode premarket
PYTHONPATH=src python -m tw0050_radar.radar --mode intraday
PYTHONPATH=src python -m tw0050_radar.radar --mode postmarket
```

## Email 設定

把 `.env` 或 GitHub Secrets 設好：

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-account@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-account@gmail.com
EMAIL_TO=you@example.com
```

如果用 Gmail，建議使用 Google 帳號的 App Password，不要使用主要登入密碼。

## GitHub Actions 設定

把專案推到新的 GitHub repo 後，到：

```text
Settings > Secrets and variables > Actions > Secrets
```

新增這些 secrets：

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

`EMAIL_TO` 可以用逗號分隔多個收件人：

```text
alice@example.com,bob@example.com
```

排程已放在 `.github/workflows/0050-news-radar.yml`：

```yaml
# 08:00 Asia/Taipei = 00:00 UTC
- cron: "0 0 * * 1-5"

# 12:00 Asia/Taipei = 04:00 UTC
- cron: "0 4 * * 1-5"

# 18:00 Asia/Taipei = 10:00 UTC
- cron: "0 10 * * 1-5"
```

GitHub Actions 的 cron 使用 UTC，所以台北時間要減 8 小時。

## 0050 成分股設定

`config/constituents_0050.json` 是可替換的成分股清單。第一版用種子名單，目的是讓評分模型先可運作。

之後建議定期用官方或 ETF 發行商資料更新：

- 元大投信 0050 持股明細
- 臺灣指數公司 / FTSE TWSE Taiwan 50 Index 成分股
- 公開資訊觀測站 ETF 相關資訊

## 評分邏輯

每篇新聞會依照下列因素加權：

- 是否命中 0050 成分股。
- 是否命中 0050 權重股，例如台積電、鴻海、聯發科、台達電等。
- 是否涉及美股、Nasdaq、費半、台積電 ADR、美債、美元、台幣匯率等市場因子。
- 是否涉及重大訊息、月營收、法說會、三大法人、融資融券、注意股、處置股。
- 盤中模式會額外加權爆量、急漲、急跌、類股轉強轉弱。
- 同時命中多檔成分股會提高分數。

## GitHub Actions 額度估算

GitHub 官方文件目前說明：

- Public repo 使用標準 GitHub-hosted runner 通常免費。
- Private repo 依方案有免費額度。
- GitHub Free 方案每月包含 2,000 分鐘、500 MB artifact storage，每個 repo 有 10 GB cache。
- 超過額度後，如果帳號沒有付款方式，通常會停止執行；有付款方式則依 GitHub 的 Actions 計價收費。

這個專案每天跑 3 次。若每次約 3-5 分鐘，一個月以 22 個交易日估算：

```text
3 次/天 x 22 天 x 5 分鐘 = 330 分鐘/月
```

通常會低於 GitHub Free 的 private repo 免費分鐘數。如果 repo 是 public，標準 runner 的排程成本通常更低。

## 下一步建議

- 把 `config/constituents_0050.json` 改成自動下載官方最新成分股。
- 加入股價、成交量、三大法人、融資融券的正式資料源。
- 加入 LLM 二次摘要，把前 10 則新聞整理成投資重點與風險提醒。
- 將每日摘要保存成 Markdown 或 Google Sheet，方便回測評分規則。

