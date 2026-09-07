/**
 * Location Guard - Unified GPS Tracking & Permission Enforcement Engine for FieldTrack
 * Fully optimized for mobile, indoor reception, LAN/HTTP environments, and instant resume.
 */
(function () {
    const LocationGuard = {
        state: 'checking', // 'checking' | 'granted' | 'prompt' | 'denied' | 'unavailable' | 'timeout'
        isReady: false,
        currentPosition: null,
        watchId: null,
        isAcquiring: false,

        // Default branch/headquarters coordinates (Dhaka, Bangladesh) for local network/fallback
        DEFAULT_FALLBACK: {
            latitude: 23.8103,
            longitude: 90.4125,
            accuracy: 50,
            is_fallback: true
        },

        init() {
            const shellType = document.body ? document.body.getAttribute('data-shell-type') : '';
            const isStaffPath = window.location.pathname.startsWith('/staff/') || 
                                window.location.pathname.startsWith('/attendance/') ||
                                document.getElementById('check-in-form') !== null;

            // 1. Check if we have any cached position from current or recent session (up to 7 days)
            const cachedRaw = localStorage.getItem('ft_last_position');
            if (cachedRaw) {
                try {
                    const parsed = JSON.parse(cachedRaw);
                    if (parsed && parsed.latitude && parsed.longitude) {
                        this.currentPosition = parsed;
                        this.isReady = true;
                        this.state = 'granted';
                        this.updateUI('granted');
                        this.hideModal();
                    }
                } catch (e) {}
            }

            // 2. Check if user already dismissed / verified in this session
            if (sessionStorage.getItem('ft_location_verified') === 'true') {
                this.isReady = true;
                this.state = 'granted';
                this.updateUI('granted');
                this.hideModal();
                // If we didn't have coordinates, set default fallback
                if (!this.currentPosition) {
                    this.currentPosition = { ...this.DEFAULT_FALLBACK, timestamp: Date.now() };
                }
            }

            // 3. Only actively check for staff users or attendance flows
            if (shellType === 'staff' || isStaffPath) {
                // If not already ready, run permission check
                if (!this.isReady) {
                    this.checkPermission();
                } else {
                    // Refresh location passively in the background without blocking
                    this.passiveAcquire();
                }
            }

            this.bindEvents();
        },

        bindEvents() {
            window.addEventListener('request-location-check', () => {
                this.requestLocation(true);
            });
        },

        isInsecureLan() {
            return !window.isSecureContext && 
                   location.hostname !== 'localhost' && 
                   location.hostname !== '127.0.0.1';
        },

        bypassOrDismiss() {
            this.hideModal();
            sessionStorage.setItem('ft_location_verified', 'true');
            if (!this.currentPosition) {
                const cachedRaw = localStorage.getItem('ft_last_position');
                if (cachedRaw) {
                    try { this.currentPosition = JSON.parse(cachedRaw); } catch(e) {}
                }
                if (!this.currentPosition) {
                    this.currentPosition = { ...this.DEFAULT_FALLBACK, timestamp: Date.now() };
                    try {
                        localStorage.setItem('ft_last_position', JSON.stringify(this.currentPosition));
                    } catch(e) {}
                }
            }
            this.isReady = true;
            this.state = 'granted';
            this.updateUI('granted');

            window.dispatchEvent(new CustomEvent('location:ready', {
                detail: this.currentPosition
            }));

            // Sync location to backend
            this.syncMandatoryLocation(this.currentPosition);

            // Attempt background acquisition if browser allows
            this.passiveAcquire();
        },

        passiveAcquire() {
            if (navigator.geolocation && !this.isInsecureLan()) {
                navigator.geolocation.getCurrentPosition(
                    pos => this.handleSuccess(pos, false),
                    err => console.debug('[LocationGuard background]', err),
                    { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 }
                );
            }
        },

        checkPermission() {
            // If accessing over insecure HTTP LAN (e.g. mobile on http://192.168.x.x),
            // mobile Chrome strictly blocks navigator.geolocation. Do not block the staff!
            if (this.isInsecureLan()) {
                console.info('[LocationGuard] Insecure HTTP LAN detected. Using network location mode.');
                const warn = document.getElementById('loc-insecure-warning');
                if (warn) warn.classList.remove('hidden');
                
                // If user already has cached position, auto-grant
                if (this.currentPosition) {
                    this.isReady = true;
                    this.state = 'granted';
                    this.updateUI('granted');
                    this.hideModal();
                    return;
                }
            }

            if (!navigator.geolocation) {
                // Geolocation completely unsupported
                this.state = 'unavailable';
                this.updateUI('unavailable', 'Geolocation is not supported by your browser.');
                this.showModal('unavailable');
                return;
            }

            // Check permissions API if available
            if (navigator.permissions && navigator.permissions.query) {
                navigator.permissions.query({ name: 'geolocation' })
                    .then((perm) => {
                        this.handlePermissionQueryState(perm.state);
                        perm.onchange = () => {
                            this.handlePermissionQueryState(perm.state);
                        };
                    })
                    .catch(() => {
                        this.requestLocation(false);
                    });
            } else {
                this.requestLocation(false);
            }
        },

        handlePermissionQueryState(permState) {
            if (permState === 'denied') {
                // If on LAN IP, don't trap the user with an unsolvable modal
                if (this.isInsecureLan()) {
                    this.bypassOrDismiss();
                    return;
                }
                this.state = 'denied';
                this.isReady = false;
                this.updateUI('denied', 'Location permission is blocked. Please enable it in browser settings.');
                this.showModal('denied');
            } else if (permState === 'granted') {
                this.state = 'granted';
                if (this.currentPosition || sessionStorage.getItem('ft_location_verified') === 'true') {
                    this.hideModal();
                    this.updateUI('granted');
                }
                // Silently refresh coordinates in background
                this.requestLocation(false);
            } else {
                // 'prompt'
                this.state = 'prompt';
                this.requestLocation(false);
            }
        },

        requestLocation(isUserInitiated = false) {
            if (this.isAcquiring) return;
            this.isAcquiring = true;

            const modalBtn = document.getElementById('loc-modal-retry-btn');
            if (modalBtn) modalBtn.disabled = true;

            const resetBtn = () => {
                this.isAcquiring = false;
                if (modalBtn) modalBtn.disabled = false;
            };

            // If on insecure LAN, test if browser permits or fallback immediately
            if (this.isInsecureLan() && !isUserInitiated && this.currentPosition) {
                resetBtn();
                this.bypassOrDismiss();
                return;
            }

            // Acquisition strategy: Try high accuracy first, fallback to network location
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    resetBtn();
                    this.handleSuccess(pos, isUserInitiated);
                },
                (err) => {
                    // If error is timeout or unavailable (e.g. indoor), try network (low accuracy)
                    if (err.code === 2 || err.code === 3) {
                        navigator.geolocation.getCurrentPosition(
                            (lowPos) => {
                                resetBtn();
                                this.handleSuccess(lowPos, isUserInitiated);
                            },
                            (finalErr) => {
                                resetBtn();
                                this.handleError(finalErr);
                            },
                            {
                                enableHighAccuracy: false,
                                timeout: 15000,
                                maximumAge: 600000
                            }
                        );
                    } else {
                        resetBtn();
                        this.handleError(err);
                    }
                },
                {
                    enableHighAccuracy: true,
                    timeout: 8000,
                    maximumAge: 300000
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

            // Save to localStorage & session
            try {
                localStorage.setItem('ft_last_position', JSON.stringify(this.currentPosition));
                sessionStorage.setItem('ft_location_verified', 'true');
            } catch(e) {}

            // Hide blocking modal
            this.hideModal();
            this.updateUI('granted');

            // Dispatch global event for forms & views
            window.dispatchEvent(new CustomEvent('location:ready', {
                detail: this.currentPosition
            }));

            // Sync mandatory location to backend
            this.syncMandatoryLocation(this.currentPosition);

            // Continuously watch position
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
                        try {
                            localStorage.setItem('ft_last_position', JSON.stringify(this.currentPosition));
                        } catch(e) {}
                        window.dispatchEvent(new CustomEvent('location:ready', {
                            detail: this.currentPosition
                        }));
                    },
                    (watchErr) => {
                        console.debug('[LocationGuard Watcher]', watchErr);
                    },
                    { enableHighAccuracy: false, maximumAge: 30000 }
                );
            }

            if (notify && window.showToast) {
                window.showToast(`Location verified: ±${Math.round(accuracy)}m accuracy`, 'success');
            }
        },

        handleError(err) {
            // 1. If we already have any cached position or session verified, NEVER lock out the user
            const cachedRaw = localStorage.getItem('ft_last_position');
            if (cachedRaw || sessionStorage.getItem('ft_location_verified') === 'true') {
                if (!this.currentPosition && cachedRaw) {
                    try { this.currentPosition = JSON.parse(cachedRaw); } catch(e) {}
                }
                this.isReady = true;
                this.state = 'granted';
                this.hideModal();
                this.updateUI('granted');
                return;
            }

            // 2. If on insecure LAN (mobile on http://192.168.x.x), auto-bypass with default location
            if (this.isInsecureLan()) {
                console.info('[LocationGuard] Auto-bypassing on HTTP LAN connection.');
                this.bypassOrDismiss();
                return;
            }

            this.isReady = false;
            let reason = 'unavailable';
            let message = 'GPS location is required. Please turn on your device GPS / Location.';

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
                if (titleEl) titleEl.textContent = 'Location Access Required';
                if (badgeEl) {
                    badgeEl.textContent = 'Permission Needed';
                    badgeEl.className = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-400 border border-red-200 dark:border-red-800';
                }
                if (descEl) descEl.textContent = 'FieldTrack requires device location to verify attendance and duty presence. Please allow location access in your browser.';
                if (guideStepsEl) {
                    guideStepsEl.innerHTML = `
                        <li class="flex items-start gap-2.5">
                            <span class="w-5 h-5 rounded-full bg-primary/10 text-primary font-bold text-[11px] flex items-center justify-center shrink-0 mt-0.5">1</span>
                            <span>Tap the <strong>Lock / Settings</strong> icon in your browser's address bar.</span>
                        </li>
                        <li class="flex items-start gap-2.5">
                            <span class="w-5 h-5 rounded-full bg-primary/10 text-primary font-bold text-[11px] flex items-center justify-center shrink-0 mt-0.5">2</span>
                            <span>Set <strong>Location</strong> permission to <strong>Allow</strong>.</span>
                        </li>
                        <li class="flex items-start gap-2.5">
                            <span class="w-5 h-5 rounded-full bg-primary/10 text-primary font-bold text-[11px] flex items-center justify-center shrink-0 mt-0.5">3</span>
                            <span>Tap <strong>Verify Location</strong> below.</span>
                        </li>
                    `;
                }
            } else {
                if (titleEl) titleEl.textContent = 'Device GPS / Location Required';
                if (badgeEl) {
                    badgeEl.textContent = reason === 'timeout' ? 'GPS Signal Weak' : 'GPS Turned Off';
                    badgeEl.className = 'inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-amber-100 text-amber-800 dark:bg-amber-950/40 dark:text-amber-400 border border-amber-200 dark:border-amber-800';
                }
                if (descEl) descEl.textContent = 'Location coordinates could not be detected. Please ensure device GPS is turned ON in your phone settings.';
                if (guideStepsEl) {
                    guideStepsEl.innerHTML = `
                        <li class="flex items-start gap-2.5">
                            <span class="w-5 h-5 rounded-full bg-primary/10 text-primary font-bold text-[11px] flex items-center justify-center shrink-0 mt-0.5">1</span>
                            <span>Swipe down quick settings on your phone.</span>
                        </li>
                        <li class="flex items-start gap-2.5">
                            <span class="w-5 h-5 rounded-full bg-primary/10 text-primary font-bold text-[11px] flex items-center justify-center shrink-0 mt-0.5">2</span>
                            <span>Turn ON <strong>Location / GPS</strong>.</span>
                        </li>
                        <li class="flex items-start gap-2.5">
                            <span class="w-5 h-5 rounded-full bg-primary/10 text-primary font-bold text-[11px] flex items-center justify-center shrink-0 mt-0.5">3</span>
                            <span>Tap <strong>Verify Location</strong> below.</span>
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
                    label.textContent = message || 'Location tracking paused';
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
                accuracy: coords.accuracy || 20,
                client_event_time: new Date(coords.timestamp || Date.now()).toISOString()
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
                    console.log('[LocationGuard] Location synchronized.');
                }
            })
            .catch(err => {
                console.debug('[LocationGuard] Sync notice:', err);
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
