from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
JSON_RE = re.compile(r"\{.*\}", re.S)


def main() -> None:
    args = _parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    segments = payload["segments"]

    selected = [
        segment
        for segment in segments
        if float(segment.get("playback_rate", 1.0)) >= args.threshold
    ]
    if not selected:
        print("No segments exceeded the rewrite threshold.")
        return

    print(f"Rewriting {len(selected)} overlong segments from {manifest_path.name}")
    rewritten_count = 0
    for batch in _batched(selected, args.batch_size):
        rewrites = _rewrite_batch(batch, model=args.model, target_rate=args.target_rate)
        rewrite_map = {item["id"]: item["text"].strip() for item in rewrites}
        for segment in batch:
            new_text = rewrite_map.get(segment["index"], "").strip()
            if new_text:
                segment["chinese"] = new_text
                rewritten_count += 1
        print(f"Rewrote {rewritten_count}/{len(selected)} segments")

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else manifest_path.with_name(f"{manifest_path.stem}.rewritten.json")
    )
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote rewritten manifest: {output_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rewrite overlong Chinese dubbing lines in a manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output")
    parser.add_argument("--model", default="gpt-oss:120b-cloud")
    parser.add_argument("--threshold", type=float, default=1.12)
    parser.add_argument("--target-rate", type=float, default=1.05)
    parser.add_argument("--batch-size", type=int, default=8)
    return parser.parse_args()


def _rewrite_batch(
    batch: list[dict[str, object]],
    model: str,
    target_rate: float,
) -> list[dict[str, str | int]]:
    items = []
    for segment in batch:
        chinese = str(segment["chinese"]).strip()
        playback_rate = float(segment.get("playback_rate", 1.0))
        target_chars = max(8, int(round(len(chinese) * (target_rate / playback_rate))) - 1)
        items.append(
            {
                "id": int(segment["index"]),
                "english": str(segment.get("english", "")).strip(),
                "chinese": chinese,
                "target_chars": target_chars,
            }
        )

    prompt = (
        "You rewrite Chinese dubbing lines to be shorter and more natural for spoken voice-over.\n"
        "Rules:\n"
        "- Keep the original meaning.\n"
        "- Use spoken Simplified Chinese.\n"
        "- Use standard Mandarin suitable for a legal/news commentary video.\n"
        "- Prefer shorter phrasing, stronger verbs, and fewer filler words.\n"
        "- Do not use slang, memes, dialect words, or internet shorthand.\n"
        "- Keep names, legal terms, and numbers accurate.\n"
        "- Trust the English source if the current Chinese line is noisy or awkward.\n"
        "- Each rewrite should be close to or below target_chars.\n"
        "- Return JSON only with this shape: "
        '{"rewrites":[{"id":1,"text":"..."}]}\n'
        f"{json.dumps(items, ensure_ascii=False)}"
    )
    completed = subprocess.run(
        [
            "ollama",
            "run",
            model,
            "--hidethinking",
            "--think=false",
            "--format",
            "json",
            prompt,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = _extract_json_object(completed.stdout)
    rewrites = payload.get("rewrites")
    if not isinstance(rewrites, list):
        raise RuntimeError(f"Unexpected rewrite payload: {payload}")
    return rewrites


def _extract_json_object(text: str) -> dict:
    cleaned = ANSI_RE.sub("", text)
    match = JSON_RE.search(cleaned)
    if not match:
        raise RuntimeError(f"Could not find JSON in ollama response: {cleaned}")
    return json.loads(match.group(0))


def _batched(items: list[dict[str, object]], batch_size: int) -> list[list[dict[str, object]]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


if __name__ == "__main__":
    main()
