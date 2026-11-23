# AI

Local AI on a desktop computer, entirely open source: a private chat and, next, a tool that reads contracts, terms of service and documentation and points out the clauses that matter, quoting them word for word.

The computer is an everyday workstation (Zorin OS 18, Ryzen 5 3600XT, 32 GB of RAM, Radeon RX 5700 XT with 8 GB of VRAM): the models run only when asked for and give the memory back when they are no longer used. Nothing leaves the computer except the model downloads.

The goals, the choices and the phases are in [docs/plan.md](docs/plan.md).

## How it works

```text
browser / tools ──► llama-swap (127.0.0.1:8080) ──► llama-server (llama.cpp, Vulkan)
                     one endpoint, starts the        one process per model,
                     model a request asks for,       on the GPU through Mesa RADV,
                     stops it when idle              weights that do not fit in VRAM in RAM
```

- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** (MIT) runs the models. The RX 5700 XT has no ROCm support, so llama.cpp is built with its Vulkan backend and uses Mesa's RADV driver. Its `llama-server` also provides the chat page.
- **[llama-swap](https://github.com/mostlygeek/llama-swap)** (MIT) gives one OpenAI-compatible endpoint for every model, starts the right `llama-server` on the first request and stops it after a period of inactivity (10 minutes by default), freeing VRAM and RAM.
- **`ai`** is a small command (and two entries in the applications menu) to start, stop and open the chat.

Everything is installed in the home directory by an Ansible playbook; root is used only for system packages and group membership.

## Layout

```text
.
├── ansible.cfg
├── site.yml                          # the playbook; areas are selected by tag
├── inventory/
│   ├── hosts.ini                     # localhost only
│   └── group_vars/all/
│       ├── main.yml                  # site settings: port, idle time, models
│       └── paths.yml                 # where things are installed (XDG layout)
├── roles/                            # in the order site.yml runs them
│   ├── gpu_vulkan/                   # Mesa Vulkan driver, tools, GPU check (tag: gpu)
│   ├── llama_cpp/                    # build llama.cpp with Vulkan at a pinned release (tag: llama-cpp)
│   ├── models/                       # download the GGUF models, checked by SHA-256 (tag: models)
│   ├── llama_swap/                   # llama-swap binary, configuration, systemd user service (tag: llama-swap)
│   └── launcher/                     # the "ai" command and the menu entries (tag: launcher)
├── docs/
│   ├── plan.md                       # goals, choices, phases
│   ├── benchmark.md                  # how the models are measured, results
│   ├── walkthrough.md                # every role, task by task
│   └── maintenance.md                # updates, models, troubleshooting, removal
├── bench/                            # benchmark inputs: sample contract, expected clauses
├── scripts/bench.py                  # phase 1 benchmark (speed, memory, clause extraction)
├── verify.md                         # commands to check each part
└── CLAUDE.md                         # how Claude contributes to this repository
```

## Installation

On Zorin OS 18 (or Ubuntu 24.04), as your own user:

```bash
sudo apt install ansible-core git
git clone https://github.com/Gu35t09/AI.git
cd AI
ansible-playbook site.yml -K
```

`-K` asks for the sudo password, used only to install packages and add the user to the `render` and `video` groups. The first run builds llama.cpp (10 to 20 minutes) and downloads the models listed in `inventory/group_vars/all/main.yml` (about 45 GB for the three phase 1 candidates). If the run adds you to a group, log out and back in before starting the service.

Then check each part with [verify.md](verify.md).

## Usage

| Command | What it does |
| --- | --- |
| `ai chat [model]` | starts llama-swap if needed and opens the chat in the browser |
| `ai on` / `ai off` | starts / stops llama-swap and every model |
| `ai status` | service state and loaded models |
| `ai models` | configured models |
| `ai unload` | unloads the models, the service keeps running |
| `ai ui` | llama-swap page: models, logs, playground |
| `ai logs` | follows the service log |

The applications menu has **AI locale** (`ai chat`) and **AI locale: spegni** (`ai off`).

Other programs can use the OpenAI-compatible API at `http://127.0.0.1:8080/v1`, with the model id as `model`.

## Documentation

- [docs/plan.md](docs/plan.md): goals, choices and phases of the project.
- [docs/walkthrough.md](docs/walkthrough.md): every role task by task, what it does and why.
- [docs/maintenance.md](docs/maintenance.md): updating llama.cpp and llama-swap, adding or removing models, troubleshooting, removal.
- [verify.md](verify.md): commands to check each part.
- [docs/benchmark.md](docs/benchmark.md): how the models are measured and compared, and the results.

## License

[GPL-3.0](LICENSE). llama.cpp and llama-swap, downloaded by the playbook, are MIT; each model has its own license, shown on its Hugging Face page.
