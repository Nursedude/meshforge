/**
 * MeshForge Map Tile Service Worker
 *
 * Provides offline tile caching for the node map.
 * Uses network-first strategy: try network, fall back to cache.
 */

// v2: purge Carto "API KEY REQUIRED" watermarked tiles cached under v1
// (Carto revoked anonymous basemap access 2026-08; dark basemap moved to Esri)
const CACHE_NAME = 'meshforge-tiles-v2';
const TILE_CACHE_LIMIT = 2000; // Max cached tiles (~100MB at 50KB/tile)

// Tile URL patterns to cache
const TILE_PATTERNS = [
    /basemaps\.cartocdn\.com/,
    /tile\.openstreetmap\.org/,
    /tiles\.stadiamaps\.com/,
    /server\.arcgisonline\.com/
];

// Check if URL is a map tile
function isTileRequest(url) {
    return TILE_PATTERNS.some(pattern => pattern.test(url));
}

// Install event - pre-cache essential resources
self.addEventListener('install', (event) => {
    console.log('[SW] Installing tile cache service worker');
    self.skipWaiting();
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
    console.log('[SW] Activating tile cache');
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames
                    .filter(name => name.startsWith('meshforge-tiles-') && name !== CACHE_NAME)
                    .map(name => {
                        console.log('[SW] Deleting old cache:', name);
                        return caches.delete(name);
                    })
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch event - intercept tile requests
self.addEventListener('fetch', (event) => {
    const url = event.request.url;

    // Only handle tile requests
    if (!isTileRequest(url)) {
        return;
    }

    event.respondWith(
        // Network-first strategy for tiles
        fetch(event.request)
            .then(response => {
                // Clone response for caching
                if (response.ok) {
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(event.request, responseClone);
                        // Trim cache if needed
                        trimCache(cache);
                    });
                }
                return response;
            })
            .catch(() => {
                // Network failed, try cache
                console.log('[SW] Network failed, trying cache for:', url);
                return caches.match(event.request).then(cachedResponse => {
                    if (cachedResponse) {
                        console.log('[SW] Serving from cache:', url);
                        return cachedResponse;
                    }
                    // Return placeholder for missing tiles
                    return new Response('', {
                        status: 404,
                        statusText: 'Tile not cached'
                    });
                });
            })
    );
});

// Trim cache to prevent storage bloat
async function trimCache(cache) {
    const keys = await cache.keys();
    if (keys.length > TILE_CACHE_LIMIT) {
        // Delete oldest entries (FIFO)
        const toDelete = keys.length - TILE_CACHE_LIMIT;
        console.log(`[SW] Trimming cache: removing ${toDelete} old tiles`);
        for (let i = 0; i < toDelete; i++) {
            await cache.delete(keys[i]);
        }
    }
}

// Message handler for cache control
self.addEventListener('message', (event) => {
    const { action, data } = event.data;

    switch (action) {
        case 'getCacheStats':
            getCacheStats().then(stats => {
                event.ports[0].postMessage(stats);
            });
            break;

        case 'clearCache':
            caches.delete(CACHE_NAME).then(() => {
                event.ports[0].postMessage({ success: true });
            });
            break;

        case 'downloadTiles':
            downloadTilesForArea(data).then(result => {
                event.ports[0].postMessage(result);
            });
            break;
    }
});

// Get cache statistics
async function getCacheStats() {
    try {
        const cache = await caches.open(CACHE_NAME);
        const keys = await cache.keys();

        // Estimate size (rough approximation)
        let totalSize = 0;
        for (const key of keys.slice(0, 100)) {
            const response = await cache.match(key);
            if (response) {
                const blob = await response.blob();
                totalSize += blob.size;
            }
        }
        const avgSize = keys.length > 0 ? totalSize / Math.min(keys.length, 100) : 0;
        const estimatedTotal = avgSize * keys.length;

        return {
            tileCount: keys.length,
            estimatedSizeMB: (estimatedTotal / (1024 * 1024)).toFixed(1),
            cacheLimit: TILE_CACHE_LIMIT
        };
    } catch (error) {
        return { error: error.message };
    }
}

// Pre-download tiles for a specific area
async function downloadTilesForArea({ bounds, minZoom, maxZoom }) {
    const cache = await caches.open(CACHE_NAME);
    let downloaded = 0;
    let failed = 0;

    // Generate tile URLs for the area
    const tiles = getTilesInBounds(bounds, minZoom, maxZoom);

    for (const { x, y, z } of tiles) {
        const url = `https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/${z}/${y}/${x}`;
        try {
            const response = await fetch(url);
            if (response.ok) {
                await cache.put(new Request(url), response);
                downloaded++;
            } else {
                failed++;
            }
        } catch (e) {
            failed++;
        }

        // Rate limiting to avoid overwhelming the server
        if (downloaded % 10 === 0) {
            await new Promise(r => setTimeout(r, 100));
        }
    }

    return { downloaded, failed, total: tiles.length };
}

// Calculate tiles in a bounding box
function getTilesInBounds(bounds, minZoom, maxZoom) {
    const tiles = [];
    const { north, south, east, west } = bounds;

    for (let z = minZoom; z <= maxZoom; z++) {
        const minTile = latLngToTile(north, west, z);
        const maxTile = latLngToTile(south, east, z);

        for (let x = minTile.x; x <= maxTile.x; x++) {
            for (let y = minTile.y; y <= maxTile.y; y++) {
                tiles.push({ x, y, z });
            }
        }
    }

    return tiles;
}

// Convert lat/lng to tile coordinates
function latLngToTile(lat, lng, zoom) {
    const n = Math.pow(2, zoom);
    const x = Math.floor((lng + 180) / 360 * n);
    const latRad = lat * Math.PI / 180;
    const y = Math.floor((1 - Math.asinh(Math.tan(latRad)) / Math.PI) / 2 * n);
    return { x: Math.max(0, x), y: Math.max(0, y) };
}
