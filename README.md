<div align="center">

# 🇵🇰 Dastak — دستک
### Bloomberg for Pakistan. AI-powered PSX research in Urdu.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-agentic-blueviolet?style=flat)](https://github.com/langchain-ai/langgraph)
[![Groq](https://img.shields.io/badge/LLM-Groq%20%28free%29-F55036?style=flat)](https://groq.com)
[![PSX](https://img.shields.io/badge/Exchange-PSX-006747?style=flat)](https://www.psx.com.pk)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active%20Development-orange?style=flat)]()

</div>

---

Most retail investors in Pakistan have no easy way to research PSX-listed companies. Financial data is scattered across SECP filings, broker portals, and Urdu news sites — and none of it talks to each other. Dastak fixes that.

It is an agentic RAG system that ingests company filings, financial disclosures, and real-time news, then synthesizes auditable, citation-backed answers in Urdu and English — the way a research analyst would, not a chatbot.

> **Fully open-source and zero paid API keys required.** Dastak runs on Groq's free inference tier and open multilingual embeddings.

---

## What it does

- **Urdu-first research** — generates answers in natural Urdu, not machine-translated English
- **Multi-source ingestion** — PDFs, SECP filings, scanned announcements, and structured news in a unified pipeline
- **Auditable outputs** — every claim links back to a source document with page and paragraph reference
- **Agent orchestration** — a LangGraph supervisor routes queries to the right sub-agent (filings, news, price data) and merges results before returning
- **Zero paid keys** — runs entirely on free-tier APIs (Groq, Gemini) and local-first embeddings
- **Retail-grade trust** — designed for brokerage sales desks that need to cite sources to clients, not just summarize

---

## Architecture

```
User Query (Urdu / English)
        │
        ▼
┌───────────────────────┐
│    Query Router       │  ← LangGraph supervisor node
│  (intent detection)   │
└──────────┬────────────┘
           │
   ┌───────┴────────┬─────────────────┐
   ▼                ▼                 ▼
Filings           News &           Price &
 Agent            Sentiment         Market
                   Agent             Agent
   │                │                 │
   ▼                ▼                 ▼
SECP PDF         Async web        PSX scraper +
 RAG             scrapers         OCR pipeline
(pgvector)       + NLP            (scanned imgs)
   │                │                 │
   └────────┬───────┴─────────────────┘
            ▼
   ┌──────────────────┐
   │   Synthesizer    │  ← deduplicates, ranks, cites
   └────────┬─────────┘
            ▼
   Urdu / English answer
   with grounded source citations
```

**Core stack:**

| Layer | Technology | Why |
|---|---|---|
| Agent orchestration | LangGraph | Stateful multi-agent graphs with human-in-loop support |
| LLM (free) | Groq → Llama 3.3 70B | Fastest free inference, ~300 tokens/sec |
| LLM fallback | Google Gemini 1.5 Flash | Free 1M token context for long filings |
| Local option | Ollama + Qwen2.5 | Fully offline, no rate limits |
| Embeddings | `paraphrase-multilingual-MiniLM-L12-v2` | Free, works on Urdu + English |
| Vector store | pgvector (PostgreSQL) | Single DB for docs + metadata |
| Filing ingestion | PyMuPDF + Docling | Handles scanned PDFs, tables, mixed layouts |
| Urdu NLP | UrduHack + custom pipeline | Tokenization, normalization, Nastaliq handling |
| News pipeline | BeautifulSoup + httpx async | ARY Business, The News, Dawn, Profit.pk |
| API layer | FastAPI | Async, typed, broker-integrable |
| Cache | Redis | De-dupe repeated tickers, rate-limit PSX scrapes |

---

## PSX Intelligence Layer

Dastak includes a custom-built PSX data agent, written from scratch, that goes significantly further than existing tools:

### What it covers

| Capability | Dastak PSX Agent |
|---|---|
| Ticker resolution | Full KSE-100 + KSE-All symbol map, auto-updated |
| SECP filings | Annual reports, quarterly accounts, material information notices |
| Price data | OHLCV history, circuit breaker status, sector indices |
| Scanned announcements | OCR pipeline for image-based PSX notices (common pre-2020) |
| Urdu news | Structured ingestion from ARY Business, Dawn, The News, Profit.pk |
| Financial ratio extraction | Structured parsing of income statement, balance sheet, cash flow |
| Dividend history | Parsed from filing text + announcement table, not scraped manually |
| Ownership / shareholding | Extracted from annual report Pattern of Shareholding sections |
| Filing change detection | Diff-based alert when a new filing lands for a tracked ticker |
| Bulk corpus download | Download + index the full SECP filing archive for a sector |

### What makes it different

Most PSX data tools stop at price feeds and basic filings. Dastak's agent treats each filing as a structured document, not a flat PDF:

- **Table-aware parsing** — financial statements are extracted as structured tables, not raw text, so ratios are computed correctly
- **Cross-filing comparison** — the agent can diff two annual reports (e.g. FY2022 vs FY2023) and surface changes in revenue, debt, and margins
- **Scanned document handling** — pre-2015 filings are often scanned images; Dastak runs OCR (Tesseract + post-correction) before indexing
- **Shareholding pattern extraction** — pulls promoter, public, and institutional holdings from the legally-mandated annual report section
- **Urdu announcement parsing** — many PSX announcements are in Urdu; Dastak normalizes and indexes these, not just English ones

---

## Quickstart

```bash
git clone https://github.com/Warisha-Bilal7/dastak
cd dastak
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in free keys (Groq + Gemini only)
```

**Run the API server:**
```bash
uvicorn app.main:app --reload --port 8000
```

**Query the agent directly:**
```bash
python -m dastak.agent --query "ENGRO کا آخری سالانہ منافع کیا تھا؟" --ticker ENGRO
```

**Expected output:**
```json
{
  "answer": "انگرو کارپوریشن نے مالی سال 2023 میں 47.3 ارب روپے کا خالص منافع رپورٹ کیا، جو گزشتہ سال کے مقابلے میں 12 فیصد زیادہ ہے۔",
  "sources": [
    { "type": "filing", "doc": "ENGRO_AR_2023.pdf", "page": 38, "section": "Income Statement" },
    { "type": "news",   "url": "https://profit.pk/...", "date": "2024-02-14" }
  ],
  "confidence": 0.91,
  "ticker": "ENGRO",
  "data_freshness": "2024-02-15T09:31:00Z"
}
```

---

## Environment variables

No paid keys required. All LLMs used here have free tiers.

```env
# LLM — free tiers
GROQ_API_KEY=             # free at console.groq.com
GEMINI_API_KEY=           # free at aistudio.google.com

# Local fallback (optional, no key needed)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dastak
REDIS_URL=redis://localhost:6379

# Embeddings (runs locally, no key)
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

# PSX scraping
PSX_SCRAPE_DELAY_MS=1200   # be polite to PSX servers
```

---

## Project structure

```
dastak/
├── agent/
│   ├── supervisor.py        # LangGraph supervisor graph
│   ├── filings_agent.py     # SECP filing fetch + RAG
│   ├── news_agent.py        # async news ingestion + sentiment
│   └── price_agent.py       # PSX price + market data
├── psx/
│   ├── scraper.py           # PSX data layer (built from scratch)
│   ├── ocr_pipeline.py      # scanned filing handling
│   ├── filing_parser.py     # table-aware PDF extraction
│   └── ticker_map.py        # KSE-100 / KSE-All symbol resolution
├── rag/
│   ├── ingest.py            # document chunking + embedding
│   ├── retriever.py         # pgvector hybrid search
│   └── synthesizer.py       # answer generation + citation linking
├── urdu/
│   ├── normalizer.py        # Urdu text normalization
│   └── generator.py         # Urdu answer formatting
├── api/
│   └── main.py              # FastAPI endpoints
└── tests/
```

---

## Roadmap

- [x] Custom PSX data layer (scraper, OCR pipeline, ticker resolution)
- [x] Multi-source ingestion pipeline (PDF, news, scanned announcements)
- [ ] LangGraph supervisor with three sub-agents
- [ ] pgvector RAG over SECP filing corpus
- [ ] Table-aware financial ratio extraction
- [ ] Cross-filing diff (FY vs FY comparison)
- [ ] Urdu answer generation with citation linking
- [ ] FastAPI endpoints for broker integration
- [ ] Shareholding pattern extraction
- [ ] WhatsApp Business API delivery layer
- [ ] Backtested sentiment signals on historical filings

---

## Use cases

**Retail brokerage desks** — sales teams answer client questions on PSX companies in seconds, with citations they can show to clients.

**Urdu-speaking individual investors** — plain-language summaries of annual reports that were previously inaccessible.

**Financial journalists** — first-pass research on quarterly results with source links pre-attached.

**Research analysts** — cross-company, cross-sector comparison queries that would take hours to do manually.

---

## Contributing

PRs are welcome. Please open an issue first for significant changes. Highest-priority areas:

- Urdu NLP edge cases (diacritics, Nastaliq, mixed-script tables)
- Additional filing parsers (older scanned formats, sector-specific layouts)
- More news sources (regional business press, broker research PDFs)
- Tests for the PSX scraping layer

---

## Built by

**Warisha Bilal** — ML Lead, [Cortexium](https://cortexium.ai) · UET Peshawar · Microsoft Learn Student Ambassador

[github.com/Warisha-Bilal7](https://github.com/Warisha-Bilal7) · [linkedin.com/in/warisha-danin-bilal](https://linkedin.com/in/warisha-danin-bilal) · warishabilal05@gmail.com 24pwai0004@uetpeshawar.edu.pk

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">
<sub>Pakistan Stock Exchange · SECP · Urdu NLP · LangGraph · RAG · FinTech Pakistan</sub>
</div>
