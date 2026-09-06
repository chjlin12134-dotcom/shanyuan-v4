---
name: shanyuan-v4
description: >
  人間善緣 v4 prototype 的完整操作手冊。當用戶說要繼續做「善緣 v4」、「shanyuan-v4」、
  或提到 app_fastapi_v3.py、Cloud Run 部署、語料庫建置時使用此 skill。
  自動載入 v4 技術架構、部署方式與待辦清單。
---
# 人間善緣 v4 — 操作手冊

## 第一步：載入上下文

```
C:\Users\June\AI_PROJECT\shanyuan_fulltext_claude v1\shanyuan-v4-repo\V4_NOTES.md
```

## 專案概覽

| 項目 | 內容 |
|------|------|
| 網址 | https://shanyuan-v4-483571107702.asia-east1.run.app |
| GCP 專案 ID | shanyuan-v4 (#483571107702) |
| 部署帳號 | chjlin1213@gmail.com |
| 本地工作區 | `C:\Users\June\AI_PROJECT\shanyuan_fulltext_claude v1\shanyuan-v4\` |
| 部署 repo | `C:\Users\June\AI_PROJECT\shanyuan_fulltext_claude v1\shanyuan-v4-repo\` |
| GitHub | https://github.com/chjlin12134-dotcom/shanyuan-v4.git |
| 平台 | Google Cloud Run (asia-east1, Docker)，HF Space 尚未部署 |

## 技術架構

- **後端：** `app_fastapi_v3.py`（FastAPI + uvicorn）
- **前端：** `index.html` → `/chat`, `/transcribe`, `/tts`, `/tts-voices`, `/healthz`
- **STT：** Google STT（優先）→ Groq Whisper（fallback）
- **TTS：** Edge TTS（預設，免費）→ Google TTS（備用，需 key）
- **LLM：** Groq Llama 3.3 70B（主）→ OpenCode Go DeepSeek V4 Flash（429 fallback）
- **道別祈福：** Claude Haiku（永遠）
- **語料庫：** `shanyuan_corpus.csv`（9034 筆），含 82 篇佛經語料

### LLM 分流

| 情境 | 模型 |
|------|------|
| 一般對話 | Groq Llama 3.3 70B |
| Groq 429 | DeepSeek V4 Flash (OpenCode Go) |
| CHAT_MODEL_TIER=premium | Claude Sonnet 4.5 |
| 道別祈福 | Claude Haiku 4.5 |

## 部署方式

```powershell
# 1. 從工作區複製修改到 repo
Copy-Item "C:\Users\June\AI_PROJECT\shanyuan_fulltext_claude v1\shanyuan-v4\app_fastapi_v3.py" "C:\Users\June\AI_PROJECT\shanyuan_fulltext_claude v1\shanyuan-v4-repo\"
# index.html 和 shanyuan_corpus.csv 也要一併複製！

# 2. Commit + push
Set-Location "C:\Users\June\AI_PROJECT\shanyuan_fulltext_claude v1\shanyuan-v4-repo"
git add -A
git commit -m "fix: description"
git push github master

# 3. Deploy to Cloud Run
$env:PATH = "C:\tools\gcloud\google-cloud-sdk\bin;$env:PATH"
gcloud run deploy shanyuan-v4 --source . --region asia-east1 --allow-unauthenticated --memory 1Gi --min-instances 1 --concurrency 20
```

> ⚠️ Cloud Run env vars 不會被 gcloud deploy --source 覆蓋（已驗證）
> ⚠️ 部署時 shanyuan_corpus.csv 要一起複製進 repo（Dockerfile COPY）

## Cloud Run 環境變數

| Key | 用途 |
|---|---|
| GROQ_API_KEY | 主 LLM |
| GROQ_STT_API_KEY | STT fallback |
| DEEPSEEK_API_KEY | DeepSeek API 備援 |
| ANTHROPIC_API_KEY | Claude 道別祈福 |
| （選用）GOOGLE_TTS_API_KEY | Google TTS |
| （選用）GOOGLE_STT_API_KEY | Google STT |

## TTS 語音設定

- 預設 `EDGE-F2`（zh-TW-HsiaoChenNeural，溫和女聲）
- Edge TTS 不支援 SSML `<phoneme>`（已確認，會讀出標籤文字）— 切勿使用
- 句首墊字 `、` 已移除（造成不自然停頓）
- `。` 會造成 edge-tts 長停頓 — farewell 指令已改為「逗號代替句號」

## 語料庫建置

- `extract_opencode.py` — OpenCode Go API 萃取腳本
- 用法：`python extract_opencode.py 1 心經_般若心經的生活觀.xlsx`（測試 1 篇）
- 用法：`python extract_opencode.py 心經_般若心經的生活觀.xlsx`（跑全部）
- 需設定 `OPENCODE_GO_API_KEY` 環境變數
- 萃取成功率約 70%（DeepSeek V4 Flash 30% 回非 JSON）

### 已爬取的佛經文章 ID

| 經典 | 文章 ID 範圍 |
|------|-------------|
| 金剛經講話 | artcle 234-338 |
| 般若心經生活觀 | artcle 996-1013, 15967 |
| 六祖壇經講話 | artcle 42-165 |
| 法華經普門品 | artcle 2160-2174 |

- 網站搜尋：`https://books.masterhsingyun.org/search/關鍵字`
- 每篇文章有「書目錄」modal，可抓取全書 URL

## 已知問題與修正記錄

| 問題 | 狀態 | 說明 |
|------|------|------|
| 祈福禮選到罵人句子 | ✅ 已修 | `get_blessing()` 加黑名單 |
| TTS 句首不自然停頓 | ✅ 已修 | 移除 `、` 墊字 |
| SSML phoneme 壞掉 | ✅ 已修 | edge-tts 不支援，已回退 |
| Farewell 句號長停頓 | ✅ 已修 | 改「逗號代替句號」 |
| Buddhist 確認語多餘 | ✅ 已修 | index.html 刪除 |
| Cloud Run 缺 env vars | ✅ 已修 | 補上關鍵 4 把 key |
| /healthz 404 | 🟡 已知 | Cloud Run 層級路由，不影響功能 |
| 萃取 30% 失敗 | 🟡 已知 | Flash 模型輸出格式不穩 |

## 收工備忘位置

`C:\Users\June\AI_PROJECT\shanyuan_fulltext_claude v1\收工備忘_2026-07-16_善緣V4.txt`

## 安全注意

- API key 只用 `os.environ.get("KEY_NAME")` 讀取
- 本機 `.env` 已加到 gitignore
- Cloud Run 金鑰在後台管理，不在 repo
- gcloud 登入用 chjlin1213@gmail.com
