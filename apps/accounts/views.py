from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib import messages
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.forms import PasswordChangeForm

class CustomLoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return self.redirect_based_on_role(request.user)
        return render(request, 'accounts/login.html')

    def post(self, request):
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, email=email, password=password)
        
        if user is not None:
            if user.is_active:
                login(request, user)
                return self.redirect_based_on_role(user)
            else:
                messages.error(request, 'Your account is disabled.')
        else:
            messages.error(request, 'Invalid email or password.')
            
        return render(request, 'accounts/login.html')
            
    def redirect_based_on_role(self, user):
        if user.role == 'admin':
            return redirect('/admin-panel/dashboard/')
        elif user.role == 'staff':
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
