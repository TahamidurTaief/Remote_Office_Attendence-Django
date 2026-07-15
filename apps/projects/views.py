from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, View
from django.contrib import messages
from django.db.models import Q
from apps.accounts.mixins import AdminRequiredMixin
from apps.branches.models import Branch
from .models import Project
from .forms import ProjectForm

class ProjectListView(AdminRequiredMixin, ListView):
    model = Project
    template_name = 'projects/project_list.html'
    context_object_name = 'projects'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset().select_related('project_manager', 'site_engineer', 'branch')
        search_query = self.request.GET.get('search', '')
        status_filter = self.request.GET.get('status', '')
        branch_filter = self.request.GET.get('branch', '')

        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(client_name__icontains=search_query) |
                Q(location__icontains=search_query)
            )
            
        if status_filter:
            queryset = queryset.filter(status=status_filter)
            
        if branch_filter:
            queryset = queryset.filter(branch_id=branch_filter)
            
        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['status_val'] = self.request.GET.get('status', '')
        context['branch_id'] = self.request.GET.get('branch', '')
        context['branches'] = Branch.objects.all()
        context['status_choices'] = Project.STATUS_CHOICES
        return context

class ProjectDetailView(AdminRequiredMixin, DetailView):
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

class ProjectCreateView(AdminRequiredMixin, CreateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, 'Project created successfully.')
        return super().form_valid(form)

class ProjectUpdateView(AdminRequiredMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def form_valid(self, form):
        messages.success(self.request, 'Project updated successfully.')
        return super().form_valid(form)

class ProjectDeleteView(AdminRequiredMixin, View):
    def post(self, request, pk):
        project = get_object_or_404(Project, pk=pk)
        project_name = project.name
        project.delete()
        messages.success(request, f'Project "{project_name}" was successfully deleted.')
        return redirect('projects:project_list')

