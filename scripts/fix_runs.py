#!/usr/bin/env python3
"""Fix all runs to standard format: translate broken descriptions, compress videos."""
from __future__ import annotations

import json
from pathlib import Path

import requests

RUNS_DIR = Path.home() / ".openclaw" / "tmp" / "mc-runs"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "videocut.toml"


def _load_config():
    import tomllib
    return tomllib.load(CONFIG_PATH.open("rb"))


def _is_broken(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped == "<think>" or stripped == "N/A":
        return True
    if not any("一" <= c <= "鿿" for c in stripped):
        return True
    if stripped.startswith("Thinking") or stripped.startswith("thinking"):
        return True
    return False


def _translate_via_chat(text: str, llm_cfg: dict) -> str | None:
    """Translate via chat API with thinking_budget=0 to suppress Qwen3 thinking."""
    import re

    base_url = llm_cfg.get("llm_base_url", "http://127.0.0.1:8888/v1").rstrip("/")
    api_key = llm_cfg.get("llm_api_key", "").strip()
    model = llm_cfg.get("llm_model", "Qwen3.5-4B-MLX-4bit")

    is_qwen = model.strip().lower().startswith("qwen3")

    system_content = (
        "You translate YouTube video descriptions into Simplified Chinese. "
        "Return only the Chinese translation. No explanations. No quotes."
    )
    user_content = f"/no_think\nTranslate this YouTube description to Simplified Chinese:\n\n{text}" if is_qwen else f"Translate this YouTube description to Simplified Chinese:\n\n{text}"

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
    if is_qwen:
        messages.append({"role": "assistant", "content": "<think>\n\n</think>\n"})

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": min(2048, max(1024, len(text))),
    }
    # thinking_budget=0 is the key server-side param that disables Qwen3 thinking
    if is_qwen:
        payload["thinking_budget"] = 0

    for attempt in range(3):
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=300,
            )
            resp.raise_for_status()
            result = resp.json()["choices"][0]["message"]["content"].strip()

            # Strip any residual think blocks
            result = re.sub(r"<think[\s\S]*?</think>", "", result, flags=re.IGNORECASE).strip()
            result = re.sub(r"</?think\s*/?>", "", result, flags=re.IGNORECASE).strip()

            if not result:
                continue
            if not any("一" <= c <= "鿿" for c in result):
                continue
            return result
        except Exception as e:
            print(f"   attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                import time
                time.sleep(2)
    return None


def _strip_think_blocks(text: str) -> str:
    import re
    text = re.sub(r"<think[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"<think[\s\S]*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"</?think\s*/?>", "", text, flags=re.IGNORECASE).strip()
    return text


def _clean_completion(text: str) -> str:
    """Match pipeline's _clean_completion_translation."""
    cleaned = text.replace("<end_of_turn>", "").strip()
    for marker in ("\n\n**Explanation:**", "\n**Explanation:**", "\n\nEnglish:", "\nEnglish:"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return ""
    first_line = lines[0].strip(" -*")
    if first_line.lower().startswith("chinese:"):
        first_line = first_line.split(":", 1)[1].strip()
    if first_line == "---" and len(lines) > 1:
        first_line = lines[1].strip(" -*")
    return first_line.strip().strip('"').strip("'")


# ── Description fix ───────────────────────────────────────────────────────────

def fix_descriptions(llm_cfg):
    for run_dir in sorted(RUNS_DIR.glob("mc-*")):
        mf = run_dir / "manifest.json"
        if not mf.exists():
            continue
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        source = manifest.get("source_metadata") or {}

        # Check publish file, not manifest (older runs may have correct Chinese
        # in publish/ even if manifest is stale)
        desc_file = run_dir / "publish" / "description.txt"
        current_desc = desc_file.read_text(encoding="utf-8").strip() if desc_file.exists() else ""
        if not _is_broken(current_desc):
            # Sync manifest from publish if needed
            lm = manifest.get("localized_metadata") or {}
            if lm.get("description") != current_desc:
                if "localized_metadata" not in manifest:
                    manifest["localized_metadata"] = {}
                manifest["localized_metadata"]["description"] = current_desc
                mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"📋 {run_dir.name}: synced manifest from publish file")
            continue

        source_desc = source.get("description", "")
        if not source_desc:
            print(f"⚠️  {run_dir.name}: no source description to translate")
            continue

        print(f"🔄 {run_dir.name}: translating description ({len(source_desc)} chars)...")
        translated = _translate_via_chat(source_desc, llm_cfg)

        if not translated:
            print(f"   ❌ translation failed, keeping source (English)")
            translated = source_desc
        else:
            print(f"   ✅ Chinese: {translated[:80]}...")

        # Update manifest
        if "localized_metadata" not in manifest:
            manifest["localized_metadata"] = {}
        manifest["localized_metadata"]["description"] = translated

        # Update publish/description.txt
        desc_file.write_text(translated, encoding="utf-8")

        # Update publish/metadata.json
        meta_file = run_dir / "publish" / "metadata.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["description"] = translated
            if "localized" in meta:
                meta["localized"]["description"] = translated
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ {run_dir.name}: files updated")


# ── Compression ───────────────────────────────────────────────────────────────

def fix_compression():
    from videocut.config import load_pipeline_config
    from videocut.media import compress_for_publish

    config = load_pipeline_config(CONFIG_PATH)
    target_mb = config.compress_to_max_mb or 500

    for run_dir in sorted(RUNS_DIR.glob("mc-*")):
        mf = run_dir / "manifest.json"
        if not mf.exists():
            continue

        compressed = run_dir / "final_compressed.mp4"
        if compressed.exists():
            continue

        manifest = json.loads(mf.read_text(encoding="utf-8"))
        candidate = run_dir / "final_video.mp4"

        if not candidate.exists():
            print(f"⚠️  {run_dir.name}: no final_video.mp4 to compress")
            continue

        size_mb = candidate.stat().st_size / 1024 / 1024
        print(f"🔄 {run_dir.name}: compressing {size_mb:.0f}MB -> ≤{target_mb}MB...")

        try:
            compress_for_publish(
                input_path=candidate,
                output_path=compressed,
                target_size_mb=target_mb,
                max_width=1920,
                max_height=1080,
            )
        except Exception as e:
            print(f"   ❌ compression failed: {e}")
            continue

        # Replace original with compressed version under the same name
        candidate.unlink()
        compressed.rename(candidate)

        # Delete intermediate subtitled file
        subtitled = run_dir / "final_subtitled.mp4"
        if subtitled.exists():
            subtitled.unlink()

        # Update manifest
        manifest["final_video"] = str(candidate)
        mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✅ {run_dir.name}: {candidate.stat().st_size/1024/1024:.0f}MB")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--desc-only", action="store_true")
    parser.add_argument("--compress-only", action="store_true")
    args = parser.parse_args()

    config = _load_config()
    llm_cfg = config.get("translation", {})

    if not args.compress_only:
        print("=" * 60)
        print("Fixing broken descriptions")
        print("=" * 60)
        fix_descriptions(llm_cfg)
        print()

    if not args.desc_only:
        print("=" * 60)
        print("Compressing videos to standard")
        print("=" * 60)
        fix_compression()
        print()

    print("Done.")


if __name__ == "__main__":
    main()
