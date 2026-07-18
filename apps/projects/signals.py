from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import ProjectTask

@receiver(post_save, sender=ProjectTask)
def update_project_progress_on_save(sender, instance, **kwargs):
    if instance.project:
        instance.project.recalculate_progress()

@receiver(post_delete, sender=ProjectTask)
def update_project_progress_on_delete(sender, instance, **kwargs):
    try:
        if instance.project:
            instance.project.recalculate_progress()
    except Exception:
        pass
