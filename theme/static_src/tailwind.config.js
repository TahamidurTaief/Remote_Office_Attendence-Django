/**
 * FieldTrack Design System — Tailwind Config
 *
 * Primary: #1877F2 Meta blue scale (Meta Business Suite reference — DO NOT revert)
 * Accent:  #10B981 emerald (success / positive / CTA-secondary only)
 * Semantic: red-500 (danger), amber-500 (warning) — NOT brand accents
 * Dark mode: class-based (toggled via <html class="dark"> from Alpine + localStorage)
 * Grid: 8px base (spacing scale already covers multiples of 2/4/8)
 *
 * Primary scale anchored on Meta's base blue #1877F2 (--fds-unified-blue-50)
 * Tints  50→400  : lighter, toward white  (--fds-unified-blue-100 → 60)
 * Base   500      : #1877F2 exactly
 * Shades 600→900  : darker, toward navy  (--fds-unified-blue-40 → 10)
 */

module.exports = {
    darkMode: 'class',
    content: [
        '../templates/**/*.html',
        '../../templates/**/*.html',
        '../../**/templates/**/*.html',
        '../../templates/cotton/**/*.html',
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: [
                    'Segoe UI Historic',
                    'Segoe UI',
                    'Helvetica',
                    'Arial',
                    'sans-serif',
                ],
            },
            colors: {
                // ── Primary brand (Meta Blue #1877F2) ─────────────────
                primary: {
                    50:  '#F4FAFF',   // --fds-unified-blue-100
                    100: '#E7F3FF',   // --fds-unified-blue-95 / deemphasized bg
                    200: '#CDE5FF',   // --fds-unified-blue-90
                    300: '#A8D1FF',   // --fds-unified-blue-80
                    400: '#5FAAFF',   // --fds-unified-blue-65
                    500: '#1877F2',   // BASE — Meta primary blue
                    600: '#166FE5',   // hover/active (Meta spec)
                    700: '#1455B0',   // --fds-unified-blue-35
                    800: '#083E89',   // --fds-unified-blue-25
                    900: '#00193D',   // --fds-unified-blue-10
                },
                // ── Accent (emerald) — success/positive/CTA-secondary ─
                accent: {
                    50:  '#ECFDF5',
                    100: '#D1FAE5',
                    200: '#A7F3D0',
                    300: '#6EE7B7',
                    400: '#34D399',
                    500: '#10B981',
                    600: '#059669',
                    700: '#047857',
                    800: '#065F46',
                    900: '#064E3B',
                },
            },
            // ── Border radius tokens (Meta geodesic values) ───────────
            borderRadius: {
                'card':   '8px',    // --geodesic-appearance-radius-container
                'btn':    '6px',    // --geodesic-appearance-radius-control
                'input':  '6px',    // --geodesic-appearance-radius-content
                'modal':  '8px',    // --geodesic-appearance-radius-layer
                'drawer': '8px',    // --geodesic-appearance-radius-layer
                'chip':   '6px',    // badge/chip radius
                'avatar': '999px',  // unchanged
            },
            // ── Shadow tokens (Meta flat — overlays only) ─────────────
            boxShadow: {
                'soft':     '0 1px 2px rgba(0,0,0,0.10)',
                'dropdown': '0 2px 8px rgba(0,0,0,0.10), 0 1px 1px rgba(0,0,0,0.10)',
                'elevated': '0 2px 12px 2px rgba(0,0,0,0.10), 0 1px 2px rgba(0,0,0,0.10)',
                'none-dark': '0 0 0 1px rgba(255,255,255,0.06)',
            },
            // ── Background images ─────────────────────────────────────
            backgroundImage: {
                'primary-gradient': 'linear-gradient(135deg, #1877F2 0%, #4599FF 100%)',
                // Soft page-bg wash only — NEVER on cards or buttons
                'soft-gradient': 'linear-gradient(135deg, rgba(24,119,242,0.03) 0%, rgba(231,243,255,0.06) 50%, rgba(24,119,242,0.02) 100%)',
            },
        },
    },
    plugins: [
        require('@tailwindcss/forms'),
        require('@tailwindcss/typography'),
        require('@tailwindcss/aspect-ratio'),
    ],
}
