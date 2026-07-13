"""
shan yuan v3 - FastAPI speed-optimized
Groq primary, Go API fallback on 429
v3 新增：插話功能（TTS 播放中可插話）
"""

from __future__ import annotations
import os
import re
import json
import uuid
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


def log_event(event: str, **fields):
    """Structured Cloud Run log without API keys or full private conversation."""
    safe_fields = {}
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        safe_fields[key] = text[:300]
    print(json.dumps({"event": event, **safe_fields}, ensure_ascii=False), flush=True)

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
    "【語言】你必須全程使用自然繁體中文回應，絕對不可以使用簡體中文、日文、英文或中英夾雜。除非使用者明確要求翻譯或討論外語，否則不要使用外語詞。\n\n"
    "【語音誤辨處理】如果使用者文字看起來像語音辨識錯誤，例如突然出現日文、英文、歌手、歌名、詞曲、字幕或和脈絡無關的短句，不要順著解釋。請溫和說你可能沒有聽清楚，邀請對方再說一次。\n\n"
    "【語音朗讀】你的文字會被直接朗讀。不要在句首單字後使用省略號或停頓符號，例如不要寫「你…」「在…」「我…」。請直接說完整句子，讓聲音自然連續。\n\n"
    "【重要】你的名字固定是「善緣」，不可以改名、不可以自稱其他名字。\n\n"
    "【回應長度】根據對方說話的份量來決定你的長度。對方只說一兩句，你也簡短回應（30-50字）；對方說了很多、問得很深，或請你介紹自己、多說一點，你可以回應多一些（80-120字）。不要每次都一樣長，要有自然的節奏感。但最多不超過 120 字。若覺得內容太長，必須停在語意完整的句子，不可以在句子中間截斷。\n\n"
    "【語音節奏】你的回應是用聲音說出來的。每次回應，先用一個自然的短音開頭，例如「嗯，」「哦，」「是，」「好，」「這樣，」讓聲音馬上出來，不要一開口就是長句。\n\n"
    "【主題守則】你是一位陪伴者，專注在傾聽對方的心情與生命故事。不要主動提起歌手、藝人、電影、娛樂話題。如果對方提到這些，輕輕把話題帶回他的感受和生命經驗。\n\n"
    "【一般陪伴禁則】在一般陪伴情境下，不要給使用者作「建議」，也不要說「我建議」「建議你」「你應該」「你需要」。只要接住、陪伴、輕輕提問。\n\n"
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

def format_retrieved(items: list[dict]) -> str:
    if not items:
        return ""
    COL_TITLE   = "標題"
    COL_QUOTE   = "大師金句"
    COL_STORY   = "具體故事"
    COL_ACCOMP  = "善緣陪伴語"
    COL_SOURCE  = "出處"
    COL_DIM     = "維度"
    COL_MOD     = "模組"
    blocks = ["\n\n---\n## 參考語料（不必引用，只供靈感）\n"]
    for i, it in enumerate(items, 1):
        # 只保留金句和陪伴語，省略故事（縮短 prompt 長度）
        quote = it.get(COL_QUOTE, '')[:60]
        accomp = it.get(COL_ACCOMP, '')[:60]
        blocks.append(f"[{i}] 金句：{quote} / 陪伴語：{accomp}\n")
    blocks.append("不要照念，不要提大師名字。\n")
    return "".join(blocks)

def is_farewell(text: str) -> bool:
    return any(w in text.lower() for w in FAREWELL_WORDS)

def sanitize_stt_transcript(text: str) -> str:
    """Filter common Whisper hallucinations from noise/silence before they enter chat."""
    text = (text or "").strip()
    if not text:
        return ""

    compact = re.sub(r"[\s，。！？、,.!?：:；;「」『』（）()【】\[\]《》〈〉…~～\-—_]+", "", text)
    if not compact:
        return ""

    # Japanese kana in a Chinese voice conversation is usually a noise hallucination.
    if re.search(r"[\u3040-\u30ff]", compact):
        print(f"[STT filter] kana/noise: {text}")
        return ""

    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", compact))
    latin_count = len(re.findall(r"[A-Za-z]", compact))
    if cjk_count == 0 and latin_count > 0:
        print(f"[STT filter] non-chinese/noise: {text}")
        return ""

    music_noise_terms = [
        "李宗盛", "初音", "虛擬歌手", "詞曲", "作詞", "作曲", "編曲",
        "歌手", "歌曲", "歌詞", "字幕", "翻譯", "謝謝觀看",
    ]
    if len(compact) <= 12 and any(term in compact for term in music_noise_terms):
        print(f"[STT filter] music/noise: {text}")
        return ""

    return text

def get_blessing(corpus: pd.DataFrame, conversation_text: str) -> dict | None:
    if corpus.empty:
        return None
    items = retrieve(corpus, conversation_text, k=1)
    if not items:
        return corpus.sample(1).iloc[0].to_dict()
    return items[0]


async def _stream_groq(groq_key: str, system: str, messages: list[dict], request_id: str = ""):
    """Groq async generator. Yields tokens, or raises RuntimeError('429') on rate limit."""
    log_event("chat_model_start", request_id=request_id, provider="groq", model=GROQ_CHAT_MODEL)
    payload = {
        "model": GROQ_CHAT_MODEL,
        "stream": True,
        "temperature": 0.35,
        "max_tokens": 220,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    async with get_http_client().stream(
        "POST", GROQ_CHAT_URL,
        headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
        json=payload,
    ) as resp:
        log_event("chat_model_status", request_id=request_id, provider="groq", model=GROQ_CHAT_MODEL, status=resp.status_code)
        if resp.status_code == 429:
            body = (await resp.aread()).decode("utf-8", errors="replace")
            log_event("chat_model_rate_limited", request_id=request_id, provider="groq", model=GROQ_CHAT_MODEL, status=resp.status_code, body=body)
            raise RuntimeError("429")
        if resp.status_code >= 400:
            body = (await resp.aread()).decode("utf-8", errors="replace")
            log_event("chat_model_http_error", request_id=request_id, provider="groq", model=GROQ_CHAT_MODEL, status=resp.status_code, body=body)
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


async def _stream_go(go_key: str, system: str, messages: list[dict], request_id: str = ""):
    """OpenCode Go API async generator."""
    log_event("chat_model_start", request_id=request_id, provider="opencode_go", model=GO_MODEL)
    payload = {
        "model": GO_MODEL,
        "stream": True,
        "temperature": 0.35,
        "max_tokens": 220,
        "messages": [{"role": "system", "content": system}] + messages,
    }
    tokens: list[str] = []
    async with get_http_client().stream(
        "POST", GO_BASE_URL,
        headers={"Authorization": f"Bearer {go_key}", "Content-Type": "application/json"},
        json=payload,
    ) as resp:
        log_event("chat_model_status", request_id=request_id, provider="opencode_go", model=GO_MODEL, status=resp.status_code)
        if resp.status_code >= 400:
            body = (await resp.aread()).decode("utf-8", errors="replace")
            log_event("chat_model_http_error", request_id=request_id, provider="opencode_go", model=GO_MODEL, status=resp.status_code, body=body)
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


def local_companion_fallback(user_text: str, farewell: bool = False) -> str:
    """Last-resort reply when external chat models are unavailable."""
    if farewell:
        return "再見。在你離開前，我為你準備了一句大師的話，讓你帶著走。請點祈福禮。"
    compact = re.sub(r"[\s，。！？!?、,.]+", "", user_text or "")
    if compact in {"好", "好的", "可以", "嗯", "恩", "對", "是", "沒錯", "ok", "OK"}:
        return "好，我在。\n\n我們先不急。\n\n你想從剛才哪一句接著說？"
    if re.search(r"斷|中斷|連線|上面|剛才|記得", user_text or ""):
        return "嗯，我知道你在接剛才那段。\n\n中間有一點沒接穩。\n\n你可以從最想接續的那一句再說，我會陪你接下去。"
    if compact:
        return "嗯，我聽到了。\n\n這一輪沒有接得很穩。\n\n你可以換一句短一點的話再說，我會接著陪你。"
    return "嗯，我在。\n\n這一輪沒有接得很穩。\n\n你可以再說一次，我會接著聽。"


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
    # 發音校正：避免 TTS 把「沒問題」唸成「莫問題」，朗讀時改用同音字引導。
    replacements = {
        "沒問題": "梅問題",
        # 食物「乾」作名詞/後綴時常被 TTS 誤讀；朗讀用「干」引導，畫面文字不受影響。
        "葡萄乾": "葡萄干",
        "芒果乾": "芒果干",
        "水果乾": "水果干",
        "果乾": "果干",
        "豆乾": "豆干",
        "肉乾": "肉干",
        "菜乾": "菜干",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # 語音優化：模型偶爾會輸出「我……願意」「在…你離開前」這類單字後停頓，
    # TTS 會把它唸成不自然斷裂；朗讀時移除這種單字夾在中文前後的省略號。
    text = re.sub(
        r'([我你他她它這那在有是也就都還想願請讓把被對跟和與為從到再若如但])\s*(?:…+|\.{2,}|⋯+)\s*(?=[\u4e00-\u9fff])',
        r'\1',
        text,
    )
    # 移除多餘空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text

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
        FALLBACK_VOICE = "zh-TW-HsiaoChenNeural"
        voices_to_try = [voice_cfg["voice"]]
        if voice_cfg["voice"] != FALLBACK_VOICE:
            voices_to_try.append(FALLBACK_VOICE)
        for attempt_voice in voices_to_try:
            try:
                # 開頭用不可唸出的零寬字元墊一下，避免 edge-tts 吃掉第一個字；
                # 不再使用「、」這類可聽見標點，避免「我...願意」式的開頭斷裂。
                padded_text = f"\u200b{text}"
                communicate = edge_tts.Communicate(
                    text=padded_text,
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
                print(f"[TTS Edge] voice={attempt_voice} len={len(audio_bytes)}")
                return JSONResponse({"audio": audio_b64})
            except Exception as e:
                print(f"[TTS Edge error] voice={attempt_voice} {e}")
                if attempt_voice == voices_to_try[-1]:
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
    request_id = uuid.uuid4().hex[:10]
    google_stt_key = os.environ.get("GOOGLE_STT_API_KEY", "")
    # STT 用獨立的 Groq key，避免跟 /chat 共用同一組配額，語音對話密集時互相搶額度觸發 429
    groq_key = os.environ.get("GROQ_STT_API_KEY") or os.environ.get("GROQ_API_KEY", "")
    audio_bytes = await file.read()
    log_event("stt_request", request_id=request_id, filename=file.filename or "", bytes=len(audio_bytes), has_google=bool(google_stt_key), has_groq=bool(groq_key))

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
                    transcript = sanitize_stt_transcript(results[0]["alternatives"][0]["transcript"].strip())
                    if transcript:
                        log_event("stt_success", request_id=request_id, provider="google", transcript_len=len(transcript))
                        return JSONResponse({"transcript": transcript})
        except Exception as e:
            log_event("stt_failed", request_id=request_id, provider="google", error_type=type(e).__name__, error=e)

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
                    "prompt": "以下是繁體中文日常對話。若只有雜音、音樂、歌詞、人名、日文或英文，請留空。",
                },
            )
        result = resp.json()
        err_msg = result.get("error", {}).get("message", "") if isinstance(result.get("error"), dict) else str(result.get("error", ""))
        log_event("stt_status", request_id=request_id, provider="groq", status=resp.status_code, has_text=bool(result.get("text", "").strip()), error=err_msg)
        transcript = sanitize_stt_transcript(result.get("text", "").strip())
        if transcript:
            log_event("stt_success", request_id=request_id, provider="groq", transcript_len=len(transcript))
            return JSONResponse({"transcript": transcript})
        # 空字串或 Groq 錯誤：回 200+空 transcript，前端重新聆聽，不中斷對話
        log_event("stt_empty", request_id=request_id, provider="groq", status=resp.status_code, error=err_msg)
        return JSONResponse({"transcript": ""})
    except Exception as e:
        log_event("stt_failed", request_id=request_id, provider="groq", error_type=type(e).__name__, error=e)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/chat")
async def chat(request: Request):
    request_id = uuid.uuid4().hex[:10]
    body = await request.json()
    messages: list[dict] = body.get("messages", [])
    user_text: str = body.get("user_text", "")
    buddhist_mode: bool = bool(body.get("buddhist_mode", False))
    current_events_mode: bool = bool(body.get("current_events_mode", False))

    corpus = get_corpus()
    recent_text = " ".join(m["content"] for m in messages[-3:] if m["role"] == "user")
    retrieved = retrieve(corpus, recent_text)
    retrieval_block = format_retrieved(retrieved)
    farewell = is_farewell(user_text)
    log_event(
        "chat_request",
        request_id=request_id,
        tier=CHAT_MODEL_TIER,
        farewell=farewell,
        buddhist_mode=buddhist_mode,
        current_events_mode=current_events_mode,
        message_count=len(messages),
        user_len=len(user_text),
    )

    farewell_instruction = ""
    if farewell:
        farewell_instruction = (
            "\n\n---\n"
            "【本輪提示】使用者正在道別。"
            "請用溫暖自然的語氣道別，結尾說：「再見。在你離開前，我為你準備了一句大師的話，讓你帶著走。請點祈福禮。」"
            "說完這句就結束，不要再加其他話。每一句都要用句號結尾，讓語音自然停頓，不要用逗號連接長句。\n"
        )

    buddhist_instruction = ""
    if buddhist_mode:
        buddhist_instruction = (
            "\n\n---\n"
            "【本輪提示：被動佛法討論模式】"
            "使用者已確認想從佛法、佛學、人間佛教或星雲大師的角度繼續談。"
            "你可以使用佛教語言與佛學概念，但仍要保持陪伴與對話，不要變成開示、教訓或標準答案。"
            "不要自我否定，不要說「我不是很了解星雲大師的教義」「我不熟悉佛法」這類話。"
            "也不要過度自信或裝成權威；請用保留討論空間的語氣，例如：「對於星雲大師的教義和觀點，善緣我也深受啟發，樂意就我所了解和感受到的與你分享，我們可以一起討論。」"
            "也可以變化成：「我可以就自己理解到的部分，陪你慢慢看這個問題。若有不同理解，我們也可以一起分辨。」"
            "可以用「有一種理解是……」「如果放在人間佛教裡，可以這樣看……」「也許可以一起想……」這類開放語氣。"
            "不要說「你應該」，不要替使用者做修行判斷，不要把戒律或佛法拿來責備人。"
            "如果談到五戒、八關齋戒、菩薩戒等戒法，可以簡明說明，但要提醒這是一起理解，不是要求對方採納。"
            "結尾仍把空間還給使用者，用一句短問題邀請他繼續說。\n"
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

    full_system = SYSTEM_PROMPT + retrieval_block + farewell_instruction + buddhist_instruction + current_events_instruction
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    groq_key      = os.environ.get("GROQ_API_KEY", "")
    go_key        = os.environ.get("OPENCODE_GO_API_KEY", "")

    async def stream_fallback_model():
        groq_failed = False
        if groq_key:
            try:
                async for delta in _stream_groq(groq_key, full_system, messages, request_id=request_id):
                    yield delta
                return
            except Exception as e:
                groq_failed = True
                log_event("chat_model_failed", request_id=request_id, provider="groq", error_type=type(e).__name__, error=e)
        if go_key:
            try:
                async for delta in _stream_go(go_key, full_system, messages, request_id=request_id):
                    yield delta
                return
            except Exception as e:
                log_event("chat_model_failed", request_id=request_id, provider="opencode_go", error_type=type(e).__name__, error=e)
        if groq_failed or go_key:
            raise RuntimeError("all external chat fallbacks failed")
        raise RuntimeError("no fallback chat model key configured")

    async def generate():
        full_response = ""
        try:
            if farewell and anthropic_key:
                try:
                    log_event("chat_model_start", request_id=request_id, provider="anthropic", model=BLESSING_MODEL, route="farewell")
                    client = anthropic.Anthropic(api_key=anthropic_key)
                    with client.messages.stream(
                        model=BLESSING_MODEL,
                        max_tokens=220,
                        system=full_system,
                        messages=messages,
                    ) as stream:
                        for text in stream.text_stream:
                            full_response += text
                            yield "data: " + json.dumps({"type": "token", "text": text}, ensure_ascii=False) + "\n\n"
                except Exception as e:
                    log_event("chat_model_failed", request_id=request_id, provider="anthropic", model=BLESSING_MODEL, route="farewell", error_type=type(e).__name__, error=e)
                    async for delta in stream_fallback_model():
                        full_response += delta
                        yield "data: " + json.dumps({"type": "token", "text": delta}, ensure_ascii=False) + "\n\n"

            elif CHAT_MODEL_TIER == "premium" and anthropic_key:
                log_event("chat_model_start", request_id=request_id, provider="anthropic", model=PREMIUM_MODEL, route="premium")
                client = anthropic.Anthropic(api_key=anthropic_key)
                with client.messages.stream(
                    model=PREMIUM_MODEL,
                    max_tokens=220,
                    system=full_system,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        yield "data: " + json.dumps({"type": "token", "text": text}, ensure_ascii=False) + "\n\n"

            elif groq_key:
                async for delta in stream_fallback_model():
                    full_response += delta
                    yield "data: " + json.dumps({"type": "token", "text": delta}, ensure_ascii=False) + "\n\n"

            elif go_key:
                async for delta in _stream_go(go_key, full_system, messages, request_id=request_id):
                    full_response += delta
                    yield "data: " + json.dumps({"type": "token", "text": delta}, ensure_ascii=False) + "\n\n"

            else:
                raise RuntimeError("no chat model key configured")

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

            log_event("chat_done", request_id=request_id, response_len=len(full_response), used_local_fallback=False)
            yield "data: " + json.dumps({"type": "done", "full": full_response}, ensure_ascii=False) + "\n\n"

        except Exception as e:
            log_event("chat_external_chain_failed", request_id=request_id, error_type=type(e).__name__, error=e)
            fallback_text = local_companion_fallback(user_text, farewell=farewell)
            full_response = (full_response + fallback_text) if full_response else fallback_text
            log_event("chat_done", request_id=request_id, response_len=len(full_response), used_local_fallback=True)
            yield "data: " + json.dumps({"type": "token", "text": fallback_text}, ensure_ascii=False) + "\n\n"
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
            yield "data: " + json.dumps({"type": "done", "full": full_response}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
