# Checksum — Agentic Cinema Hackathon

> **Sesi baru: baca file ini sampai habis, lalu buka `docs/00-START-HERE.md`.**
> Semua keputusan di bawah ini SUDAH DIKUNCI. Jangan diperdebatkan ulang tanpa
> instruksi eksplisit dari user — waktu terlalu sempit untuk re-litigasi.

---

## Apa ini

Submission untuk **Agentic Cinema: The Blockbuster Hackathon** (Devpost, sponsor
Google Cloud), **track ClickHouse**. Solo entry.

**Checksum** adalah agent yang memverifikasi angka analitik studio sebelum angka itu
dipakai mengambil keputusan. Untuk satu pertanyaan, ia menjalankan 6–8 query berbeda
secara paralel di ClickHouse — beda definisi metrik, beda dedup, beda window — lalu
melaporkan apakah jawabannya **stabil** lintas pembacaan. Kalau peringkat berubah
tergantung definisi, itu dilaporkan sebagai temuan utama, bukan disembunyikan.

**Problem statement:**
> Apa yang terjadi kalau SQL-nya benar sempurna, tapi angkanya salah menurut definisi
> industri — dan seseorang memperpanjang serial $200 juta di atasnya?

---

## ⏰ DEADLINE — tidak bisa ditawar

**9 Sep 2026, 14:00 PDT = 10 Sep 2026, 04:00 WIB**

Cek sisa waktu sebelum mengambil keputusan scope apa pun:
```bash
python3 -c "import datetime,zoneinfo; d=datetime.datetime(2026,9,10,4,0,tzinfo=zoneinfo.ZoneInfo('Asia/Jakarta'))-datetime.datetime.now(zoneinfo.ZoneInfo('Asia/Jakarta')); print(f'{d.days}h {d.seconds//3600}j {d.seconds%3600//60}m tersisa')"
```

---

## 🚨 CONSTRAINT KERAS — melanggar satu saja = diskualifikasi

Empat aturan berikut mudah dilanggar tanpa sadar. Baca ulang sebelum menambah dependensi.

### 1. HANYA AI dari Google. Titik.
Kutipan resmi:
> *"Projects may only use Google Cloud artificial intelligence tools... No other AI
> models, agent frameworks, or AI APIs are permitted, regardless of vendor — this
> includes but is not limited to AWS, Microsoft, OpenAI, and Anthropic AI tools."*

- **Yang boleh:** `google-adk`, `google-genai`, `google-generativeai`, `google-cloud-aiplatform`
- **HARAM:** OpenAI, Anthropic, DeepSeek, Groq, Mistral, Cohere, Ollama, HuggingFace
  inference, LangChain/LlamaIndex/CrewAI (agent framework non-Google), embedding model
  non-Google, reranker pihak ketiga.
- **DeepSeek** dipakai user di project lain (`webradar`). **Jangan bawa ke sini.**
- Larangan ini soal AI **di dalam produk**. Memakai Claude Code untuk *menulis* kode
  tetap boleh di track ClickHouse (dua track lain punya aturan dev-tool sendiri, kita tidak).
- Batasan ini **tidak** melarang layanan non-AI: hosting, database, web framework bebas.

### 2. Integrasi harus dipanggil di runtime, dalam kode
> *"must demonstrate the use of Google Cloud and the Partner services at runtime in your
> code — imported and actually called... not just named in the README."*

Menyebut di README = **auto-fail Stage One**. Google SDK dan `mcp-clickhouse` harus
benar-benar di-import dan dieksekusi di jalur kode yang hidup.

### 3. Proyek baru saja
> *"Projects must be newly created by the entrant during the Contest Period. The Project
> must be Your original creation not a modification or extension of Your or anyone
> else's existing work."*

Jangan salin kode dari `webradar` atau repo user yang lain. Contest Period: 27 Jul – 9 Sep 2026.

### 4. Repo publik + file lisensi terdeteksi
> *"The repository must be public and include a complete open source license file. This
> license should be detectable and visible at the top of the repository page (in the
> About section)."*

`LICENSE` (MIT) sudah ada di root. **Jangan diganti nama, jangan dipindah, jangan dihapus.**
GitHub harus menampilkan "MIT" di sidebar About.

---

## 🔒 Keputusan yang sudah dikunci

| Hal | Keputusan | Jangan |
|---|---|---|
| Track | ClickHouse | Pindah track |
| Nama | **Checksum** | Ganti nama |
| Tim | Solo | — |
| Model | Gemini via **AI Studio API key** | Pakai Vertex AI — user tidak punya billing aktif |
| Orkestrasi | **`google-adk`** | Pakai LangChain/LlamaIndex/PydanticAI — dilarang aturan #1 |
| MCP | **`mcp-clickhouse` via stdio** | Pakai remote MCP endpoint — nama paket stdio cocok kata-per-kata dengan teks aturan, lebih aman untuk screening otomatis |
| Backend | FastAPI + SSE | — |
| Frontend | React | shadcn/template generic |
| Hosting | Non-GCP (billing tidak aktif) | — |

---

## ⚠️ Jebakan teknis yang sudah diketahui

1. **⚠️ PIN `mcp>=1.24,<2`. Ini jebakan paling mahal — sudah memakan waktu di GATE.**
   `google-adk` 2.8.0 butuh `mcp<2`, tapi konstraint itu **hanya ada di extra**
   (`extra == "mcp"`), jadi `pip install google-adk mcp` polos menarik `mcp` 2.1.1 yang
   melanggarnya. `mcp_toolset.py` lalu gagal di `from mcp.shared.session import ProgressFnT`,
   dan `google/adk/tools/mcp_tool/__init__.py` **menelan ImportError itu di `try/except`**.
   Gejalanya menyesatkan: `cannot import name 'McpToolset'` — seolah casing salah.
   Kombinasi kerja terverifikasi: **`google-adk` 2.8.0 + `mcp` 1.29.1 + Python 3.13.**

2. **`McpToolset`, bukan `MCPToolset`.** Casing ini **sudah dikonfirmasi benar** 6 Sep 2026.
   Kalau import gagal, tersangka utamanya jebakan #1, **bukan** casing. (Catatan: ADK 2.8.0
   sebenarnya mengekspor kedua casing.)

3. **Python lokal 3.14.6.** Venv proyek di 3.13: `uv venv --python 3.13 .venv`.
   MCP server dijalankan lewat `uv run --with mcp-clickhouse --python 3.13 mcp-clickhouse`.
   Terverifikasi jalan.

4. **✅ RISIKO #1 SUDAH MATI — ADK ↔ `mcp-clickhouse` TERBUKTI JALAN (6 Sep 2026).**
   Meski ADK tidak ada di daftar integrasi resmi ClickHouse, stdio wiring-nya menyala.
   Bukti: `scripts/gate_check.py` + `scripts/gate_query.py`, keduanya jalan tanpa API key.

5. **⚠️ Nama tool: `run_query` — BUKAN `run_select_query`.** Tebakan awal salah.
   `mcp-clickhouse` 0.6.0 mengekspos tepat 3 tool: `list_databases`, `list_tables`,
   `run_query`. Salah menulis `tool_filter` = agent kehilangan satu-satunya tool query,
   dan gagal **senyap**.

6. **`run_query` bukan read-only by design.** Ia melayani read dan write; yang menahan
   hanya env `CLICKHOUSE_ALLOW_WRITE_ACCESS` (default `false`), dan deskripsi tool-nya
   sendiri menyebut itu *"a best-effort accident guard, not a security boundary."*
   Di Playground ada lapis kedua (`ACCESS_DENIED`), tapi di **ClickHouse Cloud milik
   sendiri user adalah admin — lapisan itu hilang.** `before_tool_callback` jadi
   satu-satunya batas nyata. Jangan perlakukan sebagai nice-to-have.

7. **AI Studio free tier punya rate limit.** Juri akan menguji URL live. Butuh caching
   atau demo-mode supaya tidak kena 429 saat dinilai.

---

## Aturan kerja di repo ini

- **Bahasa dokumen internal (`docs/`, `CLAUDE.md`): Indonesia.**
  **Bahasa artefak submission (`README.md`, kode, komentar, UI, video): English.**
  Aturan mewajibkan submission berbahasa Inggris.
- `docs/` masuk `.gitignore` — isinya konteks strategis internal, tidak untuk mata juri.
- Sebelum menambah dependensi apa pun, cek constraint #1 di atas.
- Update `docs/06-EKSEKUSI.md` bagian STATUS setiap menyelesaikan satu blok kerja.
- Jangan tulis satu baris frontend sebelum GATE di `docs/06-EKSEKUSI.md` lolos.
