/**
 * Location Tracker - Survives page reloads
 * Uses localStorage timestamps, NOT setInterval counter
 */

const LocationTracker = {
  
  STORAGE_KEY: 'ft_last_sent',
  LAST_SENT_KEY: 'ft_last_sent',
  ACTIVE_KEY: 'ft_active',
  CONFIG_KEY: 'ft_interval_ms',
  INTERVAL_KEY: 'ft_interval_ms',
  
  intervalMs: 10 * 60 * 1000, // default 10 min
  checkTimer: null,
  timer: null,
  countdownTimer: null,
  CHECK_EVERY_MS: 30 * 1000,
  isSending: false,
  
  async init() {
    try {
      const res = await fetch(
        '/attendance/tracking-config/',
        { credentials: 'same-origin' }
      );
      if (res.ok) {
        const data = await res.json();
        const ms = data.interval_ms;
        localStorage.setItem(this.INTERVAL_KEY, ms);
        this.intervalMs = ms;
        console.log('[Tracker] Interval fetched:', ms/60000, 'min');

        if (!data.is_enabled) {
          console.log('[Tracker] Tracking disabled by admin');
          const indicator = document.getElementById('tracker-indicator');
          if (indicator) indicator.style.display = 'none';
          return;
        }

        const configVersion = data.interval_minutes + '_v1';
        const cachedVersion = localStorage.getItem('ft_config_version');
        if (cachedVersion !== configVersion) {
          localStorage.removeItem(this.LAST_SENT_KEY);
          localStorage.setItem('ft_config_version', configVersion);
          console.log('[Tracker] Config changed, resetting timer');
        }
      } else {
        const cached = localStorage.getItem(this.INTERVAL_KEY);
        this.intervalMs = cached ? parseInt(cached) : 10 * 60 * 1000;
      }
    } catch(e) {
      const cached = localStorage.getItem(this.INTERVAL_KEY);
      this.intervalMs = cached ? parseInt(cached) : 10 * 60 * 1000;
      console.log('[Tracker] Using cached interval');
    }
  },
  
  startTracking() {
    localStorage.setItem(this.ACTIVE_KEY, 'true');
    this.resume();
    this.startCountdown(); // ADD THIS
    console.log('[Tracker] Started tracking');
  },
  
  resume() {
    if (this.checkTimer) clearInterval(this.checkTimer);
    if (this.timer) clearInterval(this.timer);
    this.checkAndSend();
    this.checkTimer = setInterval(() => {
      this.checkAndSend();
    }, this.CHECK_EVERY_MS);
    this.timer = this.checkTimer;
    this.startCountdown(); // ADD THIS
  },
  
  stopTracking() {
    localStorage.setItem(this.ACTIVE_KEY, 'false');
    localStorage.removeItem(this.LAST_SENT_KEY);
    if (this.checkTimer) {
      clearInterval(this.checkTimer);
      this.checkTimer = null;
    }
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.stopCountdown(); // ADD THIS
    console.log('[Tracker] Stopped');
  },
  
  checkAndSend() {
    const intervalMs = this.intervalMs || parseInt(localStorage.getItem(this.INTERVAL_KEY) || 10 * 60 * 1000);
    const lastSent = localStorage.getItem(this.STORAGE_KEY);
    const now = Date.now();
    
    if (!lastSent) {
      // Never sent before - send now
      this.sendLocation();
      return;
    }
    
    const elapsed = now - parseInt(lastSent);
    const remaining = intervalMs - elapsed;
    
    if (elapsed >= intervalMs) {
      // Interval has passed - send location
      console.log('[Tracker] Interval reached, sending...');
      this.sendLocation();
    } else {
      // Not yet time
      console.log(
        '[Tracker] Next send in', 
        Math.round(remaining / 1000 / 60), 
        'min',
        Math.round((remaining % 60000) / 1000),
        'sec'
      );
    }
  },
  
  sendLocation() {
    if (this.isSending) {
      console.log('[Tracker] Send already in progress');
      return;
    }
    if (!navigator.geolocation) {
      console.log('[Tracker] GPS not supported');
      return;
    }
    
    this.isSending = true;
    
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const lat = pos.coords.latitude;
        const lng = pos.coords.longitude;
        const acc = pos.coords.accuracy;
        
        // Get address
        let address = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
        try {
          const r = await fetch(
            `https://nominatim.openstreetmap.org/reverse` +
            `?lat=${lat}&lon=${lng}&format=json`,
            { headers: {'Accept-Language': 'en'} }
          );
          const d = await r.json();
          address = d.display_name || address;
        } catch(e) {}
        
        // Get CSRF token
        const csrf = document.querySelector(
          '[name=csrfmiddlewaretoken]')?.value || 
          document.querySelector('meta[name="csrf-token"]')?.content ||
          this.getCookie('csrftoken');
        
        // POST to backend
        const fd = new FormData();
        fd.append('latitude', lat);
        fd.append('longitude', lng);
        fd.append('accuracy', acc);
        fd.append('address', address);
        fd.append('csrfmiddlewaretoken', csrf);
        
        try {
          const res = await fetch(
            '/attendance/save-location/', 
            {method: 'POST', body: fd}
          );
          const data = await res.json();
          
          if (data.stop_tracking) {
            // No active shift, stop
            console.log('[Tracker] No active shift. Stopping.');
            this.stopTracking();
            return;
          }
          
          if (data.success) {
            // Save timestamp of successful send
            localStorage.setItem(
              this.LAST_SENT_KEY, 
              Date.now().toString()
            );
            console.log('[Tracker] Sent at', 
              new Date().toLocaleTimeString());
            
            // Update UI indicator if exists
            const indicator = document.getElementById(
              'tracker-status');
            if (indicator) {
              indicator.textContent = 
                'Last sync: ' + 
                new Date().toLocaleTimeString();
            }
            
            this.updateCountdownUI(); // ADD THIS - reset countdown
          }
        } catch(e) {
          console.log('[Tracker] Send failed:', e);
        } finally {
          this.isSending = false;
        }
      },
      (err) => {
        console.log('[Tracker] GPS error:', err.message);
        this.isSending = false;
      },
      {
        enableHighAccuracy: true, 
        timeout: 10000, 
        maximumAge: 60000
      }
    );
  },
  
  getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) 
      return parts.pop().split(';').shift();
    return '';
  },

  startCountdown() {
    // Update countdown every second
    if (this.countdownTimer) {
      clearInterval(this.countdownTimer);
    }
    
    this.countdownTimer = setInterval(() => {
      this.updateCountdownUI();
    }, 1000);
    
    // Update immediately
    this.updateCountdownUI();
  },
  
  updateCountdownUI() {
    const el = document.getElementById('sync-countdown');
    if (!el) return;
    
    const intervalMs = this.intervalMs || parseInt(
      localStorage.getItem(this.INTERVAL_KEY) 
      || 10 * 60 * 1000
    );
    
    const lastSent = parseInt(
      localStorage.getItem(this.LAST_SENT_KEY) || '0'
    );
    
    if (lastSent === 0) {
      el.textContent = 'Syncing soon...';
      return;
    }
    
    const now = Date.now();
    const elapsed = now - lastSent;
    const remaining = intervalMs - elapsed;
    
    if (remaining <= 0) {
      el.textContent = 'Syncing...';
      return;
    }
    
    const mins = Math.floor(remaining / 60000);
    const secs = Math.floor((remaining % 60000) / 1000);
    const pad = (n) => String(n).padStart(2, '0');
    
    el.textContent = `Next sync in ${pad(mins)}:${pad(secs)}`;
  },
  
  stopCountdown() {
    if (this.countdownTimer) {
      clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
    const el = document.getElementById('sync-countdown');
    if (el) el.textContent = '';
  },
  
  updateUI() {
    this.updateCountdownUI();
  }
};

window.LocationTracker = LocationTracker;
