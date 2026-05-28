# llama.cpp Router Setup

This is the local LLM pattern observed on `len` and intended for `roni1`.

## Observed on `len`

`len` runs current llama.cpp router mode through `llama-server`; there is no separate `llama-router` binary.

User service observed on `len`:

```ini
# /home/xangma/.config/systemd/user/llama-qwen36.service
[Unit]
Description=Qwen 3.6 llama-server on localhost:8001
After=network-online.target

[Service]
WorkingDirectory=/home/xangma
ExecStart=/home/xangma/repos/llama.cpp/build-linux-cuda/bin/llama-server --models-preset /home/xangma/llama-router/models.ini --models-max 1 --host 100.123.170.91 --port 8001 --metrics
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Preset:

```ini
version = 1

[*]
parallel = 1
metrics = true
no-webui = true

[qwen3.6]
hf-repo = unsloth/Qwen3.6-27B-MTP-GGUF
hf-file = Qwen3.6-27B-UD-Q4_K_XL.gguf
ctx-size = 16384
batch-size = 4096
ubatch-size = 1024
flash-attn = on
temp = 0.7
top-p = 0.8
top-k = 20
min-p = 0.00
no-mmproj = true
reasoning = off
n-gpu-layers = 99
spec-type = draft-mtp
spec-draft-n-max = 6
cache-reuse = 256
```

The active child process shows MTP is enabled:

```text
--spec-type draft-mtp --spec-draft-n-max 6 --alias qwen3.6
```

The API is OpenAI-compatible:

```bash
curl http://HOST_OR_LOCALHOST:8001/health
curl http://HOST_OR_LOCALHOST:8001/v1/models
curl http://HOST_OR_LOCALHOST:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6","messages":[{"role":"user","content":"Reply OK."}],"max_tokens":16}'
```

## Current `roni1` deployment

`roni1` now has a systemd-managed llama.cpp router using `/home/xangma/repos/llama.cpp-latest/build-linux-cuda/bin/llama-server`.

Service:

```ini
# /home/xangma/.config/systemd/user/llama-router.service
[Unit]
Description=llama.cpp router on 148.197.150.206:8001
After=network-online.target

[Service]
WorkingDirectory=/home/xangma
Environment=HOME=/home/xangma
ExecStart=/home/xangma/repos/llama.cpp-latest/build-linux-cuda/bin/llama-server --models-preset /home/xangma/llama-router/models.ini --models-max 1 --host 148.197.150.206 --port 8001 --metrics
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Runtime status verified on 2026-05-20:

- llama.cpp `version: 9235 (d14ce3dab)`
- user service `llama-router.service` enabled and active
- `loginctl show-user xangma -p Linger` returns `Linger=yes`
- endpoint: `http://148.197.150.206:8001/v1`
- model id: `qwen3.6`
- model file cached from `unsloth/Qwen3.6-27B-MTP-GGUF`
- generation response included `draft_n=6` and `draft_n_accepted=5`
- service logs included `statistics draft-mtp`

Useful checks:

```bash
systemctl --user status llama-router.service --no-pager -l
curl -sS http://148.197.150.206:8001/health
curl -sS http://148.197.150.206:8001/v1/models
curl -sS http://148.197.150.206:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6","messages":[{"role":"user","content":"Reply with exactly OK."}],"max_tokens":16,"temperature":0}'
```

Enable bot-side grouping after deploying the bot changes:

```yaml
llm:
  enabled: true
  base_url: http://148.197.150.206:8001/v1
  model: qwen3.6
  retry_attempts: 2
  retry_backoff_seconds: 1.0
  prompt_summary_chars: 600
  group_opportunities: true
```

Keep the endpoint private to loopback, a tunnel, or a tightly scoped private
network ACL. If `api_key_env_var` is configured, do not send it to a non-local
plain HTTP endpoint.

Run `funding-bot --config config.yaml dry-run` first. That path can exercise the LLM and render the exact Slack digest text without posting to Slack.
