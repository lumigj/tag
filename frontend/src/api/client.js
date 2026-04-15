async function request(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Request failed (${response.status}) for ${path}`);
  }
  return response.json();
}

export async function getSummary() {
  return request("/api/summary");
}

export async function getRawLatest(limit = 120) {
  return request(`/api/raw/latest?limit=${limit}`);
}

export async function getProcessedLatest(limit = 80) {
  return request(`/api/processed/latest?limit=${limit}`);
}

export async function getLocatorLatest() {
  return request("/api/locator/latest");
}

export async function postRingDevice() {
  const response = await fetch("/api/ring", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) {
    const msg = body?.error || `Request failed (${response.status})`;
    throw new Error(msg);
  }
  return body;
}
/** Set per-beacon offsets `{ B1, B2, B3 }`, or `{ offset }` to use one value for all beacons. */
export async function postLocatorOffsets(payload) {
  const response = await fetch("/api/locator/offset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const errBody = await response.json().catch(() => ({}));
    throw new Error(errBody.error || `Request failed (${response.status}) for /api/locator/offset`);
  }
  return response.json();
}

/** Same value for B1, B2, and B3. */
export async function postLocatorOffset(offset) {
  return postLocatorOffsets({ offset });
}
