/**
 * Location Guard - Permission and GPS Availability Monitor for FieldTrack
 */
(function () {
    const LocationGuard = {
        timer: null,

        init() {
            this.check();
            // Periodically check in case of browser-level toggles
            this.timer = setInterval(() => {
                this.check();
            }, 10000);
        },

        check() {
            if (!navigator.geolocation) {
                this.updateIndicatorState('denied', 'GPS not supported');
                return;
            }

            if (navigator.permissions && navigator.permissions.query) {
                navigator.permissions.query({ name: 'geolocation' })
                    .then((result) => {
                        this.handlePermissionState(result.state);
                        result.onchange = () => {
                            this.handlePermissionState(result.state);
                        };
                    })
                    .catch(() => {
                        // Fallback if query fails
                        this.checkWithDirectCall();
                    });
            } else {
                this.checkWithDirectCall();
            }
        },

        checkWithDirectCall() {
            navigator.geolocation.getCurrentPosition(
                () => this.handlePermissionState('granted'),
                (err) => {
                    if (err.code === 1) { // PERMISSION_DENIED
                        this.handlePermissionState('denied');
                    }
                },
                { timeout: 5000, maximumAge: 60000 }
            );
        },

        handlePermissionState(state) {
            if (state === 'denied') {
                this.updateIndicatorState('denied', 'Location tracking paused — permission denied');
            } else if (state === 'granted') {
                this.updateIndicatorState('granted');
            } else {
                // Prompt state
                this.updateIndicatorState('prompt');
            }
        },

        updateIndicatorState(state, message) {
            const indicator = document.getElementById('tracker-indicator');
            if (!indicator) return;

            const dot = indicator.querySelector('span.w-2') || indicator.querySelector('.bg-emerald-400') || indicator.querySelector('.bg-red-500');
            const label = indicator.querySelector('.text-green-100');

            if (state === 'denied') {
                if (dot) {
                    dot.classList.remove('bg-emerald-400', 'animate-pulse');
                    dot.classList.add('bg-red-500');
                }
                if (label) {
                    label.textContent = message || 'Location tracking paused — permission denied';
                }

                // Add instruction helper if it doesn't exist
                let helper = document.getElementById('location-permission-helper');
                if (!helper) {
                    helper = document.createElement('div');
                    helper.id = 'location-permission-helper';
                    helper.className = 'mt-2.5 p-2 bg-red-500/20 border border-red-500/30 rounded-lg text-[10px] text-red-200 leading-relaxed font-semibold';
                    helper.innerHTML = `
                        <div class="flex items-start gap-2">
                            <i data-lucide="alert-triangle" class="w-3.5 h-3.5 mt-0.5 shrink-0 text-red-300"></i>
                            <div>
                                Background location tracking is paused. To enable:
                                <ol class="list-decimal list-inside mt-1 space-y-0.5 text-red-200">
                                    <li>Click the lock/settings icon in your browser's address bar.</li>
                                    <li>Ensure Location permissions are set to "Allow".</li>
                                    <li>Reload the page to sync.</li>
                                </ol>
                            </div>
                        </div>
                    `;
                    indicator.appendChild(helper);
                    if (window.lucide) {
                        window.lucide.createIcons();
                    }
                }
            } else if (state === 'granted') {
                if (dot) {
                    dot.classList.remove('bg-red-500');
                    dot.classList.add('bg-emerald-400', 'animate-pulse');
                }
                if (label) {
                    label.textContent = 'Location Tracking Active';
                }

                const helper = document.getElementById('location-permission-helper');
                if (helper) {
                    helper.remove();
                }
            }
        }
    };

    window.LocationGuard = LocationGuard;

    // Auto-init
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => LocationGuard.init());
    } else {
        LocationGuard.init();
    }
})();
