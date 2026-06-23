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
const PLATFORM_CACHE_TTL_MS = 30000;

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

function proxyWms(req, res, requestUrl) {
  const targetUrl = new URL(TARGET_PATH + requestUrl.search, TARGET_BASE);

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
    const headers = {
      ...proxyRes.headers,
      "cache-control": proxyRes.headers["cache-control"] || "no-cache",
    };
    res.writeHead(proxyRes.statusCode || 502, headers);
    proxyRes.pipe(res);
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

  const cached = platformCache.get(bbox);
  if (cached && Date.now() - cached.timestamp < PLATFORM_CACHE_TTL_MS) {
    res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
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

  platformCache.set(bbox, { timestamp: Date.now(), payload });
  res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

function serveStatic(res, pathname) {
  const normalizedPath = pathname === "/" ? "/fe/index.html" : pathname;
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
