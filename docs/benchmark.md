# Benchmark

Phase 1 of [plan.md](plan.md): measure the candidate models on the real computer and pick the main one.

## Run

```bash
ai on
python3 scripts/bench.py                  # every configured model
python3 scripts/bench.py qwen3.5-9b       # only some
```

Close games and other GPU programs first: free VRAM changes how much of a model goes on the GPU. One model takes from 5 to 20 minutes, most of it the 16k prompt on the mixture-of-experts models. Progress is printed as it goes; the report is written to `bench/results/<host>-<date>.md`, with every number in the `.json` next to it. `bench/results/` is not committed: the summary goes in the table below.

The script uses only the Python standard library and talks to llama-swap, so every model is measured exactly as the chat and the tools use it.

## What it measures

| Column | How |
| --- | --- |
| Load s | the model is unloaded, then a one-word request; the time not spent on the prompt and the answer is the load. A second run of the script measures loading from the page cache (much faster). |
| VRAM GB | increase of amdgpu's `mem_info_vram_used` over the idle desktop, at the peak (after the 16k prompt) |
| RAM GB | resident memory of `llama-server` at the peak: for mixture-of-experts models, the experts that did not fit in VRAM |
| PP 4k / 16k t/s | prompt processing speed with about 4k and 16k tokens of contract text: how long reading a long document takes |
| Gen t/s | generation speed, thinking off (a 300-word story) |
| Gen t/s (thinking) | generation speed with thinking on (a date question, expected answer 45); the report shows how many tokens the reasoning took |
| Clauses found | the model is asked for the unfavourable clauses of [`bench/contract-it.md`](../bench/contract-it.md), as a JSON list with word-for-word quotes. A planted clause ([`bench/expected.json`](../bench/expected.json), 13 in all) counts as found only through a quote that is really in the text. |
| Quotes verified | quotes present in the contract, over the items returned: the rest are paraphrased or invented. This is the check phase 2 relies on. |

The report also has three short answers per model (Italian, English, an English paragraph summarised in Italian) and the raw clause extraction, to judge the quality by reading.

## How to choose

1. **Clauses found and quotes verified** first: reading contracts is the hard use. A model that misses many clauses or invents quotes is not usable for it, however fast.
2. **Italian quality** of the answers, by reading.
3. **Speed**: for chat, generation of 10 t/s or more is comfortable; for documents, prompt processing decides the wait (16k tokens at 100 t/s is under three minutes).
4. **Memory**: the model plus the desktop must fit in 32 GB of RAM with room left for a browser.

The main model becomes `ai_default_model` in `inventory/group_vars/all/main.yml`; a fast one can stay for quick questions.

## Results

*To be filled in with the first run on the workstation.*
