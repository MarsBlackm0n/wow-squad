"""Photos de profil des joueurs : validation, recadrage carré, réencodage WebP, index en mémoire pour les URLs.

Une photo appartient à un joueur (le champ `Character.player`), pas à un perso : tous ses persos l'affichent.
Le réencodage supprime les métadonnées (EXIF, position GPS des photos de téléphone).
"""

from __future__ import annotations

import io
from urllib.parse import quote

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from .db import PlayerPhoto, select

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SIZE = 256
MAX_PIXELS = 40_000_000  # garde-fou contre les images piégées (« bombes de décompression »)

# clé joueur -> horodatage de mise à jour (sert de version dans l'URL pour le cache navigateur)
_index: dict[str, int] = {}


def player_key(player: str | None) -> str:
    return " ".join((player or "").split()).lower()[:40]


def crop_box(width: int, height: int, zoom: float = 1.0, cx: float = 0.5, cy: float = 0.5) -> tuple[int, int, int, int]:
    """Carré à découper : côté = petit côté / zoom, centré sur (cx, cy) en fraction de l'image, sans déborder.

    Même calcul que l'aperçu côté navigateur (static/app.js), pour que le résultat soit celui affiché.
    """
    zoom = min(max(zoom, 1.0), 4.0)
    side = min(width, height) / zoom
    left = min(max(cx * width - side / 2, 0), width - side)
    top = min(max(cy * height - side / 2, 0), height - side)
    return round(left), round(top), round(left + side), round(top + side)


def process(data: bytes, zoom: float = 1.0, cx: float = 0.5, cy: float = 0.5) -> bytes:
    """Image envoyée -> WebP carré 256×256 recadré selon (zoom, cx, cy). Lève ValueError avec un message lisible."""
    if not data:
        raise ValueError("Le fichier est vide.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("L'image dépasse 5 Mo. Choisis une image plus légère.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.width * probe.height > MAX_PIXELS:
                raise ValueError("L'image est trop grande (dimensions).")
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            img.seek(0)  # GIF animé : première image
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            img = img.crop(crop_box(img.width, img.height, zoom, cx, cy))
            img = img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "WEBP", quality=84, method=6)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Ce fichier n'est pas une image reconnue (JPG, PNG, WebP ou GIF).") from None


def refresh(db: Session) -> None:
    rows = db.execute(select(PlayerPhoto.key, PlayerPhoto.updated_at)).all()
    _index.clear()
    _index.update({key: int(updated.timestamp()) for key, updated in rows})


def url_for(player: str | None) -> str | None:
    key = player_key(player)
    if key and key in _index:
        return f"/photo/{quote(key)}?v={_index[key]}"
    return None
