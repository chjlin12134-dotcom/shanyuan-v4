# 善緣 v4 repo 備忘

## 目前狀態（2026-07-12）

本資料夾是善緣 V4 的 Cloud Run / GitHub 部署 repo。

- 本機路徑：`C:\Users\June\AI_project\shanyuan_fulltext_claude v1\shanyuan-v4-repo`
- GitHub repo：`chjlin12134-dotcom/shanyuan-v4`
- branch：`master`
- Cloud Run project：`shanyuan-v4`
- Cloud Run service：`shanyuan-v4`
- region：`asia-east1`
- 公開 URL：`https://shanyuan-v4-483571107702.asia-east1.run.app/`
- 最新已部署修正 commit：`c465a11 Harden voice STT and farewell fallback`
- 建議測試 URL：`https://shanyuan-v4-483571107702.asia-east1.run.app/?v=voice-hardening-c465a11`

## 重要環境變數

不要把 key 寫進程式或聊天中，由使用者在 Cloud Run Console 自行填：

- `ANTHROPIC_API_KEY`：道別／祈福禮優先使用。
- `GROQ_STT_API_KEY`：語音辨識 STT 專用，建議使用獨立 Groq 帳號。
- `GROQ_API_KEY`：日常聊天備援／標準路線，建議與 STT 不同 Groq 帳號。
- `OPENCODE_GO_API_KEY`：最後備援，不作為品質主路線。

## 已知設計

- V4 不覆蓋 V3.2 / V3.2B。
- V4 已加入被動佛法語言模式、近期時事邊界、語音使用說明、STT 雜訊過濾。
- `再見` 仍優先走 Anthropic；只有 Anthropic key 未讀到或呼叫失敗時，才退回 Groq / OpenCode，避免前端錯誤。
- STT 會過濾短雜訊中的日文、英文、李宗盛、初音、詞曲、歌手、字幕等常見 Whisper 幻覺。

## 下次測試重點

1. 真人語音連續對話是否穩定。
2. 背景雜音是否仍誤辨成日文/英文/音樂資訊。
3. 說「再見」是否正常出現道別與祈福禮。
4. 善緣是否仍偶爾混入英文或日文。

## 舊 HF 計畫備註

原本曾計畫建立 v4 Hugging Face Space，但新 HF 帳號 / 新 Space 免費 CPU 限制不穩，且 HF 新政策對新 Docker/Gradio Space 較不友善。目前 V4 主路線改為 Google Cloud Run。V3.2B 等舊 HF Space 不要隨便 pause/delete。
