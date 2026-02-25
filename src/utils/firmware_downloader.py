"""
Firmware Downloader for Meshtastic devices.

Downloads and caches firmware releases from GitHub.
Supports version management and checksum verification.
"""

import hashlib
import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


@dataclass
class FirmwareRelease:
    """Represents a firmware release."""
    version: str
    tag_name: str
    release_date: str
    prerelease: bool
    assets: List[Dict[str, Any]] = field(default_factory=list)
    download_url: str = ""
    size_bytes: int = 0

    @property
    def is_stable(self) -> bool:
        """Check if this is a stable release."""
        return not self.prerelease and "alpha" not in self.version.lower()


@dataclass
class FirmwareAsset:
    """Represents a downloadable firmware asset."""
    name: str
    download_url: str
    size_bytes: int
    content_type: str
    hardware_type: str = ""

    @classmethod
    def from_github_asset(cls, asset: Dict[str, Any]) -> "FirmwareAsset":
        """Create from GitHub API response."""
        name = asset.get("name", "")
        # Determine hardware type from filename
        hw_type = ""
        name_lower = name.lower()
        if "tbeam" in name_lower:
            hw_type = "tbeam"
        elif "heltec" in name_lower:
            hw_type = "heltec"
        elif "tlora" in name_lower:
            hw_type = "tlora"
        elif "rak4631" in name_lower:
            hw_type = "rak4631"
        elif "esp32" in name_lower:
            hw_type = "esp32"

        return cls(
            name=name,
            download_url=asset.get("browser_download_url", ""),
            size_bytes=asset.get("size", 0),
            content_type=asset.get("content_type", ""),
            hardware_type=hw_type
        )


class FirmwareDownloader:
    """Downloads and manages Meshtastic firmware."""

    GITHUB_RELEASES_URL = "https://api.github.com/repos/meshtastic/firmware/releases"
    CACHE_TTL = timedelta(hours=1)
    USER_AGENT = "MeshForge/1.0"

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize firmware downloader.

        Args:
            cache_dir: Directory for cached firmware files.
        """
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = get_real_user_home() / ".config" / "meshforge" / "firmware"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._releases_cache: Dict[str, Any] = {}
        self._cache_timestamp: Optional[datetime] = None

        logger.info(f"[Firmware] Cache directory: {self.cache_dir}")

    def _get_ssl_context(self) -> ssl.SSLContext:
        """Get SSL context for HTTPS requests."""
        ctx = ssl.create_default_context()
        return ctx

    def _api_request(self, url: str) -> Any:
        """Make GitHub API request.

        Args:
            url: API endpoint URL.

        Returns:
            Parsed JSON response.
        """
        req = urllib.request.Request(url)
        req.add_header("User-Agent", self.USER_AGENT)
        req.add_header("Accept", "application/vnd.github.v3+json")

        try:
            with urllib.request.urlopen(
                req, timeout=15, context=self._get_ssl_context()
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                logger.warning("[Firmware] GitHub API rate limit reached")
                raise RuntimeError("GitHub API rate limit. Try again later.")
            raise
        except urllib.error.URLError as e:
            logger.error(f"[Firmware] Network error: {e.reason}")
            raise RuntimeError(f"Network error: {e.reason}")

    def get_releases(self, include_prereleases: bool = False,
                     force_refresh: bool = False) -> List[FirmwareRelease]:
        """Get available firmware releases.

        Args:
            include_prereleases: Include pre-release versions.
            force_refresh: Force cache refresh.

        Returns:
            List of firmware releases.
        """
        # Check cache
        cache_key = f"releases_{include_prereleases}"
        if not force_refresh and self._is_cache_valid():
            if cache_key in self._releases_cache:
                return self._releases_cache[cache_key]

        logger.info("[Firmware] Fetching releases from GitHub...")

        try:
            data = self._api_request(self.GITHUB_RELEASES_URL)
        except Exception as e:
            logger.error(f"[Firmware] Failed to fetch releases: {e}")
            # Return cached data if available
            if cache_key in self._releases_cache:
                return self._releases_cache[cache_key]
            return []

        releases = []
        for item in data:
            release = FirmwareRelease(
                version=item.get("tag_name", "").lstrip("v"),
                tag_name=item.get("tag_name", ""),
                release_date=item.get("published_at", ""),
                prerelease=item.get("prerelease", False),
                assets=[
                    FirmwareAsset.from_github_asset(a).__dict__
                    for a in item.get("assets", [])
                ]
            )

            if include_prereleases or release.is_stable:
                releases.append(release)

        # Update cache
        self._releases_cache[cache_key] = releases
        self._cache_timestamp = datetime.now()

        logger.info(f"[Firmware] Found {len(releases)} releases")
        return releases

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if not self._cache_timestamp:
            return False
        return datetime.now() - self._cache_timestamp < self.CACHE_TTL

    def get_latest_version(self, stable_only: bool = True) -> Optional[str]:
        """Get latest available firmware version.

        Args:
            stable_only: Only consider stable releases.

        Returns:
            Version string or None.
        """
        releases = self.get_releases(include_prereleases=not stable_only)
        if releases:
            return releases[0].version
        return None

    def get_assets_for_hardware(self, version: str,
                                 hardware_type: str) -> List[FirmwareAsset]:
        """Get firmware assets for a specific hardware type.

        Args:
            version: Firmware version.
            hardware_type: Hardware type (e.g., "tbeam", "heltec").

        Returns:
            List of matching firmware assets.
        """
        releases = self.get_releases(include_prereleases=True)

        for release in releases:
            if release.version == version or release.tag_name == version:
                assets = []
                for asset_dict in release.assets:
                    asset = FirmwareAsset(**asset_dict)
                    if hardware_type.lower() in asset.name.lower():
                        assets.append(asset)
                return assets

        return []

    def download_firmware(self, asset: FirmwareAsset,
                          progress_callback=None) -> Path:
        """Download a firmware asset.

        Args:
            asset: Firmware asset to download.
            progress_callback: Optional callback(bytes_downloaded, total_bytes).

        Returns:
            Path to downloaded file.
        """
        # Create version directory
        filename = asset.name
        local_path = self.cache_dir / filename

        # Check if already downloaded
        if local_path.exists() and local_path.stat().st_size == asset.size_bytes:
            logger.info(f"[Firmware] Using cached: {filename}")
            return local_path

        logger.info(f"[Firmware] Downloading: {filename}")

        req = urllib.request.Request(asset.download_url)
        req.add_header("User-Agent", self.USER_AGENT)

        try:
            with urllib.request.urlopen(
                req, timeout=120, context=self._get_ssl_context()
            ) as response:
                total_size = int(response.headers.get("Content-Length", 0))
                downloaded = 0

                with open(local_path, "wb") as f:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)

                        if progress_callback:
                            progress_callback(downloaded, total_size)

            logger.info(f"[Firmware] Downloaded {downloaded} bytes to {local_path}")
            return local_path

        except Exception as e:
            # Clean up partial download
            if local_path.exists():
                local_path.unlink()
            logger.error(f"[Firmware] Download failed: {e}")
            raise

    def verify_firmware(self, firmware_path: Path,
                        expected_hash: Optional[str] = None) -> bool:
        """Verify firmware file integrity.

        Args:
            firmware_path: Path to firmware file.
            expected_hash: Optional SHA256 hash to verify against.

        Returns:
            True if verification passed.
        """
        if not firmware_path.exists():
            logger.error(f"[Firmware] File not found: {firmware_path}")
            return False

        # Calculate SHA256
        sha256 = hashlib.sha256()
        with open(firmware_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)

        file_hash = sha256.hexdigest()

        if expected_hash:
            if file_hash.lower() != expected_hash.lower():
                logger.error(f"[Firmware] Hash mismatch: {file_hash} != {expected_hash}")
                return False
            logger.info("[Firmware] Hash verified successfully")

        return True

    def get_cached_firmware(self) -> List[Path]:
        """Get list of cached firmware files.

        Returns:
            List of cached firmware paths.
        """
        return sorted(self.cache_dir.glob("*.bin"))

    def clear_cache(self) -> int:
        """Clear firmware cache.

        Returns:
            Number of files deleted.
        """
        count = 0
        for f in self.cache_dir.glob("*.bin"):
            try:
                f.unlink()
                count += 1
            except Exception as e:
                logger.warning(f"[Firmware] Could not delete {f}: {e}")

        logger.info(f"[Firmware] Cleared {count} cached files")
        return count
