/**
 * Location Guard - Unified GPS Tracking & Permission Enforcement Engine for FieldTrack
 * Ensures strict GPS location gating: Staff cannot check in/out or use the system without active GPS.
 */
(function () {
    const LocationGuard = {
        state: 'checking', // 'checking' | 'granted' | 'prompt' | 'denied' | 'unavailable' | 'timeout'
        isReady: false,
        currentPosition: null,
        watchId: null,
        permissionWatcher: null,
        isAcquiring: false,

        init() {
            const shellType = document.body ? document.body.getAttribute('data-shell-type') : '';
            const isStaffPath = window.location.pathname.startsWith('/staff/') || 
                                window.location.pathname.startsWith('/attendance/') ||
                                document.getElementById('check-in-form') !== null;

            // Only auto-enforce and block for staff users or attendance flows
            if (shellType === 'staff' || isStaffPath) {
                this.checkPermission();
            }
            this.bindEvents();
        },

        bindEvents() {
            // Listen for manual trigger requests
            window.addEventListener('request-location-check', () => {
                this.requestLocation(true);
            });
        },

        checkPermission() {
            if (!navigator.geolocation) {
                this.state = 'unavailable';
                this.updateUI('unavailable', 'Geolocation is not supported by your browser.');
                this.showModal('unavailable');
                return;
            }

            if (navigator.permissions && navigator.permissions.query) {
                navigator.permissions.query({ name: 'geolocation' })
                    .then((perm) => {
                        this.handlePermissionQueryState(perm.state);
                        perm.onchange = () => {
                            this.handlePermissionQueryState(perm.state);
                        };
                    })
                    .catch(() => {
                        // Permissions query unsupported/failed; trigger direct check
                        this.requestLocation(false);
                    });
            } else {
                this.requestLocation(false);
            }
        },

        handlePermissionQueryState(permState) {
            if (permState === 'denied') {
                this.state = 'denied';
                this.isReady = false;
                this.updateUI('denied', 'Location permission is blocked. Please enable it in browser settings.');
                this.showModal('denied');
            } else if (permState === 'granted') {
                this.state = 'granted';
                // Automatically capture exact coordinates
                this.requestLocation(false);
            } else {
                // 'prompt' - As soon as they enter, prompt for location
                this.state = 'prompt';
                this.requestLocation(false);
            }
        },

        requestLocation(isUserInitiated = false) {
            if (this.isAcquiring) return;
            this.isAcquiring = true;

            const modalBtn = document.getElementById('loc-modal-retry-btn');
            const modalBtnText = document.getElementById('loc-modal-btn-text');
            const modalSpinner = document.getElementById('loc-modal-spinner');

            if (modalBtn) modalBtn.disabled = true;
            if (modalSpinner) modalSpinner.classList.remove('hidden');
            if (modalBtnText) modalBtnText.textContent = 'Detecting exact GPS signal...';

            // High accuracy position request with 15s timeout
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    this.isAcquiring = false;
                    if (modalBtn) modalBtn.disabled = false;
                    if (modalSpinner) modalSpinner.classList.add('hidden');
                    if (modalBtnText) modalBtnText.textContent = 'Turn On Location & Verify';

                    this.handleSuccess(pos, isUserInitiated);
                },
                (err) => {
                    this.isAcquiring = false;
                    if (modalBtn) modalBtn.disabled = false;
                    if (modalSpinner) modalSpinner.classList.add('hidden');
                    if (modalBtnText) modalBtnText.textContent = 'Turn On Location & Verify';

                    this.handleError(err);
                },
                {
                    enableHighAccuracy: true,
                    timeout: 15000,
                    maximumAge: 0
                }
            );
        },

        handleSuccess(position, notify = false) {
            const lat = position.coords.latitude;
            const lng = position.coords.longitude;
            const accuracy = position.coords.accuracy || 20;

            this.state = 'granted';
            this.isReady = true;
            this.currentPosition = {
                latitude: lat,
                longitude: lng,
                accuracy: accuracy,
                timestamp: position.timestamp || Date.now()
            };

            // Hide blocking modal
            this.hideModal();
            this.updateUI('granted');

            // Dispatch global event for forms & views
            window.dispatchEvent(new CustomEvent('location:ready', {
                detail: this.currentPosition
            }));

            // Sync mandatory location to backend
            this.syncMandatoryLocation(this.currentPosition);

            // Continuously watch position to keep it accurate
            if (!this.watchId) {
                this.watchId = navigator.geolocation.watchPosition(
                    (newPos) => {
                        const newAcc = newPos.coords.accuracy || 20;
                        this.currentPosition = {
                            latitude: newPos.coords.latitude,
                            longitude: newPos.coords.longitude,
                            accuracy: newAcc,
                            timestamp: newPos.timestamp || Date.now()
                        };
                        window.dispatchEvent(new CustomEvent('location:ready', {
                            detail: this.currentPosition
                        }));
                    },
                    (watchErr) => {
                        console.warn('[LocationGuard Watcher]', watchErr);
                    },
                    { enableHighAccuracy: true, maximumAge: 10000 }
                );
            }

            if (notify && window.showToast) {
                window.showToast(`Location verified: ±${Math.round(accuracy)}m accuracy`, 'success');
            }
        },

        handleError(err) {
            this.isReady = false;
            let reason = 'unavailable';
            let message = 'GPS location is required. Please turn on your device GPS/Location.';

            if (err.code === 1) { // PERMISSION_DENIED
                this.state = 'denied';
                reason = 'denied';
                message = 'Location permission was denied. Please allow location access in your browser.';
            } else if (err.code === 2) { // POSITION_UNAVAILABLE
                this.state = 'unavailable';
                reason = 'unavailable';
                message = 'Device GPS / Location is turned OFF. Please turn on Location in quick settings.';
            } else if (err.code === 3) { // TIMEOUT
                this.state = 'timeout';
                reason = 'timeout';
                message = 'GPS signal acquisition timed out. Please tap retry to detect your location.';
            }

            this.updateUI(this.state, message);
            this.showModal(reason);

            window.dispatchEvent(new CustomEvent('location:blocked', {
                detail: { code: err.code, message: message }
            }));
        },

        showModal(reason = 'denied') {
            const modal = document.getElementById('location-guard-modal');
            if (!modal) return;

            const titleEl = document.getElementById('loc-modal-title');
            const descEl = document.getElementById('loc-modal-desc');
            const badgeEl = document.getElementById('loc-modal-badge');
            const guideStepsEl = document.getElementById('loc-modal-steps');

            if (reason === 'denied') {
                if (titleEl) titleEl.textContent = 'Location Permission Required';
                if (badgeEl) {
                    badgeEl.textContent = 'Permission Denied';
                    badgeEl.className = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-400 border border-red-200 dark:border-red-800';
                }
                if (descEl) descEl.textContent = 'FieldTrack requires exact GPS location to verify attendance and duty presence. You cannot work in the system until location permission is granted.';
                if (guideStepsEl) {
                    guideStepsEl.innerHTML = `
                        <li class="flex items-start gap-2">
                            <span class="w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#1877F2] font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">1</span>
                            <span>Tap the <strong>Lock / Settings</strong> icon in your browser's address bar.</span>
                        </li>
                        <li class="flex items-start gap-2">
                            <span class="w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#1877F2] font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">2</span>
                            <span>Set <strong>Location</strong> permission to <strong>Allow</strong>.</span>
                        </li>
                        <li class="flex items-start gap-2">
                            <span class="w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#1877F2] font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">3</span>
                            <span>Click the <strong>Turn On Location & Verify</strong> button below.</span>
                        </li>
                    `;
                }
            } else {
                if (titleEl) titleEl.textContent = 'Device GPS / Location Required';
                if (badgeEl) {
                    badgeEl.textContent = reason === 'timeout' ? 'GPS Signal Weak' : 'GPS Turned Off';
                    badgeEl.className = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-400 border border-amber-200 dark:border-amber-800';
                }
                if (descEl) descEl.textContent = 'FieldTrack cannot detect your coordinates. Your phone or computer location service is turned off. Please turn it on to check in/out and work.';
                if (guideStepsEl) {
                    guideStepsEl.innerHTML = `
                        <li class="flex items-start gap-2">
                            <span class="w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#1877F2] font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">1</span>
                            <span>Swipe down your quick settings or open device settings.</span>
                        </li>
                        <li class="flex items-start gap-2">
                            <span class="w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#1877F2] font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">2</span>
                            <span>Turn ON <strong>Location / GPS</strong> toggle.</span>
                        </li>
                        <li class="flex items-start gap-2">
                            <span class="w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-[#1877F2] font-bold text-xs flex items-center justify-center shrink-0 mt-0.5">3</span>
                            <span>Return here and tap <strong>Turn On Location & Verify</strong>.</span>
                        </li>
                    `;
                }
            }

            modal.classList.remove('hidden');
            modal.classList.add('flex');
            document.body.classList.add('overflow-hidden');
        },

        hideModal() {
            const modal = document.getElementById('location-guard-modal');
            if (modal) {
                modal.classList.add('hidden');
                modal.classList.remove('flex');
            }
            document.body.classList.remove('overflow-hidden');
        },

        updateUI(state, message) {
            const indicator = document.getElementById('tracker-indicator');
            if (!indicator) return;

            const dot = indicator.querySelector('span.w-2') || indicator.querySelector('.bg-emerald-400') || indicator.querySelector('.bg-red-500');
            const label = indicator.querySelector('.text-green-100');

            if (state === 'denied' || state === 'unavailable') {
                if (dot) {
                    dot.classList.remove('bg-emerald-400', 'animate-pulse');
                    dot.classList.add('bg-red-500');
                }
                if (label) {
                    label.textContent = message || 'Location tracking paused — GPS disabled';
                }
            } else if (state === 'granted') {
                if (dot) {
                    dot.classList.remove('bg-red-500');
                    dot.classList.add('bg-emerald-400', 'animate-pulse');
                }
                if (label) {
                    label.textContent = 'Location Tracking Active';
                }
            }
        },

        syncMandatoryLocation(coords) {
            const csrfToken = this.getCsrf();
            if (!csrfToken) return;

            const payload = {
                latitude: coords.latitude,
                longitude: coords.longitude,
                accuracy: coords.accuracy,
                client_event_time: new Date(coords.timestamp).toISOString()
            };

            fetch('/attendance/save-location-mandatory/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify(payload)
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    console.log('[LocationGuard] Mandatory location synchronized.');
                }
            })
            .catch(err => {
                console.warn('[LocationGuard] Mandatory location sync failed:', err);
            });
        },

        getCsrf() {
            const el = document.querySelector('[name=csrfmiddlewaretoken]');
            if (el) return el.value;
            const m = document.cookie.match(/csrftoken=([^;]+)/);
            return m ? m[1] : '';
        }
    };

    window.LocationGuard = LocationGuard;

    // Auto-init on DOMContentLoaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => LocationGuard.init());
    } else {
        LocationGuard.init();
    }
})();
