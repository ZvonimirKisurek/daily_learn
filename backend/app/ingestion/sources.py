from pathlib import Path

import yaml

from app.ingestion.models import SourceSpec


def load_sources(path: str | Path) -> list[SourceSpec]:
    """Load + validate one YAML catalogue. A file-level `track:` key applies to every entry that does not set its own.
    Raises on schema errors or duplicate slugs."""
    raw = yaml.safe_load(Path(path).read_text())
    track = raw.get("track", "ai")
    specs = [SourceSpec.model_validate({"track": track, **entry}) for entry in raw["sources"]]
    seen: set[str] = set()
    for s in specs:
        if s.slug in seen:
            raise ValueError(f"duplicate source slug: {s.slug}")
        seen.add(s.slug)
    return specs


def load_all_sources(settings) -> list[SourceSpec]:
    """Every catalogue the app ingests: the AI one (SOURCES_FILE) plus the extra tracks' files (EXTRA_SOURCES_FILES,
    comma-separated; missing files are skipped so an old checkout keeps working)."""
    specs = load_sources(settings.sources_file)
    seen = {s.slug for s in specs}
    for extra in [p.strip() for p in settings.extra_sources_files.split(",") if p.strip()]:
        if not Path(extra).exists():
            continue
        for s in load_sources(extra):
            if s.slug in seen:
                raise ValueError(f"duplicate source slug across catalogues: {s.slug}")
            seen.add(s.slug)
            specs.append(s)
    return specs
