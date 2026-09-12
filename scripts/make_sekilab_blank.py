"""Write assets/sekilab_blank.pptx — lab widescreen, no content slides.

Slide size matches GUFM ``21_decks/_templates/Seki_Lab_Template_latest.pptx``
(20 in x 11.25 in) so ``qgis_figures_to_pptx`` default decks sit in the
Sekimoto-lab frame without appending to the 17-slide filled template.

    uv run --no-sync --extra pptx scripts/make_sekilab_blank.py
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation

# Matches GUFM 21_decks/_templates/Seki_Lab_Template_latest.pptx
SEKILAB_SLIDE_WIDTH = 18288000
SEKILAB_SLIDE_HEIGHT = 10287000

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "sekilab_blank.pptx"


def main() -> None:
    prs = Presentation()
    prs.slide_width = SEKILAB_SLIDE_WIDTH
    prs.slide_height = SEKILAB_SLIDE_HEIGHT
    OUT.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(OUT))
    print(f"wrote {OUT} layouts={len(prs.slide_layouts)} slides={len(prs.slides)}")


if __name__ == "__main__":
    main()
