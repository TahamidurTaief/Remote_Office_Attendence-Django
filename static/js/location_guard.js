const LocationGuard = {
  
  CHECK_INTERVAL_MS: 15000,
  timer: null,
  modalVisible: false,
  
  init() {
    // Check immediately
    this.check();
    
    // Then check every 15 seconds
    this.timer = setInterval(() => {
      this.check();
    }, this.CHECK_INTERVAL_MS);
  },
  
  check() {
    if (!('geolocation' in navigator)) {
      this.showModal('not_supported');
      return;
    }
    
    navigator.permissions.query(
      { name: 'geolocation' }
    ).then((result) => {
      if (result.state === 'denied') {
        this.showModal('denied');
      } else if (result.state === 'granted') {
        this.hideModal();
      } else {
        // 'prompt' state - try to get location
        navigator.geolocation.getCurrentPosition(
          () => this.hideModal(),
          (err) => {
            if (err.code === 1) {
              this.showModal('denied');
            }
          },
          { timeout: 5000, maximumAge: 60000 }
        );
      }
      
      // Watch for permission changes
      result.onchange = () => {
        if (result.state === 'denied') {
          this.showModal('denied');
        } else if (result.state === 'granted') {
          this.hideModal();
        }
      };
    }).catch(() => {
      // Fallback if permissions API not supported
      navigator.geolocation.getCurrentPosition(
        () => this.hideModal(),
        (err) => {
          if (err.code === 1) {
            this.showModal('denied');
          }
        },
        { timeout: 5000, maximumAge: 60000 }
      );
    });
  },
  
  showModal(type) {
    if (this.modalVisible) return;
    this.modalVisible = true;
    
    const existing = document.getElementById(
      'location-guard-modal');
    if (existing) existing.remove();
    
    const content = {
      denied: {
        title: 'Location Required',
        body: `To use this app:<br><br>
         <strong>Step 1:</strong> Turn on GPS/Location 
         on your device settings.<br><br>
         <strong>Step 2:</strong> Allow location 
         permission when your browser asks.<br><br>
         After enabling, tap "Try Again" below.`,
        icon: 'M12 2C8.13 2 5 5.13 5 9c0 5.25 ' +
              '7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z' +
              'M12 11.5c-1.38 0-2.5-1.12-2.5-2.5s' +
              '1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-' +
              '1.12 2.5-2.5 2.5z',
        btn: 'Try Again'
      },
      not_supported: {
        title: 'GPS Not Available',
        body: 'Your device or browser does not support GPS. ' +
              'Please use Chrome browser on an Android or ' +
              'iOS device for best experience.',
        icon: 'M12 2C8.13 2 5 5.13 5 9c0 5.25 ' +
              '7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z',
        btn: 'OK'
      }
    };
    
    const c = content[type] || content.denied;
    
    const modal = document.createElement('div');
    modal.id = 'location-guard-modal';
    modal.style.cssText = `
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.75);
      z-index: 999999;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
      backdrop-filter: blur(4px);
    `;
    
    modal.innerHTML = `
      <div style="
        background: white;
        border-radius: 20px;
        padding: 2rem;
        max-width: 360px;
        width: 100%;
        text-align: center;
        box-shadow: 0 25px 50px rgba(0,0,0,0.3);
      ">
        <div style="
          width: 72px; height: 72px;
          background: #FEE2E2;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          margin: 0 auto 1.25rem;
        ">
          <svg width="36" height="36" 
               viewBox="0 0 24 24" 
               fill="#DC2626">
            <path d="${c.icon}"/>
          </svg>
        </div>
        <h3 style="
          font-size: 18px;
          font-weight: 700;
          color: #111827;
          margin: 0 0 0.75rem;
          font-family: system-ui, sans-serif;
        ">${c.title}</h3>
        <p style="
          font-size: 14px;
          color: #6B7280;
          margin: 0 0 1.5rem;
          line-height: 1.6;
          font-family: system-ui, sans-serif;
        ">${c.body}</p>
        <button 
          onclick="LocationGuard.retry()"
          style="
            width: 100%;
            padding: 14px;
            background: #4F46E5;
            color: white;
            border: none;
            border-radius: 14px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            font-family: system-ui, sans-serif;
            margin-bottom: 12px;
          ">${c.btn}</button>
        <p style="
          font-size: 12px;
          color: #9CA3AF;
          font-family: system-ui, sans-serif;
        ">
          Location is required for attendance tracking
        </p>
      </div>
    `;
    
    document.body.appendChild(modal);
  },
  
  hideModal() {
    this.modalVisible = false;
    const modal = document.getElementById(
      'location-guard-modal');
    if (modal) modal.remove();
  },
  
  retry() {
    this.hideModal();
    setTimeout(() => this.check(), 500);
  }
};

// Auto-init when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener(
    'DOMContentLoaded', () => LocationGuard.init());
} else {
  LocationGuard.init();
}

window.LocationGuard = LocationGuard;
