# Tag Frontend (Live MQTT Data)

This frontend reads live data produced by `tag/pc/u1-mqtt-subscribe.py`.

## Data flow

- `tag/rpi/u1-mqtt-publish.py` publishes raw MQTT payloads.
- `tag/pc/u1-mqtt-subscribe.py` stores into:
  - `cloud_u1raw.db` table `raw_data`
  - `cloud_u1fea.db` table `processed_data`
- `tag/frontend/server.js` exposes REST APIs that query those tables.
- React UI polls `/api/*` and renders the dashboard.

## Run

```bash
cd tag/frontend
npm install
```

Terminal 1:

```bash
cd tag/frontend
npm run api
```

Terminal 2:

```bash
cd tag/frontend
npm run dev
```

By default, API DB paths are:

- `../pc/cloud_u1raw.db`
- `../pc/cloud_u1fea.db`

Override with env vars if needed:

```bash
RAW_DB_PATH="/absolute/path/cloud_u1raw.db" PROCESSED_DB_PATH="/absolute/path/cloud_u1fea.db" npm run api
```
