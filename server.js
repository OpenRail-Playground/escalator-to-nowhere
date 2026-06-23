const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const PORT = process.env.PORT ? Number(process.env.PORT) : 5510;
const ROOT_DIR = __dirname;
const TARGET_BASE = "http://192.168.46.51:6666";
const TARGET_PATH = "/geoserver-datanet-inframaps-prod-servant/ows";
const OVERPASS_ENDPOINTS = [
  "https://overpass-api.de/api/interpreter",
  "https://lz4.overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
];
const platformCache = new Map();
const wmsCache = new Map();
const CACHE_TTL_MS = 60 * 60 * 10000;
const PLATFORM_CACHE_MAX_ENTRIES = 30000;
const WMS_CACHE_MAX_ENTRIES = 80000;
const cacheStats = {
  platform: {
    hits: 0,
    misses: 0,
    evictions: 0,
    expirations: 0,
    writes: 0,
  },
  wms: {
    hits: 0,
    misses: 0,
    evictions: 0,
    expirations: 0,
    writes: 0,
  },
};

const mimeTypes = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

function sendNotFound(res) {
  res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
  res.end("Not found");
}

function setCacheEntry(cache, key, value, maxEntries, stats) {
  const isUpdate = cache.has(key);
  if (!isUpdate && cache.size >= maxEntries) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey) {
      cache.delete(oldestKey);
      if (stats) {
        stats.evictions += 1;
      }
    }
  }
  cache.set(key, { timestamp: Date.now(), ...value });
  if (stats) {
    stats.writes += 1;
  }
}

function getFreshCacheEntry(cache, key, stats) {
  const cached = cache.get(key);
  if (!cached) {
    if (stats) {
      stats.misses += 1;
    }
    return null;
  }

  if (Date.now() - cached.timestamp >= CACHE_TTL_MS) {
    cache.delete(key);
    if (stats) {
      stats.misses += 1;
      stats.expirations += 1;
    }
    return null;
  }

  if (stats) {
    stats.hits += 1;
  }

  return cached;
}

function getWmsCacheBytes() {
  let total = 0;
  wmsCache.forEach((entry) => {
    if (entry && entry.body && typeof entry.body.length === "number") {
      total += entry.body.length;
    }
  });
  return total;
}

function serveCacheStats(res) {
  const payload = {
    ttl_ms: CACHE_TTL_MS,
    platform_cache: {
      size: platformCache.size,
      max_entries: PLATFORM_CACHE_MAX_ENTRIES,
      ...cacheStats.platform,
    },
    wms_cache: {
      size: wmsCache.size,
      max_entries: WMS_CACHE_MAX_ENTRIES,
      approx_bytes: getWmsCacheBytes(),
      ...cacheStats.wms,
    },
  };

  res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload, null, 2));
}

function proxyWms(req, res, requestUrl) {
  const targetUrl = new URL(TARGET_PATH + requestUrl.search, TARGET_BASE);
  const cacheKey = targetUrl.href;
  const cached = getFreshCacheEntry(wmsCache, cacheKey, cacheStats.wms);

  if (cached) {
    res.writeHead(cached.statusCode, cached.headers);
    res.end(cached.body);
    return;
  }

  const options = {
    protocol: targetUrl.protocol,
    hostname: targetUrl.hostname,
    port: targetUrl.port || 80,
    method: "GET",
    path: targetUrl.pathname + targetUrl.search,
    headers: {
      Accept: req.headers.accept || "*/*",
      "User-Agent": "local-map-proxy/1.0",
    },
  };

  const proxyReq = http.request(options, (proxyRes) => {
    const chunks = [];
    proxyRes.on("data", (chunk) => {
      chunks.push(chunk);
    });

    proxyRes.on("end", () => {
      const statusCode = proxyRes.statusCode || 502;
      const body = Buffer.concat(chunks);
      const headers = {
        ...proxyRes.headers,
        "cache-control":
          proxyRes.headers["cache-control"] || "public, max-age=3600",
      };

      if (statusCode >= 200 && statusCode < 400) {
        setCacheEntry(
          wmsCache,
          cacheKey,
          { statusCode, headers, body },
          WMS_CACHE_MAX_ENTRIES,
          cacheStats.wms,
        );
      }

      res.writeHead(statusCode, headers);
      res.end(body);
    });
  });

  proxyReq.on("error", () => {
    res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Failed to reach aerial WMS server");
  });

  proxyReq.end();
}

function requestJson(endpointUrl, queryBody) {
  return new Promise((resolve, reject) => {
    const targetUrl = new URL(endpointUrl);
    const body = `data=${encodeURIComponent(queryBody)}`;
    const client = targetUrl.protocol === "https:" ? https : http;

    const req = client.request(
      {
        protocol: targetUrl.protocol,
        hostname: targetUrl.hostname,
        port: targetUrl.port || (targetUrl.protocol === "https:" ? 443 : 80),
        method: "POST",
        path: targetUrl.pathname + targetUrl.search,
        headers: {
          "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
          "Content-Length": Buffer.byteLength(body),
          Accept: "application/json",
          "User-Agent": "local-map-proxy/1.0",
        },
        timeout: 15000,
      },
      (upstreamRes) => {
        let raw = "";

        upstreamRes.setEncoding("utf8");
        upstreamRes.on("data", (chunk) => {
          raw += chunk;
        });

        upstreamRes.on("end", () => {
          if ((upstreamRes.statusCode || 500) >= 400) {
            reject(new Error(`Overpass HTTP ${upstreamRes.statusCode}`));
            return;
          }

          try {
            const payload = JSON.parse(raw);
            if (!payload || !Array.isArray(payload.elements)) {
              reject(new Error("Invalid Overpass response"));
              return;
            }
            resolve(payload);
          } catch (error) {
            reject(new Error("Failed to parse Overpass response"));
          }
        });
      },
    );

    req.on("timeout", () => {
      req.destroy(new Error("Overpass request timeout"));
    });
    req.on("error", reject);
    req.write(body);
    req.end();
  });
}

function isValidBbox(bbox) {
  if (!bbox) {
    return false;
  }

  const parts = bbox.split(",").map((part) => Number(part));
  if (parts.length !== 4 || parts.some((value) => !Number.isFinite(value))) {
    return false;
  }

  const [south, west, north, east] = parts;
  return south < north && west < east;
}

async function servePlatforms(res, requestUrl) {
  const bbox = requestUrl.searchParams.get("bbox") || "";
  if (!isValidBbox(bbox)) {
    res.writeHead(400, { "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ error: "Invalid bbox parameter" }));
    return;
  }

  const cached = getFreshCacheEntry(platformCache, bbox, cacheStats.platform);
  if (cached) {
    res.writeHead(200, {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
      "X-Cache": "HIT",
    });
    res.end(JSON.stringify(cached.payload));
    return;
  }

  const overpassQuery = `
[out:json][timeout:20];
(
  node["railway"="platform"](${bbox});
  way["railway"="platform"](${bbox});
  relation["railway"="platform"](${bbox});
  node["public_transport"="platform"](${bbox});
  way["public_transport"="platform"](${bbox});
  relation["public_transport"="platform"](${bbox});
  node["railway"~"^(station|halt|stop)$"](${bbox});
  way["railway"~"^(station|halt|stop)$"](${bbox});
  relation["railway"~"^(station|halt|stop)$"](${bbox});
  node["public_transport"="station"](${bbox});
  way["public_transport"="station"](${bbox});
  relation["public_transport"="station"](${bbox});
  relation["public_transport"="stop_area"](${bbox});
);
(._;>;);
out geom;
`;

  let payload = null;
  let lastError = null;

  for (const endpoint of OVERPASS_ENDPOINTS) {
    try {
      payload = await requestJson(endpoint, overpassQuery);
      break;
    } catch (error) {
      lastError = error;
    }
  }

  if (!payload) {
    res.writeHead(503, { "Content-Type": "application/json; charset=utf-8" });
    res.end(
      JSON.stringify({
        error: "Platform service unavailable",
        detail: lastError ? lastError.message : "Unknown error",
      }),
    );
    return;
  }

  setCacheEntry(
    platformCache,
    bbox,
    { payload },
    PLATFORM_CACHE_MAX_ENTRIES,
    cacheStats.platform,
  );
  res.writeHead(200, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "public, max-age=3600",
    "X-Cache": "MISS",
  });
  res.end(JSON.stringify(payload));
}

function serveStatic(res, pathname) {
  const normalizedPath =
    pathname === "/" ? "fe/index.html" : pathname.replace(/^\/+/, "");
  const filePath = path.normalize(path.join(ROOT_DIR, normalizedPath));

  if (!filePath.startsWith(ROOT_DIR)) {
    sendNotFound(res);
    return;
  }

  fs.stat(filePath, (statErr, stats) => {
    if (statErr || !stats.isFile()) {
      sendNotFound(res);
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const contentType = mimeTypes[ext] || "application/octet-stream";

    res.writeHead(200, { "Content-Type": contentType });
    fs.createReadStream(filePath).pipe(res);
  });
}

const server = http.createServer(async (req, res) => {
  const requestUrl = new URL(
    req.url,
    `http://${req.headers.host || "localhost"}`,
  );

  if (requestUrl.pathname === "/cache-stats") {
    serveCacheStats(res);
    return;
  }

  if (requestUrl.pathname === "/wms") {
    proxyWms(req, res, requestUrl);
    return;
  }

  if (requestUrl.pathname === "/platforms") {
    try {
      await servePlatforms(res, requestUrl);
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ error: "Internal server error" }));
    }
    return;
  }

  serveStatic(res, requestUrl.pathname);
});

server.listen(PORT, () => {
  console.log(`Server running at http://localhost:${PORT}`);
});
