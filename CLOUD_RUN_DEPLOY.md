# 善緣 V4 - Google Cloud Run 部署備忘

本 repo 已調整為 Cloud Run 可部署版本。

## 已完成的 Cloud Run 適配

- Dockerfile 改為監聽 `0.0.0.0`
- Dockerfile 改為讀取 Cloud Run 提供的 `PORT`
- 本機預設 fallback port：`8080`
- 新增 `.dockerignore`，避免把 `.git`、暫存、環境檔一起打包
- `/healthz` 可作為健康檢查或喚醒測試 endpoint

## 建議 Cloud Run 設定

- Service name：`shanyuan-v4`
- Region：建議先用 `asia-east1` 或 `asia-east2`
- Min instances：先設 `0`，避免常駐費用
- Max instances：先設 `1` 或 `2`，避免成本失控
- CPU：先用預設即可
- Memory：建議先 `1Gi`，若啟動不穩再調高
- Authentication：若要公開使用，選 Allow unauthenticated invocations

## 需要設定的環境變數 / Secret

請在 Cloud Run 後台設定，不要寫進 repo。

- `ANTHROPIC_API_KEY`
- `GROQ_API_KEY`
- `GROQ_STT_API_KEY`
- `OPENCODE_GO_API_KEY`

可選：

- `CHAT_MODEL_TIER`
- `BLESSING_MODEL`

## 喚醒設計

Cloud Run 若 `min instances = 0`，閒置後會冷啟動。v4 已有：

- `/healthz`

可以用來做簡單喚醒檢查。但若用排程器定期 ping，可能增加請求量與費用；建議先不上喚醒，實測冷啟動是否可接受，再決定。
