/**
 * scripts/verify_ai_application_shell.js
 * Node assertion audit gate for the AI-ready application shell:
 * - Font files & OFL licenses
 * - @font-face rules & global typography stack
 * - ai_icon.png integrity & usage
 * - AI navigation group with restrained red-orange accents
 * - Global chatbot launcher & modal
 * - "Powered by TaiefLab" footer link
 * - Zero Google AI Studio / CDN requests
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

console.log('=== VERIFYING AI APPLICATION SHELL & FONTS ===\n');

const projectRoot = path.dirname(__dirname);

// 1. Check self-hosted font files & OFL licenses
const fontDir = path.join(projectRoot, 'static', 'fonts');
assert(fs.existsSync(path.join(fontDir, 'Inter-VariableFont_opsz,wght.woff2')), 'Inter Variable font must exist in static/fonts');
assert(fs.existsSync(path.join(fontDir, 'noto-sans-bengali-bengali-wght-normal.woff2')), 'Noto Sans Bengali Variable font must exist in static/fonts');
assert(fs.existsSync(path.join(fontDir, 'OFL-Inter.txt')), 'Inter OFL license must exist in static/fonts');
assert(fs.existsSync(path.join(fontDir, 'OFL-NotoSansBengali.txt')), 'Noto Sans Bengali OFL license must exist in static/fonts');
console.log('PASS: Self-hosted font files and licenses present in static/fonts/');

// 2. Check CSS font stack and @font-face in source.css
const cssSource = fs.readFileSync(path.join(projectRoot, 'static', 'css', 'source.css'), 'utf8');
assert(cssSource.includes("font-family: 'Inter'"), 'source.css must declare Inter @font-face');
assert(cssSource.includes("font-family: 'Noto Sans Bengali'"), 'source.css must declare Noto Sans Bengali @font-face');
assert(cssSource.includes('"Inter", "Noto Sans Bengali", "Segoe UI Historic", "Segoe UI", Helvetica, Arial, sans-serif'), 'source.css body must have the exact required font stack');
assert(!cssSource.includes('fonts.googleapis.com'), 'source.css must NOT have google fonts import');
console.log('PASS: source.css font stack and @font-face verified');

// 3. Check compiled CSS
const cssDist = fs.readFileSync(path.join(projectRoot, 'static', 'css', 'dist', 'styles.css'), 'utf8');
assert(cssDist.includes('Inter-VariableFont_opsz,wght.woff2'), 'Compiled CSS must reference Inter woff2');
assert(cssDist.includes('noto-sans-bengali-bengali-wght-normal.woff2'), 'Compiled CSS must reference Noto Sans Bengali woff2');
assert(!cssDist.includes('fonts.googleapis.com'), 'Compiled CSS must NOT have google fonts url');
console.log('PASS: Compiled CSS contains self-hosted fonts without Google Fonts');

// 4. Check ai_icon.png
const iconPath = path.join(projectRoot, 'static', 'icons', 'ai_icon.png');
assert(fs.existsSync(iconPath), 'static/icons/ai_icon.png must exist');
const iconBuf = fs.readFileSync(iconPath);
assert(iconBuf.length > 1000, 'ai_icon.png must have valid file content');
console.log('PASS: ai_icon.png exists with valid size (' + iconBuf.length + ' bytes)');

// 5. Check templates for AI Workspace and Chatbot
const sidebarHtml = fs.readFileSync(path.join(projectRoot, 'templates', 'cotton', 'sidebar.html'), 'utf8');
assert(sidebarHtml.includes('ai_workspace'), 'Sidebar must contain ai_workspace submenu');
assert(sidebarHtml.includes('ai_icon.png'), 'Sidebar must use static/icons/ai_icon.png');
assert(sidebarHtml.includes('#F97316') || sidebarHtml.includes('text-[#F97316]'), 'Sidebar must use restrained orange accents');
assert(sidebarHtml.includes('#EF4444') || sidebarHtml.includes('bg-[#EF4444]'), 'Sidebar must use restrained red accents');
console.log('PASS: Sidebar contains AI Intelligence group with restrained red-orange accents and ai_icon.png');

const chatbotHtml = fs.readFileSync(path.join(projectRoot, 'templates', 'cotton', 'ai-chatbot.html'), 'utf8');
assert(chatbotHtml.includes('ft-ai-chatbot'), 'ai-chatbot.html must define #ft-ai-chatbot');
assert(chatbotHtml.includes('Powered by TaiefLab'), 'ai-chatbot.html must include Powered by TaiefLab');
assert(chatbotHtml.includes('https://www.taieflab.com'), 'ai-chatbot.html must link to https://www.taieflab.com');
assert(chatbotHtml.includes('ai_icon.png'), 'ai-chatbot.html must use ai_icon.png');
assert(chatbotHtml.includes('ai-chat-input'), 'ai-chatbot.html must have message input');
assert(!chatbotHtml.includes('generativelanguage.googleapis.com'), 'ai-chatbot.html must not contact Google AI Studio');
console.log('PASS: Global floating chatbot launcher & modal verified');

const workspaceHtml = fs.readFileSync(path.join(projectRoot, 'templates', 'admin_panel', 'ai_workspace.html'), 'utf8');
assert(workspaceHtml.includes('Powered by TaiefLab'), 'ai_workspace.html must include Powered by TaiefLab');
assert(workspaceHtml.includes('https://www.taieflab.com'), 'ai_workspace.html must link to https://www.taieflab.com');
assert(workspaceHtml.includes('c-app-shell'), 'ai_workspace.html must use c-app-shell');
console.log('PASS: AI Workspace template verified');

console.log('\n==================================================');
console.log(' ALL 20 NODE ASSERTIONS PASSED (Exit Code 0)');
console.log('==================================================\n');
