"""Do the basemap presets still serve real tiles?

Deselected by default (``-m network``) so offline and CI runs stay green. Run
deliberately, e.g. before a release:

    uv run --no-sync pytest -m network -v

Why this exists: CARTO moved basemaps.cartocdn.com behind an API key, and the
failure was invisible. The tile server keeps answering **HTTP 200 with a valid
PNG** — it just paints "API KEY REQUIRED" across every pixel. So neither the
status code nor ``QgsRasterLayer.isValid()`` can tell a live basemap from a dead
one, and three presets quietly produced watermarked figures.

The discriminator that does work is colour complexity. A watermarked or blank
tile is nearly flat; a real map tile is not. Measured on the z12 Tokyo tile that
exposed the bug: CARTO's watermarked tile had **21** distinct colours against
**179** for a working service, and a real z14 city tile has 400-700.

Pick tiles with known dense content. A uniform tile is a false positive waiting
to happen — the z12 tile over Tokyo Bay is a single flat colour on *every*
provider, working or not, and briefly looked like evidence that Esri's dark
canvas was broken when it is fine.
"""

from __future__ import annotations

import io
import math
import urllib.error
import urllib.request

import pytest

from qgis_mcp_workflows.server import _BASEMAP_PRESETS, _resolve_basemap

pytestmark = pytest.mark.network

# Dense urban tiles, chosen so every provider has real content here.
TILES = [
    pytest.param(35.68, 139.76, 14, id="tokyo-shinjuku-z14"),
    pytest.param(48.857, 2.352, 14, id="paris-z14"),
]

# A watermarked CARTO tile measured 21; working tiles measured 179-724.
MIN_DISTINCT_COLOURS = 40
FETCH_TIMEOUT = 30


def _tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "qgis-mcp-workflows/test"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        pytest.skip(f"tile server unreachable: {exc}")


@pytest.mark.parametrize("preset", sorted(_BASEMAP_PRESETS))
@pytest.mark.parametrize(("lat", "lon", "z"), TILES)
def test_preset_serves_a_real_tile(preset: str, lat: float, lon: float, z: int) -> None:
    Image = pytest.importorskip("PIL.Image", reason="Pillow needed to inspect tile pixels")

    spec = _resolve_basemap(preset, 1.0)
    x, y = _tile_xy(lat, lon, z)
    url = spec["url"].replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))

    raw = _fetch(url)
    assert raw, f"{preset}: empty response body"

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    colours = img.getcolors(maxcolors=300_000) or []
    assert len(colours) >= MIN_DISTINCT_COLOURS, (
        f"{preset}: tile z{z}/{x}/{y} has only {len(colours)} distinct colours "
        f"({len(raw)}B from {url}). That is the signature of a watermarked, "
        f"key-walled or blank tile — the provider may have changed its terms."
    )
