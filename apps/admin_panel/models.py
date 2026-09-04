from django.db import models
from django.conf import settings
from django.utils import timezone


class AISetting(models.Model):
    """
    Stores system-wide AI configuration:
    - AI Behavior: persona / custom instructions, temperature, max tokens, operational context toggle
    - Primary API provider, model, and key
    - Ordered fallback API keys / providers for automatic failover
    """
    PROVIDER_CHOICES = [
        ('gemini', 'Google Gemini'),
        ('openai', 'OpenAI (GPT-4o / GPT-4o-mini)'),
        ('groq', 'Groq (Llama / Mixtral)'),
        ('anthropic', 'Anthropic Claude'),
        ('openrouter', 'OpenRouter (Universal)'),
    ]

    DEFAULT_SYSTEM_PROMPT = (
        "You are FieldTrack AI Assistant, the intelligent workforce and operational analytics "
        "copilot for the FieldTrack workforce platform. Provide accurate, professional, concise, "
        "and truthful insights based on the provided operational records. Answer directly in 2 to 4 sentences."
    )

    # Behavior settings
    system_prompt = models.TextField(
        default=DEFAULT_SYSTEM_PROMPT,
        help_text="Custom system instructions or persona guiding the AI's responses."
    )
    temperature = models.FloatField(
        default=0.3,
        help_text="Sampling temperature (0.0 = deterministic/focused, 1.0 = creative)."
    )
    max_tokens = models.IntegerField(
        default=800,
        help_text="Maximum tokens per AI response."
    )
    include_operational_context = models.BooleanField(
        default=True,
        help_text="Inject allowlisted, read-only RBAC operational database metrics into queries."
    )

    # Primary provider configuration
    primary_provider = models.CharField(
        max_length=50,
        choices=PROVIDER_CHOICES,
        default='gemini'
    )
    primary_model = models.CharField(
        max_length=100,
        default='gemini-2.5-flash',
        help_text="Model identifier (e.g. gemini-2.5-flash, gpt-4o-mini, llama-3.3-70b-versatile)"
    )
    primary_api_key = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Primary API key. If empty, falls back to server environment variables."
    )

    # Multiple Fallback APIs (ordered list of provider configs)
    # Stored as JSON: [{"provider": "groq", "model": "llama-3.3-70b-versatile", "api_key": "...", "label": "Backup Groq", "is_active": True}, ...]
    fallback_configs = models.JSONField(
        default=list,
        blank=True,
        help_text="List of ordered fallback providers and keys invoked when the primary fails."
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ai_settings_updated'
    )

    class Meta:
        verbose_name = 'AI Setting'
        verbose_name_plural = 'AI Settings'

    def __str__(self):
        return f"AISetting(primary={self.primary_provider}, updated={self.updated_at.strftime('%Y-%m-%d %H:%M')})"

    @classmethod
    def get_settings(cls):
        """
        Singleton retrieval method: returns existing configuration or creates the default.
        """
        setting, _ = cls.objects.get_or_create(id=1)
        return setting
