# Moonraker Stream Extra Data Design

## Goal

Expose every Ravens Perch stream URL advertised for a camera through Moonraker webcam `extra_data`, so other software can discover RTSP, HLS, snapshot, and WebRTC options without relying on the browser frontend's primary WebRTC `stream_url`.

## Context

Ravens Perch currently registers each camera with Moonraker through `/server/webcams/item`. The registered webcam entry uses `service: webrtc-mediamtx`, a WebRTC `stream_url`, and a snapshot URL. Ravens Perch already builds the full stream set through `daemon.stream_manager.get_stream_urls()`, but Moonraker only receives the WebRTC URL as the primary stream.

Moonraker webcam entries include an `extra_data` object intended for custom frontend/application data. Storing stream metadata there keeps the data attached to the advertised webcam and avoids requiring consumers to query a separate Ravens Perch namespace.

Moonraker webcam API reference: https://moonraker.readthedocs.io/en/latest/external_api/webcams/

## Data Shape

Ravens Perch will write its metadata under a namespaced key:

```json
{
  "ravens_perch": {
    "schema_version": 1,
    "camera_id": "7",
    "path": "7",
    "streams": {
      "webrtc": {
        "url": "http://printer.local:8889/7/",
        "protocol": "webrtc"
      },
      "rtsp": {
        "url": "rtsp://printer.local:8554/7",
        "protocol": "rtsp"
      },
      "hls": {
        "url": "http://printer.local:8888/7/",
        "protocol": "hls"
      },
      "snapshot": {
        "url": "http://printer.local/cameras/snapshot/7.jpg?token=public-token",
        "protocol": "http"
      }
    }
  }
}
```

The initial schema is intentionally small:

- `schema_version`: integer version for future compatibility.
- `camera_id`: Ravens Perch camera ID as a string.
- `path`: MediaMTX path name for this camera.
- `streams`: object keyed by stream type, with URL and protocol metadata.

## Architecture

Add a small helper in `daemon/moonraker_client.py` that builds the Ravens Perch `extra_data` fragment from `camera_id` and host. The helper will use the same URL construction rules as the rest of the daemon, so Moonraker, the dashboard, and other consumers see consistent stream addresses.

`register_camera()` will accept optional `extra_data` and include it when creating a webcam entry. When updating an existing Moonraker webcam, Ravens Perch will preserve unrelated existing `extra_data` keys and replace only the `ravens_perch` key.

The existing Moonraker fields remain unchanged:

- `stream_url` stays WebRTC for Fluidd/Mainsail/browser compatibility.
- `snapshot_url` stays the existing Ravens Perch snapshot URL.
- `service` stays `webrtc-mediamtx`.

## Data Flow

1. A camera is discovered or reconciled.
2. The caller resolves the system host as it does today.
3. Ravens Perch builds `stream_url`, `snapshot_url`, and the `extra_data.ravens_perch` stream metadata from the same host and camera ID.
4. `register_camera()` creates or updates the Moonraker webcam entry.
5. Consumers calling `/server/webcams/list` can read `webcams[].extra_data.ravens_perch.streams`.

## Error Handling

If Moonraker rejects registration or update, existing registration error behavior remains unchanged. Ravens Perch logs the failure and the reconciler/worker retries later through the current registration path.

If an existing webcam has malformed or non-object `extra_data`, Ravens Perch treats it as empty and writes its own `ravens_perch` metadata. This avoids failing camera registration because another client stored unexpected custom data.

## Testing

Focused unit tests will cover:

- The stream metadata helper returns the expected schema and URLs.
- New webcam registration sends `extra_data.ravens_perch` with the WebRTC, RTSP, HLS, and snapshot entries.
- Existing webcam updates preserve unrelated `extra_data` keys while replacing Ravens Perch metadata.
- The daemon registration worker and web route registration helper pass stream metadata into `register_camera()`.

No browser/UI test is required because this change only affects Moonraker API payloads.
