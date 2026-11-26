# voice-server — installer / orchestration Makefile
#
# This is the portable installer for the repo. It drives the Docker
# Compose projects and the one host process (wsproxy.py) with plain
# `make` targets. No root required, no distro-specific packaging.
#
# Why a Makefile and not a PKGBUILD (considered and rejected):
# this is a Docker-based project, not a system package — its
# deliverables are compose files, model YAMLs and a stdlib Python
# script, not binaries to install into /usr. A PKGBUILD is
# Arch-only; the reference host (the main local server) runs Ubuntu, and the
# Makefile works the same on any Linux with Docker + Python 3.
#
# Usage:
#   make help     # list targets (default)
#   make env      # create parakeet-api/.env with a generated key
#   make up       # start parakeet-api + LocalAI
#   make models   # install LocalAI gallery models (localai must be up)
#   make proxy    # start wsproxy.py on :8084 (one candidate list)
#   make status   # containers + wsproxy state
#   make up-gpu   # start LocalAI with the GPU image (any GPU machine)
#   make proxy-failover  # restart wsproxy (same one candidate list)
#
# Override the one candidate list, e.g.:
#   make proxy WS_PROXY_FAILOVER="dictate-streaming-tdt@127.0.0.1:8081"

PARAKEET_DIR  := parakeet-api
STREAMING_DIR := streaming-dictation
SPEACHES_DIR  := speaches

# Optional override for wsproxy's one candidate list. Empty means
# wsproxy.py uses DEFAULT_FAILOVER (edit that string in wsproxy.py).
WS_PROXY_FAILOVER ?=

.PHONY: help env up up-speaches down down-speaches logs models proxy proxy-stop status check-docker up-gpu down-gpu models-gpu proxy-failover

help: ## Show this help (default target).
	@echo "voice-server — free local Wispr Flow alternative"
	@echo ""
	@echo "Usage: make <target> [WS_PROXY_FAILOVER=<list>]"
	@echo ""
	@echo "Setup:"
	@echo "  env           Create $(PARAKEET_DIR)/.env from .env.example (generates an API key)"
	@echo "  up            Start parakeet-api (:8816) and LocalAI (:8081)"
	@echo "  up-speaches   Start speaches (:8001) — opt-in only, not part of 'up'"
	@echo "  models        Install LocalAI gallery models + print manual Nemotron/PnC steps"
	@echo "  proxy         Start wsproxy.py on :8084 (one candidate list)"
	@echo ""
	@echo "Second server (fast remote, GPU) — up-gpu/models-gpu run on the GPU machine:"
	@echo "  up-gpu        Start LocalAI with the GPU image (:8081, same container name)"
	@echo "  down-gpu      Stop the GPU LocalAI"
	@echo "  models-gpu    Install Whisper large-v3 on the GPU box (after 'make models')"
	@echo "  proxy-failover Restart wsproxy on :8084 (same one candidate list)"
	@echo ""
	@echo "Day to day:"
	@echo "  status        Show containers and whether wsproxy.py is running"
	@echo "  logs          Follow localai + parakeet-api logs (Ctrl-C to stop)"
	@echo "  proxy-stop    Stop wsproxy.py"
	@echo "  down          Stop parakeet-api + LocalAI"
	@echo "  down-speaches Stop speaches"
	@echo ""
	@echo "Streaming pipelines for Dictate's Real-time model field / candidate lists:"
	@echo "  dictate-streaming           Parakeet EOU 120M (recommended)"
	@echo "  dictate-streaming-nemotron  Nemotron 0.6B"
	@echo "  dictate-streaming-tdt       Parakeet TDT 0.6B v3"
	@echo "  dictate-streaming-tdt-v2    Parakeet TDT 0.6B v2 (English-only, 6.05% WER)"
	@echo "  dictate-streaming-whisper   Whisper large-v3 (fast remote GPU only, Japanese + punctuation)"

check-docker: ## Fail fast if docker / compose are missing.
	@command -v docker >/dev/null 2>&1 || { echo "ERROR: 'docker' not found. Install Docker first."; exit 1; }
	@docker compose version >/dev/null 2>&1 || { echo "ERROR: 'docker compose' (v2) not found. Install Docker Compose v2."; exit 1; }

env: ## Create parakeet-api/.env from .env.example if missing (generates a key).
	@if [ -f $(PARAKEET_DIR)/.env ]; then \
		echo "$(PARAKEET_DIR)/.env already exists — leaving it alone."; \
	else \
		cp $(PARAKEET_DIR)/.env.example $(PARAKEET_DIR)/.env; \
		if command -v openssl >/dev/null 2>&1; then \
			KEY=$$(openssl rand -hex 24); \
			sed -i "s/^SERVER__API_KEY=.*/SERVER__API_KEY=$$KEY/" $(PARAKEET_DIR)/.env; \
			echo "Created $(PARAKEET_DIR)/.env with a generated API key."; \
		else \
			echo "Created $(PARAKEET_DIR)/.env, but openssl is missing —"; \
			echo "edit SERVER__API_KEY in $(PARAKEET_DIR)/.env by hand."; \
		fi; \
	fi

up: check-docker env ## Start parakeet-api (:8816) and LocalAI (:8081).
	@cd $(PARAKEET_DIR) && docker compose up -d
	@cd $(STREAMING_DIR) && docker compose up -d
	@echo ""
	@echo "Up. Next: 'make models' (first run), then 'make proxy'."

up-speaches: check-docker ## Start speaches (:8001) — opt-in; NOT part of 'up'.
	@if [ ! -d $(SPEACHES_DIR) ]; then \
		echo "No $(SPEACHES_DIR)/ directory. Nothing to start."; exit 1; fi
	@if [ ! -f $(SPEACHES_DIR)/docker-compose.yml ] && [ ! -f $(SPEACHES_DIR)/compose.yaml ]; then \
		echo "No compose file in $(SPEACHES_DIR)/. Add one (see README) and retry."; exit 1; fi
	@cd $(SPEACHES_DIR) && docker compose up -d
	@echo "speaches up on :8001."

down: ## Stop parakeet-api + LocalAI.
	@cd $(PARAKEET_DIR) && docker compose down
	@cd $(STREAMING_DIR) && docker compose down
	@echo "Stopped parakeet-api and LocalAI. (wsproxy.py, if running, is separate: 'make proxy-stop'.)"

down-speaches: ## Stop speaches.
	@cd $(SPEACHES_DIR) && docker compose down

logs: ## Follow localai + parakeet-api logs (Ctrl-C to stop).
	@trap 'kill %1 %2 2>/dev/null' INT TERM; \
	docker logs -f localai & \
	docker logs -f parakeet-api & \
	wait

models: ## Install LocalAI gallery models; print manual Nemotron/PnC steps.
	@echo "Installing LocalAI gallery models (container 'localai' must be up)..."
	@docker exec localai /local-ai backends install whisper
	@docker exec localai /local-ai backends install parakeet-cpp
	@docker exec localai /local-ai backends install llama-cpp
	@docker exec localai chown -R localai:localai /backends
	@docker exec localai /local-ai models install silero-vad-ggml
	@docker exec localai /local-ai models install parakeet-cpp-realtime_eou_120m-v1
	@docker exec localai /local-ai models install parakeet-cpp-tdt-0.6b-v3
	@docker exec localai /local-ai models install parakeet-cpp-tdt-0.6b-v2
	@docker exec localai /local-ai models install qwen3-0.6b
	@echo ""
	@echo "Gallery models done. Nemotron needs two manual downloads into"
	@echo "$(STREAMING_DIR)/models/ (large files — not auto-fetched):"
	@echo ""
	@echo "  cd $(STREAMING_DIR)/models"
	@echo "  curl -L -o nemotron-speech-streaming-en-0.6b.q8_0.gguf \\"
	@echo "    https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b/resolve/main/nemotron-speech-streaming-en-0.6b.q8_0.gguf"
	@echo "  curl -L -o pnc-bert-base-en.q8_0.gguf \\"
	@echo "    https://huggingface.co/suneetk/nemo-speech-companions/resolve/main/pnc-bert-base-en.q8_0.gguf"
	@echo ""
	@echo "Then install the nemo backend (once):"
	@echo "  docker exec localai /local-ai backends install nemo-speech-cpp"
	@echo ""
	@echo "Note: the PnC BERT is attached but does not punctuate in"
	@echo "practice for this model (see README, Nemotron section)."

proxy: ## Start wsproxy.py on :8084 (one candidate list).
	@if pgrep -f '(^|/)python3 .*wsproxy\.py' >/dev/null 2>&1; then \
		echo "wsproxy.py is already running. 'make proxy-stop' first, or 'make status' to check."; \
		exit 1; \
	fi
	@command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found."; exit 1; }
	@cd $(STREAMING_DIR) && WS_PROXY_FAILOVER="$(WS_PROXY_FAILOVER)" nohup python3 wsproxy.py > wsproxy.log 2>&1 &
	@sleep 1
	@if pgrep -f '(^|/)python3 .*wsproxy\.py' >/dev/null 2>&1; then \
		echo "wsproxy.py up on :8084. Log: $(STREAMING_DIR)/wsproxy.log"; \
	else \
		echo "wsproxy.py failed to start — see $(STREAMING_DIR)/wsproxy.log"; exit 1; \
	fi

proxy-stop: ## Stop wsproxy.py.
	@pkill -f '(^|/)python3 .*wsproxy\.py' 2>/dev/null && echo "wsproxy.py stopped." || echo "wsproxy.py was not running."

status: ## Show containers + whether wsproxy.py is running.
	@echo "== containers =="
	@docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" --filter "name=localai" --filter "name=parakeet-api" --filter "name=speaches" 2>/dev/null || echo "(docker ps failed — is Docker running?)"
	@echo ""
	@echo "== wsproxy.py =="
	@pgrep -af '(^|/)python3 .*wsproxy\.py' 2>/dev/null || echo "wsproxy.py: not running (start with 'make proxy')"

# --- Second server: fast remote (GPU) ----------------------------------
# up-gpu / down-gpu / models-gpu run ON THE GPU MACHINE (a laptop with
# an NVIDIA GPU, or any GPU box). They use the same streaming-dictation
# compose file plus docker-compose.gpu.yml, so the container name,
# ports, volumes and pipeline YAMLs are identical to the CPU setup --
# only the image is the GPU one. That is the whole point: the same
# repo and the same commands test on any machine (main local server,
# fast remote laptop, a spare test box), and the fast remote is just
# the GPU variant. proxy-failover runs ON THE MAIN LOCAL SERVER: it
# restarts the single :8084 wsproxy (same one candidate list). NOTE:
# `make proxy-stop` kills every wsproxy.py (pkill -f), so do not use
# it while a second wsproxy (e.g. :8085) is running.

up-gpu: check-docker ## Start LocalAI with the GPU image (:8081). Run ON the GPU machine.
	@cd $(STREAMING_DIR) && docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
	@echo ""
	@echo "LocalAI (GPU) up on :8081. Next: 'make models' (first run), then 'make models-gpu' for Whisper."
	@echo "Check the GPU is visible: docker exec localai nvidia-smi"

down-gpu: ## Stop the GPU LocalAI. Run ON the GPU machine.
	@cd $(STREAMING_DIR) && docker compose -f docker-compose.yml -f docker-compose.gpu.yml down
	@echo "Stopped the GPU LocalAI. (wsproxy on the main server will fall back to its own LocalAI.)"

# models-gpu first removes any cpu-* backend variants: a volume created
# by the CPU container keeps cpu variants, and 'backends install' then
# skips the backend as already installed, so the GPU server (capability
# nvidia-cuda-12) reports "backend not found". The chown matters too:
# installs run as root via docker exec and create root-only dirs the
# server user cannot read.
models-gpu: ## Install Whisper large-v3 on the GPU box (container 'localai' must be up). Run ON the GPU machine.
	@echo "Installing Whisper large-v3 on the GPU box..."
	@docker exec localai sh -c 'rm -rf /backends/whisper /backends/cpu-whisper /backends/llama-cpp /backends/cpu-llama-cpp /backends/parakeet-cpp /backends/cpu-parakeet-cpp /backends/nemo-speech-cpp /backends/cpu-nemo-speech-cpp'
	@docker exec localai /local-ai backends install whisper
	@docker exec localai /local-ai backends install llama-cpp
	@docker exec localai /local-ai backends install parakeet-cpp
	@docker exec localai /local-ai backends install nemo-speech-cpp
	@docker exec localai chown -R localai:localai /backends
	@docker exec localai /local-ai models install whisper-large
	@echo ""
	@echo "Whisper done. The other pipelines come from 'make models'."
	@echo "Nemotron needs the two manual GGUF downloads into"
	@echo "$(STREAMING_DIR)/models/ (see 'make models' output) plus:"
	@echo "  docker exec localai /local-ai backends install nemo-speech-cpp"

proxy-failover: ## Restart wsproxy on :8084 (same one candidate list). Run ON the main local server.
	@pkill -f '(^|/)python3 .*wsproxy\.py' 2>/dev/null; sleep 1
	@command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found."; exit 1; }
	@cd $(STREAMING_DIR) && WS_PROXY_FAILOVER="$(WS_PROXY_FAILOVER)" nohup python3 wsproxy.py > wsproxy.log 2>&1 &
	@sleep 1
	@if pgrep -f '(^|/)python3 .*wsproxy\.py' >/dev/null 2>&1; then \
		echo "wsproxy.py up on :8084."; \
		echo "Log: $(STREAMING_DIR)/wsproxy.log (look for 'WS session model:')."; \
	else \
		echo "wsproxy.py failed to start — see $(STREAMING_DIR)/wsproxy.log"; exit 1; \
	fi
