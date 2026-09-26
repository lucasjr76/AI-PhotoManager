"""Dev only: write THIRD_PARTY_NOTICES.md from the runtime dependencies' metadata + models.

Usage: uv run python tools/third_party_notices.py
"""

import re
from importlib.metadata import distributions, requires
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "THIRD_PARTY_NOTICES.md"


def runtime_closure() -> list[str]:
    """aipdm's runtime dependencies, transitively (no dev group)."""
    names = {d.metadata["Name"].lower().replace("_", "-"): d for d in distributions()}
    seen: set[str] = set()
    todo = ["aipdm"]
    while todo:
        name = todo.pop()
        if name in seen or name not in names:
            continue
        seen.add(name)
        for req in requires(names[name].metadata["Name"]) or []:
            if "extra ==" in req:
                continue
            todo.append(re.split(r"[ ;<>=!~\[(]", req, maxsplit=1)[0].lower().replace("_", "-"))
    seen.discard("aipdm")
    return sorted(seen)


def license_of(name: str) -> str:
    for dist in distributions():
        if dist.metadata["Name"].lower().replace("_", "-") == name:
            meta = dist.metadata
            text = meta.get("License-Expression") or ""
            if not text:
                classifiers = [
                    c.split("::")[-1].strip()
                    for c in meta.get_all("Classifier") or []
                    if c.startswith("License")
                ]
                text = ", ".join(classifiers) or (meta.get("License") or "").splitlines()[0:1]
                text = text if isinstance(text, str) else (text[0] if text else "ver pacote")
            return f"{meta['Version']} | {text[:90]}"
    return "? | ?"


def main() -> None:
    rows = [f"| {n} | {license_of(n)} |" for n in runtime_closure()]
    models = (ROOT / "models" / "LICENSES.md").read_text(encoding="utf-8").split("\n", 1)[1]
    OUT.write_text(
        "# Avisos de terceiros\n\n"
        "O AI-PhotoDocsManager inclui os componentes abaixo, cada um sob sua própria licença.\n\n"
        "## Bibliotecas\n\n| Pacote | Versão | Licença |\n|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n**pillow-heif:** os binários incluem libheif/libde265 (LGPL-3.0) e x265 (GPL-2.0);"
        " o código-fonte está em https://github.com/bigcat88/pillow_heif.\n\n"
        "## Modelos e dados\n"
        + models
        + "\nDados de lugares: GeoNames (https://www.geonames.org), licença CC BY 4.0.\n",
        encoding="utf-8",
    )
    print(f"{OUT.name}: {len(rows)} pacotes")


if __name__ == "__main__":
    main()
