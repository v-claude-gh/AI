#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Phase 1 benchmark: speed, memory and a first quality check of each model.

Runs against llama-swap, so every model is measured exactly as the chat and
the tools use it (same llama-server arguments, --fit offload, KV cache).
Only the Python standard library is used.

For each model:
  load       time to load the model from a cold start (after an unload)
  memory     VRAM in use (amdgpu sysfs) and RAM of the llama-server process
  prompt     prompt processing speed at about 4k and 16k tokens
  generation generation speed, without and with thinking
  clauses    extraction of the unfavourable clauses of bench/contract-it.md:
             how many of the planted ones are found, and how many quotes are
             really in the text
  answers    three short answers (Italian, English, English text summarised
             in Italian), saved for reading

Usage:
  ai on
  python3 scripts/bench.py                    # every configured model
  python3 scripts/bench.py qwen3.5-9b         # only some
The report is written to bench/results/<host>-<date>.md (and .json).
"""

import argparse
import datetime
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "bench")

CHECKLIST = [
    "durata, rinnovo automatico, disdetta e preavviso",
    "prezzi, aumenti, costi nascosti, penali",
    "sospensione o interruzione del servizio",
    "modifiche unilaterali delle condizioni",
    "dati personali: cessione a terzi, conservazione",
    "licenza o diritti sui contenuti del cliente",
    "limitazioni di responsabilita e garanzie",
    "foro competente, arbitrato, rinuncia ad azioni collettive",
    "recesso e rimborsi",
]

QUESTIONS = [
    ("it", "Spiega in italiano, in cinque punti brevi, cosa controllare prima "
           "di firmare un contratto di affitto di un appartamento."),
    ("en", "In English, explain in five short bullet points the difference "
           "between a legal guarantee and a commercial warranty for consumer "
           "goods in the European Union."),
    ("en>it", "Riassumi in italiano, in tre frasi, cosa comporta per l'utente "
              "questo paragrafo:\n\n\"We may share aggregated and "
              "de-identified information, as well as your contact details, "
              "with our advertising partners. You can opt out at any time in "
              "your account settings, but opting out will not affect "
              "information shared before the change. We retain your data for "
              "as long as your account is active and for up to 36 months "
              "thereafter.\""),
]


# ---------------------------------------------------------------------------
# HTTP


class Client:
    def __init__(self, url, timeout):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.url + path, data=data, method=method,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw.strip().startswith((b"{", b"[")) else raw

    def models(self):
        return [m["id"] for m in self._request("GET", "/v1/models")["data"]]

    def unload(self):
        self._request("POST", "/api/models/unload")

    def chat(self, model, prompt, max_tokens, thinking=False):
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            # Honoured by the chat templates that support a thinking switch
            # (Qwen, Gemma); ignored by the others.
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        start = time.monotonic()
        r = self._request("POST", "/v1/chat/completions", body)
        elapsed = time.monotonic() - start
        msg = r["choices"][0]["message"]
        return {
            "content": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "timings": r.get("timings", {}),
            "elapsed": elapsed,
        }


# ---------------------------------------------------------------------------
# Memory


def vram_used_bytes():
    """VRAM in use on the AMD GPUs, from amdgpu's sysfs counters."""
    total = 0
    for path in glob.glob("/sys/class/drm/card*/device/mem_info_vram_used"):
        try:
            with open(path) as f:
                total += int(f.read().strip())
        except (OSError, ValueError):
            pass
    return total or None


def llama_server_rss_bytes():
    """Resident memory of the running llama-server processes."""
    total = 0
    for status in glob.glob("/proc/[0-9]*/status"):
        try:
            with open(status) as f:
                text = f.read()
        except OSError:
            continue
        if re.search(r"^Name:\s+llama-server$", text, re.M):
            m = re.search(r"^VmRSS:\s+(\d+) kB", text, re.M)
            if m:
                total += int(m.group(1)) * 1024
    return total or None


def gb(value):
    return "n/a" if value is None else f"{value / 1024 ** 3:.1f}"


# ---------------------------------------------------------------------------
# Clause extraction check


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("*", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_json_list(text):
    """The first JSON array in the answer, tolerating code fences and prose."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, list) else None


def clause_prompt(contract):
    checklist = "\n".join(f"- {c}" for c in CHECKLIST)
    return (
        "Sei un consulente che tutela il consumatore. Leggi il contratto qui "
        "sotto e individua tutte le clausole sfavorevoli o rischiose per il "
        "cliente, considerando in particolare:\n"
        f"{checklist}\n\n"
        "Rispondi SOLO con un array JSON. Ogni elemento ha i campi "
        '"categoria", "rischio" (una frase) e "citazione": la citazione deve '
        "essere copiata parola per parola dal contratto, senza modifiche.\n\n"
        f"CONTRATTO:\n\n{contract}"
    )


def check_clauses(answer, contract, expected):
    items = parse_json_list(answer)
    if items is None:
        return {"parsed": False, "items": 0, "verified": 0,
                "found": [], "missed": [c["id"] for c in expected]}
    norm_contract = normalize(contract)
    verified_quotes = []
    for item in items:
        quote = normalize(str(item.get("citazione", ""))) if isinstance(item, dict) else ""
        quote = quote.strip(" \"'.")
        if len(quote) >= 12 and quote in norm_contract:
            verified_quotes.append(quote)
    found = [c["id"] for c in expected
             if any(re.search(p, q, re.I) for q in verified_quotes for p in c["patterns"])]
    return {
        "parsed": True,
        "items": len(items),
        "verified": len(verified_quotes),
        "found": found,
        "missed": [c["id"] for c in expected if c["id"] not in found],
    }


# ---------------------------------------------------------------------------
# Benchmark of one model


def bench_model(client, model, contract, expected, log):
    res = {"model": model}

    log(f"[{model}] unload and cold load")
    client.unload()
    time.sleep(3)
    vram_base = vram_used_bytes()
    r = client.chat(model, "Rispondi solo: ok", 8)
    t = r["timings"]
    compute_s = (t.get("prompt_ms", 0) + t.get("predicted_ms", 0)) / 1000
    res["load_s"] = round(r["elapsed"] - compute_s, 1)
    res["vram_base"] = vram_base
    res["vram_loaded"] = vram_used_bytes()
    res["rss_loaded"] = llama_server_rss_bytes()

    log(f"[{model}] generation without thinking")
    r = client.chat(model, "Scrivi un racconto di circa 300 parole su un faro "
                           "in una notte di tempesta.", 400)
    res["gen_tps"] = r["timings"].get("predicted_per_second")
    res["gen_n"] = r["timings"].get("predicted_n")

    log(f"[{model}] generation with thinking")
    r = client.chat(model, "Quanti giorni ci sono tra il 3 marzo e il 17 "
                           "aprile dello stesso anno? Rispondi con il numero.",
                    2048, thinking=True)
    res["think_tps"] = r["timings"].get("predicted_per_second")
    res["think_n"] = r["timings"].get("predicted_n")
    res["think_answer"] = r["content"].strip()[:200]

    # contract-it.md is about 1.1k tokens: 4 and 14 copies give about 4k and
    # 16k tokens (the exact counts are reported).
    for label, repeat in (("4k", 4), ("16k", 14)):
        log(f"[{model}] prompt processing, about {label} tokens")
        text = "\n\n".join([contract] * repeat)
        try:
            r = client.chat(model, text + "\n\nRiassumi il testo in una frase.", 64)
            res[f"pp_{label}_n"] = r["timings"].get("prompt_n")
            res[f"pp_{label}_tps"] = r["timings"].get("prompt_per_second")
        except urllib.error.HTTPError as e:
            res[f"pp_{label}_error"] = f"HTTP {e.code}"
    res["vram_peak"] = vram_used_bytes()
    res["rss_peak"] = llama_server_rss_bytes()

    log(f"[{model}] clause extraction")
    r = client.chat(model, clause_prompt(contract), 3000)
    res["clauses"] = check_clauses(r["content"], contract, expected)
    res["clauses_s"] = round(r["elapsed"], 1)
    res["clauses_answer"] = r["content"]

    res["answers"] = []
    for lang, question in QUESTIONS:
        log(f"[{model}] answer ({lang})")
        r = client.chat(model, question, 700)
        res["answers"].append({"lang": lang, "question": question,
                               "answer": r["content"].strip()})
    return res


# ---------------------------------------------------------------------------
# Report


def fmt(value, digits=1):
    return "n/a" if value is None else f"{value:.{digits}f}"


def report_md(results, info, n_expected):
    lines = [
        f"# Benchmark {info['date']} ({info['host']})",
        "",
        f"llama.cpp: `{info['llama_cpp']}`",
        "",
        "| Model | Load s | VRAM GB | RAM GB | PP 4k t/s | PP 16k t/s | Gen t/s | Gen t/s (thinking) | Clauses found | Quotes verified |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['model']} | error: {r['error']} |||||||||")
            continue
        vram = None
        if r.get("vram_peak") is not None and r.get("vram_base") is not None:
            vram = r["vram_peak"] - r["vram_base"]
        c = r["clauses"]
        lines.append(
            f"| {r['model']} | {r['load_s']} | {gb(vram)} | {gb(r.get('rss_peak'))} "
            f"| {fmt(r.get('pp_4k_tps'))} | {fmt(r.get('pp_16k_tps'))} "
            f"| {fmt(r.get('gen_tps'))} | {fmt(r.get('think_tps'))} "
            f"| {len(c['found'])}/{n_expected} | {c['verified']}/{c['items']}"
            f"{'' if c['parsed'] else ' (no JSON)'} |")
    lines += [
        "",
        "- VRAM: increase over the idle desktop, at the peak (after the 16k prompt). RAM: llama-server resident memory.",
        "- PP: prompt processing; the exact prompt sizes are in the JSON file (`pp_4k_n`, `pp_16k_n`).",
        "- Clauses found: planted unfavourable clauses of `bench/contract-it.md` matched by a verified quote. Quotes verified: quotes really present in the contract, over the items returned.",
        "",
    ]
    for r in results:
        if "error" in r:
            continue
        c = r["clauses"]
        lines += [
            f"## {r['model']}",
            "",
            f"Thinking test (3 March to 17 April, expected 45): {r['think_n']} tokens, answer: `{r['think_answer']}`",
            "",
            f"Clauses missed: {', '.join(c['missed']) or 'none'}",
            "",
        ]
        for a in r["answers"]:
            lines += [f"### Answer ({a['lang']})", "", f"> {a['question'].splitlines()[0]}", "", a["answer"], ""]
        lines += ["<details><summary>Clause extraction, raw answer</summary>", "",
                  "```", r["clauses_answer"], "```", "", "</details>", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("models", nargs="*", help="model ids (default: all)")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--out", default=os.path.join(BENCH, "results"))
    parser.add_argument("--timeout", type=int, default=1800,
                        help="seconds allowed for one request")
    args = parser.parse_args()

    client = Client(args.url, args.timeout)
    try:
        available = client.models()
    except (urllib.error.URLError, OSError) as e:
        sys.exit(f"llama-swap does not answer on {args.url} ({e}): start it with 'ai on'")
    models = args.models or available
    unknown = [m for m in models if m not in available]
    if unknown:
        sys.exit(f"unknown models: {', '.join(unknown)} (configured: {', '.join(available)})")

    with open(os.path.join(BENCH, "contract-it.md"), encoding="utf-8") as f:
        contract = f.read()
    with open(os.path.join(BENCH, "expected.json"), encoding="utf-8") as f:
        expected = json.load(f)["clauses"]

    try:
        version = subprocess.run(["llama-server", "--version"], capture_output=True,
                                 text=True, timeout=30)
        llama_cpp = " ".join((version.stdout + version.stderr).split())
    except (OSError, subprocess.TimeoutExpired):
        llama_cpp = "unknown"
    info = {"date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "host": socket.gethostname(), "llama_cpp": llama_cpp}

    def log(msg):
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    results = []
    for model in models:
        try:
            results.append(bench_model(client, model, contract, expected, log))
        except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
            log(f"[{model}] failed: {e}")
            results.append({"model": model, "error": str(e)})
    client.unload()

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, f"{info['host']}-{datetime.datetime.now():%Y%m%d-%H%M}")
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump({"info": info, "results": results}, f, ensure_ascii=False, indent=2)
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write(report_md(results, info, len(expected)))
    print(f"\nReport: {stem}.md")


if __name__ == "__main__":
    main()
