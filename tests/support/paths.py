import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
CORE_ROOT = REPOSITORY_ROOT / "turnstile_core"
FRONTEND_SOURCE = REPOSITORY_ROOT / "frontend" / "src"
INFRA_ROOT = REPOSITORY_ROOT / "infra"

_CSS_IMPORT = re.compile(r'^\s*@import\s+["\']([^"\']+)["\'];\s*$', re.MULTILINE)


def read_frontend_styles() -> str:
	def read_stylesheet(path: Path, stack: tuple[Path, ...]) -> str:
		resolved = path.resolve()
		if resolved in stack:
			raise AssertionError(f"Circular CSS import: {resolved}")
		source = resolved.read_text(encoding="utf-8")
		parts: list[str] = []
		cursor = 0
		for match in _CSS_IMPORT.finditer(source):
			target = match.group(1)
			if ":" in target or target.startswith("/"):
				raise AssertionError(f"Unsupported global CSS import: {target}")
			parts.append(source[cursor : match.start()])
			parts.append(read_stylesheet(resolved.parent / target, (*stack, resolved)))
			cursor = match.end()
		parts.append(source[cursor:])
		return "".join(parts)

	return read_stylesheet(FRONTEND_SOURCE / "styles.css", ())


def read_apim_dashboard_source() -> str:
	pages = FRONTEND_SOURCE / "data-sources" / "apim" / "pages"
	sources = [(pages / "dashboard-page.tsx").read_text(encoding="utf-8")]
	sources.extend(
		path.read_text(encoding="utf-8")
		for path in sorted(pages.glob("dashboard-*.tsx"))
		if path.name != "dashboard-page.tsx"
	)
	return "\n".join(sources)