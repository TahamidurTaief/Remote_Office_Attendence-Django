# Django Cotton Design System Guidelines & Rules

## 🚫 CRITICAL RULE: NEVER USE SELF-CLOSING COTTON TAGS
Django Cotton does **NOT** correctly parse self-closing component tags (e.g. `<c-badge ... />` or `<c-sidebar ... />`).
Using self-closing tags causes component tree parser corruption and sidebar duplication bugs across pages!

### Correct Usage:
- **ALWAYS** use explicit opening and closing tags:
  - ✅ `<c-badge label="Completed"></c-badge>`
  - ✅ `<c-sidebar active_href="{{ request.path }}"></c-sidebar>`
  - ✅ `<c-input type="text" id="name" label="Name"></c-input>`
  - ✅ `<c-button type="submit" label="Save"></c-button>`

### Forbidden Usage:
  - ❌ `<c-badge label="Completed" />`
  - ❌ `<c-sidebar active_href="{{ request.path }}" />`
  - ❌ `<c-input type="text" id="name" label="Name" />`
  - ❌ `<c-button type="submit" label="Save" />`

---

## 🛡️ PRE-COMMIT VERIFICATION GATE
Before every commit, you **MUST** run:
```bash
python scripts/verify_ui.py
```
Or `.venv/Scripts/python scripts/verify_ui.py` on Windows.

- **Gate 10** in `scripts/verify_ui.py` strictly verifies zero self-closing cotton tags across all 188+ templates.
- A `FAILED` result is **BLOCKING**. Do NOT commit past a failed verification result.
