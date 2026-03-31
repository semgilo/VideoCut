from __future__ import annotations

import json
import math
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse
import unicodedata

import requests

from videocut.models import Segment, VideoMetadata


JSON_RE = re.compile(r"\{.*\}", re.S)
PROTECTED_PLACEHOLDER_TEMPLATE = "[[VC_TERM_{index:04d}]]"
PLACEHOLDER_VARIANT_RE = re.compile(r"\[\[?VC_TERM_(?P<id>\d{1,4}|xxxx)\]?\]?", re.IGNORECASE)

class OpenAICompatibleTranslator:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int,
        batch_size: int,
        concurrency: int = 1,
        target_cps: float = 4.5,
        char_tolerance: float = 0.2,
        # Legacy options kept for backward compatibility with old scripts.
        min_playback_rate: float | None = None,
        max_playback_rate: float | None = None,
        enforce_char_budget: bool = False,
        budget_refine_passes: int = 1,
        protected_terms: list[str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size
        self.concurrency = max(1, concurrency)
        self.target_cps = max(1.0, target_cps)
        self.char_tolerance = min(max(0.0, char_tolerance), 0.95)
        if min_playback_rate is not None or max_playback_rate is not None:
            legacy_min = 0.8 if min_playback_rate is None else min_playback_rate
            legacy_max = 1.2 if max_playback_rate is None else max_playback_rate
            legacy_tolerance = max(abs(1.0 - legacy_min), abs(legacy_max - 1.0))
            self.char_tolerance = min(max(self.char_tolerance, legacy_tolerance), 0.95)
        self.enforce_char_budget = enforce_char_budget
        self.budget_refine_passes = max(1, budget_refine_passes)
        self.protected_terms = _dedupe_terms(protected_terms or [])

    def translate(self, segments: list[Segment]) -> None:
        if _requires_completion_api(self.model):
            self._translate_with_completion_model(segments)
            return
        batches = list(_batched(segments, self.batch_size))
        translated_count = 0
        total_segments = len(segments)
        if self.concurrency <= 1 or len(batches) <= 1:
            for batch in batches:
                translated_count += self._translate_batch_resilient(batch)
                print(f"Translated {translated_count}/{total_segments} segments")
            return
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(batches))) as executor:
            futures = [executor.submit(self._translate_batch_resilient, batch) for batch in batches]
            for future in as_completed(futures):
                translated_count += future.result()
                print(f"Translated {translated_count}/{total_segments} segments")


    def _translate_batch(self, batch: list[Segment]) -> list[dict[str, str | int]]:
        payload_segments = []
        for segment in batch:
            min_budget, max_budget = _subtitle_char_budget(
                duration=segment.duration,
                target_cps=self.target_cps,
                char_tolerance=self.char_tolerance,
            )
            payload_segments.append(
                {
                    "id": segment.index,
                    "text": segment.english,
                    "duration": round(segment.duration, 2),
                    "min_budget": min_budget,
                    "max_budget": max_budget,
                }
            )
        masked_segments, required_placeholders, placeholder_to_term = _mask_segment_payload(
            payload_segments,
            self.protected_terms,
        )
        system_prompt = (
            "You are a subtitle translator for dubbing. Translate English subtitles into concise, "
            "spoken Simplified Chinese that sounds natural aloud. Prefer shorter phrasing over literal "
            "translation when the meaning stays intact. Keep names, product terms, and numbers accurate. "
            "Each subtitle has 'min_budget' and 'max_budget' fields, which define the allowed Chinese "
            "character range (excluding spaces and punctuation). "
            "The translation MUST stay within that range: not shorter than min_budget and not longer "
            "than max_budget. If the source text is too long, paraphrase concisely. If it is too short, "
            "rewrite naturally without adding unrelated facts. "
            "If placeholder tokens like [[VC_TERM_0001]] appear, copy them exactly and do not translate, "
            "remove, or rename them. "
            "Avoid adding filler words or explanations that were not in the source. "
            'Return JSON only with this shape: {"translations":[{"id":1,"text":"..."}]}.'
        )
        user_prompt = (
            "Translate each subtitle item to Simplified Chinese. Preserve the ids exactly. "
            "Respect the min_budget/max_budget character constraints for each item. "
            "If any [[VC_TERM_xxxx]] token appears, keep it unchanged in the output.\n"
            f"{json.dumps(masked_segments, ensure_ascii=False)}"
        )
        parsed = self._complete_json(system_prompt, user_prompt)
        translations = parsed.get("translations")
        if not isinstance(translations, list):
            raise RuntimeError(f"Unexpected translator payload: {parsed}")
        return _restore_segment_translations(translations, required_placeholders, placeholder_to_term)

    def translate_metadata(self, metadata: VideoMetadata) -> VideoMetadata:
        if _requires_completion_api(self.model):
            return self._translate_metadata_with_completion_model(metadata)
        metadata_input = metadata_payload(metadata)
        masked_metadata, required_placeholders, placeholder_to_term = _mask_metadata_payload(
            metadata_input,
            self.protected_terms,
        )
        system_prompt = (
            "You localize YouTube video metadata into concise, natural Simplified Chinese. "
            "Translate the title, description, and tags while preserving proper nouns exactly, "
            "including personal names, brand names, product names, place names, @handles, URLs, "
            "hashtags, model numbers, and numeric values. Keep the original meaning and tone. "
            "If placeholder tokens like [[VC_TERM_0001]] appear, copy them exactly and do not translate, "
            "remove, or rename them. "
            "Do not invent facts or add marketing filler. If source tags are empty, derive a small set "
            "of grounded tags from the title and description only. "
            'Return JSON only with this shape: {"title":"...","description":"...","tags":["..."]}.'
        )
        user_prompt = (
            "Localize this metadata to Simplified Chinese while preserving proper nouns. "
            "If any [[VC_TERM_xxxx]] token appears, keep it unchanged in the output.\n"
            f"{json.dumps(masked_metadata, ensure_ascii=False)}"
        )
        parsed = self._complete_json(system_prompt, user_prompt)
        translated_tags = parsed.get("tags")
        if not isinstance(translated_tags, list):
            raise RuntimeError(f"Unexpected metadata payload: {parsed}")
        restored_title = _restore_masked_text(
            str(parsed.get("title") or metadata.title).strip(),
            required_placeholders["title"],
            placeholder_to_term,
        )
        restored_description = _restore_masked_text(
            str(parsed.get("description") or metadata.description).strip(),
            required_placeholders["description"],
            placeholder_to_term,
        )
        restored_tags = [
            _restore_masked_text(
                str(tag).strip(),
                required_placeholders["tags"][index] if index < len(required_placeholders["tags"]) else [],
                placeholder_to_term,
            )
            for index, tag in enumerate(translated_tags)
            if str(tag).strip()
        ]
        return VideoMetadata(
            title=restored_title,
            description=restored_description,
            tags=restored_tags,
            uploader=metadata.uploader,
            channel=metadata.channel,
            video_id=metadata.video_id,
            webpage_url=metadata.webpage_url,
            upload_date=metadata.upload_date,
        )
    def _translate_with_completion_model(self, segments: list[Segment]) -> None:
        term_to_placeholder, placeholder_to_term = _build_placeholder_maps(self.protected_terms)
        total_segments = len(segments)
        if self.concurrency <= 1 or len(segments) <= 1:
            for translated_count, segment in enumerate(segments, start=1):
                translated = _translate_completion_text(
                    translator=self,
                    text=segment.english,
                    prompt_builder=lambda t, p, s, d=segment.duration: _subtitle_completion_prompt(
                        t,
                        p,
                        s,
                        d,
                        self.target_cps,
                        self.char_tolerance,
                    ),
                    term_to_placeholder=term_to_placeholder,
                    placeholder_to_term=placeholder_to_term,
                    max_tokens=_completion_max_tokens(segment.english, minimum=96, maximum=220),
                    empty_error_label=f"segment {segment.index}",
                )
                segment.chinese = self._fit_translation_to_budget(
                    segment=segment,
                    translated_text=translated,
                )
                print(f"Translated {translated_count}/{total_segments} segments")
            return
        with ThreadPoolExecutor(max_workers=min(self.concurrency, len(segments))) as executor:
            future_to_segment = {
                executor.submit(
                    _translate_completion_text,
                    translator=self,
                    text=segment.english,
                    prompt_builder=lambda t, p, s, d=segment.duration: _subtitle_completion_prompt(
                        t,
                        p,
                        s,
                        d,
                        self.target_cps,
                        self.char_tolerance,
                    ),
                    term_to_placeholder=term_to_placeholder,
                    placeholder_to_term=placeholder_to_term,
                    max_tokens=_completion_max_tokens(segment.english, minimum=96, maximum=220),
                    empty_error_label=f"segment {segment.index}",
                ): segment
                for segment in segments
            }
            translated_count = 0
            for future in as_completed(future_to_segment):
                segment = future_to_segment[future]
                segment.chinese = self._fit_translation_to_budget(
                    segment=segment,
                    translated_text=future.result(),
                )
                translated_count += 1
                print(f"Translated {translated_count}/{total_segments} segments")

    def _translate_metadata_with_completion_model(self, metadata: VideoMetadata) -> VideoMetadata:
        term_to_placeholder, placeholder_to_term = _build_placeholder_maps(self.protected_terms)
        title = _translate_completion_field(
            translator=self,
            text=metadata.title,
            field_name="title",
            term_to_placeholder=term_to_placeholder,
            placeholder_to_term=placeholder_to_term,
        )
        description = _translate_completion_field(
            translator=self,
            text=metadata.description,
            field_name="description",
            term_to_placeholder=term_to_placeholder,
            placeholder_to_term=placeholder_to_term,
        )
        tags = [
            _translate_completion_field(
                translator=self,
                text=tag,
                field_name="tag",
                term_to_placeholder=term_to_placeholder,
                placeholder_to_term=placeholder_to_term,
            )
            for tag in metadata.tags
            if tag.strip()
        ]
        return VideoMetadata(
            title=title,
            description=description,
            tags=tags,
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
                translated = mapping[segment.index]
                segment.chinese = self._fit_translation_to_budget(
                    segment=segment,
                    translated_text=translated,
                )
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

    def _fit_translation_to_budget(self, segment: Segment, translated_text: str) -> str:
        min_budget, max_budget = _subtitle_char_budget(
            duration=segment.duration,
            target_cps=self.target_cps,
            char_tolerance=self.char_tolerance,
        )
        candidate = translated_text.strip()
        char_count = _count_spoken_characters(candidate)
        if min_budget <= char_count <= max_budget:
            return candidate
        print(
            f"Warning: segment {segment.index} translated length {char_count} is outside "
            f"[{min_budget}, {max_budget}]. Keeping single-pass translation output."
        )
        return candidate

    def _rewrite_translation_to_budget(
        self,
        segment: Segment,
        current_translation: str,
        min_budget: int,
        max_budget: int,
    ) -> str:
        term_to_placeholder, placeholder_to_term = _build_placeholder_maps(self.protected_terms)
        masked_english, placeholders = _mask_text(segment.english, term_to_placeholder)
        masked_current, _ = _mask_text(current_translation, term_to_placeholder)

        if _requires_completion_api(self.model):
            prompt = (
                "Rewrite the Chinese subtitle so it sounds natural when spoken and preserves meaning. "
                f"Chinese character count (excluding spaces and punctuation) must be between {min_budget} "
                f"and {max_budget}. {_placeholder_instruction(placeholders, True)} "
                "Return only the rewritten Chinese subtitle. No explanation.\n\n"
                f"English: {masked_english}\n"
                f"Current Chinese: {masked_current}\n"
                "Rewritten Chinese:"
            )
            completion = self._complete_text(
                prompt=prompt,
                max_tokens=_completion_max_tokens(segment.english, minimum=96, maximum=240),
            )
            rewritten = _clean_completion_translation(completion)
        else:
            system_prompt = (
                "You rewrite Chinese dubbing subtitles. Keep meaning accurate and spoken style natural. "
                f"Output Chinese text only. Character count (excluding spaces and punctuation) must be "
                f"between {min_budget} and {max_budget}. "
                f"{_placeholder_instruction(placeholders, True)} "
                'Return JSON only: {"text":"..."}'
            )
            user_prompt = (
                "Rewrite this subtitle with strict length bounds.\n"
                f'{{"english":{json.dumps(masked_english, ensure_ascii=False)},'
                f'"current":{json.dumps(masked_current, ensure_ascii=False)},'
                f'"min_budget":{min_budget},"max_budget":{max_budget}}}'
            )
            parsed = self._complete_json(system_prompt, user_prompt)
            rewritten = str(parsed.get("text", "")).strip()

        if not rewritten:
            raise RuntimeError(f"Budget rewrite returned empty text for segment {segment.index}")
        restored = _restore_masked_text(rewritten, placeholders, placeholder_to_term)
        return restored

    def _complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        if _is_ollama_base_url(self.base_url):
            return self._complete_json_with_ollama_native(system_prompt, user_prompt)

        headers = {
            "Content-Type": "application/json",
        }
        api_key = self.api_key.strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": _build_chat_messages(self.model, system_prompt, user_prompt),
        }
        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                response_payload = response.json()
                content = response_payload["choices"][0]["message"]["content"]
                return _extract_json_object(content)
            except (requests.RequestException, KeyError, ValueError) as error:
                last_error = error
                if attempt == 4 or not _should_retry_completion(error):
                    raise
                wait_seconds = attempt * 2
                print(
                    "Translator request failed "
                    f"(attempt {attempt}/4): {error}. Retrying in {wait_seconds}s."
                )
                time.sleep(wait_seconds)

        raise RuntimeError("Translator request failed after retries") from last_error

    def _complete_text(self, prompt: str, max_tokens: int) -> str:
        headers = {"Content-Type": "application/json"}
        api_key = self.api_key.strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stop": ["<end_of_turn>", "\n\nEnglish:", "\nEnglish:", "\n\n**Explanation:**"],
        }
        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                response = requests.post(
                    f"{self.base_url}/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                response_payload = response.json()
                return str(response_payload["choices"][0]["text"])
            except (requests.RequestException, KeyError, ValueError) as error:
                last_error = error
                if attempt == 4 or not _should_retry_completion(error):
                    raise
                wait_seconds = attempt * 2
                print(
                    "Translator request failed "
                    f"(attempt {attempt}/4): {error}. Retrying in {wait_seconds}s."
                )
                time.sleep(wait_seconds)

        raise RuntimeError("Translator request failed after retries") from last_error

    def _complete_json_with_ollama_native(self, system_prompt: str, user_prompt: str) -> dict:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "temperature": 0.2,
            },
            "messages": _build_chat_messages(self.model, system_prompt, user_prompt),
        }
        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                response = requests.post(
                    f"{_ollama_native_base_url(self.base_url)}/api/chat",
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                response_payload = response.json()
                content = response_payload["message"]["content"]
                return _extract_json_object(content)
            except (requests.RequestException, KeyError, ValueError) as error:
                last_error = error
                if attempt == 4 or not _should_retry_completion(error):
                    raise
                wait_seconds = attempt * 2
                print(
                    "Translator request failed "
                    f"(attempt {attempt}/4): {error}. Retrying in {wait_seconds}s."
                )
                time.sleep(wait_seconds)

        raise RuntimeError("Translator request failed after retries") from last_error


def llm_translation_enabled(*, base_url: str, model: str, api_key: str) -> bool:
    normalized_base_url = base_url.strip()
    normalized_model = model.strip()
    normalized_api_key = api_key.strip()
    if not normalized_base_url or not normalized_model:
        return False
    return bool(normalized_api_key or is_local_base_url(normalized_base_url))


def is_local_base_url(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").strip().lower()
    return hostname in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def ensure_endpoint_reachable(base_url: str, timeout: float = 3.0) -> None:
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise RuntimeError(f"LLM base URL is invalid: {base_url}")
    port = parsed.port
    if port is None:
        port = 443 if (parsed.scheme or "").lower() == "https" else 80
    with socket.create_connection((hostname, port), timeout=timeout):
        return


def metadata_payload(metadata: VideoMetadata) -> dict[str, object]:
    return {
        "title": metadata.title,
        "description": metadata.description,
        "tags": metadata.tags,
        "uploader": metadata.uploader,
        "channel": metadata.channel,
        "webpage_url": metadata.webpage_url,
    }


def load_protected_terms(path: str | Path) -> list[str]:
    term_path = Path(path).expanduser()
    if not term_path.exists():
        return []
    return _dedupe_terms(
        line.strip()
        for line in term_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _extract_json_object(text: str) -> dict:
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    match = JSON_RE.search(text)
    if not match:
        raise RuntimeError(f"Could not find JSON in translator response: {text}")
    return json.loads(match.group(0))


def _is_ollama_base_url(base_url: str) -> bool:
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").strip().lower()
    port = parsed.port
    return hostname in {"127.0.0.1", "localhost", "0.0.0.0", "::1"} and port == 11434


def _ollama_native_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}".rstrip("/")


def _should_retry_completion(error: Exception) -> bool:
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        status_code = error.response.status_code if error.response is not None else None
        return status_code == 429 or (status_code is not None and status_code >= 500)
    if isinstance(error, requests.RequestException):
        return True
    return False


def _batched(items: list[Segment], batch_size: int) -> Iterable[list[Segment]]:
    for index in range(0, len(items), batch_size):
        yield items[index : index + batch_size]


def _build_chat_messages(model: str, system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    if _requires_user_first_template(model):
        return [
            {
                "role": "user",
                "content": (
                    "Follow these translation rules exactly.\n"
                    f"{system_prompt}\n\n"
                    f"{user_prompt}"
                ),
            }
        ]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _requires_user_first_template(model: str) -> bool:
    return model.strip().lower().startswith("translategemma")


def _requires_completion_api(model: str) -> bool:
    return model.strip().lower().startswith("translategemma")


def _dedupe_terms(terms: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_term in terms:
        term = raw_term.strip()
        if not term or term in seen:
            continue
        deduped.append(term)
        seen.add(term)
    return deduped


def _build_placeholder_maps(protected_terms: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    term_to_placeholder = {
        term: PROTECTED_PLACEHOLDER_TEMPLATE.format(index=index)
        for index, term in enumerate(protected_terms, start=1)
    }
    placeholder_to_term = {
        placeholder: term
        for term, placeholder in term_to_placeholder.items()
    }
    return term_to_placeholder, placeholder_to_term


def _mask_segment_payload(
    payload_segments: list[dict[str, str | int]],
    protected_terms: list[str],
) -> tuple[list[dict[str, str | int]], dict[int, list[str]], dict[str, str]]:
    term_to_placeholder, placeholder_to_term = _build_placeholder_maps(protected_terms)
    masked_segments: list[dict[str, str | int]] = []
    required_placeholders: dict[int, list[str]] = {}
    for item in payload_segments:
        segment_id = int(item["id"])
        masked_text, placeholders = _mask_text(str(item["text"]), term_to_placeholder)
        masked_item = dict(item)
        masked_item["id"] = segment_id
        masked_item["text"] = masked_text
        masked_segments.append(masked_item)
        required_placeholders[segment_id] = placeholders
    return masked_segments, required_placeholders, placeholder_to_term


def _restore_segment_translations(
    translations: list[dict[str, str | int]],
    required_placeholders: dict[int, list[str]],
    placeholder_to_term: dict[str, str],
) -> list[dict[str, str | int]]:
    restored: list[dict[str, str | int]] = []
    for item in translations:
        segment_id = int(item["id"])
        restored.append(
            {
                "id": segment_id,
                "text": _restore_masked_text(
                    str(item["text"]).strip(),
                    required_placeholders.get(segment_id, []),
                    placeholder_to_term,
                ),
            }
        )
    return restored


def _mask_metadata_payload(
    payload: dict[str, object],
    protected_terms: list[str],
) -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    term_to_placeholder, placeholder_to_term = _build_placeholder_maps(protected_terms)
    masked_title, title_placeholders = _mask_text(str(payload.get("title") or ""), term_to_placeholder)
    masked_description, description_placeholders = _mask_text(
        str(payload.get("description") or ""),
        term_to_placeholder,
    )
    masked_tags: list[str] = []
    tag_placeholders: list[list[str]] = []
    for tag in payload.get("tags") or []:
        masked_tag, placeholders = _mask_text(str(tag), term_to_placeholder)
        masked_tags.append(masked_tag)
        tag_placeholders.append(placeholders)
    return (
        {
            **payload,
            "title": masked_title,
            "description": masked_description,
            "tags": masked_tags,
        },
        {
            "title": title_placeholders,
            "description": description_placeholders,
            "tags": tag_placeholders,
        },
        placeholder_to_term,
    )


def _mask_text(text: str, term_to_placeholder: dict[str, str]) -> tuple[str, list[str]]:
    if not text or not term_to_placeholder:
        return text, []
    placeholders: list[str] = []
    masked_text = text
    for term in sorted(term_to_placeholder, key=len, reverse=True):
        placeholder = term_to_placeholder[term]
        if term not in masked_text:
            continue
        masked_text = masked_text.replace(term, placeholder)
        placeholders.append(placeholder)
    return masked_text, placeholders


def _restore_masked_text(
    text: str,
    required_placeholders: list[str],
    placeholder_to_term: dict[str, str],
) -> str:
    normalized_text, unknown_tokens = _normalize_placeholder_variants(text, placeholder_to_term)
    present_placeholders = [
        placeholder for placeholder in placeholder_to_term if placeholder in normalized_text
    ]
    missing = [placeholder for placeholder in required_placeholders if placeholder not in present_placeholders]
    if missing:
        missing_terms = [placeholder_to_term[placeholder] for placeholder in missing]
        raise RuntimeError(
            f"Protected terms were altered by the translator: {missing_terms}"
        )
    unexpected = [placeholder_to_term[placeholder] for placeholder in present_placeholders if placeholder not in required_placeholders]
    if unexpected or unknown_tokens:
        details: list[str] = []
        if unexpected:
            details.append(f"unexpected protected terms appeared: {unexpected}")
        if unknown_tokens:
            details.append(f"unknown placeholder tokens appeared: {unknown_tokens}")
        raise RuntimeError("; ".join(details))
    restored = normalized_text
    for placeholder, term in placeholder_to_term.items():
        restored = restored.replace(placeholder, term)
    return restored.strip()


def _normalize_placeholder_variants(
    text: str,
    placeholder_to_term: dict[str, str],
) -> tuple[str, list[str]]:
    unknown_tokens: list[str] = []

    def replacer(match: re.Match[str]) -> str:
        token_id = match.group("id")
        raw_token = match.group(0)
        if not token_id.isdigit():
            unknown_tokens.append(raw_token)
            return raw_token
        normalized = PROTECTED_PLACEHOLDER_TEMPLATE.format(index=int(token_id))
        if normalized not in placeholder_to_term:
            unknown_tokens.append(raw_token)
            return raw_token
        return normalized

    return PLACEHOLDER_VARIANT_RE.sub(replacer, text), unknown_tokens


def _subtitle_completion_prompt(
    text: str,
    placeholders: list[str],
    strict: bool,
    duration: float = 0.0,
    target_cps: float = 4.5,
    char_tolerance: float = 0.2,
) -> str:
    min_budget, max_budget = _subtitle_char_budget(
        duration=duration,
        target_cps=target_cps,
        char_tolerance=char_tolerance,
    )
    budget_hint = (
        f"Target duration: {duration:.1f}s. Chinese character count (excluding spaces and punctuation) "
        f"must be between {min_budget} and {max_budget}. "
    ) if duration > 0 else ""
    return (
        "Translate the following English subtitle into concise spoken Simplified Chinese. "
        f"{budget_hint}"
        f"{_placeholder_instruction(placeholders, strict)} "
        "Return only the translation. No explanations. No quotes.\n\n"
        f"English: {text}\n"
        "Chinese:"
    )


def _metadata_completion_prompt(
    field_name: str,
    text: str,
    placeholders: list[str],
    strict: bool,
) -> str:
    return (
        f"Translate the following YouTube {field_name} into concise natural Simplified Chinese. "
        f"{_placeholder_instruction(placeholders, strict)} "
        "Return only the translation. No explanations. No quotes.\n\n"
        f"English: {text}\n"
        "Chinese:"
    )


def _completion_max_tokens(text: str, minimum: int, maximum: int) -> int:
    estimated = len(text) * 2 + 24
    return max(minimum, min(maximum, estimated))


def _subtitle_char_budget(
    duration: float,
    target_cps: float,
    char_tolerance: float,
) -> tuple[int, int]:
    slot_duration = max(0.01, duration)
    cps = max(1.0, target_cps)
    base = slot_duration * cps
    tolerance = min(max(0.0, char_tolerance), 0.95)
    min_budget = max(2, int(math.floor(base * (1.0 - tolerance))))
    max_budget = max(min_budget, int(math.ceil(base * (1.0 + tolerance))))
    return min_budget, max_budget


def _count_spoken_characters(text: str) -> int:
    count = 0
    for char in text:
        if char.isspace():
            continue
        if unicodedata.category(char).startswith("P"):
            continue
        count += 1
    return count


def _placeholder_instruction(placeholders: list[str], strict: bool) -> str:
    if not placeholders:
        return (
            "No VC_TERM placeholder appears in this English input. "
            "Do not output any VC_TERM placeholder token."
        )
    joined = ", ".join(placeholders)
    if strict:
        return (
            "The Chinese output must include these placeholders exactly as written: "
            f"{joined}. Do not omit, rename, or paraphrase them."
        )
    return f"Preserve these placeholders exactly if they appear: {joined}."


def _clean_completion_translation(text: str) -> str:
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


def _translate_completion_field(
    translator: OpenAICompatibleTranslator,
    text: str,
    field_name: str,
    term_to_placeholder: dict[str, str],
    placeholder_to_term: dict[str, str],
) -> str:
    if not text.strip():
        return ""
    return _translate_completion_text(
        translator=translator,
        text=text,
        prompt_builder=lambda masked_text, placeholders, strict: _metadata_completion_prompt(
            field_name,
            masked_text,
            placeholders,
            strict,
        ),
        term_to_placeholder=term_to_placeholder,
        placeholder_to_term=placeholder_to_term,
        max_tokens=_completion_max_tokens(text, minimum=96, maximum=384),
        empty_error_label=f"metadata field {field_name}",
    )


def _translate_completion_text(
    translator: OpenAICompatibleTranslator,
    text: str,
    prompt_builder,
    term_to_placeholder: dict[str, str],
    placeholder_to_term: dict[str, str],
    max_tokens: int,
    empty_error_label: str,
) -> str:
    masked_text, placeholders = _mask_text(text, term_to_placeholder)
    last_error: Exception | None = None
    for strict in (False, True):
        completion = translator._complete_text(
            prompt=prompt_builder(masked_text, placeholders, strict),
            max_tokens=max_tokens,
        )
        cleaned = _clean_completion_translation(completion)
        if not cleaned:
            last_error = RuntimeError(
                f"Completion translator returned empty text for {empty_error_label}"
            )
            continue
        try:
            return _restore_masked_text(cleaned, placeholders, placeholder_to_term)
        except RuntimeError as error:
            last_error = error
    raise RuntimeError(f"Failed to preserve protected terms for {empty_error_label}") from last_error


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped
