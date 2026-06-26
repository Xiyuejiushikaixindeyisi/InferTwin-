# Tokenizers

HitFloor stores offline tokenizer profiles here. Each profile is selected by
the request `model` field through `manifest.yaml`.

Production profiles should vendor tokenizer files only, never model weights.

```text
tokenizers/<profile>/
  manifest.yaml
  tokenizer.json
  tokenizer_config.json
  special_tokens_map.json
  chat_template.jinja
  kv_meta.json
```

