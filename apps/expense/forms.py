from django import forms
from django.core.exceptions import ValidationError
from .models import Expense, ExpenseCategory

class ExpenseForm(forms.ModelForm):
    category = forms.ModelChoiceField(
        queryset=ExpenseCategory.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'})
    )

    class Meta:
        model = Expense
        fields = ['project', 'amount', 'category', 'description', 'attachment']
        widgets = {
            'project': forms.Select(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'}),
            'amount': forms.NumberInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500', 'step': '0.01'}),
            'description': forms.Textarea(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500', 'rows': 4}),
            'attachment': forms.ClearableFileInput(attrs={'class': 'w-full px-3 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'}),
        }

    def clean_attachment(self):
        attachment = self.cleaned_data.get('attachment')
        if attachment:
            if attachment.size > 5 * 1024 * 1024:
                raise ValidationError("File size cannot exceed 5MB.")
            import os
            ext = os.path.splitext(attachment.name)[1].lower()
            allowed = ['.pdf', '.jpg', '.jpeg', '.png', '.webp']
            if ext not in allowed:
                raise ValidationError("Only PDF and Image files (JPG, PNG, WEBP) are allowed.")
        return attachment
