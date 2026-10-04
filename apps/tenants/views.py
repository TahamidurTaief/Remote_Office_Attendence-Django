import json
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render, redirect
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from apps.accounts.mixins import AccessMixin
from .services import CompanyConfigurationService


class CompanyConfigurationView(AccessMixin, View):
    """
    Tenant-scoped Company Configuration View.
    Supports GET (settings.view) and POST (settings.edit).
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        try:
            config = CompanyConfigurationService.get_configuration(request)
        except PermissionDenied as e:
            if request.headers.get('Accept') == 'application/json' or request.GET.get('format') == 'json':
                return JsonResponse({'status': 'error', 'message': str(e)}, status=403)
            return HttpResponseForbidden(str(e))

        data = CompanyConfigurationService.serialize_config(config, mask_secrets=True)

        if request.headers.get('Accept') == 'application/json' or request.GET.get('format') == 'json':
            return JsonResponse({'status': 'success', 'data': data})

        return render(request, 'tenants/company_settings.html', {
            'config': config,
            'config_data': data,
            'canonical_tenant': config.tenant,
        })

    def post(self, request, *args, **kwargs):
        is_json = request.content_type == 'application/json' or request.headers.get('Accept') == 'application/json'

        update_data = {}
        if request.content_type == 'application/json':
            try:
                update_data = json.loads(request.body.decode('utf-8'))
            except json.JSONDecodeError:
                return JsonResponse({'status': 'error', 'message': 'Invalid JSON body.'}, status=400)
        else:
            update_data = request.POST.dict()

        try:
            config = CompanyConfigurationService.update_configuration(request, update_data)
        except PermissionDenied as e:
            if is_json:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=403)
            return HttpResponseForbidden(str(e))
        except ValidationError as e:
            err_msg = e.message_dict if hasattr(e, 'message_dict') else str(e)
            if is_json:
                return JsonResponse({'status': 'error', 'errors': err_msg}, status=400)
            messages.error(request, f"Validation error: {err_msg}")
            return redirect('tenants:company_settings')

        data = CompanyConfigurationService.serialize_config(config, mask_secrets=True)

        if is_json:
            return JsonResponse({'status': 'success', 'message': 'Configuration updated successfully.', 'data': data})

        messages.success(request, 'Company configuration updated successfully.')
        return redirect('tenants:company_settings')
