from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path

from videocut.models import VideoMetadata


def load_video_metadata(path: Path) -> VideoMetadata:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_tags = payload.get("tags")
    tags = raw_tags if isinstance(raw_tags, list) else []
    return VideoMetadata(
        title=_clean_text(payload.get("title") or payload.get("fulltitle") or ""),
        description=str(payload.get("description") or "").strip(),
        tags=[str(tag).strip() for tag in tags if str(tag).strip()],
        uploader=_clean_text(payload.get("uploader") or ""),
        channel=_clean_text(payload.get("channel") or ""),
        video_id=_clean_text(payload.get("id") or ""),
        webpage_url=_clean_text(payload.get("webpage_url") or payload.get("original_url") or ""),
        upload_date=_format_upload_date(payload.get("upload_date")),
    )


def metadata_to_dict(metadata: VideoMetadata | None) -> dict[str, object] | None:
    if metadata is None:
        return None
    return {
        "title": metadata.title,
        "description": metadata.description,
        "tags": metadata.tags,
        "uploader": metadata.uploader,
        "channel": metadata.channel,
        "video_id": metadata.video_id,
        "webpage_url": metadata.webpage_url,
        "upload_date": metadata.upload_date,
    }


def metadata_from_dict(payload: dict[str, object] | None) -> VideoMetadata | None:
    if not isinstance(payload, dict):
        return None
    raw_tags = payload.get("tags")
    tags = raw_tags if isinstance(raw_tags, list) else []
    return VideoMetadata(
        title=str(payload.get("title") or ""),
        description=str(payload.get("description") or ""),
        tags=[str(tag).strip() for tag in tags if str(tag).strip()],
        uploader=str(payload.get("uploader") or ""),
        channel=str(payload.get("channel") or ""),
        video_id=str(payload.get("video_id") or ""),
        webpage_url=str(payload.get("webpage_url") or ""),
        upload_date=str(payload.get("upload_date") or ""),
    )


def export_publish_assets(
    output_dir: Path,
    source_metadata: VideoMetadata | None,
    localized_metadata: VideoMetadata | None,
    cover_image_path: Path | None,
    final_video: Path,
) -> dict[str, str | None]:
    publish_dir = output_dir / "publish"
    publish_dir.mkdir(parents=True, exist_ok=True)

    cover_output_path = _copy_cover_image(cover_image_path, publish_dir)
    title = _pick_text(
        localized_metadata.title if localized_metadata else "",
        source_metadata.title if source_metadata else "",
    )
    description = _pick_text(
        localized_metadata.description if localized_metadata else "",
        source_metadata.description if source_metadata else "",
    )
    tags = list(localized_metadata.tags) if localized_metadata and localized_metadata.tags else []
    if not tags and source_metadata is not None:
        tags = list(source_metadata.tags)

    title_path = publish_dir / "title.txt"
    tags_path = publish_dir / "tags.txt"
    description_path = publish_dir / "description.txt"
    metadata_path = publish_dir / "metadata.json"
    preview_path = publish_dir / "content_preview.html"

    # Safety net: ensure base title fits within 60 characters
    if len(title) > 60:
        title = title[:59].rstrip() + "…"
    tags = _sort_tags_by_importance(tags)
    title_path.write_text(f"{title}\n", encoding="utf-8")
    tags_path.write_text(", ".join(tags), encoding="utf-8")
    description_path.write_text(description, encoding="utf-8")

    metadata_payload = {
        "source": metadata_to_dict(source_metadata),
        "localized": metadata_to_dict(localized_metadata),
        "title": title,
        "tags": tags,
        "description": description,
        "cover_image": str(cover_output_path) if cover_output_path else None,
        "final_video": str(final_video),
    }
    metadata_path.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    preview_html = _build_preview_html(
        title=title,
        description=description,
        tags=tags,
        source_metadata=source_metadata,
        localized_metadata=localized_metadata,
        cover_image_path=cover_output_path,
        final_video=final_video,
        page_dir=publish_dir,
    )
    preview_path.write_text(preview_html, encoding="utf-8")
    return {
        "cover_image": str(cover_output_path) if cover_output_path else None,
        "metadata_json": str(metadata_path),
        "title_text": str(title_path),
        "tags_text": str(tags_path),
        "description_text": str(description_path),
        "preview_html": str(preview_path),
    }


def _copy_cover_image(cover_image_path: Path | None, publish_dir: Path) -> Path | None:
    if cover_image_path is None or not cover_image_path.exists():
        return None
    suffix = cover_image_path.suffix.lower() or ".jpg"
    output_path = publish_dir / f"cover{suffix}"
    if output_path.resolve() == cover_image_path.resolve():
        return output_path
    shutil.copy2(cover_image_path, output_path)
    return output_path


def _build_preview_html(
    title: str,
    description: str,
    tags: list[str],
    source_metadata: VideoMetadata | None,
    localized_metadata: VideoMetadata | None,
    cover_image_path: Path | None,
    final_video: Path,
    page_dir: Path,
) -> str:
    cover_rel = _relative_path(cover_image_path, page_dir) if cover_image_path else ""
    video_rel = _relative_path(final_video, page_dir)
    escaped_title = html.escape(title or "Untitled")
    description_html = "<br>".join(html.escape(description).splitlines()) or "No description."
    tags_html = "".join(f'<span class="tag">{html.escape(tag)}</span>' for tag in tags)
    channel_name = ""
    if source_metadata is not None:
        channel_name = source_metadata.channel or source_metadata.uploader
    source_url = source_metadata.webpage_url if source_metadata is not None else ""
    source_url_html = html.escape(source_url)
    source_title_html = html.escape(source_metadata.title) if source_metadata is not None else ""
    localized_title_html = html.escape(
        _pick_text(localized_metadata.title if localized_metadata else "", title)
    )
    upload_date = html.escape(source_metadata.upload_date) if source_metadata is not None else ""
    channel_html = html.escape(channel_name)
    poster_attr = f' poster="{html.escape(cover_rel)}"' if cover_rel else ""
    source_link = (
        f'<a class="source-link" href="{source_url_html}">{source_url_html}</a>' if source_url else ""
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --line: #d7dde4;
      --tag: #e8f1ff;
      --tag-text: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      background: linear-gradient(180deg, #eef2f7 0%, var(--bg) 100%);
      color: var(--text);
    }}
    .page {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }}
    .layout {{
      display: grid;
      gap: 24px;
      grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr);
      align-items: start;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 16px 36px rgba(15, 23, 42, 0.08);
      overflow: hidden;
    }}
    video, .cover {{
      display: block;
      width: 100%;
      background: #000;
    }}
    .cover-wrap {{
      border-top: 1px solid var(--line);
      padding: 16px;
    }}
    .cover-wrap h2,
    .meta h1,
    .meta h2 {{
      margin: 0 0 12px;
    }}
    .meta {{
      padding: 20px;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 12px;
    }}
    .title-pair {{
      padding: 16px 20px 0;
    }}
    .title-pair p {{
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }}
    .tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 18px 0;
    }}
    .tag {{
      padding: 6px 10px;
      border-radius: 999px;
      background: var(--tag);
      color: var(--tag-text);
      font-size: 13px;
      font-weight: 600;
    }}
    .description {{
      white-space: normal;
      line-height: 1.75;
      color: #1f2937;
    }}
    .source-link {{
      display: inline-block;
      margin-top: 12px;
      color: #2563eb;
      text-decoration: none;
      word-break: break-all;
    }}
    .source-link:hover {{ text-decoration: underline; }}
    @media (max-width: 920px) {{
      .layout {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <div class="layout">
      <section class="panel">
        <video controls preload="metadata"{poster_attr}>
          <source src="{html.escape(video_rel)}" type="video/mp4">
        </video>
        <div class="title-pair">
          <p>原始标题</p>
          <h2>{source_title_html or escaped_title}</h2>
          <p>中文标题</p>
          <h2>{localized_title_html}</h2>
        </div>
        {f'<div class="cover-wrap"><h2>封面</h2><img class="cover" src="{html.escape(cover_rel)}" alt="cover"></div>' if cover_rel else ''}
      </section>
      <aside class="panel meta">
        <div class="eyebrow">{channel_html}{' · ' if channel_html and upload_date else ''}{upload_date}</div>
        <h1>{escaped_title}</h1>
        <div class="tags">{tags_html}</div>
        <div class="description">{description_html}</div>
        {source_link}
      </aside>
    </div>
  </main>
</body>
</html>
"""


def _relative_path(path: Path, base_dir: Path) -> str:
    return os.path.relpath(path, start=base_dir)


def _sort_tags_by_importance(tags: list[str]) -> list[str]:
    """Sort tags: CJK first, then by length descending (more specific = more important)."""
    def _is_cjk(text: str) -> bool:
        return any("一" <= char <= "鿿" for char in text)

    seen: set[str] = set()
    deduped = []
    for tag in tags:
        lowered = tag.strip().lower()
        if lowered and lowered not in seen:
            deduped.append(tag.strip())
            seen.add(lowered)

    cjk_tags = [t for t in deduped if _is_cjk(t)]
    other_tags = [t for t in deduped if not _is_cjk(t)]
    cjk_tags.sort(key=lambda t: (-len(t), t))
    other_tags.sort(key=lambda t: (-len(t), t))
    return cjk_tags + other_tags


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _pick_text(primary: str, fallback: str) -> str:
    return primary.strip() or fallback.strip()


def _format_upload_date(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw
