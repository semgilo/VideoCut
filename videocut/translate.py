from __future__ import annotations

import json
import re
from collections.abc import Iterable

import requests

from videocut.models import Segment, VideoMetadata


JSON_RE = re.compile(r"\{.*\}", re.S)


class OpenAICompatibleTranslator:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int, batch_size: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size

    def translate(self, segments: list[Segment]) -> None:
        if not self.api_key:
            raise RuntimeError(
                "VIDEOCUT_LLM_API_KEY is empty. Set it in .env or pass --llm-api-key."
            )

        translated_count = 0
        total_segments = len(segments)
        for batch in _batched(segments, self.batch_size):
            translated_count += self._translate_batch_resilient(batch)
            print(f"Translated {translated_count}/{total_segments} segments")

    def _translate_batch(self, batch: list[Segment]) -> list[dict[str, str | int]]:
        payload_segments = [{"id": segment.index, "text": segment.english} for segment in batch]
        system_prompt = (
            "You are a subtitle translator for dubbing. Translate English subtitles into concise, "
            "spoken Simplified Chinese that sounds natural aloud. Prefer shorter phrasing over literal "
            "translation when the meaning stays intact. Keep names, product terms, and numbers accurate. "
            "Avoid adding filler words or explanations that were not in the source. "
            'Return JSON only with this shape: {"translations":[{"id":1,"text":"..."}]}.'
        )
        user_prompt = (
            "Translate each subtitle item to Simplified Chinese. Preserve the ids exactly.\n"
            f"{json.dumps(payload_segments, ensure_ascii=False)}"
        )
        parsed = self._complete_json(system_prompt, user_prompt)
        translations = parsed.get("translations")
        if not isinstance(translations, list):
            raise RuntimeError(f"Unexpected translator payload: {parsed}")
        return translations

    def translate_metadata(self, metadata: VideoMetadata) -> VideoMetadata:
        if not self.api_key:
            raise RuntimeError(
                "VIDEOCUT_LLM_API_KEY is empty. Set it in .env or pass --llm-api-key."
            )

        system_prompt = (
            "You localize YouTube video metadata into concise, natural Simplified Chinese. "
            "Translate the title, description, and tags while preserving proper nouns exactly, "
            "including personal names, brand names, product names, place names, @handles, URLs, "
            "hashtags, model numbers, and numeric values. Keep the original meaning and tone. "
            "Do not invent facts or add marketing filler. If source tags are empty, derive a small set "
            "of grounded tags from the title and description only. "
            'Return JSON only with this shape: {"title":"...","description":"...","tags":["..."]}.'
        )
        user_prompt = (
            "Localize this metadata to Simplified Chinese while preserving proper nouns:\n"
            f"{json.dumps(metadata_payload(metadata), ensure_ascii=False)}"
        )
        parsed = self._complete_json(system_prompt, user_prompt)
        translated_tags = parsed.get("tags")
        if not isinstance(translated_tags, list):
            raise RuntimeError(f"Unexpected metadata payload: {parsed}")
        return VideoMetadata(
            title=str(parsed.get("title") or metadata.title).strip(),
            description=str(parsed.get("description") or metadata.description).strip(),
            tags=[str(tag).strip() for tag in translated_tags if str(tag).strip()],
            uploader=metadata.uploader,
            channel=metadata.channel,
            video_id=metadata.video_id,
            webpage_url=metadata.webpage_url,
            upload_date=metadata.upload_date,
        )

    def _translate_batch_resilient(self, batch: list[Segment]) -> int:
        try:
            translations = self._translate_batch(batch)
            mapping = {item["id"]: item["text"].strip() for item in translations}
            missing_ids = [segment.index for segment in batch if segment.index not in mapping]
            if missing_ids:
                raise RuntimeError(f"Translator response is missing ids: {missing_ids}")
            for segment in batch:
                segment.chinese = mapping[segment.index]
            return len(batch)
        except (requests.RequestException, RuntimeError, ValueError) as error:
            if len(batch) == 1:
                raise RuntimeError(f"Failed to translate segment {batch[0].index}") from error
            midpoint = len(batch) // 2
            print(
                f"Translation batch {batch[0].index}-{batch[-1].index} failed: {error}. "
                "Retrying with smaller batches."
            )
            return self._translate_batch_resilient(batch[:midpoint]) + self._translate_batch_resilient(
                batch[midpoint:]
            )

    def _complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return _extract_json_object(content)


def metadata_payload(metadata: VideoMetadata) -> dict[str, object]:
    return {
        "title": metadata.title,
        "description": metadata.description,
        "tags": metadata.tags,
        "uploader": metadata.uploader,
        "channel": metadata.channel,
        "webpage_url": metadata.webpage_url,
    }


def _extract_json_object(text: str) -> dict:
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    match = JSON_RE.search(text)
    if not match:
        raise RuntimeError(f"Could not find JSON in translator response: {text}")
    return json.loads(match.group(0))


def _batched(items: list[Segment], batch_size: int) -> Iterable[list[Segment]]:
    for index in range(0, len(items), batch_size):
        yield items[index : index + batch_size]
