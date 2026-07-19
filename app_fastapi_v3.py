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

APOLOGY_TERMS = ["對不起", "抱歉"]

# 使用者核定的「沒把握時」標準誠實回應——不管是被追問出處查無所獲、
# 還是使用者主動要求一句話卻查不到夠貼近的內容，只要沒把握，
# 一律用同一套講法，不即興編一個聽起來完整但沒查證過的答案。
NO_CONFIDENT_SOURCE_REPLY = (
    "我這一刻不能完全確定大師或佛經的原話怎麼說，"
    "但是我從大師這邊熏習所受到的啟發已融匯在我的生活中，"
    "因此如您願意，我也很樂意跟你分享我自己學到的觀點。"
)

def _find_bracket_citations(text: str) -> list[str]:
    return re.findall(r"《([^》]{1,30})》", text)

# 沒有真的查證過的時候，絕對不准把話算在大師頭上——不管有沒有帶書名號都不行。
# 這些是「以大師為主詞、宣稱這是他的立場」的講法，例如「大師常說……」「大師教導的精神是……」。
# 使用者明確要求：善緣沒有資格在沒把握時自己詮釋大師的精神是什麼，
# 沒把握就只能講「這是我自己耳濡目染學到的心得」，不能講「大師怎麼說／大師的教導是」。
MASTER_ATTRIBUTION_PATTERN = re.compile(
    r"大師.{0,12}(常說|說過|曾說|教導的精神|教導我們|一向強調|一向認為|一向主張|的教導是|的精神是|認為|主張)"
)

def validate_buddhist_reply(text: str, verified_sources: list[str]) -> str | None:
    """檢查善緣這一輪的回覆有沒有違反「不道歉、不編造出處、不在沒把握時替大師發言」的硬規則。
    `verified_sources` 是這一輪所有「系統真的查證過」的出處字串（可能同時來自
    「事後被追問查證」的 verify_source() 和「主動要求引言」的 _best_corpus_match()
    兩條路徑，兩者都算數，只要有一個對得上就放行）。
    回傳違規描述字串；None 代表通過檢查。
    這是程式碼層級的最後一道防線——prompt 指令對 LLM 只是機率性的建議，
    真實測試證實光靠文字規則沒辦法讓模型 100% 遵守，需要用程式碼強制擋下來。"""
    if any(w in text for w in APOLOGY_TERMS):
        return "含道歉語"
    citations = _find_bracket_citations(text)
    for c in citations:
        # 只有這輪系統真的查證到的出處，才准許在回覆裡用《書名》的形式點名，
        # 其他一律視為未經查證、可能是幻覺出來的出處。
        if any(vs and (c in vs or vs in c) for vs in verified_sources):
            continue
        return f"引用了未經查證的出處《{c}》"
    # 沒有任何這輪查證過的出處時，不准出現「大師常說／大師教導的精神是」這類
    # 把話算在大師頭上的講法——就算沒有帶書名號，這也是在沒把握時替大師發言。
    if not verified_sources and MASTER_ATTRIBUTION_PATTERN.search(text):
        return "沒有查證卻用「大師常說／教導」這類講法替大師發言"
    return None

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
    # 使用者不一定會直接問「出處」，更常見的是指出前後矛盾/簡化，
    # 這些也是同一類「在追問剛剛那句話有沒有真的把握」的訊號，
    # 沒收錄的話 is_source_check_question 會漏判，模型就得不到
    # 「誠實澄清、不要道歉」的本輪提示，容易連續道歉、更顯得不可信。
    "你上面說", "你剛剛說", "你剛才說", "這樣簡化", "這樣詮釋",
    "跟你剛才", "跟你剛剛", "矛盾", "不一致", "怎麼可以這樣",
]

def is_source_check_question(text: str) -> bool:
    return any(w in text for w in SOURCE_CHECK_TERMS)

# 門檻依實測校準：近乎逐字引用的分數約 50，主題相近但沒有真的引用的鬆散比喻約 13。
# 25 落在中間、偏保守——寧可少報一點出處（誠實說是自己的理解），也不要對沒有真正引用的內容自信地報錯出處。
SOURCE_CHECK_THRESHOLD = 25

def _best_corpus_match(corpus: pd.DataFrame, query_text: str) -> tuple[int, dict | None]:
    """通用比對：給一段查詢文字，回傳語料庫裡分數最高的那一筆和分數。
    `verify_source()`（查證善緣上一輪實際講的話）和「使用者主動要求一句相關的話」
    這兩種情境，本質上都是同一個「這段文字跟語料庫哪一筆最貼近、貼近到什麼程度」的問題，
    共用同一套比對邏輯。"""
    if corpus.empty or not query_text:
        return 0, None
    q_tokens = Counter(tokenize(query_text))
    if not q_tokens:
        return 0, None
    best_score, best_idx = 0, None
    for idx, row in corpus.iterrows():
        doc = f"{row.get('標題','')} {row.get('大師金句','')} {row.get('具體故事','')} {row.get('善緣陪伴語','')}"
        d_tokens = Counter(tokenize(doc))
        score = sum(min(q_tokens[t], d_tokens[t]) for t in q_tokens if t in d_tokens)
        if score > best_score:
            best_score, best_idx = score, idx
    if best_idx is None:
        return best_score, None
    return best_score, corpus.iloc[best_idx].to_dict()

def verify_source(corpus: pd.DataFrame, prior_reply: str) -> dict | None:
    """針對善緣上一輪實際講的話，重新查一次語料庫，回傳最相關的那筆（含比對分數），
    分數不夠高就當作查無確切出處，不能讓模型自己憑印象聲稱出處。"""
    score, row = _best_corpus_match(corpus, prior_reply)
    if row is None or score < SOURCE_CHECK_THRESHOLD:
        return None
    return row

QUOTE_REQUEST_TERMS = [
    "大師的話", "大師說過", "大師的原話", "佛經", "經文", "有沒有相關的話",
    "給我一句", "大師怎麼說", "有沒有大師", "有沒有經典", "有沒有相關的開示",
    "跟我的情況有關的話", "有沒有一句話", "有沒有什麼話", "有沒有教導",
]

def is_quote_request(text: str) -> bool:
    """判斷使用者是不是主動要求一句跟他情況有關、大師或佛經的具體話語
    （不是在追問善緣剛才講的那句話出處，是主動點名想要一句「新的」引言）。"""
    return any(w in text for w in QUOTE_REQUEST_TERMS)

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
        "【重要：只有兩種情況可以講話】每次要提到大師的想法或佛法內容之前，先判斷這是哪一種，不要有第三種模糊地帶："
        "(1) 有絕對把握——這句話的內容跟上面參考語料某一筆金句/陪伴語幾乎一致（不是主題相近，是內容真的對得起來）→ "
        "才可以說「大師在《出處》裡提到『……』」，出處要照原字精準講，例如「如是說4」不能模糊成「如是說等書」；"
        "(2) 除此之外的所有情況（包含你自己對「大師的精神方向、大師會怎麼看」的猜測、推論、詮釋）→ "
        "一律不准把話算在大師頭上，不管有沒有加引號都不行——不要說「大師常說……」「大師教導的精神是……」"
        "「大師的教導裡有一種方向是……」「大師一向強調……」這類把大師當作主詞、宣稱這是他的立場的講法。"
        "你自己沒有資格替大師詮釋他的精神是什麼；你有把握的只有「這是我自己耳濡目染、學習體認到的心得」這件事本身。"
        "第二種情況一律用第一人稱、為自己的話負責，例如「我從大師這裡耳濡目染學到的是……」「我自己的體會是……」"
        "「受大師啟發，我覺得……」，不要包裝成大師說的話，也不要用「大師教導我們」這種聽起來像在轉述他人立場的講法。"
        "拿不準到底是 (1) 還是 (2) 的時候，一律當作 (2) 處理：先誠實講成自己的體會，"
        "不要先用聽起來很肯定的句子起頭、等被追問了才改口——這個判斷要在你講出這句話的當下就做好，不是等被問才決定。"
        "這樣之後不管有沒有被追問「這真的是大師說的嗎、哪裡來的、你怎麼可以這樣簡化」，你都只是把原本就講清楚的立場再說一次，"
        "不會有「其實我不確定、我記錯了、對不起我沒做好」這種前後不一致、顯得不可信的情況——"
        "被質疑當下才承認不知道，是最傷信任感的事，絕對要避免，也絕對不要編造一個聽起來像真的、但其實不存在的出處。"
        "如果真的不小心說得太肯定、被指出來了，誠實澄清就好，不要道歉——這不是你做錯了什麼要道歉，是誠實表達話語的性質。\n"
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
            "可以用「我從大師的話裡學到……」「我自己耳濡目染體認到的是……」這類熟悉、貼近、但為自己的話負責的語氣，"
            "不要用「大師常說……」這種把話算在大師頭上的講法，除非你真的有把握（見下面『出處查證』的規則）——"
            "沒把握的時候，你自己沒有資格替大師詮釋他的精神或立場是什麼，只能誠實說這是你自己的體會。"
            "同時要謙遜，不要講得像佛學權威或什麼都懂，也不要把話說死、下武斷的結論，保留「這是我的理解，你的體會也可以不一樣」的空間。"
            "不要說「你應該」，不要替使用者做修行判斷，不要把戒律或佛法拿來責備人。"
            "如果談到五戒、八關齋戒、菩薩戒等戒法，可以簡明說明，但要提醒這是一起理解，不是要求對方採納。"
            "結尾仍把空間還給使用者，用一句短問題邀請他繼續說。\n"
        )

    # 原本只在 is_source_check_question(user_text) 命中關鍵字時才查證，
    # 但真實對話（尤其語音辨識後的文字，常有錯字、換句話說、跟原本關鍵字表對不上）
    # 證實這個關鍵字表會漏判：使用者換一種問法、或 STT 把字認錯，
    # 查證就完全不會被觸發，模型會在沒有任何查證結果可用的情況下，
    # 自己「補」出一個聽起來合理但其實是編的書名（例如《人間佛教》系列、《佛光菜根譚》），
    # 而且會一路編下去、越編越具體。
    # 改成只要在 buddhist_mode，不管使用者這輪問法有沒有命中關鍵字，
    # 每一輪都用善緣上一輪實際講的內容重新查一次語料庫，
    # 讓模型每一輪都有真實的查證結果可以依據，沒有機會空手编造。
    source_check_instruction = ""
    verified: dict | None = None
    if buddhist_mode:
        prior_assistant = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), "")
        verified = verify_source(corpus, prior_assistant) if prior_assistant else None
        asked_directly = "使用者在追問你上一句話的出處，" if is_source_check_question(user_text) else ""
        if verified:
            v_source = verified.get("出處", "")
            v_quote = verified.get("大師金句", "")[:80]
            source_check_instruction = (
                "\n\n---\n"
                f"【本輪提示：出處查證】{asked_directly}系統剛用你上一輪實際講的內容重新比對了語料庫，"
                f"查到高度相關的一筆：出處「{v_source}」，原句「{v_quote}」。"
                f"如果要講出處，只能講這個查到的「{v_source}」，不要換一個沒有查證過的說法或書名。\n"
            )
        else:
            source_check_instruction = (
                "\n\n---\n"
                f"【本輪提示：出處查證】{asked_directly}系統剛用你上一輪實際講的內容重新比對了語料庫，"
                "沒有查到夠相關、可以精準引用的出處。接下來如果要提到這件事，只能誠實告訴使用者：這是你自己對大師思想的理解或歸納，不是逐字引用某一本書。"
                "絕對不要為了讓回答聽起來更完整、更有根據，就另外編一個書名、經名或系列名稱"
                "（例如「人間佛教系列」「佛光菜根譚」之類——除非它就是上面『參考語料』裡真的列出來的出處，否則不要講出任何具體書名），"
                "沒有查到就是沒有查到，不要生出一個聽起來合理但其實是編的出處，也不要一輪一輪把細節越編越具體。"
                "用坦然、平靜的語氣講清楚就好，不要說「不記得」「記錯了」，也不要說「抱歉」「對此我感到抱歉」「未能提供更精確的說明」這類道歉語——"
                "這不是你做錯了什麼要道歉，是誠實告訴對方這句話的性質，講完直接接回對話即可。\n"
            )

    # 上面的 source_check_instruction 是「事後被追問」的被動情境；
    # 這裡是使用者「主動要求」給他一句跟他情況有關、大師或佛經的具體話語，
    # 是可以正面滿足他期待的機會——如果語料庫裡真的查得到夠貼近的內容，
    # 就鼓勵善緣自然地引用出來；查不到的話，不要為了滿足期待硬套一句不貼切
    # 或自己編的話，改用使用者核定過的標準誠實回應。
    quote_request_instruction = ""
    q_match: dict | None = None  # 只在分數夠高（真的查到）時才會賦值，供下面驗證白名單使用
    if buddhist_mode and is_quote_request(user_text):
        q_score, q_best = _best_corpus_match(corpus, recent_text)
        if q_best and q_score >= SOURCE_CHECK_THRESHOLD:
            q_match = q_best
            q_source = q_match.get("出處", "")
            q_quote = q_match.get("大師金句", "")[:80]
            quote_request_instruction = (
                "\n\n---\n"
                "【本輪提示：使用者要求具體的話語】使用者想要一句真的跟他的情況有關、大師或佛經的原話。"
                f"系統剛查過語料庫，找到一筆把握夠高、貼近他情況的內容：出處「{q_source}」，原句「{q_quote}」。"
                f"請自然地把這句話帶進你的回應裡，明確講出處「{q_source}」，讓使用者感覺到你是真的找到了適合他的話，不要含糊帶過。\n"
            )
        else:
            quote_request_instruction = (
                "\n\n---\n"
                "【本輪提示：使用者要求具體的話語】使用者想要一句真的跟他的情況有關、大師或佛經的原話，"
                "但系統剛查過語料庫，沒有查到把握夠高、真的貼近他這個具體情況的原句。"
                "這種時候不要為了滿足他的期待，就硬套一句不夠貼切、或自己編的話。"
                "請照這個方式誠實回應（語氣可以自然調整，但意思要一致，不要加道歉語）："
                f"「{NO_CONFIDENT_SOURCE_REPLY}」\n"
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

    full_system = SYSTEM_PROMPT + retrieval_block + farewell_instruction + buddhist_instruction + source_check_instruction + quote_request_instruction + current_events_instruction
    # 這一輪所有「系統真的查證過」的出處，供 validate_buddhist_reply() 當白名單：
    # 不管善緣是在回答「被追問出處」還是「主動要求引言」，只要引用的書名對得上
    # 其中任何一筆，就代表這是有憑有據的引用，不是幻覺。
    verified_sources = [v.get("出處", "") for v in (verified, q_match) if v]
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    groq_key      = os.environ.get("GROQ_API_KEY", "")
    go_key        = os.environ.get("OPENCODE_GO_API_KEY", "")

    async def _collect_llm_response(system: str) -> str:
        """非串流版本：跑一次模型選擇 cascade（premium／groq／go 備援），
        把完整回覆收集成一個字串後回傳，不逐 token yield 給前端。
        給需要「先驗證內容、再決定要不要送到使用者面前」的情境用
        （目前只有 buddhist_mode 的道歉語／未經查證出處檢查）。"""
        text = ""
        if CHAT_MODEL_TIER == "premium":
            client = anthropic.Anthropic(api_key=anthropic_key)
            with client.messages.stream(
                model=PREMIUM_MODEL, max_tokens=150, system=system, messages=messages,
            ) as stream:
                for chunk in stream.text_stream:
                    text += chunk
        elif groq_key:
            try:
                async for delta in _stream_groq(groq_key, system, messages):
                    text += delta
            except RuntimeError:
                async for delta in _stream_go(go_key, system, messages):
                    text += delta
        else:
            async for delta in _stream_go(go_key, system, messages):
                text += delta
        return text

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

            elif buddhist_mode and is_quote_request(user_text) and q_match is None:
                # 使用者明確要求一句跟他情況有關的大師／佛經話語，但系統剛查過語料庫，
                # 沒有查到把握夠高的內容。這是一個範圍很窄、能明確判定的情境，
                # 真實測試證實就算 prompt 裡已經附上這句標準回應要模型照講，
                # 模型還是常常自己另外發揮、講一句沒有真的引用、卻也沒有清楚交代
                # 「沒把握」這件事的模糊話——不算違反道歉／編造出處的硬規則，
                # 但沒有達到使用者要的效果。這裡乾脆不呼叫 LLM，直接用使用者
                # 核定過的標準回應，保證每次都是同一句話，也省了一次生成的延遲。
                print("[chat][buddhist] 主動要求引言但查無把握，直接用標準誠實回應（不呼叫 LLM）")
                full_response = NO_CONFIDENT_SOURCE_REPLY
                yield "data: " + json.dumps({"type": "token", "text": full_response}, ensure_ascii=False) + "\n\n"

            elif buddhist_mode:
                # 佛法討論模式：這裡是「道歉語／編造出處」問題實際發生的地方，
                # 真實測試證實光靠 prompt 文字規則沒辦法讓模型 100% 遵守。
                # 改成先在後端把完整回覆收集起來、跑 validate_buddhist_reply() 驗證過，
                # 不合格就用更嚴格的糾正提示重新生成一次，兩次都不合格就換保底的
                # 誠實版本——寧可犧牲一點逐字即時感（語音模式本來就是整段合成才播放，
                # 不受影響；文字模式會從「一個字一個字跳出來」變成「整段一次出現」），
                # 也不能讓編造出處或連續道歉的內容真的送到使用者面前。
                full_response = await _collect_llm_response(full_system)
                violation = validate_buddhist_reply(full_response, verified_sources) if full_response.strip() else None
                if violation:
                    print(f"[chat][buddhist] 第一次回覆違規（{violation}），重新生成一次")
                    corrected_system = full_system + (
                        "\n\n---\n【系統糾正】你剛剛的草稿回覆有問題："
                        f"{violation}。請重新回答一次：這次絕對不要出現「對不起」「抱歉」這類道歉語，"
                        "也絕對不要點名任何沒有在上面『出處查證』或『參考語料』裡出現過的書名、經名、出處，"
                        "也絕對不要用「大師常說……」「大師教導的精神是……」這類把話算在大師頭上的講法——"
                        "除非上面的『出處查證』真的給你一筆查到的出處，否則一律改用「我從大師這裡耳濡目染學到的是……」"
                        "「我自己的體會是……」這種為自己的話負責的第一人稱說法。\n"
                    )
                    full_response = await _collect_llm_response(corrected_system)
                    violation = validate_buddhist_reply(full_response, verified_sources) if full_response.strip() else None
                if violation:
                    print(f"[chat][buddhist] 第二次仍違規（{violation}），改用保底誠實版本")
                    full_response = NO_CONFIDENT_SOURCE_REPLY
                if full_response:
                    yield "data: " + json.dumps({"type": "token", "text": full_response}, ensure_ascii=False) + "\n\n"

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
