(function() {
    // Register Alpine.js component for dropdown
    function registerAlpineDropdown() {
        if (window.Alpine && Alpine.data) {
            if (Alpine.components && Alpine.components.customDropdown) return;
            Alpine.data('customDropdown', () => ({
                open: false,
                search: '',
                options: [],
                selectedValue: '',
                placeholder: 'Select...',
                targetId: '',
                targetName: '',
                isDisabled: false,

                init() {
                    const container = this.$root.closest('.custom-dropdown-container');
                    const selectEl = container ? container.previousElementSibling : null;
                    
                    if (selectEl && selectEl.tagName === 'SELECT') {
                        this.targetId = selectEl.id || '';
                        this.targetName = selectEl.name || '';
                        this.isDisabled = selectEl.disabled;
                        this.placeholder = selectEl.getAttribute('placeholder') || 'Select...';
                        
                        this.loadOptions(selectEl);
                        this.selectedValue = selectEl.value;
                        
                        // Sync when original select value changes programmatically
                        this.changeHandler = () => {
                            if (this.selectedValue !== selectEl.value) {
                                this.selectedValue = selectEl.value;
                            }
                        };
                        selectEl.addEventListener('change', this.changeHandler);
                        
                        // Watch for disabled attribute change or child option changes
                        this.observer = new MutationObserver((mutations) => {
                            this.loadOptions(selectEl);
                            this.selectedValue = selectEl.value;
                            this.isDisabled = selectEl.disabled;
                        });
                        this.observer.observe(selectEl, {
                            childList: true,
                            attributes: true,
                            attributeFilter: ['disabled']
                        });
                    }

                    // Watch the 'open' state to dynamically raise the z-index stack when expanded
                    this.$watch('open', value => {
                        if (value) {
                            this.$el.style.setProperty('z-index', '9999', 'important');
                            if (this.$el.parentElement) {
                                this.$el.parentElement.style.setProperty('z-index', '9999', 'important');
                            }
                        } else {
                            this.$el.style.removeProperty('z-index');
                            if (this.$el.parentElement) {
                                this.$el.parentElement.style.removeProperty('z-index');
                            }
                        }
                    });
                },

                loadOptions(selectEl) {
                    this.options = Array.from(selectEl.options).map(opt => ({
                        value: opt.value,
                        text: opt.textContent.trim()
                    }));
                },

                get selectedText() {
                    const opt = this.options.find(o => o.value === this.selectedValue);
                    return opt ? opt.text : this.placeholder;
                },

                get filteredOptions() {
                    if (!this.search) return this.options;
                    return this.options.filter(o => o.text.toLowerCase().includes(this.search.toLowerCase()));
                },

                select(val) {
                    this.selectedValue = val;
                    const container = this.$root.closest('.custom-dropdown-container');
                    const selectEl = container ? container.previousElementSibling : null;
                    if (selectEl) {
                        selectEl.value = val;
                        selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    this.open = false;
                    this.search = '';
                },
                
                destroy() {
                    const container = this.$root.closest('.custom-dropdown-container');
                    const selectEl = container ? container.previousElementSibling : null;
                    if (selectEl && this.changeHandler) {
                        selectEl.removeEventListener('change', this.changeHandler);
                    }
                    if (this.observer) {
                        this.observer.disconnect();
                    }
                }
            }));
        }
    }

    if (window.Alpine) {
        registerAlpineDropdown();
    } else {
        document.addEventListener('alpine:init', registerAlpineDropdown);
    }

    function convertSelectToCustomDropdown(selectEl) {
        if (selectEl.dataset.customDropdownInitialized || selectEl.classList.contains('no-custom-dropdown')) {
            return;
        }
        // Mark as initialized
        selectEl.dataset.customDropdownInitialized = 'true';
        
        // Hide the original select
        selectEl.style.setProperty('display', 'none', 'important');
        
        const name = selectEl.name || '';
        const id = selectEl.id || '';
        const placeholder = selectEl.getAttribute('placeholder') || 'Select...';
        
        // Create wrapper div
        const wrapper = document.createElement('div');
        wrapper.className = 'custom-dropdown-container relative w-full text-left inline-block';
        
        // Copy layout classes from original select
        const layoutClasses = Array.from(selectEl.classList).filter(cls => 
            cls.startsWith('w-') || 
            cls.startsWith('m-') || 
            cls.startsWith('mt-') || 
            cls.startsWith('mb-') || 
            cls.startsWith('ml-') || 
            cls.startsWith('mr-') || 
            cls.startsWith('p-') || 
            cls.startsWith('flex-') || 
            cls.startsWith('col-') ||
            cls.startsWith('row-') ||
            cls.startsWith('grid-') ||
            cls.startsWith('block') ||
            cls.startsWith('inline-block') ||
            cls.startsWith('hidden')
        );
        if (layoutClasses.length) {
            wrapper.classList.add(...layoutClasses);
        }
        
        // Custom Dropdown HTML Template
        wrapper.innerHTML = `
            <div x-data="customDropdown" @click.away="open = false" class="w-full relative">
                
                <!-- Dropdown Trigger Button -->
                <button type="button" @click="if (!isDisabled) open = !open"
                        :disabled="isDisabled"
                        :class="isDisabled ? 'bg-gray-50 border-gray-150 text-gray-400 cursor-not-allowed' : 'bg-white border-gray-200 text-gray-700 hover:border-gray-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500'"
                        class="w-full flex items-center justify-between border rounded-md px-2.5 py-1.5 text-[12px] transition duration-150 ease-in-out shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                    <span x-text="selectedText" class="truncate"></span>
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                         class="text-gray-400 shrink-0 ml-1.5 transition-transform duration-200" :class="open ? 'transform rotate-180' : ''">
                         <path d="m6 9 6 6 6-6"/>
                    </svg>
                </button>
                
                <!-- Dropdown Menu Panel -->
                <div x-show="open" 
                     x-transition:enter="transition ease-out duration-100" 
                     x-transition:enter-start="transform opacity-0 scale-95" 
                     x-transition:enter-end="transform opacity-100 scale-100" 
                     x-transition:leave="transition ease-in duration-75" 
                     x-transition:leave-start="transform opacity-100 scale-100" 
                     x-transition:leave-end="transform opacity-0 scale-95"
                     class="absolute z-[999] mt-1 w-full bg-white border border-gray-150 rounded-lg shadow-lg max-h-60 overflow-hidden flex flex-col" 
                     x-cloak>
                    
                    <!-- Search Input Box -->
                    <div class="px-2 py-1.5 border-b border-gray-100 bg-white shrink-0" @click.stop>
                        <div class="flex items-center gap-1.5 bg-[#F2F2F7] rounded px-2 py-1">
                            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-gray-400 shrink-0"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                            <input type="text" x-model="search" placeholder="Search..."
                                   class="bg-transparent text-[11px] text-gray-700 placeholder-gray-400 focus:outline-none w-full !border-0 !p-0 !shadow-none !ring-0">
                        </div>
                    </div>
                    
                    <!-- Scrollable Options List -->
                    <div class="overflow-y-auto py-1 max-h-44 scrollbar-hide">
                        <template x-for="opt in filteredOptions" :key="opt.value">
                            <button type="button" @click="select(opt.value)"
                                    class="w-full text-left px-3 py-1.5 text-[12px] hover:bg-[#F2F2F7] hover:text-[#0B5FA5] transition-colors flex items-center justify-between"
                                    :class="selectedValue === opt.value ? 'bg-[#EFF6FF] text-[#0B5FA5] font-semibold' : 'text-gray-700'">
                                <span x-text="opt.text" class="truncate"></span>
                                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" class="text-[#0B5FA5] shrink-0" x-show="selectedValue === opt.value"><path d="M20 6 9 17l-5-5"/></svg>
                            </button>
                        </template>
                        <div x-show="filteredOptions.length === 0" class="px-3 py-2.5 text-[11px] text-gray-400 text-center">
                            No matching items
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Insert custom dropdown adjacent to original select
        selectEl.parentNode.insertBefore(wrapper, selectEl.nextSibling);
    }

    function initCustomDropdowns() {
        registerAlpineDropdown();
        document.querySelectorAll('select').forEach(convertSelectToCustomDropdown);
    }

    // Initialize on page load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initCustomDropdowns);
    } else {
        initCustomDropdowns();
    }

    // Initialize after HTMX settlement
    document.body.addEventListener('htmx:afterSettle', initCustomDropdowns);
})();
