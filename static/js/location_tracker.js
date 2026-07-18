/**
 * Location Tracker - Background Geolocation Sync for FieldTrack
 */
(function () {
    const LocationTracker = {
        intervalId: null,
        timeoutId: null,
        countdownId: null,
        isSending: false,

        async init() {
            // Prepared state initialization
            console.log('[LocationTracker] Init');
            const isActive = localStorage.getItem('ft_active') === 'true';
            return Promise.resolve(isActive);
        },

        getCsrf() {
            const el = document.querySelector('[name=csrfmiddlewaretoken]');
            if (el) return el.value;
            const m = document.cookie.match(/csrftoken=([^;]+)/);
            return m ? m[1] : '';
        },

        getIntervalMinutes() {
            const input = document.getElementById('tracking-interval');
            if (input) {
                const val = parseInt(input.value);
                if (!isNaN(val)) return val;
            }
            return 0;
        },

        startTracking() {
            const mins = this.getIntervalMinutes();
            if (mins <= 0) {
                console.log('[LocationTracker] Tracking interval is 0 or disabled. Ignoring start.');
                return;
            }

            console.log(`[LocationTracker] Starting tracking with interval: ${mins} minutes`);
            localStorage.setItem('ft_active', 'true');
            localStorage.setItem('ft_session_start', Date.now().toString());
            
            const intervalMs = mins * 60 * 1000;
            const nextSync = Date.now() + intervalMs;
            localStorage.setItem('ft_next_sync', nextSync.toString());

            this.clearTimers();
            this.scheduleNextSync(intervalMs, intervalMs);
            this.startCountdown();
        },

        stopTracking() {
            console.log('[LocationTracker] Stopping tracking');
            localStorage.setItem('ft_active', 'false');
            localStorage.removeItem('ft_next_sync');
            localStorage.removeItem('ft_session_start');
            this.clearTimers();
            
            const countdownEl = document.getElementById('sync-countdown');
            if (countdownEl) {
                countdownEl.textContent = '';
            }
        },

        resume() {
            const isActive = localStorage.getItem('ft_active') === 'true';
            if (!isActive) {
                console.log('[LocationTracker] resume called but ft_active is false');
                return;
            }

            const mins = this.getIntervalMinutes();
            if (mins <= 0) {
                console.log('[LocationTracker] resume called but tracking interval is disabled');
                return;
            }

            const intervalMs = mins * 60 * 1000;
            const nextSyncStr = localStorage.getItem('ft_next_sync');
            let nextSync = nextSyncStr ? parseInt(nextSyncStr) : 0;
            let remaining = nextSync - Date.now();

            console.log(`[LocationTracker] Resuming loop. Target next sync: ${new Date(nextSync).toLocaleTimeString()}, remaining: ${Math.round(remaining / 1000)}s`);

            // If invalid or in the past, sync soon/immediately
            if (isNaN(remaining) || remaining <= 0 || remaining > intervalMs) {
                remaining = 1000; // Trigger in 1s
                nextSync = Date.now() + remaining;
                localStorage.setItem('ft_next_sync', nextSync.toString());
            }

            this.clearTimers();
            this.scheduleNextSync(remaining, intervalMs);
            this.startCountdown();
        },

        clearTimers() {
            if (this.timeoutId) {
                clearTimeout(this.timeoutId);
                this.timeoutId = null;
            }
            if (this.intervalId) {
                clearInterval(this.intervalId);
                this.intervalId = null;
            }
            if (this.countdownId) {
                clearInterval(this.countdownId);
                this.countdownId = null;
            }
        },

        scheduleNextSync(delayMs, intervalMs) {
            this.timeoutId = setTimeout(() => {
                this.sendLocation();
                
                // Set the next regular intervals
                const nextSync = Date.now() + intervalMs;
                localStorage.setItem('ft_next_sync', nextSync.toString());
                
                this.intervalId = setInterval(() => {
                    this.sendLocation();
                    const tickNextSync = Date.now() + intervalMs;
                    localStorage.setItem('ft_next_sync', tickNextSync.toString());
                }, intervalMs);
            }, delayMs);
        },

        sendLocation() {
            if (this.isSending) return;
            if (!navigator.geolocation) {
                console.warn('[LocationTracker] Geolocation not supported by this browser.');
                return;
            }

            this.isSending = true;
            navigator.geolocation.getCurrentPosition(
                async (position) => {
                    const lat = position.coords.latitude;
                    const lng = position.coords.longitude;
                    const accuracy = position.coords.accuracy;

                    console.log(`[LocationTracker] Sending location: (${lat}, ${lng}) with accuracy ${accuracy}m`);

                    const payload = {
                        latitude: lat,
                        longitude: lng,
                        accuracy: accuracy,
                        address: ''
                    };

                    try {
                        const response = await fetch('/attendance/location-sync/', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': this.getCsrf()
                            },
                            body: JSON.stringify(payload)
                        });

                        const data = await response.json();
                        if (response.ok && data.success) {
                            console.log('[LocationTracker] Location sync successfully completed.');
                            const indicator = document.getElementById('tracker-status');
                            if (indicator) {
                                indicator.textContent = 'Last sync: ' + new Date().toLocaleTimeString();
                            }
                        } else {
                            console.warn('[LocationTracker] Server rejected location sync:', data.error || 'Unknown error');
                        }
                    } catch (err) {
                        console.warn('[LocationTracker] Failed to POST location sync:', err);
                    } finally {
                        this.isSending = false;
                    }
                },
                (error) => {
                    console.warn('[LocationTracker] Geolocation error:', error.message);
                    this.isSending = false;
                },
                {
                    enableHighAccuracy: true,
                    timeout: 10000,
                    maximumAge: 60000
                }
            );
        },

        startCountdown() {
            if (this.countdownId) {
                clearInterval(this.countdownId);
            }

            this.countdownId = setInterval(() => {
                this.updateCountdownUI();
            }, 1000);
            
            this.updateCountdownUI();
        },

        updateCountdownUI() {
            const el = document.getElementById('sync-countdown');
            if (!el) return;

            const nextSyncStr = localStorage.getItem('ft_next_sync');
            if (!nextSyncStr) {
                el.textContent = 'Syncing...';
                return;
            }

            const remaining = parseInt(nextSyncStr) - Date.now();
            if (remaining <= 5000) {
                el.textContent = 'Syncing...';
                return;
            }

            const mins = Math.floor(remaining / 60000);
            const secs = Math.floor((remaining % 60000) / 1000);
            
            const pad = (n) => String(n).padStart(2, '0');
            el.textContent = `Next sync in ${pad(mins)}:${pad(secs)}`;
        }
    };

    window.LocationTracker = LocationTracker;
})();
