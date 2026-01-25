/**
 * Ravens Perch - Minimal JavaScript for HTMX enhancements
 */

// Theme toggle functionality
function toggleTheme() {
    var currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    var newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('ravens-perch-theme', newTheme);
}

// Initialize theme toggle button
document.addEventListener('DOMContentLoaded', function() {
    var themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }
});

// Hamburger menu toggle
document.addEventListener('DOMContentLoaded', function() {
    var hamburger = document.getElementById('nav-hamburger');
    var navLinks = document.getElementById('nav-links');

    if (hamburger && navLinks) {
        hamburger.addEventListener('click', function() {
            hamburger.classList.toggle('active');
            navLinks.classList.toggle('open');
            hamburger.setAttribute('aria-expanded', navLinks.classList.contains('open'));
        });

        // Close menu when clicking a link (mobile)
        navLinks.querySelectorAll('a').forEach(function(link) {
            link.addEventListener('click', function() {
                hamburger.classList.remove('active');
                navLinks.classList.remove('open');
                hamburger.setAttribute('aria-expanded', 'false');
            });
        });

        // Close menu when clicking outside
        document.addEventListener('click', function(event) {
            if (!hamburger.contains(event.target) && !navLinks.contains(event.target)) {
                hamburger.classList.remove('active');
                navLinks.classList.remove('open');
                hamburger.setAttribute('aria-expanded', 'false');
            }
        });
    }
});

// Auto-dismiss flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', function() {
    const flashMessages = document.querySelectorAll('.flash');
    flashMessages.forEach(function(flash) {
        setTimeout(function() {
            flash.style.opacity = '0';
            flash.style.transition = 'opacity 0.3s';
            setTimeout(function() {
                flash.remove();
            }, 300);
        }, 5000);
    });
});

// Handle cascading dropdowns for resolution/framerate
document.addEventListener('htmx:afterSwap', function(event) {
    // If we just updated the resolution dropdown, trigger framerate update
    if (event.target.id === 'resolution') {
        const framerateSelect = document.getElementById('framerate');
        if (framerateSelect && event.detail.xhr.response) {
            try {
                const resolutions = JSON.parse(event.detail.xhr.response);
                framerateSelect.innerHTML = '';
                resolutions.forEach(function(res) {
                    const option = document.createElement('option');
                    option.value = res;
                    option.textContent = res;
                    framerateSelect.appendChild(option);
                });
            } catch (e) {
                console.debug('Response was not JSON, likely HTML partial');
            }
        }
    }

    // If we updated framerate dropdown
    if (event.target.id === 'framerate') {
        try {
            const framerates = JSON.parse(event.detail.xhr.response);
            event.target.innerHTML = '';
            framerates.forEach(function(fps) {
                const option = document.createElement('option');
                option.value = fps;
                option.textContent = fps + ' fps';
                event.target.appendChild(option);
            });
        } catch (e) {
            console.debug('Response was not JSON, likely HTML partial');
        }
    }
});

// Show loading indicator during HTMX requests
document.body.addEventListener('htmx:beforeRequest', function(event) {
    const target = event.target;
    if (target.tagName === 'BUTTON') {
        target.disabled = true;
        target.dataset.originalText = target.textContent;
        target.textContent = 'Loading...';
    }
});

document.body.addEventListener('htmx:afterRequest', function(event) {
    const target = event.target;
    if (target.tagName === 'BUTTON' && target.dataset.originalText) {
        target.disabled = false;
        target.textContent = target.dataset.originalText;
        delete target.dataset.originalText;
    }
});

// Handle API status updates for dashboard
document.body.addEventListener('htmx:afterRequest', function(event) {
    if (event.detail.pathInfo && event.detail.pathInfo.requestPath.includes('/api/status')) {
        try {
            const statuses = JSON.parse(event.detail.xhr.response);
            statuses.forEach(function(camera) {
                const card = document.getElementById('camera-' + camera.id);
                if (!card) return;

                // Update connection class
                if (camera.connected) {
                    card.classList.remove('disconnected');
                } else {
                    card.classList.add('disconnected');
                }

                // Update status badge
                const badge = card.querySelector('.status-badge');
                if (badge) {
                    if (!camera.connected) {
                        badge.className = 'status-badge status-offline';
                        badge.textContent = 'Offline';
                    } else if (camera.stream_active) {
                        badge.className = 'status-badge status-active';
                        badge.textContent = 'Live';
                    } else {
                        badge.className = 'status-badge status-starting';
                        badge.textContent = 'Starting';
                    }
                }
            });

            // Update connection count
            const statusText = document.getElementById('connection-status');
            if (statusText) {
                const connected = statuses.filter(function(c) { return c.connected; }).length;
                const total = statuses.length;
                statusText.textContent = connected + ' of ' + total + ' connected';
            }
        } catch (e) {
            console.debug('Could not parse status response');
        }
    }
});

// Copy to clipboard utility
function copyToClipboard(text) {
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(function() {
            showToast('Copied to clipboard');
        }).catch(function(err) {
            console.error('Failed to copy:', err);
        });
    } else {
        // Fallback for older browsers
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy');
            showToast('Copied to clipboard');
        } catch (err) {
            console.error('Failed to copy:', err);
        }
        document.body.removeChild(textarea);
    }
}

// Simple toast notification
function showToast(message, duration) {
    duration = duration || 2000;
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    toast.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#333;color:#fff;padding:12px 24px;border-radius:4px;z-index:9999;opacity:0;transition:opacity 0.3s;';
    document.body.appendChild(toast);

    // Trigger reflow and fade in
    toast.offsetHeight;
    toast.style.opacity = '1';

    setTimeout(function() {
        toast.style.opacity = '0';
        setTimeout(function() {
            toast.remove();
        }, 300);
    }, duration);
}

// Add click handlers for URL copying
document.addEventListener('click', function(event) {
    const codeElement = event.target.closest('.url-item code');
    if (codeElement) {
        copyToClipboard(codeElement.textContent);
    }
});

// Keyboard shortcuts
document.addEventListener('keydown', function(event) {
    // Escape to go back
    if (event.key === 'Escape') {
        const backLink = document.querySelector('.back-link');
        if (backLink) {
            window.location.href = backLink.href;
        }
    }
});
