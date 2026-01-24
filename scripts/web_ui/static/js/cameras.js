/**
 * Ravens Perch Camera Configuration - Client-side JavaScript
 */

// ============================================================================
// STATE
// ============================================================================

let cameras = [];
let devices = [];
let currentEditingCamera = null;
let currentAddingDevice = null;

// ============================================================================
// API FUNCTIONS
// ============================================================================

async function fetchAPI(endpoint, options = {}) {
    const response = await fetch(`/api${endpoint}`, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        },
        ...options
    });
    
    if (!response.ok) {
        const error = await response.json().catch(() => ({ error: 'Request failed' }));
        throw new Error(error.error || `HTTP ${response.status}`);
    }
    
    return response.json();
}

async function loadCameras() {
    try {
        const data = await fetchAPI('/cameras');
        cameras = data.cameras;
        updateStatusBar(data);
        renderCameras();
    } catch (error) {
        console.error('Failed to load cameras:', error);
        showToast('Failed to load cameras', 'error');
    }
}

async function loadDevices() {
    try {
        const data = await fetchAPI('/devices');
        devices = data.devices;
        renderDevices();
    } catch (error) {
        console.error('Failed to load devices:', error);
    }
}

async function updateCamera(uid, updates) {
    try {
        const result = await fetchAPI(`/cameras/${uid}`, {
            method: 'PUT',
            body: JSON.stringify(updates)
        });
        
        if (result.sync_errors && result.sync_errors.length > 0) {
            showToast(`Saved with warnings: ${result.sync_errors.join(', ')}`, 'warning');
        } else {
            showToast('Camera settings saved', 'success');
        }
        
        await loadCameras();
        return result;
    } catch (error) {
        showToast(`Failed to save: ${error.message}`, 'error');
        throw error;
    }
}

async function deleteCamera(uid) {
    try {
        await fetchAPI(`/cameras/${uid}`, { method: 'DELETE' });
        showToast('Camera removed', 'success');
        await loadCameras();
        await loadDevices();
    } catch (error) {
        showToast(`Failed to delete: ${error.message}`, 'error');
        throw error;
    }
}

async function addDevice(devicePath, settings) {
    try {
        const result = await fetchAPI('/devices/add', {
            method: 'POST',
            body: JSON.stringify({
                device_path: devicePath,
                ...settings
            })
        });
        
        if (result.sync_errors && result.sync_errors.length > 0) {
            showToast(`Added with warnings: ${result.sync_errors.join(', ')}`, 'warning');
        } else {
            showToast('Camera added successfully', 'success');
        }
        
        await loadCameras();
        await loadDevices();
        return result;
    } catch (error) {
        showToast(`Failed to add camera: ${error.message}`, 'error');
        throw error;
    }
}

async function syncAll() {
    try {
        showToast('Syncing cameras...', 'info');
        const result = await fetchAPI('/sync', { method: 'POST' });
        showToast(`Synced ${result.mediamtx_success} camera(s)`, 'success');
        await loadCameras();
    } catch (error) {
        showToast(`Sync failed: ${error.message}`, 'error');
    }
}

// ============================================================================
// RENDERING
// ============================================================================

function updateStatusBar(data) {
    const mtxStatus = document.getElementById('mediamtx-status');
    const mrStatus = document.getElementById('moonraker-status');
    const camCount = document.getElementById('camera-count');
    
    mtxStatus.textContent = `MediaMTX: ${data.mediamtx_available ? 'Online' : 'Offline'}`;
    mtxStatus.className = `status-indicator ${data.mediamtx_available ? 'online' : 'offline'}`;
    
    mrStatus.textContent = `Moonraker: ${data.moonraker_available ? 'Online' : 'Offline'}`;
    mrStatus.className = `status-indicator ${data.moonraker_available ? 'online' : 'offline'}`;
    
    camCount.textContent = `Cameras: ${cameras.length}`;
}

function renderCameras() {
    const grid = document.getElementById('camera-grid');
    
    if (cameras.length === 0) {
        grid.innerHTML = `
            <div class="no-devices">
                <p>No cameras configured yet.</p>
                <p>Add a camera from the "Available Devices" section below.</p>
            </div>
        `;
        return;
    }
    
    grid.innerHTML = cameras.map(cam => renderCameraCard(cam)).join('');
}

function renderCameraCard(cam) {
    const isConnected = cam.connected;
    const webrtcUrl = cam.urls?.webrtc || '';
    
    // Format details
    const format = (cam.format || 'mjpeg').toUpperCase();
    const resolution = cam.resolution || '1280x720';
    const fps = cam.framerate || 30;
    const bitrate = cam.bitrate || '4M';
    
    // Moonraker badge
    const moonrakerBadge = cam.moonraker_enabled 
        ? '<span class="moonraker-badge">Moonraker</span>'
        : '<span class="moonraker-badge disabled">No Moonraker</span>';
    
    return `
        <div class="camera-card ${isConnected ? '' : 'disconnected'}" data-uid="${cam.uid}">
            <div class="preview-container">
                ${isConnected ? `
                    <iframe src="${webrtcUrl}" 
                            allow="autoplay"
                            loading="lazy"></iframe>
                ` : `
                    <div class="preview-placeholder">
                        <span class="icon">📷</span>
                        <span>Camera Disconnected</span>
                    </div>
                `}
            </div>
            <div class="camera-info">
                <div class="camera-header">
                    <span class="camera-name">${escapeHtml(cam.friendly_name)}</span>
                    <span class="camera-uid">${cam.uid}</span>
                </div>
                <div class="camera-details">
                    <span>📐 ${resolution}</span>
                    <span>🎬 ${fps} fps</span>
                    <span>📊 ${bitrate}</span>
                    <span>🎥 ${format}</span>
                    ${moonrakerBadge}
                </div>
                <div class="stream-links">
                    <a href="${cam.urls?.webrtc}" target="_blank">WebRTC</a>
                    <a href="${cam.urls?.snapshot}" target="_blank">Snapshot</a>
                    <a href="${cam.urls?.hls}" target="_blank">HLS</a>
                </div>
                <div class="camera-actions">
                    <button class="btn btn-primary btn-small" onclick="openSettingsModal('${cam.uid}')">
                        ⚙️ Settings
                    </button>
                </div>
            </div>
        </div>
    `;
}

function renderDevices() {
    const list = document.getElementById('device-list');
    
    // Filter to unconfigured devices
    const unconfigured = devices.filter(d => !d.configured);
    
    if (unconfigured.length === 0) {
        list.innerHTML = `
            <div class="no-devices">
                <p>All connected cameras are configured.</p>
            </div>
        `;
        return;
    }
    
    list.innerHTML = unconfigured.map(dev => `
        <div class="device-item" data-path="${dev.path}">
            <div class="device-info-text">
                <span class="device-name">${escapeHtml(dev.hardware_name)}</span>
                <span class="device-path">${dev.path}${dev.serial_number ? ` (S/N: ${dev.serial_number})` : ''}</span>
            </div>
            <button class="btn btn-primary btn-small" onclick="openAddModal('${dev.path}')">
                ➕ Add Camera
            </button>
        </div>
    `).join('');
}

// ============================================================================
// MODALS - SETTINGS
// ============================================================================

function openSettingsModal(uid) {
    const cam = cameras.find(c => c.uid === uid);
    if (!cam) return;
    
    currentEditingCamera = cam;
    
    // Populate form
    document.getElementById('settings-uid').value = cam.uid;
    document.getElementById('settings-name').value = cam.friendly_name || '';
    document.getElementById('settings-bitrate').value = cam.bitrate || '4M';
    document.getElementById('settings-rotation').value = cam.rotation || 0;
    document.getElementById('settings-moonraker').checked = cam.moonraker_enabled || false;
    
    // Populate format/resolution/fps dropdowns
    populateCapabilityDropdowns(
        cam.capabilities || {},
        cam.format,
        cam.resolution,
        cam.framerate,
        'settings-format',
        'settings-resolution',
        'settings-framerate'
    );
    
    document.getElementById('settings-modal').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('settings-modal').classList.add('hidden');
    currentEditingCamera = null;
}

async function handleSettingsSubmit(event) {
    event.preventDefault();
    
    if (!currentEditingCamera) return;
    
    const form = event.target;
    const updates = {
        friendly_name: form.friendly_name.value,
        format: form.format.value,
        resolution: form.resolution.value,
        framerate: parseInt(form.framerate.value),
        bitrate: form.bitrate.value,
        rotation: parseInt(form.rotation.value),
        moonraker_enabled: form.moonraker_enabled.checked
    };
    
    try {
        await updateCamera(currentEditingCamera.uid, updates);
        closeModal();
    } catch (error) {
        // Error already shown via toast
    }
}

async function deleteCameraFromModal() {
    if (!currentEditingCamera) return;
    
    if (!confirm(`Delete camera "${currentEditingCamera.friendly_name}"? This will remove it from MediaMTX and Moonraker.`)) {
        return;
    }
    
    try {
        await deleteCamera(currentEditingCamera.uid);
        closeModal();
    } catch (error) {
        // Error already shown via toast
    }
}

// ============================================================================
// MODALS - ADD DEVICE
// ============================================================================

function openAddModal(devicePath) {
    const dev = devices.find(d => d.path === devicePath);
    if (!dev) return;
    
    currentAddingDevice = dev;
    
    // Populate device info
    document.getElementById('add-device-path').value = dev.path;
    document.getElementById('add-device-name').textContent = dev.hardware_name;
    document.getElementById('add-device-path-display').textContent = dev.path;
    document.getElementById('add-friendly-name').value = '';
    document.getElementById('add-moonraker').checked = true;
    
    // Populate format/resolution/fps dropdowns
    populateCapabilityDropdowns(
        dev.capabilities || {},
        null, null, null,
        'add-format',
        'add-resolution',
        'add-framerate'
    );
    
    document.getElementById('add-device-modal').classList.remove('hidden');
}

function closeAddModal() {
    document.getElementById('add-device-modal').classList.add('hidden');
    currentAddingDevice = null;
}

async function handleAddSubmit(event) {
    event.preventDefault();
    
    if (!currentAddingDevice) return;
    
    const form = event.target;
    const settings = {
        friendly_name: form.friendly_name.value || null,
        format: form.format.value,
        resolution: form.resolution.value,
        framerate: parseInt(form.framerate.value),
        moonraker_enabled: form.moonraker_enabled.checked
    };
    
    try {
        await addDevice(currentAddingDevice.path, settings);
        closeAddModal();
    } catch (error) {
        // Error already shown via toast
    }
}

// ============================================================================
// CAPABILITY DROPDOWNS
// ============================================================================

function populateCapabilityDropdowns(capabilities, currentFormat, currentRes, currentFps, 
                                      formatId, resId, fpsId) {
    const formatSelect = document.getElementById(formatId);
    const resSelect = document.getElementById(resId);
    const fpsSelect = document.getElementById(fpsId);
    
    // Get available formats
    const formats = Object.keys(capabilities);
    
    // Populate format dropdown
    formatSelect.innerHTML = formats.length > 0 
        ? formats.map(fmt => `<option value="${fmt}" ${fmt === currentFormat ? 'selected' : ''}>${fmt.toUpperCase()}</option>`).join('')
        : '<option value="mjpeg">MJPEG</option>';
    
    // Set up change handlers
    formatSelect.onchange = () => updateResolutions(capabilities, formatSelect, resSelect, fpsSelect);
    resSelect.onchange = () => updateFramerates(capabilities, formatSelect, resSelect, fpsSelect);
    
    // Initial population
    updateResolutions(capabilities, formatSelect, resSelect, fpsSelect, currentRes, currentFps);
}

function updateResolutions(capabilities, formatSelect, resSelect, fpsSelect, targetRes, targetFps) {
    const format = formatSelect.value;
    const resolutions = capabilities[format] ? Object.keys(capabilities[format]) : [];
    
    // Sort resolutions by pixel count (descending)
    resolutions.sort((a, b) => {
        const [aw, ah] = a.split('x').map(Number);
        const [bw, bh] = b.split('x').map(Number);
        return (bw * bh) - (aw * ah);
    });
    
    resSelect.innerHTML = resolutions.length > 0
        ? resolutions.map(res => `<option value="${res}" ${res === targetRes ? 'selected' : ''}>${res}</option>`).join('')
        : '<option value="1280x720">1280x720</option>';
    
    updateFramerates(capabilities, formatSelect, resSelect, fpsSelect, targetFps);
}

function updateFramerates(capabilities, formatSelect, resSelect, fpsSelect, targetFps) {
    const format = formatSelect.value;
    const resolution = resSelect.value;
    
    let framerates = [];
    if (capabilities[format] && capabilities[format][resolution]) {
        framerates = capabilities[format][resolution];
    }
    
    // Sort descending
    framerates.sort((a, b) => b - a);
    
    fpsSelect.innerHTML = framerates.length > 0
        ? framerates.map(fps => `<option value="${fps}" ${fps === targetFps ? 'selected' : ''}>${fps} fps</option>`).join('')
        : '<option value="30">30 fps</option>';
}

// ============================================================================
// TOAST NOTIFICATIONS
// ============================================================================

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <span>${escapeHtml(message)}</span>
    `;
    
    container.appendChild(toast);
    
    // Remove after 4 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ============================================================================
// UTILITIES
// ============================================================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================================
// INITIALIZATION
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Load initial data
    loadCameras();
    loadDevices();
    
    // Set up form handlers
    document.getElementById('camera-settings-form').addEventListener('submit', handleSettingsSubmit);
    document.getElementById('add-device-form').addEventListener('submit', handleAddSubmit);
    
    // Sync all button
    document.getElementById('sync-all-btn').addEventListener('click', syncAll);
    
    // Close modals on backdrop click
    document.getElementById('settings-modal').addEventListener('click', (e) => {
        if (e.target.id === 'settings-modal') closeModal();
    });
    document.getElementById('add-device-modal').addEventListener('click', (e) => {
        if (e.target.id === 'add-device-modal') closeAddModal();
    });
    
    // Refresh data periodically
    setInterval(() => {
        loadCameras();
        loadDevices();
    }, 30000); // Every 30 seconds
});

// Global function for delete button in modal
window.deleteCamera = deleteCameraFromModal;
window.openSettingsModal = openSettingsModal;
window.openAddModal = openAddModal;
window.closeModal = closeModal;
window.closeAddModal = closeAddModal;
