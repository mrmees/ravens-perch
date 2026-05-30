# Moonraker API Key Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow Ravens Perch to talk to secured Moonraker installations by sending an optional Moonraker API key on backend and installer Moonraker requests.

**Architecture:** Store the Moonraker API key as a file secret under `data/`, never in SQLite and never in browser-visible stream URLs. `moonraker_client.py` owns auth header construction for daemon integrations, while `print_status.py` consumes that helper for its direct Moonraker polling. The installer gets a matching `moonraker_curl` wrapper so migration, cleanup, and verification work when Moonraker requires `X-Api-Key`.

**Tech Stack:** Python stdlib + `requests`, Flask/Jinja settings UI, shell installer, existing `unittest` suite, Ruff.

---

### File Structure

- Create: `daemon/moonraker_auth.py`
  - Responsibility: read/save/clear Moonraker API key file, validate no line breaks, expose configured status and auth headers.
- Modify: `daemon/moonraker_client.py`
  - Responsibility: add `X-Api-Key` headers to all MoonrakerClient requests when configured.
- Modify: `daemon/print_status.py`
  - Responsibility: include Moonraker auth headers on direct print status and metadata requests.
- Modify: `daemon/web_ui/routes.py`
  - Responsibility: save/clear Moonraker API key from Settings and pass key status to the template.
- Modify: `daemon/web_ui/templates/settings.html`
  - Responsibility: add a non-revealing API key input and clear checkbox near Moonraker URL.
- Modify: `install.sh`
  - Responsibility: prompt for an optional Moonraker API key, store it as a `600` secret, and use it in Moonraker curl calls.
- Test: `tests/test_moonraker_api_key.py`
  - Responsibility: secret helper behavior, MoonrakerClient auth header behavior, print status auth header behavior, settings save/clear route.
- Modify/Test: `tests/test_settings_auth_ui.py`, `tests/test_install_lifecycle.py`
  - Responsibility: assert Settings and installer expose the new API key paths.

---

### Task 1: Moonraker API Key Secret Helper

**Files:**
- Create: `daemon/moonraker_auth.py`
- Test: `tests/test_moonraker_api_key.py`

- [ ] **Step 1: Write failing tests for save/load/clear/header behavior**

```python
class MoonrakerApiKeyConfigTests(unittest.TestCase):
    def test_save_load_and_clear_api_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "moonraker-api-key"
            save_moonraker_api_key("secret-key", key_file=key_file)
            self.assertEqual(read_moonraker_api_key(key_file=key_file), "secret-key")
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
            self.assertTrue(moonraker_api_key_configured(key_file=key_file))
            self.assertTrue(clear_moonraker_api_key(key_file=key_file))
            self.assertEqual(read_moonraker_api_key(key_file=key_file), "")

    def test_rejects_api_key_with_line_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MoonrakerApiKeyError):
                save_moonraker_api_key("bad\nkey", key_file=Path(tmp) / "moonraker-api-key")

    def test_auth_headers_are_empty_without_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(moonraker_auth_headers(key_file=Path(tmp) / "missing"), {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_moonraker_api_key`
Expected: FAIL because `daemon.moonraker_auth` does not exist.

- [ ] **Step 3: Implement helper module**

```python
class MoonrakerApiKeyError(ValueError):
    pass

def read_moonraker_api_key(key_file: Optional[Path] = None) -> str:
    path = _api_key_file(key_file)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()

def moonraker_auth_headers(key_file: Optional[Path] = None) -> Dict[str, str]:
    api_key = read_moonraker_api_key(key_file=key_file)
    return {"X-Api-Key": api_key} if api_key else {}
```

- [ ] **Step 4: Run helper tests**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_moonraker_api_key`
Expected: PASS for Task 1 tests.

---

### Task 2: Add API Key Headers to Daemon Moonraker Requests

**Files:**
- Modify: `daemon/moonraker_client.py`
- Modify: `daemon/print_status.py`
- Test: `tests/test_moonraker_api_key.py`

- [ ] **Step 1: Write failing tests for request headers**

```python
def test_moonraker_client_sends_api_key_header(self):
    client = MoonrakerClient("http://moonraker.local")
    with patch("daemon.moonraker_client.moonraker_auth_headers", return_value={"X-Api-Key": "secret"}), \
         patch.object(client.session, "get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = {"result": {"ok": True}}
        success, _, _ = client._request("/server/info")
    self.assertTrue(success)
    self.assertEqual(get.call_args.kwargs["headers"], {"X-Api-Key": "secret"})

def test_print_status_sends_api_key_header(self):
    monitor = PrintStatusMonitor(moonraker_url="http://moonraker.local")
    with patch("daemon.print_status.moonraker_auth_headers", return_value={"X-Api-Key": "secret"}), \
         patch("daemon.print_status.requests.get") as get:
        get.return_value.status_code = 200
        get.return_value.json.return_value = {"result": {"status": {}}}
        monitor._poll_status()
    self.assertEqual(get.call_args.kwargs["headers"], {"X-Api-Key": "secret"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_moonraker_api_key`
Expected: FAIL because headers are not passed.

- [ ] **Step 3: Add headers to client and print status**

```python
headers = moonraker_auth_headers()
response = self.session.get(url, params=params, headers=headers, timeout=timeout)
```

```python
response = requests.get(url, headers=moonraker_auth_headers(), timeout=5)
```

- [ ] **Step 4: Run targeted tests**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_moonraker_api_key`
Expected: PASS.

---

### Task 3: Settings UI Save/Clear Support

**Files:**
- Modify: `daemon/web_ui/routes.py`
- Modify: `daemon/web_ui/templates/settings.html`
- Modify: `tests/test_settings_auth_ui.py`
- Test: `tests/test_moonraker_api_key.py`

- [ ] **Step 1: Write failing route and template tests**

```python
def test_settings_route_saves_moonraker_api_key(self):
    with tempfile.TemporaryDirectory() as tmp:
        key_file = Path(tmp) / "moonraker-api-key"
        env = {"RAVENS_PERCH_MOONRAKER_API_KEY_FILE": str(key_file)}
        form = {"moonraker_url": "http://127.0.0.1:7125", "moonraker_api_key": "secret"}
        with patch.dict("os.environ", env, clear=False), patch.object(routes, "render_template", side_effect=lambda _t, **c: c["message"]):
            app = create_app()
            with app.test_request_context("/cameras/settings", method="POST", data=form, headers={"HX-Request": "true"}):
                response = routes.update_global_settings()
        self.assertIn("Settings saved", response)
        self.assertEqual(read_moonraker_api_key(key_file=key_file), "secret")
```

```python
def test_settings_page_has_moonraker_api_key_controls(self):
    template = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
    self.assertIn('name="moonraker_api_key"', template)
    self.assertIn('name="clear_moonraker_api_key"', template)
    self.assertIn("Leave blank to keep", template)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_moonraker_api_key tests.test_settings_auth_ui`
Expected: FAIL because fields and route handling do not exist.

- [ ] **Step 3: Implement Settings save/clear and UI**

```python
if request.form.get("clear_moonraker_api_key"):
    clear_moonraker_api_key()
elif request.form.get("moonraker_api_key", "").strip():
    save_moonraker_api_key(request.form["moonraker_api_key"].strip())
```

- [ ] **Step 4: Run targeted tests**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_moonraker_api_key tests.test_settings_auth_ui`
Expected: PASS.

---

### Task 4: Installer Prompt and Moonraker Curl Helper

**Files:**
- Modify: `install.sh`
- Modify: `tests/test_install_lifecycle.py`

- [ ] **Step 1: Write failing installer tests**

```python
def test_installer_prompts_for_moonraker_api_key_and_uses_helper(self):
    text = INSTALL_SH.read_text(encoding="utf-8")
    self.assertIn("configure_moonraker_api_key", text)
    self.assertIn("RAVENS_PERCH_MOONRAKER_API_KEY", text)
    self.assertIn("moonraker_curl", text)
    self.assertIn('X-Api-Key:', text)
    self.assertNotIn('curl -s "http://127.0.0.1:7125/server/webcams/list"', text)
```

- [ ] **Step 2: Run installer tests to verify failure**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_install_lifecycle`
Expected: FAIL because the helper/prompt do not exist.

- [ ] **Step 3: Add installer key prompt and helper**

```bash
configure_moonraker_api_key() {
    local key_file="${INSTALL_DIR}/data/moonraker-api-key"
    local api_key="${RAVENS_PERCH_MOONRAKER_API_KEY:-}"
    if [ -z "${RAVENS_PERCH_MOONRAKER_API_KEY+x}" ]; then
        read -r -p "Moonraker API key (leave blank if not required): " api_key
    fi
    if [ -n "$api_key" ]; then
        printf '%s\n' "$api_key" > "$key_file"
        chmod 600 "$key_file"
    fi
}

moonraker_curl() {
    local url="$1"
    shift
    local key_file="${INSTALL_DIR}/data/moonraker-api-key"
    if [ -f "$key_file" ]; then
        local api_key
        api_key="$(head -n 1 "$key_file")"
        curl -s -H "X-Api-Key: ${api_key}" "$@" "$url"
    else
        curl -s "$@" "$url"
    fi
}
```

- [ ] **Step 4: Replace Moonraker curl calls with helper**

Run: `rg -n "curl .*7125|MOONRAKER_URL" install.sh`
Expected: Moonraker HTTP calls use `moonraker_curl` where auth may be required.

- [ ] **Step 5: Run installer tests**

Run: `/tmp/ravens-test-venv/bin/python -m unittest tests.test_install_lifecycle tests.test_install_auth_prompts`
Expected: PASS.

---

### Task 5: Final Verification and Commit

**Files:**
- All changed files from Tasks 1-4

- [ ] **Step 1: Run full verification**

Run:

```bash
/tmp/ravens-test-venv/bin/python -m ruff check daemon tests
/tmp/ravens-test-venv/bin/python -m unittest discover tests
/tmp/ravens-test-venv/bin/python -m compileall daemon
bash -n install.sh
```

Expected: all commands exit `0`.

- [ ] **Step 2: Commit**

```bash
git add daemon/moonraker_auth.py daemon/moonraker_client.py daemon/print_status.py daemon/web_ui/routes.py daemon/web_ui/templates/settings.html install.sh tests/test_moonraker_api_key.py tests/test_settings_auth_ui.py tests/test_install_lifecycle.py docs/superpowers/plans/2026-05-30-moonraker-api-key-access.md
git commit -m "Add Moonraker API key support"
```

---

### Self-Review

- Spec coverage: API key file secret, daemon requests, print status polling, settings UI, installer curl paths, and tests are all covered.
- Placeholder scan: No TBD/TODO placeholders remain in the plan.
- Type consistency: Helper names are consistent across tasks: `read_moonraker_api_key`, `save_moonraker_api_key`, `clear_moonraker_api_key`, `moonraker_api_key_configured`, `moonraker_auth_headers`.

### Execution Status

- [x] Task 1: Moonraker API key secret helper
- [x] Task 2: API key headers on daemon Moonraker requests
- [x] Task 3: Settings UI save/clear support
- [x] Task 4: Installer prompt and Moonraker curl helper
- [x] Task 5: Full verification before commit
