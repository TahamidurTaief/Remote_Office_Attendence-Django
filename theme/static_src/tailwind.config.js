/**
 * FieldTrack Design System — Tailwind Config
 *
 * Primary: #0B5FA5 blue scale (LOCKED — do not change)
 * Accent:  #10B981 emerald (success / positive / CTA-secondary only)
 * Semantic: red-500 (danger), amber-500 (warning) — NOT brand accents
 * Dark mode: class-based (toggled via <html class="dark"> from Alpine + localStorage)
 * Grid: 8px base (spacing scale already covers multiples of 2/4/8)
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
            colors: {
                // ── Primary brand (blue) ──────────────────────────────
                primary: {
                    50:  '#F0F7FC',
                    100: '#E1EFF9',
                    200: '#BCDFF2',
                    300: '#7EC3E9',
                    400: '#3BA3DD',
                    500: '#0B5FA5',
                    600: '#094E8A',
                    700: '#083F6F',
                    800: '#073258',
                    900: '#052541',
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
            // ── Border radius tokens ──────────────────────────────────
            borderRadius: {
                'card':   '12px',
                'btn':    '8px',
                'input':  '8px',
                'modal':  '16px',
                'drawer': '16px',
            },
            // ── Shadow tokens ─────────────────────────────────────────
            boxShadow: {
                'soft':     '0 1px 3px rgba(0,0,0,0.08)',
                'elevated': '0 8px 24px rgba(0,0,0,0.10)',
                'none-dark': '0 0 0 1px rgba(255,255,255,0.06)',
            },
            // ── Background images ─────────────────────────────────────
            backgroundImage: {
                'primary-gradient': 'linear-gradient(135deg, #0B5FA5 0%, #3B8BD4 100%)',
                // Soft page-bg wash only — NEVER on cards or buttons
                'soft-gradient': 'linear-gradient(135deg, rgba(254,240,138,0.04) 0%, rgba(186,230,253,0.06) 25%, rgba(187,247,208,0.04) 50%, rgba(254,215,170,0.04) 100%)',
            },
        },
    },
    plugins: [
        require('@tailwindcss/forms'),
        require('@tailwindcss/typography'),
        require('@tailwindcss/aspect-ratio'),
    ],
}
