from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib import messages
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache

class CustomLoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return self.redirect_based_on_role(request.user)
        return render(request, 'accounts/login.html')

    def post(self, request):
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        # Get client IP
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
            
        cache_key = f"login_attempts_{ip}"
        attempts = cache.get(cache_key, 0)
        
        if attempts >= 5:
            messages.error(request, 'Too many login attempts. Please try again in 5 minutes.')
            return render(request, 'accounts/login.html')
            
        user = authenticate(request, email=email, password=password)
        
        if user is not None:
            if user.is_active:
                login(request, user)
                cache.delete(cache_key)
                return self.redirect_based_on_role(user)
            else:
                messages.error(request, 'Your account is disabled.')
        else:
            cache.set(cache_key, attempts + 1, timeout=300)  # 5 minutes block
            messages.error(request, 'Invalid email or password.')
            
        return render(request, 'accounts/login.html')
            
    def redirect_based_on_role(self, user):
        if user.role == 'admin':
            return redirect('/admin-panel/dashboard/')
        elif user.role in ['staff', 'manager']:
            return redirect('/staff/home/')
        return redirect('/')

class CustomLogoutView(View):
    def get(self, request):
        logout(request)
        return redirect('/login/')
        
    def post(self, request):
        logout(request)
        return redirect('/login/')

class ChangePasswordView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'accounts/change_password.html')

    def post(self, request):
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password was successfully updated!')
            return redirect('/change-password/')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
            return render(request, 'accounts/change_password.html')


def index_view(request):
    return render(request, 'index.html')
