(function () {
    'use strict';

    window.initGlobalContextMenu = function () {
        return {
            open: false,
            justOpened: false,
            _customHandled: false,
            x: 100,
            y: 100,
            item: {
                title: '',
                subtitle: '',
                actions: [],
                emp: null,
                targetRow: null
            },
            handleContextMenu: function (e) {
                // If a custom open-action-menu event was already dispatched on this row/tick, skip generic handling
                if (this._customHandled) return;

                if (e.target.closest('input, textarea, select, [contenteditable=true]')) return;

                const row = e.target.closest('tr, [data-context-menu]');
                if (!row || row.closest('thead')) return;

                e.preventDefault();
                e.stopPropagation();

                this.extractAndOpen(e, row, false);
            },
            handleActionMenu: function (detail) {
                if (!detail) return;
                var self = this;
                this._customHandled = true;
                setTimeout(function () { self._customHandled = false; }, 150);
                this.openFromDetail(detail);
            },
            openFromDetail: function (detail) {
                const isButton = detail.isButton || false;
                const emp = detail.emp || null;
                const event = detail.event;

                let targetRow = null;
                if (event && event.target) {
                    targetRow = event.target.closest('tr, [data-context-menu]');
                }

                if (emp) {
                    this.item = {
                        title: emp.name || 'Employee Record',
                        subtitle: emp.number || '',
                        emp: emp,
                        actions: detail.actions || [],
                        targetRow: targetRow
                    };
                    this.positionMenu(event, isButton);
                    return;
                }

                if (targetRow) {
                    this.extractAndOpen(event, targetRow, isButton);
                    if (detail.title) this.item.title = detail.title;
                    if (detail.subtitle) this.item.subtitle = detail.subtitle;
                    if (detail.actions && detail.actions.length > 0) {
                        this.item.actions = detail.actions;
                    }
                    return;
                }

                this.item = {
                    title: detail.title || 'Selected Record',
                    subtitle: detail.subtitle || '',
                    emp: null,
                    actions: detail.actions || [],
                    targetRow: null
                };

                this.positionMenu(event, isButton);
            },
            extractAndOpen: function (e, row, isButton) {
                isButton = isButton || false;
                this.item.targetRow = row;
                this.item.emp = null;

                // 1. Determine title and subtitle
                let title = row.getAttribute('data-title');
                let subtitle = row.getAttribute('data-subtitle');

                if (!title) {
                    // Look for non-avatar link or heading text
                    const titleCandidates = row.querySelectorAll('a.font-semibold, a.font-medium, td a, td .font-semibold, td .font-medium');
                    for (let i = 0; i < titleCandidates.length; i++) {
                        const candidate = titleCandidates[i];
                        if (candidate.closest('[class*="avatar"], .c-avatar')) continue;
                        const txt = candidate.textContent.trim();
                        if (txt.length > 1) {
                            title = txt.split('\n')[0].trim();
                            break;
                        }
                    }
                }

                if (!title) {
                    // Fallback to first non-empty text cell that isn't single character
                    const cells = row.querySelectorAll('td');
                    for (let i = 0; i < cells.length; i++) {
                        const cellTxt = cells[i].textContent.trim();
                        if (cellTxt.length > 1) {
                            title = cellTxt.split('\n')[0].trim();
                            break;
                        }
                    }
                }

                if (!subtitle) {
                    const subEl = row.querySelector('.font-mono, [class*="text-[11px]"], [class*="text-xs"], .text-muted, [style*="text-muted"]');
                    subtitle = subEl ? subEl.textContent.trim().split('\n')[0] : '';
                }

                this.item.title = title || 'Selected Record';
                this.item.subtitle = subtitle;

                // 2. Extract action buttons and links from the row
                const actions = [];

                if (row.getAttribute('data-detail')) {
                    actions.push({ label: 'View Details', icon: 'eye', href: row.getAttribute('data-detail'), type: 'link' });
                }
                if (row.getAttribute('data-edit')) {
                    actions.push({ label: 'Edit Record', icon: 'pencil', href: row.getAttribute('data-edit'), type: 'link' });
                }
                if (row.getAttribute('data-delete')) {
                    actions.push({ label: 'Delete Record', icon: 'trash-2', actionUrl: row.getAttribute('data-delete'), type: 'delete', isDanger: true });
                }

                const links = row.querySelectorAll('td a, td button, a.ft-btn, button.ft-btn');
                links.forEach(function (el) {
                    const isMoreButton = el.querySelector('[data-lucide="more-vertical"]') || el.getAttribute('title') === 'More Actions' || el.classList.contains('c-context-ignore');
                    if (isMoreButton) return;

                    // Skip main title link if it's just navigating to detail view to prevent duplicate
                    const isTitleLink = el.classList.contains('font-semibold') || el.classList.contains('font-medium');

                    const text = (el.textContent.trim() || el.getAttribute('title') || el.getAttribute('aria-label') || '').trim();
                    const href = el.getAttribute('href');
                    const hxGet = el.getAttribute('hx-get');
                    const hxPost = el.getAttribute('hx-post');

                    if (!text && !href && !hxGet && !hxPost) return;

                    let icon = 'arrow-right';
                    let isDanger = false;
                    const lower = text.toLowerCase();

                    if (lower.indexOf('delete') !== -1 || lower.indexOf('remove') !== -1 || el.querySelector('[data-lucide*="trash"]')) {
                        icon = 'trash-2';
                        isDanger = true;
                    } else if (lower.indexOf('edit') !== -1 || lower.indexOf('update') !== -1 || el.querySelector('[data-lucide*="pencil"], [data-lucide*="edit"]')) {
                        icon = 'pencil';
                    } else if (lower.indexOf('view') !== -1 || lower.indexOf('detail') !== -1 || el.querySelector('[data-lucide*="eye"]')) {
                        icon = 'eye';
                    } else if (lower.indexOf('inactiv') !== -1 || el.querySelector('[data-lucide*="user-x"]')) {
                        icon = 'user-x';
                    } else if (lower.indexOf('activ') !== -1 || el.querySelector('[data-lucide*="user-check"]')) {
                        icon = 'user-check';
                    } else if (lower.indexOf('suspend') !== -1 || el.querySelector('[data-lucide*="pause"]')) {
                        icon = 'pause-circle';
                        isDanger = true;
                    } else if (lower.indexOf('archive') !== -1 || el.querySelector('[data-lucide*="archive"]')) {
                        icon = 'archive';
                    } else if (lower.indexOf('play') !== -1 || lower.indexOf('resume') !== -1 || el.querySelector('[data-lucide*="play"]')) {
                        icon = 'play';
                    } else if (lower.indexOf('drawer') !== -1 || lower.indexOf('inspect') !== -1 || el.querySelector('[data-lucide*="panel"]')) {
                        icon = 'panel-right-open';
                    }

                    // Avoid duplicate actions
                    const labelText = text || (icon === 'trash-2' ? 'Delete' : (icon === 'pencil' ? 'Edit' : 'View Details'));
                    if (!isTitleLink && !actions.some(function (a) { return a.label.toLowerCase() === labelText.toLowerCase(); })) {
                        actions.push({
                            label: labelText,
                            icon: icon,
                            href: href,
                            hxGet: hxGet,
                            hxPost: hxPost,
                            el: el,
                            type: href ? 'link' : 'click',
                            isDanger: isDanger
                        });
                    }
                });

                this.item.actions = actions;
                this.positionMenu(e, isButton);
            },
            positionMenu: function (e, isButton) {
                let posX = 100, posY = 100;
                const menuW = 280;
                const menuH = 340;
                const winW = window.innerWidth;
                const winH = window.innerHeight;

                const targetEl = (e && e.target) ? e.target.closest('button, a, tr, [data-context-menu]') : null;

                if (isButton && targetEl) {
                    const rect = targetEl.getBoundingClientRect();
                    posX = (rect.right + menuW > winW) ? Math.max(12, rect.right - menuW) : rect.left;
                    posY = (rect.bottom + menuH > winH) ? Math.max(12, rect.top - menuH) : rect.bottom + 4;
                } else if (e && (e.clientX !== undefined || e.pageX !== undefined)) {
                    const clientX = (e.clientX !== undefined && e.clientX !== 0) ? e.clientX : (e.pageX ? e.pageX - window.scrollX : 100);
                    const clientY = (e.clientY !== undefined && e.clientY !== 0) ? e.clientY : (e.pageY ? e.pageY - window.scrollY : 100);
                    posX = (clientX + menuW > winW) ? Math.max(12, clientX - menuW) : clientX;
                    posY = (clientY + menuH > winH) ? Math.max(12, clientY - menuH) : clientY;
                } else if (targetEl) {
                    const rect = targetEl.getBoundingClientRect();
                    posX = rect.left;
                    posY = rect.bottom + 4;
                } else {
                    posX = winW / 2 - menuW / 2;
                    posY = winH / 2 - menuH / 2;
                }

                this.x = Math.round(posX);
                this.y = Math.round(posY);
                this.justOpened = true;
                this.open = true;

                var self = this;
                setTimeout(function () { self.justOpened = false; }, 200);

                this.$nextTick(function () {
                    const menu = self.$refs.contextMenu;
                    if (menu) {
                        const actualW = menu.offsetWidth || 280;
                        const actualH = menu.offsetHeight || 340;
                        if (self.x + actualW > winW) {
                            self.x = Math.max(12, winW - actualW - 12);
                        }
                        if (self.y + actualH > winH) {
                            self.y = Math.max(12, winH - actualH - 12);
                        }
                    }
                    if (window.lucide) window.lucide.createIcons();
                });
            },
            triggerItemAction: function (action) {
                this.open = false;
                if (action.actionUrl) {
                    this.triggerActionUrl(action.actionUrl);
                } else if (action.type === 'link' && action.href) {
                    window.location.href = action.href;
                } else if (action.hxGet) {
                    if (window.htmx) htmx.ajax('GET', action.hxGet, { target: '#modal-container', swap: 'innerHTML' });
                } else if (action.el) {
                    action.el.click();
                }
            },
            triggerActionUrl: function (url, target, swap) {
                target = target || '#modal-container';
                swap = swap || 'innerHTML';
                this.open = false;
                if (window.htmx) {
                    htmx.ajax('GET', url, { target: target, swap: swap });
                } else {
                    window.location.href = url;
                }
            },
            openAiWithContext: function () {
                this.open = false;
                const query = 'Analyze status, history, and details for ' + (this.item.title || 'this record') + (this.item.subtitle ? ' (' + this.item.subtitle + ')' : '');
                window.dispatchEvent(new CustomEvent('open-ai-chatbot', { detail: { message: query } }));
            }
        };
    };
})();
