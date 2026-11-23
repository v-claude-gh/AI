# Plan

## Goals

1. **Private chat**: a local assistant for everyday questions, in Italian and English, that sends nothing outside the computer. It does not replace the large cloud models for designing and writing code.
2. **Reading contracts, terms of service and documentation**: give it a URL, a PDF or a text file; it returns the clauses that matter (renewal, cancellation, penalties, data sharing, jurisdiction...) each with the exact text it comes from, so nothing has to be taken on trust.

## Constraints

- **Hardware**: Ryzen 5 3600XT (6 cores, 12 threads), 32 GB of RAM, Radeon RX 5700 XT with 8 GB of VRAM.
- **Everyday computer**: the models run only when asked for and must give VRAM and RAM back to games and work.
- **Zorin OS 18 Core** (Ubuntu 24.04 base).
- **Local only** for now: nothing listens on the network.
- **Entirely open source**: every component under an OSI-approved license; the repository is GPL-3.0.

## Choices

### Vulkan, not ROCm

The RX 5700 XT is RDNA 1 (Navi 10, gfx1010), which ROCm does not support. llama.cpp's Vulkan backend, on Mesa's RADV driver, works on it and is often as fast as ROCm for token generation on AMD cards.

### llama.cpp + llama-swap

| Option | License | Why |
| --- | --- | --- |
| llama.cpp + llama-swap | MIT + MIT | **chosen**: one endpoint, models loaded on demand and unloaded when idle, configuration versioned in this repository |
| Jan | Apache-2.0 | closest to LM Studio, but its configuration lives in the app and is hard to version |
| LM Studio | proprietary | not open source |
| Open WebUI | Open WebUI License (BSD-3 plus a branding clause) | not OSI-approved |

The chat is the page built into `llama-server`; llama-swap's own page adds a playground, the loaded models and the logs.

### Models

8 GB of VRAM sets the limits:

- dense models up to about 8–9B at 4 bits fit entirely on the GPU and are fast;
- **mixture-of-experts** models of about 30B with 3–4B active parameters per token are the sweet spot: the experts stay in RAM, the GPU holds attention, the KV cache and as many experts as fit; they are much more capable than an 8B and still usable for chat;
- dense models of 14–32B do not fit and run at a few tokens per second.

`llama-server` works out the GPU/RAM split by itself at load time (`--fit`, on by default).

Phase 1 candidates (`inventory/group_vars/all/main.yml`):

| Model | Type | Role |
| --- | --- | --- |
| Qwen3.6 35B-A3B | MoE, ~3B active | main candidate |
| Gemma 4 26B-A4B | MoE, ~4B active | alternative, compare Italian quality |
| Qwen3.5 9B | dense | fast, entirely in VRAM |

The choice is made on measurements, not on reviews.

### Colibrì: not for now

[Colibrì](https://github.com/JustVugg/colibri) streams very large mixture-of-experts models (e.g. GLM-5.2, 744B) from an NVMe disk. It needs about 370 GB of free disk and generates 0.05–2 tokens per second: too slow for chat and for reading documents. It may come back as an experiment for occasional hard questions.

## Phases

### Phase 1: installation and benchmark

- Ansible playbook: Vulkan driver, llama.cpp built at a pinned release, llama-swap, models, launcher. **Done.**
- Measure each candidate on the real computer: generation and prompt speed (tokens/s), VRAM and RAM, the longest context that fits, and quality on a few questions in Italian and English and on extracting clauses from a sample contract.
- Result: `docs/benchmark.md` and the choice of the main model. The benchmark is `scripts/bench.py`; its contract (`bench/contract-it.md`) and expected clauses also seed phase 3.

### Phase 2: the analysis tool

A Python command-line tool in this repository, using the same llama-swap endpoint:

1. **Input**: URL (with a headless browser for pages built by JavaScript), PDF, text file. OCR for scanned PDFs only if it turns out to be needed.
2. **Clean text** (no menus, banners, footers), split by article/section.
3. **Fixed checklist** of clauses, each section sent to the model separately:
   - duration, automatic renewal, cancellation and notice;
   - prices, hidden costs, penalties;
   - unilateral changes;
   - personal data, sharing with third parties, data retention;
   - licence over the user's content;
   - limitation of liability, warranties;
   - jurisdiction, arbitration, class-action waivers;
   - withdrawal and refunds.
4. **Report in Italian**, also for documents in English. Every point quotes the clause; a script checks that the quote is really in the document and marks it unreliable otherwise. This is what makes a small local model usable here: it can still miss a clause, but it cannot make one up unnoticed.

Libraries under licenses compatible with GPL-3.0 only (e.g. Playwright, trafilatura, pypdf/pdfminer.six; not PyMuPDF, which is AGPL).

### Phase 3: test on real documents

5–6 real contracts and terms of service whose problem clauses are known in advance: the tool has to find them all. This says how far its output can be trusted.

### Later, if needed

- Questions and answers over technical documentation (same tool, question mode).
- Access from other devices on the home network.
- Colibrì as an experiment.
