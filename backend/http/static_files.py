from __future__ import annotations

from ..config import Settings


def validate_production_web_dist(settings: Settings) -> None:
    if not settings.production:
        return
    index = settings.web_dist_dir / "index.html"
    if not index.is_file():
        raise RuntimeError(
            "WEB_DIST_DIR must contain index.html when PRODUCTION=true: "
            f"{settings.web_dist_dir}"
        )