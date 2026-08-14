from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.accounts.mixins import RoleRequiredMixin
from .auth import has_audit_unlock
from .models import AuditEvent, TrashEntry
from .services import AuditService, TrashService


class TrashListView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = ["admin", "system_owner", "manager", "hr"]

    template_name = "audit/trash_list.html"
    partial_template_name = "audit/partials/trash_table.html"

    def get_queryset(self, request):
        qs = TrashService.get_scoped_entries(request.user).order_by("-deleted_at")
        module = request.GET.get("module", "").strip()
        deleted_by = request.GET.get("deleted_by", "").strip()
        status = request.GET.get("status", TrashEntry.STATUS_ACTIVE).strip()
        search = request.GET.get("q", "").strip()
        if status:
            qs = qs.filter(status=status)
        if module:
            qs = qs.filter(module=module)
        if deleted_by:
            qs = qs.filter(deleted_by__email__icontains=deleted_by)
        if search:
            qs = qs.filter(Q(object_label__icontains=search) | Q(object_id__icontains=search))
        return qs

    def get(self, request):
        from django.core.paginator import Paginator
        entries = self.get_queryset(request)
        paginator = Paginator(entries, 20)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context = {
            "page_obj": page_obj,
            "entries": page_obj.object_list,
            "status_filter": request.GET.get("status", TrashEntry.STATUS_ACTIVE),
            "module_filter": request.GET.get("module", ""),
            "deleted_by_filter": request.GET.get("deleted_by", ""),
            "search_query": request.GET.get("q", ""),
        }
        template = self.partial_template_name if request.headers.get("HX-Request") == "true" else self.template_name
        return render(request, template, context)


class TrashRestoreView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = ["admin", "system_owner", "manager", "hr"]

    def post(self, request, pk):
        scoped = TrashService.get_scoped_entries(request.user)
        entry = get_object_or_404(scoped, pk=pk)
        TrashService.restore(entry, actor=request.user, request=request)
        messages.success(request, f"Restored {entry.object_label}.")
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = request.META.get("HTTP_REFERER", "/audit/trash/")
            return response
        return redirect("audit:trash_list")


class TrashPermanentDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if not request.user.is_superuser:
            raise PermissionDenied("Only super-admin can permanently delete trashed records.")
        scoped = TrashService.get_scoped_entries(request.user)
        entry = get_object_or_404(scoped, pk=pk)
        try:
            TrashService.permanent_delete(entry, actor=request.user, request=request)
            messages.success(request, f"Permanently deleted {entry.object_label}.")
        except Exception as exc:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Permanent delete failed for entry {pk}: {str(exc)}", exc_info=True)
            messages.error(request, "Unable to permanently delete this employee due to active historical or business dependencies.")
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = request.META.get("HTTP_REFERER", "/audit/trash/")
            return response
        return redirect("audit:trash_list")


class TrashBulkActionView(LoginRequiredMixin, RoleRequiredMixin, View):
    allowed_roles = ["admin", "system_owner", "manager", "hr"]

    def post(self, request):
        ids = [pk for pk in request.POST.getlist("ids") if pk.isdigit()]
        action = request.POST.get("bulk_action", "").strip()
        scoped = TrashService.get_scoped_entries(request.user)

        if action != "empty_trash" and not ids:
            messages.warning(request, "No items selected.")
            return redirect("audit:trash_list")

        if action == "restore":
            entries = scoped.filter(pk__in=ids)
            for entry in entries:
                TrashService.restore(entry, actor=request.user, request=request)
            messages.success(request, f"Restored {entries.count()} trash record(s).")
        elif action == "permanent_delete":
            if not request.user.is_superuser:
                raise PermissionDenied("Super-admin only.")
            entries = scoped.filter(pk__in=ids)
            processed_count = 0
            blocked_count = 0
            for entry in entries:
                try:
                    TrashService.permanent_delete(entry, actor=request.user, request=request)
                    processed_count += 1
                except ValidationError:
                    blocked_count += 1
                except Exception:
                    blocked_count += 1
            messages.success(request, f"Processed permanent delete: {processed_count} purged, {blocked_count} blocked by dependencies.")
        elif action == "empty_trash":
            if not request.user.is_superuser:
                raise PermissionDenied("Super-admin only.")
            entries = scoped.filter(status=TrashEntry.STATUS_ACTIVE)
            processed_count = 0
            blocked_count = 0
            for entry in entries:
                try:
                    TrashService.permanent_delete(entry, actor=request.user, request=request)
                    processed_count += 1
                except ValidationError:
                    blocked_count += 1
                except Exception:
                    blocked_count += 1
            messages.success(request, f"Empty trash completed: {processed_count} purged, {blocked_count} blocked by dependencies.")
        return redirect("audit:trash_list")


class ActivityListView(LoginRequiredMixin, View):
    template_name = "audit/activity_list.html"
    partial_template_name = "audit/partials/activity_table.html"

    def get_queryset(self, request):
        qs = AuditService.get_scoped_events(request.user)
        module = request.GET.get("module", "").strip()
        action = request.GET.get("action", "").strip()
        search = request.GET.get("q", "").strip()
        if module:
            qs = qs.filter(module=module)
        if action:
            qs = qs.filter(action=action)
        if search:
            qs = qs.filter(
                Q(object_label__icontains=search) |
                Q(object_id__icontains=search) |
                Q(actor_user__email__icontains=search) |
                Q(reason_note__icontains=search)
            )
        return qs

    def get(self, request):
        from django.core.paginator import Paginator
        events = self.get_queryset(request)
        paginator = Paginator(events, 20)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        context = {
            "page_obj": page_obj,
            "events": page_obj.object_list,
            "module_filter": request.GET.get("module", ""),
            "action_filter": request.GET.get("action", ""),
            "search_query": request.GET.get("q", ""),
        }
        template = self.partial_template_name if request.headers.get("HX-Request") == "true" else self.template_name
        return render(request, template, context)


class AuditEventDetailView(LoginRequiredMixin, View):
    template_name = "audit/event_detail.html"
    partial_template_name = "audit/partials/event_detail.html"

    def get(self, request, uuid):
        event = get_object_or_404(AuditService.get_scoped_events(request.user), uuid=uuid)
        if not has_audit_unlock(request):
            # Store the target so reauth can redirect back
            request.session['pending_reauth_target'] = request.path
            request.session.modified = True
            if request.headers.get('HX-Request'):
                # HTMX: return the modal partial with a trigger to open it
                response = render(request, 'accounts/partials/reauth_modal.html', {
                    'target_url': request.path,
                    'reauth_scope': 'audit_detail',
                    'reauth_title': 'Audit Detail Unlock',
                    'reauth_help_text': 'Confirm your current password or MFA code to view detailed before and after data.',
                })
                response['HX-Trigger'] = 'open-reauth-modal'
                return response
            # Full-page: render the proper styled reauth page
            return render(request, 'audit/reauth_gate.html', {
                'target_url': request.path,
                'reauth_scope': 'audit_detail',
                'reauth_title': 'Audit Detail Unlock',
                'reauth_help_text': 'Confirm your current password or MFA code to view this detailed audit record.',
            })
        AuditService.log_access(request.user, event, reason='audit_detail_opened', request=request)
        if request.headers.get('HX-Request'):
            return render(request, self.partial_template_name, {'event': event})
        return render(request, self.template_name, {'event': event})


class PinMenuView(LoginRequiredMixin, View):
    def post(self, request):
        from django.http import HttpResponse
        from apps.audit.models import PinnedMenuItem
        from apps.audit.menu_registry import PINNABLE_MENUS, can_view_menu

        menu_key = request.POST.get("menu_key")
        if not menu_key or menu_key not in PINNABLE_MENUS:
            return HttpResponse("Invalid menu key.", status=400)
        
        if not can_view_menu(request.user, menu_key):
            return HttpResponse("Unauthorized.", status=403)
            
        PinnedMenuItem.objects.get_or_create(user=request.user, menu_key=menu_key)
        
        if request.headers.get("HX-Request"):
            from urllib.parse import urlparse
            referer = request.META.get("HTTP_REFERER", "")
            ref_path = urlparse(referer).path if referer else "/"
            response = render(request, "cotton/sidebar.html", {
                "active_href": ref_path,
                "path": ref_path,
            })
            response["HX-Trigger"] = "pinned-updated"
            return response
            
        return redirect(request.META.get("HTTP_REFERER", "/"))


class UnpinMenuView(LoginRequiredMixin, View):
    def post(self, request):
        from django.http import HttpResponse
        from apps.audit.models import PinnedMenuItem

        menu_key = request.POST.get("menu_key")
        if not menu_key:
            return HttpResponse("Invalid menu key.", status=400)
            
        PinnedMenuItem.objects.filter(user=request.user, menu_key=menu_key).delete()
        
        if request.headers.get("HX-Request"):
            from urllib.parse import urlparse
            referer = request.META.get("HTTP_REFERER", "")
            ref_path = urlparse(referer).path if referer else "/"
            response = render(request, "cotton/sidebar.html", {
                "active_href": ref_path,
                "path": ref_path,
            })
            response["HX-Trigger"] = "pinned-updated"
            return response
            
        return redirect(request.META.get("HTTP_REFERER", "/"))


class SidebarPartialView(LoginRequiredMixin, View):
    def get(self, request):
        from urllib.parse import urlparse
        referer = request.META.get("HTTP_REFERER", "")
        ref_path = urlparse(referer).path if referer else "/"
        return render(request, "cotton/sidebar.html", {
            "active_href": ref_path,
            "path": ref_path,
        })


class SecureMediaView(LoginRequiredMixin, View):
    def get(self, request, pk):
        from django.http import FileResponse, Http404
        from django.shortcuts import get_object_or_404
        from django.core.files.storage import default_storage
        from apps.audit.models import MediaAsset
        from apps.audit.media_service import MediaService
        
        asset = get_object_or_404(MediaAsset, pk=pk)
        try:
            MediaService.get_secure_url(asset, request.user)
        except PermissionError:
            return HttpResponse("Unauthorized.", status=403)
            
        if asset.provider == "local":
            if default_storage.exists(asset.provider_file_id):
                return FileResponse(default_storage.open(asset.provider_file_id), content_type=asset.mime_type)
        raise Http404("File not found.")


