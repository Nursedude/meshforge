"""Offline tile cache menu for AIToolsHandler.

Extracted from ai_tools.py for file size compliance (CLAUDE.md #6).

Host class must provide `self.ctx` (TUIContext).
"""

import logging

from utils.safe_import import safe_import

TileCache, HAWAII_BOUNDS, _HAS_TILE_CACHE = safe_import(
    'utils.tile_cache', 'TileCache', 'HAWAII_BOUNDS'
)

logger = logging.getLogger(__name__)


class TileCacheMixin:
    """Mixin: tile cache stats / download / estimate / clear menus."""

    def _tile_cache_menu(self):
        """Manage offline tile cache for maps."""
        while True:
            choices = [
                ("stats", "Cache Stats         View tile cache status"),
                ("download", "Download Region     Cache tiles for area"),
                ("estimate", "Estimate Size       Preview download size"),
                ("clear", "Clear Expired       Remove old tiles"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Offline Tile Cache",
                "Manage cached map tiles for offline use:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "stats": ("Cache Stats", self._tile_cache_stats),
                "download": ("Download Region", self._tile_cache_download),
                "estimate": ("Estimate Size", self._tile_cache_estimate),
                "clear": ("Clear Expired", self._tile_cache_clear),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _tile_cache_stats(self):
        """Display tile cache statistics."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            cache = TileCache()
            stats = cache.get_stats()

            info = [
                f"Cached Tiles: {stats['tile_count']}",
                f"Cache Size:   {stats['size_mb']:.1f} MB",
            ]
            if stats.get('oldest'):
                info.append(f"Oldest Tile:  {stats['oldest']}")
            if stats.get('newest'):
                info.append(f"Newest Tile:  {stats['newest']}")
            if stats['tile_count'] == 0:
                info.append("")
                info.append("No tiles cached yet. Use 'Download Region'")
                info.append("to cache tiles for offline map viewing.")

            self.ctx.dialog.msgbox("Tile Cache Stats", "\n".join(info))
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to get cache stats: {e}")

    def _tile_cache_download(self):
        """Download tiles for a geographic region."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            region_choices = [
                ("hawaii", "Hawaii              (18.5-22.5N, 160.5-154.5W)"),
                ("custom", "Custom Region       Enter coordinates"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Download Region",
                "Select region to cache tiles for:",
                region_choices
            )

            if choice is None or choice == "back":
                return

            if choice == "hawaii":
                bounds = HAWAII_BOUNDS
            elif choice == "custom":
                coords = self.ctx.dialog.inputbox(
                    "Custom Region",
                    "Enter bounds as: south,west,north,east\n"
                    "Example: 21.0,-158.5,21.7,-157.5"
                )
                if not coords:
                    return
                try:
                    parts = [float(x.strip()) for x in coords.split(',')]
                    if len(parts) != 4:
                        self.ctx.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                        return
                    bounds = tuple(parts)
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid coordinates.")
                    return
            else:
                return

            estimate = TileCache.estimate_download_size(bounds)
            if 'error' in estimate:
                self.ctx.dialog.msgbox("Error", estimate['error'])
                return

            confirm = self.ctx.dialog.yesno(
                "Confirm Download",
                f"Tiles to download: {estimate['total_tiles']}\n"
                f"Estimated size: {estimate['estimated_mb']:.1f} MB\n\n"
                "Proceed with download?"
            )

            if not confirm:
                return

            self.ctx.dialog.infobox("Downloading", "Caching tiles... This may take a while.")

            cache = TileCache()
            result = cache.download_region(bounds)

            if 'error' in result:
                self.ctx.dialog.msgbox("Error", result['error'])
            else:
                self.ctx.dialog.msgbox(
                    "Download Complete",
                    f"Downloaded: {result['downloaded']} tiles\n"
                    f"Skipped (cached): {result['skipped']}\n"
                    f"Failed: {result['failed']}"
                )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Tile download failed: {e}")

    def _tile_cache_estimate(self):
        """Estimate download size for a region."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            coords = self.ctx.dialog.inputbox(
                "Estimate Size",
                "Enter bounds as: south,west,north,east\n"
                "Example: 21.0,-158.5,21.7,-157.5\n"
                "(Leave empty for Hawaii)"
            )

            if coords:
                try:
                    parts = [float(x.strip()) for x in coords.split(',')]
                    if len(parts) != 4:
                        self.ctx.dialog.msgbox("Error", "Enter exactly 4 coordinates.")
                        return
                    bounds = tuple(parts)
                except ValueError:
                    self.ctx.dialog.msgbox("Error", "Invalid coordinates.")
                    return
            else:
                bounds = HAWAII_BOUNDS

            estimate = TileCache.estimate_download_size(bounds)

            if 'error' in estimate:
                self.ctx.dialog.msgbox("Error", estimate['error'])
            else:
                self.ctx.dialog.msgbox(
                    "Download Estimate",
                    f"Region: ({bounds[0]:.1f}, {bounds[1]:.1f}) to "
                    f"({bounds[2]:.1f}, {bounds[3]:.1f})\n"
                    f"Tile count: {estimate['total_tiles']}\n"
                    f"Estimated size: {estimate['estimated_mb']:.1f} MB\n"
                    f"Within limit: {'Yes' if estimate['within_limit'] else 'No'}"
                )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Estimation failed: {e}")

    def _tile_cache_clear(self):
        """Clear expired tiles from cache."""
        if not _HAS_TILE_CACHE:
            self.ctx.dialog.msgbox("Error", "Tile cache module not available.")
            return

        try:
            confirm = self.ctx.dialog.yesno(
                "Clear Expired Tiles",
                "Remove tiles older than 30 days?\n\n"
                "This frees disk space but requires re-download\n"
                "for offline use."
            )

            if not confirm:
                return

            cache = TileCache()
            result = cache.clear_expired()

            freed_mb = result['bytes_freed'] / (1024 * 1024)
            self.ctx.dialog.msgbox(
                "Cache Cleared",
                f"Removed: {result['removed']} expired tiles\n"
                f"Space freed: {freed_mb:.1f} MB"
            )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Cache clear failed: {e}")
