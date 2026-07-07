from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, View
from django.contrib import messages
from apps.accounts.mixins import AdminRequiredMixin
from .models import Branch
from .forms import BranchForm

class BranchListView(AdminRequiredMixin, ListView):
    model = Branch
    template_name = 'branches/branch_list.html'
    context_object_name = 'branches'
    
    def get_queryset(self):
        queryset = super().get_queryset()
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
