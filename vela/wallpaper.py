"""The desk's custom wallpaper.

One image, stored beside the rest of the user's data as
`<data_dir>/wallpaper.<ext>`. Vela does not resize, re-encode or inspect it
beyond what it must: the type has to be one the browser can draw, and the file
has to be small enough that serving it does not get in the way of the desk
loading. The bundled wallpapers are not stored here — they ship with the
dashboard and cost nothing.
"""

from __future__ import annotations

from pathlib import Path

#: An 8 MB photograph is already generous for a desk background, and it is the
#: point past which loading it starts to be noticeable on a phone.
MAX_WALLPAPER_BYTES = 8 * 1024 * 1024

#: Only the formats every browser Vela supports can decode. No SVG: it is a
#: document, not a picture, and this one is drawn behind the whole shell.
WALLPAPER_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

#: The first bytes each accepted format really starts with. A content type is a
#: claim; this is a check.
_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
}


class WallpaperError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Wallpaper:
    def __init__(self, data_dir: Path):
        self._dir = data_dir

    def _candidates(self) -> list[Path]:
        return [self._dir / f"wallpaper{extension}" for extension in WALLPAPER_TYPES.values()]

    def path(self) -> Path | None:
        """The stored wallpaper, or None when the user has not set one."""
        for candidate in self._candidates():
            if candidate.is_file():
                return candidate
        return None

    def media_type(self, path: Path) -> str:
        for media_type, extension in WALLPAPER_TYPES.items():
            if path.suffix == extension:
                return media_type
        return "application/octet-stream"

    def extension_for(self, content_type: str) -> str:
        extension = WALLPAPER_TYPES.get((content_type or "").split(";")[0].strip().lower())
        if extension is None:
            raise WallpaperError(415, "A wallpaper must be a JPEG, PNG or WebP image")
        return extension

    def save(self, content: bytes, extension: str) -> dict:
        if len(content) > MAX_WALLPAPER_BYTES:
            raise WallpaperError(413, "A wallpaper is at most 8 MB")
        if not content:
            raise WallpaperError(422, "The image was empty")
        if not any(content.startswith(signature) for signature in _SIGNATURES[extension]):
            raise WallpaperError(422, "That file is not the image type it claims to be")
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / f"wallpaper{extension}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        # Only one wallpaper exists; changing format must not leave the old one
        # behind for `path()` to find first.
        for candidate in self._candidates():
            if candidate != target:
                candidate.unlink(missing_ok=True)
        return {"ok": True, "bytes": len(content)}

    def remove(self) -> dict:
        removed = False
        for candidate in self._candidates():
            if candidate.is_file():
                candidate.unlink()
                removed = True
        return {"ok": True, "removed": removed}
