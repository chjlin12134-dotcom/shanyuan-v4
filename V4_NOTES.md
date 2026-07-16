# 善緣 v4 原型備忘

本資料夾是從 `shanyuan-v3-2b` 複製出的獨立 v4 工作區。

## 隔離原則

- 不修改 `shanyuan-v3-2`
- 不修改 `shanyuan-v3-2b`
- 不沿用 v3-2b 的 push 腳本
- 部署前需另建 HuggingFace Space 與 v4 專用 repo / token

## v4 新增方向

1. 被動佛法討論模式
   - 平常不主動使用佛教語言。
   - 使用者主動提到佛教、佛法、佛學、星雲大師、五戒、八關齋戒、菩薩戒等主題時，先詢問確認。
   - 使用者確認後，才用佛法、人間佛教或星雲大師角度陪他討論。

2. 近期時事邊界
   - 不查新聞、不讀連結、不要求貼文章。
   - 使用者自己描述近期事件時，善緣不假裝掌握最新細節，而是請他說說看到什麼、在意什麼。

3. 語音資源釋放
   - 結束語音對話時，強制停止錄音、TTS、插話偵測、LLM/STT 請求與相關計時器。
   - 目標是避免手機右上角橘色麥克風點在結束後仍持續出現。

4. HuggingFace 喚醒設計
   - v4 後端新增 `/healthz` 輕量端點。
   - 部署後可用 cron-job.org 或同類服務每 10-15 分鐘 ping：
     `https://<v4-space-url>/healthz`

## 2026-07-16 Cloud Run 環境變數事故

### 調查結論

`ANTHROPIC_API_KEY` 和 `OPENCODE_GO_API_KEY` **從未被部署到 Cloud Run 上**。
查證 10 個歷史 revision（00015 ~ 00024），全部都只有 `GROQ_API_KEY` + `GROQ_STT_API_KEY`。

### 時間線

| Revision | 時間 (UTC+8) | 部署者 | 說明 |
|---|---|---|---|
| 00015~00022 | 7/12-7/13 | service account | 自動部署（Cloud Build），只有 Groq keys |
| 00023 | 7/16 10:04 | service account | git push → Cloud Build 自動觸發，只有 Groq keys |
| 00024 | 7/16 10:13 | chjlin1213@gmail.com | OpenCode `gcloud run deploy --source`，**未覆蓋任何 key** |
| 00025 | 7/16 10:48 | chjlin1213@gmail.com | 手動補上 OPENCODE_GO_API_KEY / ANTHROPIC_API_KEY（格式有誤待修） |

### 關鍵發現

1. OpenCode 的 `gcloud run deploy --source` **沒有覆蓋 Cloud Run 環境變數**（00023 和 00024 的 Groq keys 完全一致）
2. `GROQ_API_KEY` 在 7/13 的 Cloud Build 自動部署中被更換（00015 → 00022），此與 OpenCode 無關
3. 道別祈福之前能執行，最可能是**在本機測試**（本機 `.env` 有完整 keys），而非 Cloud Run

### 待修

- rev 00025 的 `OPENCODE_GO_API_KEY` 和 `ANTHROPIC_API_KEY` 設定值黏在一起（格式錯誤），需重新分開設定

## 尚未做

- 尚未部署到 HuggingFace。
- 尚未建立 v4 專用 HF repo / Space。
- 尚未設定 keepalive job。
