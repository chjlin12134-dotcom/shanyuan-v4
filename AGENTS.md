# 善緣 V4 — 工作狀態交接

> 最後更新：2026-09-03

## 專案路徑

`C:\Users\June\AI_project\shanyuan_fulltext_claude v1\shanyuan-v4-repo`

## Cloud Run

- 服務：`shanyuan-v4`
- 區域：`asia-east1`
- URL：`https://shanyuan-v4-483571107702.asia-east1.run.app`
- 最新 revision：`shanyuan-v4-00145-tf4`

## 今日完成（2026-09-03）

### 1. DeepSeek 改為直連 API
- `GO_BASE_URL` 從 OpenCode Go 改為 `https://api.deepseek.com/v1/chat/completions`
- `OPENCODE_GO_API_KEY` 改為 `DEEPSEEK_API_KEY`
- Fallback 順序：Groq → DeepSeek（直連）→ Gemini → 「沒聽清楚」
- 已部署 revision `00144-l7t`（代碼）+ `00145-tf4`（system prompt）

### 2. System Prompt 強化（v1.2）
- 加入「第二序控制論」哲學框架（`你的對話哲學` 區塊）
- 加入硬限制詞彙表（prompt 最前面，模型最先讀到）
- 加入破功情境範例（5 個最容易犯錯的場景）
- 加入輸出前自我檢查指令（陪伴 vs 指導）

### 3. 成本確認
- Cloud Run：minScale=0，keep-alive 在免費額度內，月費 $0
- Groq：免費方案，目前用量 $0.01
- DeepSeek：直連 API，餘額 ¥5.25
- GCP 贈金：$9,046 剩餘，9/7 到期
- 月費估算：接近 $0（用量低）

## 待觀察

- **紅線問題**：Groq 模型是否仍會「給建議」（指導與建議）→ 測試幾天看频率
- **空回應**：「我剛剛沒有聽不清楚」是否仍有出現
- **GCP 贈金**：9/7 到期後，實際 Cloud Run 計費情况

## 目前的模型架構

| 情境 | 模型 |
|---|---|
| 一般對話 | Groq `openai/gpt-oss-120b` |
| Groq 429 fallback | DeepSeek `deepseek-v4-flash`（直連 API） |
| DeepSeek 失敗 fallback | Gemini |
| 道別祈福 | Claude Haiku 4.5（永遠） |
| buddhist_mode | 先收集完整回覆 → 驗證 → 不合格重跑 |
| CHAT_MODEL_TIER=premium | Claude Sonnet 4.5（目前未啟用） |

## 已知問題

1. **Groq 模型指令遵從度不足**：偶爾會違反「不給建議」的規則。已透過 system prompt 強化改善，但根本原因是模型差異（Groq vs Claude）。如需 100% 遵從，需切換為 Claude。
2. **OpenCode Go 訂閱已過期**：DeepSeek fallback 改為直連 API 解決。
3. **GCP 贈金即將到期**：9/7 到期，到期後 Cloud Run 以 minScale=0 計費接近 $0。

## 環境變數（Cloud Run）

| Key | 用途 |
|---|---|
| GROQ_API_KEY | 主 LLM |
| GROQ_STT_API_KEY | STT fallback |
| DEEPSEEK_API_KEY | DeepSeek API 備援 |
| ANTHROPIC_API_KEY | Claude 道別祈福 |
| GOOGLE_TTS_API_KEY | Google TTS（選用） |
| GOOGLE_STT_API_KEY | Google STT（選用） |

## 部署指令

```powershell
gcloud run deploy shanyuan-v4 --source "C:\Users\June\AI_project\shanyuan_fulltext_claude v1\shanyuan-v4-repo" --region=asia-east1 --project=shanyuan-v4
```

## 下次開工

1. 確認 Groq 模型是否仍違反「不給建議」規則
2. 如果仍違反，考慮切換 `CHAT_MODEL_TIER=premium`（改用 Claude）
3. 測試語音對話的回應品質
4. 觀察 GCP 贈金到期後的實際計費
