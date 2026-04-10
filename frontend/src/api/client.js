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
