# Identical USB Camera Identity Design

## Goal

Support multiple identical USB cameras in Ravens Perch, including cameras with no serial number and cameras that report the same serial number, without requiring user confirmation before the cameras appear and start streaming.

The design must avoid silently assigning settings or names to the wrong camera. When Ravens Perch cannot identify a camera safely, it should reject the device with a clear reason instead of falling back to volatile `/dev/videoN` identity.

## Context

Ravens Perch currently derives `hardware_id` from `hardware_name` and `serial_number`:

```text
hardware_id = "{hardware_name}-{serial_number}" if serial exists, else hardware_name
```

That fails for:

- Two same-model cameras with no serial number.
- Two same-model cameras with the same bogus serial number.
- Devices discovered one at a time, where the first duplicate can claim serial identity before the second duplicate appears.

Crowsnest v5 improved discovery of identical cameras by enumerating `/dev/v4l/by-path`, but by-path is not physical-camera identity. It identifies the USB port/topology. Ravens Perch should use by-path only when intrinsic serial identity is absent or not unique.

## Identity Semantics

Ravens Perch will split device probing from identity resolution.

`get_device_info(device_path)` should gather facts only:

- `/dev/videoN` path.
- V4L2 hardware name.
- Reported USB serial number, if any.
- Resolved real video device.
- Matching `/dev/v4l/by-path` symlink, if any.
- Matching `/dev/v4l/by-id` symlink, if any.

A separate resolver will assign final identity after seeing a batch of currently detected devices.

Identity rules:

- If `(hardware_name, reported_serial_number)` is unique in the batch, use serial identity:
  `serial:<hardware_name>:<serial>`
- If serial is missing, use USB-port identity:
  `usb-path:<hardware_name>:<by-path-basename>`
- If serial exists but collides with another detected camera, treat it as non-unique and use USB-port identity for each colliding camera:
  `usb-path:<hardware_name>:<by-path-basename>`
- If a camera needs USB-port identity but no by-path symlink is available, reject it as unsupported.

Ravens Perch will not use `/dev/videoN` as an identity fallback.

## Database Model

Add `cameras.identity_key TEXT UNIQUE` as the canonical lookup key. Keep `hardware_id` for legacy compatibility with existing schema and callers, but stop using it as the source of truth for discovery, reconnect, creation, duplicate handling, or ignore decisions.

Because the existing schema also has `cameras.hardware_id TEXT UNIQUE`, new rows should set `hardware_id = identity_key`. That preserves the old uniqueness invariant without reintroducing collisions for duplicate serials. User-facing hardware display should come from `hardware_name`, `reported_serial_number`, by-path, and by-id metadata rather than parsing `hardware_id`.

Add camera metadata columns:

- `identity_strategy`: expected values include `serial`, `usb_path`, and `legacy`.
- `by_path`: the matched `/dev/v4l/by-path/...` symlink.
- `by_id`: the matched `/dev/v4l/by-id/...` symlink, when available.
- `reported_serial_number`: the raw serial reported by the USB device.

Existing rows should be backfilled with:

```text
identity_key = hardware_id
identity_strategy = legacy
reported_serial_number = serial_number
```

There is no automatic migration from old serial-less rows to new by-path rows. A legacy serial-less camera can remain as a historical row. A newly resolved port-identified camera creates or reconnects by its `identity_key`.

`ignored_cameras` should also gain `identity_key TEXT UNIQUE` and use it for ignore lookups. Existing ignored rows can be backfilled with `identity_key = hardware_id`. New ignore entries should only be created for serial-identified cameras.

## Runtime Flow

Startup scan and manual scan naturally operate on a batch:

1. Call `find_video_devices()`.
2. Probe all device facts with `get_device_info()`.
3. Resolve identities for the full batch.
4. Process accepted resolved devices.
5. Track rejected resolved devices for the dashboard.

Hotplug should also avoid one-device identity decisions. After debounce for an add event, the monitor should rescan current capture devices, resolve the current batch, and dispatch connect events for newly accepted identity keys. Polling should track identity keys rather than raw device paths or legacy hardware IDs.

Connection handling:

1. Skip rejected resolved devices and expose their rejection reason.
2. Check the ignore list by `identity_key` only for serial-identified cameras.
3. Lookup by `identity_key`.
4. Reconnect the existing row or create a new row.
5. Probe capabilities and save settings/capabilities.
6. Start the stream and queue Moonraker registration as today.

Disconnect handling can continue to receive `/dev/videoN` paths. It should find the connected camera row by `device_path`, mark it disconnected, stop its stream, and unregister it from Moonraker as today.

## UI Behavior

Port-identified cameras should auto-add and stream without a confirmation step.

For newly generated names only, port-identified cameras should receive a short visible prefix at the start of `friendly_name`, for example:

```text
USB: C270
USB: CameraA
```

If the user renames the camera, Ravens Perch should not force or restore the `USB:` prefix.

No stream overlay should be added for identity warnings.

The dashboard and detail UI should show a small identity indicator for port-identified cameras, using concise wording such as `USB port`. The detail Info tab should expose the by-path value and explain that settings follow the USB port/topology rather than the physical camera.

The Ignore action should not be available for cameras identified only by USB port. Delete remains available and removes the saved row/settings. If the same port-identified camera remains connected or is later reconnected, Ravens Perch may recreate it with default settings.

Rejected devices should appear in the existing unsupported/rejected camera section with clear reasons, such as `No stable USB port path available`.

## Ignored Cameras

The ignore list remains appropriate for serial-identified cameras, because serial identity can follow the physical device.

Port-identified cameras should not be added to `ignored_cameras`, and UI routes should reject attempts to ignore them. Ignoring a USB port identity would either hide a usable port unexpectedly or imply physical-camera tracking that Ravens Perch does not have.

## Error Handling

If a device needs port identity but has no by-path, reject it, log the reason, and surface it in the dashboard. If the by-path symlink is not ready during hotplug, a later scan or polling cycle can accept the camera once udev creates the link.

If a serial camera later appears alongside another camera with the same `(hardware_name, serial)` in the current batch, the resolver should treat both as port-identified for that batch. It must not silently merge either device into an existing serial-identified row while the serial is known to be non-unique.

## Testing

Focused tests should cover:

- `get_device_info()` captures by-path, by-id, reported serial, and hardware name.
- The resolver uses serial identity only when serial is unique in the detected batch.
- The resolver uses by-path for missing serials.
- The resolver uses by-path for duplicate serials.
- The resolver rejects missing-serial or duplicate-serial devices when by-path is absent.
- Manual scan accepts two same-model no-serial cameras on different by-paths.
- Startup scan accepts two same-model no-serial cameras on different by-paths.
- Hotplug rescans the batch and does not let the first duplicate claim serial identity before the second appears.
- DB lookups and creation use `identity_key`.
- Port-identified cameras get generated names beginning with `USB:`.
- Ignore is unavailable for port-identified cameras.
- Legacy rows get `identity_key = hardware_id` without auto-migrating to by-path.

## Out Of Scope

This design does not attempt to identify physical camera units when serials are missing or duplicated. USB-port identity follows topology. If a user swaps identical cameras between ports, settings may follow the ports.

This design does not copy Crowsnest source code. It reuses the observed behavior at the design level: map by-path symlinks to real video devices and attach by-id metadata when available.
