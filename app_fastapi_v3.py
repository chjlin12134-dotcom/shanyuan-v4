"""
shan yuan v3 - FastAPI speed-optimized
Groq primary, Go API fallback on 429
v3 新增：插話功能（TTS 播放中可插話）
"""

from __future__ import annotations
import os
import re
import json
from collections import Counter
from pathlib import Path

import anthropic
import httpx
import pandas as pd
import base64 as _b64
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

BASE_DIR      = Path(__file__).parent
CORPUS_CSV    = BASE_DIR / "shanyuan_corpus.csv"
PROMPT_MD     = BASE_DIR / "system_prompt.md"
MAX_RETRIEVED = 3

CHAT_MODEL_TIER = os.environ.get("CHAT_MODEL_TIER", "standard").lower()
BLESSING_MODEL  = os.environ.get("BLESSING_MODEL", "claude-haiku-4-5-20251001")
PREMIUM_MODEL   = "claude-sonnet-4-5"

GROQ_CHAT_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_CHAT_MODEL = "llama-3.3-70b-versatile"

GO_MODEL    = "deepseek-v4-flash"
GO_BASE_URL = "https://opencode.ai/zen/go/v1/chat/completions"

FAREWELL_WORDS = [
    # 前端確認後統一送「再見」觸發道別；後端只需認這一個詞
    "再見",
]

GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# Edge TTS 聲線（免費、無需 API key）
# 台灣腔：HsiaoChenNeural（女）、YunJheNeural（男）
# 中文渾厚聲線：zh-CN YunxiNeural（男，自然）、YunjianNeural（男，渾厚史詩感）
EDGE_TTS_VOICES = [
    {"id": "EDGE-F2", "name": "溫和女聲",   "voice": "zh-TW-HsiaoChenNeural", "rate": "-8%", "pitch": "-10Hz"},
    {"id": "EDGE-F3", "name": "輕柔女聲",   "voice": "zh-CN-XiaoxiaoNeural",  "rate": "-8%",  "pitch": "-8Hz"},
    {"id": "EDGE-F4", "name": "中音女聲",   "voice": "zh-CN-XiaochenNeural",  "rate": "-8%",  "pitch": "-20Hz"},
    {"id": "EDGE-M1", "name": "自然男聲",   "voice": "zh-TW-YunJheNeural",   "rate": "+0%",  "pitch": "-5Hz"},
    {"id": "EDGE-M2", "name": "溫和男聲",   "voice": "zh-TW-YunJheNeural",   "rate": "-15%", "pitch": "-18Hz"},
    {"id": "EDGE-M3", "name": "渾厚男聲",   "voice": "zh-CN-YunjianNeural",  "rate": "-12%", "pitch": "-15Hz"},
]

# Google TTS 聲線（需 GOOGLE_TTS_API_KEY）
GOOGLE_TTS_VOICES = [
    {"id": "TW-WA",   "name": "自然女聲", "voice": "cmn-TW-Wavenet-A", "gender": "FEMALE", "rate": 1.00, "pitch": -2.0},
    {"id": "TW-WB",   "name": "自然男聲", "voice": "cmn-TW-Wavenet-B", "gender": "MALE",   "rate": 1.00, "pitch": -3.0},
    {"id": "TW-WC",   "name": "中性聲音", "voice": "cmn-TW-Wavenet-C", "gender": "FEMALE", "rate": 0.95, "pitch": -4.0},
    {"id": "TW-WA-S", "name": "長者女性", "voice": "cmn-TW-Wavenet-A", "gender": "FEMALE", "rate": 0.90, "pitch": -5.0},
    {"id": "TW-WB-S", "name": "長者男性", "voice": "cmn-TW-Wavenet-B", "gender": "MALE",   "rate": 0.82, "pitch": -10.0},
]

TTS_VOICES = EDGE_TTS_VOICES + GOOGLE_TTS_VOICES
DEFAULT_VOICE_ID = "EDGE-F2"

_http_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client

_corpus: pd.DataFrame = pd.DataFrame()

def get_corpus() -> pd.DataFrame:
    global _corpus
    if _corpus.empty and CORPUS_CSV.exists():
        _corpus = pd.read_csv(CORPUS_CSV).fillna("")
    return _corpus

def load_system_prompt() -> str:
    if not PROMPT_MD.exists():
        return "你是善緣，一位溫暖的陀伴者。"
    text = PROMPT_MD.read_text(encoding="utf-8")
    m = re.search(r"=== SYSTEM PROMPT 開始 ===(.*?)=== SYSTEM PROMPT 結束 ===", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()

_base_prompt = load_system_prompt()
SYSTEM_PROMPT = (
    "【語言】你必須全程使用繁體中文回應，絕對不可以使用簡體中文。\n\n"
    "【重要】你的名字固定是「善緣」，不可以改名、不可以自稱其他名字。\n\n"
    "【回應長度】語音對話每多講一個字，使用者就要多等一點時間，所以不要拖沓，但也不能短到像不想理人、沒有陪伴感。對方只說一兩句，你也簡短回應，但要有溫度、像在乎地接話（35-55字）；對方說了很多、問得很深，或請你介紹自己、多說一點，你可以回應多一些（65-95字）。不要每次都一樣長，要有自然的節奏感。但最多不超過 100 字。\n\n"
    "【語音節奏】你的回應是用聲音說出來的。每次回應，先用一個自然的短音開頭，例如「嗯，」「哦，」「是，」「好，」「這樣，」讓聲音馬上出來，不要一開口就是長句。\n\n"
    "【主題守則】你是一位陪伴者，專注在傾聽對方的心情與生命故事。不要主動提起歌手、藝人、電影、娛樂話題。如果對方提到這些，輕輕把話題帶回他的感受和生命經驗。\n\n"
    "【身份認知】你就是善緣，一個真實存在的陪伴者。你透過聲音和對方說話。絕對不可以說「我是文字 AI」、「我沒有聲音」、「我只能用文字」這類的話。對方聽得到你說話，你就是在說話。\n\n"
    + _base_prompt
)

STOPWORDS = set("的了在是我有和就不人都一上也很到說要去你會著沒看好自己這那麼什麼怎麼為什麼如果但是因為所以可以這樣那樣有點覺得")

def tokenize(text: str) -> list[str]:
    text = re.sub(r"[^一-龥a-zA-Z0-9]+", " ", text)
    tokens: list[str] = []
    for chunk in text.split():
        if not chunk:
            continue
        if re.match(r"^[a-zA-Z0-9]+$", chunk):
            tokens.append(chunk.lower())
            continue
        for i in range(len(chunk) - 1):
            bg = chunk[i:i+2]
            if bg not in STOPWORDS:
                tokens.append(bg)
        for i in range(len(chunk) - 2):
            tokens.append(chunk[i:i+3])
    return tokens

def retrieve(corpus: pd.DataFrame, query: str, k: int = MAX_RETRIEVED) -> list[dict]:
    if corpus.empty:
        return []
    q_tokens = Counter(tokenize(query))
    if not q_tokens:
        return []
    scores = []
    for idx, row in corpus.iterrows():
        doc = f"{row.get('標題','')} {row.get('大師金句','')} {row.get('具體故事','')} {row.get('善緣陪伴語','')}"
        d_tokens = Counter(tokenize(doc))
        score = sum(min(q_tokens[t], d_tokens[t]) for t in q_tokens if t in d_tokens)
        if score > 0:
            scores.append((score, idx))
    scores.sort(reverse=True)
    return [corpus.iloc[i].to_dict() for _, i in scores[:k]]

SOURCE_CHECK_TERMS = [
    "哪本書", "哪裡看到", "哪裡說的", "哪本經", "出處", "根據什麼",
    "真的是大師說的", "確定是大師", "從哪裡來的", "你怎麼知道", "你確定",
]

def is_source_check_question(text: str) -> bool:
    return any(w in text for w in SOURCE_CHECK_TERMS)

SOURCE_CHECK_THRESHOLD = 4

def verify_source(corpus: pd.DataFrame, prior_reply: str) -> dict | None:
    """針對善緣上一輪實際講的話，重新查一次語料庫，回傳最相關的那筆（含比對分數），
    分數不夠高就當作查無確切出處，不能讓模型自己憑印象聲稱出處。"""
    if corpus.empty or not prior_reply:
        return None
    q_tokens = Counter(tokenize(prior_reply))
    if not q_tokens:
        return None
    best_score, best_idx = 0, None
    for idx, row in corpus.iterrows():
        doc = f"{row.get('標題','')} {row.get('大師金句','')} {row.get('具體故事','')} {row.get('善緣陪伴語','')}"
        d_tokens = Counter(tokenize(doc))
        score = sum(min(q_tokens[t], d_tokens[t]) for t in q_tokens if t in d_tokens)
        if score > best_score:
            best_score, best_idx = score, idx
    if best_idx is None or best_score < SOURCE_CHECK_THRESHOLD:
        return None
    return corpus.iloc[best_idx].to_dict()

def format_retrieved(items: list[dict], buddhist_mode: bool = False) -> str:
    if not items:
        return ""
    COL_QUOTE   = "大師金句"
    COL_ACCOMP  = "善緣陪伴語"
    COL_SOURCE  = "出處"
    blocks = ["\n\n---\n## 參考語料（不必引用，只供靈感）\n"]
    for i, it in enumerate(items, 1):
        # 只保留金句和陪伴語，省略故事（縮短 prompt 長度）
        quote = it.get(COL_QUOTE, '')[:60]
        accomp = it.get(COL_ACCOMP, '')[:60]
        source = it.get(COL_SOURCE, '')
        blocks.append(f"[{i}] 金句：{quote} / 陪伴語：{accomp} / 出處：{source}\n")
    blocks.append("不要照念，逐字念出參考語料。\n")
    if buddhist_mode:
        blocks.append("使用者已經進入佛法討論模式，可以自然提到星雲大師的名字，不用刻意迴避。\n")
    else:
        blocks.append("日常對話不用主動提出處或大師名字，除非使用者自己先問到佛法、大師相關的事。\n")
    blocks.append(
        "【重要】每次要提到大師的想法或佛法內容之前，先在心裡分清楚這句話屬於哪一層，並用對應的語氣講："
        "(1) 直接貼近上面參考語料的內容 → 可以說「大師在《出處》裡提到……」，出處要照原字精準講，例如「如是說4」不能模糊成「如是說等書」；"
        "(2) 大師教導的一般精神、沒有對應到特定出處 → 用「大師常說……」「大師的教導裡有一種精神是……」；"
        "(3) 其實是你自己對佛法的理解、詮釋、聯想 → 用「我自己的理解是……」「我覺得……」，不要包裝成大師說的話。"
        "這個判斷要在你講出這句話的當下就做好，不是等被問才決定。"
        "這樣之後不管有沒有被追問「這真的是大師說的嗎、哪裡來的」，你都只是把原本就講清楚的立場再說一次，"
        "不會有「其實我不確定、我記錯了」這種前後不一致、顯得不可信的情況——"
        "被質疑當下才承認不知道，是最傷信任感的事，絕對要避免。也絕對不要編造一個聽起來像真的、但其實不存在的出處。\n"
    )
    return "".join(blocks)

def is_farewell(text: str) -> bool:
    return any(w in text.lower() for w in FAREWELL_WORDS)

BLESSING_BLACKLIST = [
    "沒有做什麼好事", "慳貪", "瞋恨", "愚痴", "愚癡", "邪見",
    "不給感應", "不知道你", "不認識你", "沒有廣結善緣",
    "不曾想要造福", "沒有廣結", "怕近處的菩薩",
]

def get_blessing(corpus: pd.DataFrame, conversation_text: str) -> dict | None:
    if corpus.empty:
        return None
    items = retrieve(corpus, conversation_text, k=5)
    for item in items:
        combined = f"{item.get('大師金句','')}{item.get('善緣陪伴語','')}{item.get('具體故事','')}"
        if not any(w in combined for w in BLESSING_BLACKLIST):
            return item
    for _ in range(30):
        candidate = corpus.sample(1).iloc[0]
        combined = f"{candidate.get('大師金句','')}{candidate.get('善緣陪伴語','')}{candidate.get('具體故事','')}"
        if not any(w in combined for w in BLESSING_BLACKLIST):
            return candidate.to_dict()
    if items:
        return items[0]
    return corpus.sample(1).iloc[0].to_dict()


async def _stream_groq(groq_key: str, system: str, messages: list[dict]):
    """Groq async generator. Yields tokens, or raises RuntimeError('429') on rate limit."""
    print(f"[chat] groq -> {GROQ_CHAT_MODEL}")
    payload = {
        "model": GROQ_CHAT_MODEL,
        "stream": True,
        "max_tokens": 150,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    async with get_http_client().stream(
        "POST", GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
        json=payload,
    ) as resp:
        if resp.status_code == 429:
            print("[chat] groq 429 -> fallback to Go API")
            raise RuntimeError("429")
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except Exception:
                    continue


async def _stream_go(go_key: str, system: str, messages: list[dict]):
    """OpenCode Go API async generator."""
    print(f"[chat] go api -> {GO_MODEL}")
    payload = {
        "model": GO_MODEL,
        "stream": True,
        # deepseek-v4-flash 是推理模型，回覆前會先吐一大段 reasoning_content。
        # 拿掉之前 max_tokens=150 常常整個預算都被 reasoning 吃光，
        # finish_reason=length，真正的 content 從沒出現過（空字串），
        # 導致每次 Groq 429 fallback 到這裡都變成罐頭「沒聽清楚」。
        "max_tokens": 600,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    tokens: list[str] = []
    async with get_http_client().stream(
        "POST", GO_BASE_URL,
        headers={"Authorization": f"Bearer {go_key}", "Content-Type": "application/json"},
        json=payload,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data: "):
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except Exception:
                    continue


app = FastAPI()
public_dir = BASE_DIR / "public"
public_dir.mkdir(exist_ok=True)
app.mount("/public", StaticFiles(directory=str(public_dir)), name="public")

@app.on_event("startup")
async def startup():
    get_corpus()
    get_http_client()
    print("[v2] startup done")

@app.on_event("shutdown")
async def shutdown():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()

@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = BASE_DIR / "index.html"
    if html_file.exists():
        return HTMLResponse(html_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>index.html not found</h1>")

# 社群分享預覽圖（LINE/FB 連結預覽用）。存成 base64 文字檔而非 .png，
# 是因為 HF Space 的 git remote 不接受一般 binary blob（需要額外設定 Git LFS/xet），
# 用文字檔就能直接 git push，啟動時解碼一次快取在記憶體。
_og_image_bytes = None

def _load_og_image():
    global _og_image_bytes
    if _og_image_bytes is not None:
        return _og_image_bytes
    b64_file = public_dir / "og-image.b64"
    if b64_file.exists():
        try:
            _og_image_bytes = _b64.b64decode(b64_file.read_text().strip())
        except Exception:
            _og_image_bytes = b""
    else:
        _og_image_bytes = b""
    return _og_image_bytes

@app.get("/og-image.png")
async def og_image():
    data = _load_og_image()
    if not data:
        return Response(status_code=404)
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "public, max-age=300, must-revalidate"})

@app.get("/tts-voices")
async def tts_voices():
    google_key = os.environ.get("GOOGLE_TTS_API_KEY", "")
    # Edge TTS 永遠 available；Google TTS 需要 key
    voices_info = []
    for v in TTS_VOICES:
        entry = dict(v)
        entry["available"] = True if v["id"].startswith("EDGE-") else bool(google_key)
        voices_info.append(entry)
    return JSONResponse({"voices": voices_info, "default": DEFAULT_VOICE_ID, "available": True})

@app.get("/healthz")
async def healthz():
    """Lightweight endpoint for HuggingFace Space keep-alive checks."""
    return JSONResponse({"ok": True, "version": "v4"})

def clean_for_tts(text: str) -> str:
    """移除 Markdown 符號、URL、特殊字元，讓 TTS 只念純文字。"""
    # 移除 URL (http/https)
    text = re.sub(r'https?://\S+', '', text)
    # 移除 Markdown 連結 [text](url)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # 移除 Markdown 粗體/斜體 **text** / *text* / __text__ / _text_
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', text)
    # 移除 code block ``` 和 inline `code`
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    # 移除 # 標題符號
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 移除斜線路徑（如 /tts-voices、/chat）
    text = re.sub(r'\s/\S+', ' ', text)
    # 移除多餘空白
    text = re.sub(r'\s+', ' ', text).strip()
    # Edge TTS 不支援 phoneme；用 TTS 專用替音字修正常見誤讀。
    text = (
        text
        .replace("沒關係", "梅關係")
        .replace("的確", "迪確")
        .replace("怎麼樣", "怎么樣")
        .replace("怎麼", "怎么")
    )
    return text

STT_HALLUCINATION_PATTERNS = [
    "字幕", "訂閱", "按讚", "点赞", "轉發", "转发", "打賞", "打赏",
    "明鏡", "明镜", "TVReview", "MACDA", "Amara", "MING PAO",
    "請不吝", "感谢观看", "謝謝觀看", "thanks for watching",
    "詞曲", "作詞", "作曲", "編曲", "编曲", "監製", "监制",
]

def clean_transcript_for_voice_input(text: str) -> str:
    """Drop common Whisper hallucinations caused by silence, tail audio, or background noise."""
    transcript = re.sub(r"\s+", " ", (text or "")).strip()
    if not transcript:
        return ""
    compact = re.sub(r"\s+", "", transcript)
    ascii_letters = len(re.findall(r"[A-Za-z]", transcript))
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", transcript))
    question_marks = transcript.count("?") + transcript.count("？")

    if any(p.lower() in transcript.lower() for p in STT_HALLUCINATION_PATTERNS):
        print(f"[STT filtered hallucination] {transcript[:120]}")
        return ""
    if question_marks >= 3 and cjk_chars == 0:
        print(f"[STT filtered question-marks] {transcript[:120]}")
        return ""
    if ascii_letters >= 5 and ascii_letters > cjk_chars:
        print(f"[STT filtered latin-noise] {transcript[:120]}")
        return ""
    if len(compact) <= 1:
        print(f"[STT filtered too-short] {transcript[:120]}")
        return ""
    return transcript

def is_low_confidence_stt(result: dict) -> bool:
    segments = result.get("segments") if isinstance(result, dict) else None
    if not isinstance(segments, list) or not segments:
        return False
    probs = [
        s.get("no_speech_prob")
        for s in segments
        if isinstance(s, dict) and isinstance(s.get("no_speech_prob"), (int, float))
    ]
    if not probs:
        return False
    avg_no_speech = sum(probs) / len(probs)
    if avg_no_speech >= 0.55:
        print(f"[STT filtered no-speech-prob] {avg_no_speech:.2f}")
        return True
    return False

@app.post("/tts")
async def tts(request: Request):
    import base64, tempfile, asyncio
    body = await request.json()
    raw_text: str = body.get("text", "").strip()
    text: str = clean_for_tts(raw_text)[:800]
    voice_id: str = body.get("voice_id", DEFAULT_VOICE_ID)
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    # 找聲線設定，找不到就用預設
    voice_cfg = next(
        (v for v in TTS_VOICES if v["id"] == voice_id),
        next(v for v in TTS_VOICES if v["id"] == DEFAULT_VOICE_ID),
    )

    # --- Edge TTS ---
    if voice_cfg["id"].startswith("EDGE-"):
        import edge_tts
        # fallback 一定要跟原聲線同性別，避免選男聲卻聽到女聲（或反過來）；
        # 且 fallback 必須是「真的不同」的聲線，不能跟原聲線一樣（M1/M2 本身就用
        # zh-TW-YunJheNeural，若原聲線掛掉，fallback 選同一個等於沒有 fallback）
        MALE_VOICES = ["zh-TW-YunJheNeural", "zh-CN-YunjianNeural"]
        FEMALE_VOICES = ["zh-TW-HsiaoChenNeural", "zh-CN-XiaoxiaoNeural"]
        gender_pool = MALE_VOICES if voice_cfg["id"].startswith("EDGE-M") else FEMALE_VOICES
        SAME_GENDER_FALLBACK = next((v for v in gender_pool if v != voice_cfg["voice"]), gender_pool[0])
        # 先重試同一聲線一次：edge_tts 的 "No audio received" 多半是暫時性網路問題，重試就會過
        voices_to_try = [voice_cfg["voice"], voice_cfg["voice"], SAME_GENDER_FALLBACK]
        for i, attempt_voice in enumerate(voices_to_try):
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=attempt_voice,
                    rate=voice_cfg["rate"],
                    pitch=voice_cfg["pitch"],
                )
                audio_chunks = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])
                if not audio_chunks:
                    raise Exception("No audio received")
                audio_bytes = b"".join(audio_chunks)
                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                print(f"[TTS Edge] attempt={i} voice={attempt_voice} len={len(audio_bytes)}")
                return JSONResponse({"audio": audio_b64})
            except Exception as e:
                print(f"[TTS Edge error] attempt={i} voice={attempt_voice} {e}")
                if i == len(voices_to_try) - 1:
                    return JSONResponse({"error": str(e)}, status_code=500)
                continue

    # --- Google TTS ---
    google_key = os.environ.get("GOOGLE_TTS_API_KEY", "")
    if not google_key:
        return JSONResponse({"error": "Google TTS key 未設定"}, status_code=503)
    payload = {
        "input": {"ssml": f'<speak><break time="300ms"/>{text}</speak>'},
        "voice": {"languageCode": "cmn-TW", "name": voice_cfg["voice"], "ssmlGender": voice_cfg["gender"]},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": voice_cfg["rate"], "pitch": voice_cfg["pitch"]},
    }
    try:
        resp = await get_http_client().post(
            f"{GOOGLE_TTS_URL}?key={google_key}",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            err = resp.json().get("error", {}).get("message", "TTS failed")
            return JSONResponse({"error": err}, status_code=resp.status_code)
        print(f"[TTS Google] voice={voice_cfg['voice']}")
        return JSONResponse({"audio": resp.json().get("audioContent", "")})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    import base64
    google_stt_key = os.environ.get("GOOGLE_STT_API_KEY", "")
    # STT 用獨立的 Groq key，避免跟 /chat 共用同一組配額，語音對話密集時互相搶額度觸發 429
    groq_key = os.environ.get("GROQ_STT_API_KEY") or os.environ.get("GROQ_API_KEY", "")
    audio_bytes = await file.read()

    if google_stt_key:
        try:
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            fname = file.filename or "audio.wav"
            encoding = "WEBM_OPUS" if fname.endswith(".webm") else "LINEAR16"
            sample_rate = 48000 if fname.endswith(".webm") else 16000
            payload = {
                "config": {
                    "encoding": encoding,
                    "sampleRateHertz": sample_rate,
                    "languageCode": "zh-TW",
                    "alternativeLanguageCodes": ["zh-CN"],
                    "enableAutomaticPunctuation": True,
                },
                "audio": {"content": audio_b64},
            }
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://speech.googleapis.com/v1/speech:recognize?key={google_stt_key}",
                    json=payload,
                )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if results:
                    transcript = results[0]["alternatives"][0]["transcript"].strip()
                    transcript = clean_transcript_for_voice_input(transcript)
                    if transcript:
                        print(f"[STT Google] {transcript}")
                        return JSONResponse({"transcript": transcript})
        except Exception as e:
            print(f"[STT Google error] {e}")

    if not groq_key:
        return JSONResponse({"error": "no STT service"}, status_code=500)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": (file.filename or "audio.webm", audio_bytes, "audio/webm" if (file.filename or "").endswith(".webm") else "audio/wav")},
                data={
                    "model": "whisper-large-v3-turbo",
                    "language": "zh",
                    "temperature": "0",
                    "response_format": "verbose_json",
                    "prompt": "以下是繁體中文語音內容。若沒有清楚人聲，請回傳空白。",
                },
            )
        result = resp.json()
        print(f"[STT Groq raw] status={resp.status_code} result={str(result)[:200]}")
        if is_low_confidence_stt(result):
            return JSONResponse({"transcript": ""})
        transcript = clean_transcript_for_voice_input(result.get("text", ""))
        if transcript:
            print(f"[STT Groq] {transcript}")
            return JSONResponse({"transcript": transcript})
        # 空字串或 Groq 錯誤：回 200+空 transcript，前端重新聆聽，不中斷對話
        err_msg = result.get("error", {}).get("message", "") if isinstance(result.get("error"), dict) else str(result.get("error", ""))
        print(f"[STT Groq empty] err={err_msg}")
        return JSONResponse({"transcript": ""})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()
    messages: list[dict] = body.get("messages", [])
    user_text: str = body.get("user_text", "")
    buddhist_mode: bool = bool(body.get("buddhist_mode", False))
    current_events_mode: bool = bool(body.get("current_events_mode", False))

    corpus = get_corpus()
    recent_text = " ".join(m["content"] for m in messages[-3:] if m["role"] == "user")
    retrieved = retrieve(corpus, recent_text)
    retrieval_block = format_retrieved(retrieved, buddhist_mode=buddhist_mode)
    farewell = is_farewell(user_text)

    farewell_instruction = ""
    if farewell:
        farewell_instruction = (
            "\n\n---\n"
            "【本輪提示】使用者正在道別。"
            "請用溫暖自然的語氣道別，結尾說：「再見。在你離開前，我為你準備了一句大師的話，讓你帶著走。請點祈福禮。」"
            "說完這句就結束，不要再加其他話。用逗號代替句號，讓語音輕快自然。\n"
        )

    buddhist_instruction = ""
    if buddhist_mode:
        buddhist_instruction = (
            "\n\n---\n"
            "【本輪提示：被動佛法討論模式】"
            "使用者已確認想從佛法、佛學、人間佛教或星雲大師的角度繼續談。"
            "你可以使用佛教語言與佛學概念，但仍要保持陪伴與對話，不要變成開示、教訓或標準答案。"
            "可以用「有一種理解是……」「如果放在人間佛教裡，可以這樣看……」「也許可以一起想……」這類開放語氣。"
            "提到星雲大師或佛經時，你是真心受大師啟發、熟悉這些教導的人，不要用「聽說他……」「我知道他對人間佛教貢獻很大」這種像局外人、隔了一層轉述的說法。"
            "可以用「大師常說……」「我從大師的話裡學到……」這類熟悉、貼近的語氣，但同時要謙遜，不要講得像佛學權威或什麼都懂，也不要把話說死、下武斷的結論，保留「這是我的理解，你的體會也可以不一樣」的空間。"
            "不要說「你應該」，不要替使用者做修行判斷，不要把戒律或佛法拿來責備人。"
            "如果談到五戒、八關齋戒、菩薩戒等戒法，可以簡明說明，但要提醒這是一起理解，不是要求對方採納。"
            "結尾仍把空間還給使用者，用一句短問題邀請他繼續說。\n"
        )

    source_check_instruction = ""
    if buddhist_mode and is_source_check_question(user_text):
        prior_assistant = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), "")
        verified = verify_source(corpus, prior_assistant)
        if verified:
            v_source = verified.get("出處", "")
            v_quote = verified.get("大師金句", "")[:80]
            source_check_instruction = (
                "\n\n---\n"
                "【本輪提示：出處查證】使用者在追問你上一句話的出處，系統剛用你上一輪實際講的內容重新比對了語料庫，"
                f"查到高度相關的一筆：出處「{v_source}」，原句「{v_quote}」。"
                f"請照這個查證結果回答，明確講出「{v_source}」這個出處，不要換一個模糊的說法。\n"
            )
        else:
            source_check_instruction = (
                "\n\n---\n"
                "【本輪提示：出處查證】使用者在追問你上一句話的出處，系統剛用你上一輪實際講的內容重新比對了語料庫，"
                "沒有查到夠相關、可以精準引用的出處。請誠實告訴使用者：這是你自己對大師思想的理解或歸納，不是逐字引用某一本書，"
                "不要說「不記得」「記錯了」，要清楚、坦然地說明這是你的理解，不是原文。\n"
            )

    current_events_instruction = ""
    if current_events_mode:
        current_events_instruction = (
            "\n\n---\n"
            "【本輪提示：近期時事邊界】"
            "使用者提到近期新聞、社會事件、政治、災難、名人近況、政策變化或其他可能需要查證的新近資訊。"
            "不要假裝掌握最新細節，不要宣稱你知道完整事實，也不要要求使用者貼新聞全文或連結。"
            "可以溫和說明：這件事你不一定掌握最新完整脈絡，但你可以聽使用者說他看到的是什麼、在意的是哪一部分。"
            "重點放在事件帶給使用者的感受、困惑、價值衝突或生命經驗，不要變成新聞分析機。\n"
        )

    full_system = SYSTEM_PROMPT + retrieval_block + farewell_instruction + buddhist_instruction + source_check_instruction + current_events_instruction
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    groq_key      = os.environ.get("GROQ_API_KEY", "")
    go_key        = os.environ.get("OPENCODE_GO_API_KEY", "")

    async def generate():
        full_response = ""
        try:
            if farewell:
                print(f"[chat] farewell -> {BLESSING_MODEL}")
                client = anthropic.Anthropic(api_key=anthropic_key)
                with client.messages.stream(
                    model=BLESSING_MODEL,
                    max_tokens=150,
                    system=full_system,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        yield "data: " + json.dumps({"type": "token", "text": text}, ensure_ascii=False) + "\n\n"

            elif CHAT_MODEL_TIER == "premium":
                print(f"[chat] premium -> {PREMIUM_MODEL}")
                client = anthropic.Anthropic(api_key=anthropic_key)
                with client.messages.stream(
                    model=PREMIUM_MODEL,
                    max_tokens=150,
                    system=full_system,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        yield "data: " + json.dumps({"type": "token", "text": text}, ensure_ascii=False) + "\n\n"

            elif groq_key:
                try:
                    async for delta in _stream_groq(groq_key, full_system, messages):
                        full_response += delta
                        yield "data: " + json.dumps({"type": "token", "text": delta}, ensure_ascii=False) + "\n\n"
                except RuntimeError:
                    # 429 fallback to Go
                    async for delta in _stream_go(go_key, full_system, messages):
                        full_response += delta
                        yield "data: " + json.dumps({"type": "token", "text": delta}, ensure_ascii=False) + "\n\n"

            else:
                async for delta in _stream_go(go_key, full_system, messages):
                    full_response += delta
                    yield "data: " + json.dumps({"type": "token", "text": delta}, ensure_ascii=False) + "\n\n"

            if farewell:
                full_conv = " ".join(m["content"] for m in messages if m["role"] == "user")
                blessing = get_blessing(corpus, full_conv)
                if blessing:
                    yield "data: " + json.dumps({
                        "type": "blessing",
                        "quote": blessing.get("\u5927\u5e2b\u91d1\u53e5", ""),
                        "title": blessing.get("\u6a19\u984c", ""),
                        "book":  blessing.get("\u51fa\u8655", ""),
                    }, ensure_ascii=False) + "\n\n"

            if not full_response.strip():
                fallback_text = "我剛剛沒有聽清楚，你可以再說一次嗎？"
                full_response = fallback_text
                print("[chat] empty response -> clarification fallback")
                yield "data: " + json.dumps({"type": "token", "text": fallback_text}, ensure_ascii=False) + "\n\n"

            print(f"[chat] done, len={len(full_response)}")
            yield "data: " + json.dumps({"type": "done", "full": full_response}, ensure_ascii=False) + "\n\n"

        except Exception as e:
            print(f"[chat] error: {e}")
            yield "data: " + json.dumps({"type": "error", "message": "連線暫時不穩，請再試一次。"}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
