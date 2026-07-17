from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, View
from django.contrib import messages
from apps.accounts.mixins import AdminRequiredMixin
from .models import Branch, Holiday
from .forms import BranchForm, HolidayForm

class BranchListView(AdminRequiredMixin, ListView):
    model = Branch
    template_name = 'branches/branch_list.html'
    context_object_name = 'branches'
    
    def get_queryset(self):
        queryset = super().get_queryset().select_related('schedule')
        search_query = self.request.GET.get('search', '')
        status_filter = self.request.GET.get('status', '')

        if search_query:
            queryset = queryset.filter(name__icontains=search_query)
            
        if status_filter == 'active':
            queryset = queryset.filter(is_active=True)
        elif status_filter == 'inactive':
            queryset = queryset.filter(is_active=False)
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['status'] = self.request.GET.get('status', '')
        return context

class BranchCreateView(AdminRequiredMixin, CreateView):
    model = Branch
    form_class = BranchForm
    template_name = 'branches/branch_form.html'
    success_url = reverse_lazy('branches:branch_list')

    def form_valid(self, form):
        messages.success(self.request, 'Branch created successfully.')
        return super().form_valid(form)

class BranchEditView(AdminRequiredMixin, UpdateView):
    model = Branch
    form_class = BranchForm
    template_name = 'branches/branch_form.html'
    success_url = reverse_lazy('branches:branch_list')

    def form_valid(self, form):
        messages.success(self.request, 'Branch updated successfully.')
        return super().form_valid(form)

class BranchDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        branch = get_object_or_404(Branch, pk=pk)
        branch.is_active = False
        branch.save()
        messages.success(request, f'Branch "{branch.name}" was deactivated.')
        return redirect('branches:branch_list')

class HolidayListView(AdminRequiredMixin, ListView):
    model = Holiday
    template_name = 'branches/holiday_list.html'
    context_object_name = 'holidays'
    paginate_by = 15

    def get_queryset(self):
        queryset = super().get_queryset().select_related('branch')
        search_query = self.request.GET.get('search', '')
        branch_filter = self.request.GET.get('branch', '')

        if search_query:
            queryset = queryset.filter(name__icontains=search_query)

        if branch_filter:
            queryset = queryset.filter(branch_id=branch_filter)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['branch_filter'] = self.request.GET.get('branch', '')
        
        from apps.branches.utils import get_cached_branches
        context['branches'] = get_cached_branches()
        return context

class HolidayCreateView(AdminRequiredMixin, CreateView):
    model = Holiday
    form_class = HolidayForm
    template_name = 'branches/holiday_form.html'
    success_url = reverse_lazy('branches:holiday_list')

    def form_valid(self, form):
        messages.success(self.request, 'Holiday created successfully.')
        return super().form_valid(form)

class HolidayEditView(AdminRequiredMixin, UpdateView):
    model = Holiday
    form_class = HolidayForm
    template_name = 'branches/holiday_form.html'
    success_url = reverse_lazy('branches:holiday_list')

    def form_valid(self, form):
        messages.success(self.request, 'Holiday updated successfully.')
        return super().form_valid(form)

class HolidayDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        holiday = get_object_or_404(Holiday, pk=pk)
        name = holiday.name
        holiday.delete()
        messages.success(request, f'Holiday "{name}" deleted successfully.')
        return redirect('branches:holiday_list')
