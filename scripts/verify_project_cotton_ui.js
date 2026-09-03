/**
 * scripts/verify_project_cotton_ui.js
 * 
 * Strict pre-commit audit gate using Node assert for the four project-related routes:
 * 1. /projects/<id>/ (templates/projects/project_detail.html)
 * 2. /projects/<id>/gantt/ (templates/projects/project_gantt.html)
 * 3. /audit/activity/?module=projects&object_id=3 (templates/audit/activity_list.html & partials)
 * 4. /projects/<id>/edit/ (templates/projects/project_form.html)
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const projectRoot = path.dirname(__dirname);

const TARGET_FILES = [
  'templates/projects/project_detail.html',
  'templates/projects/project_gantt.html',
  'templates/projects/project_form.html',
  'templates/audit/activity_list.html',
  'templates/audit/partials/activity_table.html',
  'templates/projects/partials/task_status_dropdown.html',
  'templates/projects/partials/responsible_person_select.html',
];

const FORBIDDEN_TEXT_CLASSES = [
  /\btext-lg\b/,
  /\btext-xl\b/,
  /\btext-2xl\b/,
  /\btext-3xl\b/,
  /\btext-4xl\b/,
  /\btext-base\b/,
  /\btext-sm\b/,
  /\btext-\[1[4-9]px\]/,
  /\btext-\[2[0-9]px\]/,
];

function runAudits() {
  console.log('=== VERIFYING PROJECT COTTON UI ARCHITECTURE ===\n');

  let checkedFiles = 0;
  let totalAssertions = 0;

  for (const relPath of TARGET_FILES) {
    const fullPath = path.join(projectRoot, relPath);
    assert(fs.existsSync(fullPath), `File must exist: ${relPath}`);

    const content = fs.readFileSync(fullPath, 'utf8');
    checkedFiles++;

    // 1. Check: No self-closing cotton tags <c-... />
    const selfClosingMatches = content.match(/<c-[a-zA-Z0-9_-]+[^>]*?\/>/g);
    assert.strictEqual(
      selfClosingMatches,
      null,
      `[FAILED] ${relPath} contains self-closing cotton tag: ${selfClosingMatches && selfClosingMatches[0]}`
    );
    totalAssertions++;

    // 2. Check: No inline style="..." expressions in page templates
    // Exclude cotton component definitions (which encapsulate unavoidable math)
    if (!relPath.startsWith('templates/cotton/')) {
      const inlineStyleMatches = content.match(/(\sstyle="[^"]*"|\s:style="[^"]*")/g);
      assert.strictEqual(
        inlineStyleMatches,
        null,
        `[FAILED] ${relPath} contains inline style attribute: ${inlineStyleMatches && inlineStyleMatches[0]}`
      );
      totalAssertions++;
    }

    // 3. Check: No forbidden large text classes (text must be <= 13px)
    for (const regex of FORBIDDEN_TEXT_CLASSES) {
      const match = content.match(regex);
      assert.strictEqual(
        match,
        null,
        `[FAILED] ${relPath} contains forbidden text class matching ${regex}: "${match && match[0]}"`
      );
      totalAssertions++;
    }

    // 4. Check: No native alert() or confirm() in template scripts/handlers
    const nativeAlertConfirm = content.match(/\b(alert|confirm)\s*\(/g);
    assert.strictEqual(
      nativeAlertConfirm,
      null,
      `[FAILED] ${relPath} contains native alert/confirm call: ${nativeAlertConfirm && nativeAlertConfirm[0]}`
    );
    totalAssertions++;

    // 5. Check: Page templates must use Cotton tags for main interactive blocks
    if (relPath.endsWith('.html') && !relPath.includes('partials')) {
      assert(content.includes('<c-app-shell'), `[FAILED] ${relPath} must use <c-app-shell>`);
      assert(content.includes('<c-page-container'), `[FAILED] ${relPath} must use <c-page-container>`);
      totalAssertions += 2;
    }

    console.log(`PASS: ${relPath}`);
  }

  console.log('\n==================================================');
  console.log(` PASS SUMMARY: ${checkedFiles} templates verified, ${totalAssertions} assertions passed.`);
  console.log(' Zero self-closing tags, zero inline styles, zero large text tokens.');
  console.log('==================================================\n');
}

try {
  runAudits();
  process.exit(0);
} catch (err) {
  console.error('\n' + err.message);
  process.exit(1);
}
