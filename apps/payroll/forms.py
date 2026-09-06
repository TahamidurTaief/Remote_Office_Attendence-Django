from django import forms
from decimal import Decimal
from apps.payroll.models import (
    PayrollConfiguration,
    PayFrequency,
    PayrollPeriodType,
    AbsenceDivisorMode,
    ProrationMethod,
    RoundingRule,
    NegativeNetPayPolicy,
)


class PayrollConfigurationForm(forms.ModelForm):
    """
    Form for configuring tenant-scoped payroll rules and policies.
    """
    class Meta:
        model = PayrollConfiguration
        fields = [
            'currency',
            'pay_frequency',
            'payroll_period_type',
            'cutoff_day',
            'payment_day',
            'working_day_basis',
            'attendance_source',
            'leave_source',
            'overtime_source',
            'overtime_multiplier',
            'proration_method',
            'rounding_rule',
            'negative_net_pay_policy',
            'approval_workflow_steps',
            'payslip_prefix',
            'notes',
        ]
        widgets = {
            'currency': forms.TextInput(attrs={'placeholder': 'e.g. BDT, USD'}),
            'pay_frequency': forms.Select(),
            'payroll_period_type': forms.Select(),
            'cutoff_day': forms.NumberInput(attrs={'min': 1, 'max': 31}),
            'payment_day': forms.NumberInput(attrs={'min': 1, 'max': 31}),
            'working_day_basis': forms.Select(),
            'attendance_source': forms.TextInput(),
            'leave_source': forms.TextInput(),
            'overtime_source': forms.TextInput(),
            'overtime_multiplier': forms.NumberInput(attrs={'step': '0.05', 'min': '1.00', 'max': '5.00'}),
            'proration_method': forms.Select(),
            'rounding_rule': forms.Select(),
            'negative_net_pay_policy': forms.Select(),
            'approval_workflow_steps': forms.Select(choices=[(1, '1-Step (Direct Admin Approval)'), (2, '2-Step (Review then Final Approval)')]),
            'payslip_prefix': forms.TextInput(attrs={'placeholder': 'e.g. PSL-'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional notes for this configuration version...'}),
        }

    def clean_cutoff_day(self):
        val = self.cleaned_data.get('cutoff_day')
        if val is not None and (val < 1 or val > 31):
            raise forms.ValidationError("Cutoff day must be between 1 and 31.")
        return val

    def clean_payment_day(self):
        val = self.cleaned_data.get('payment_day')
        if val is not None and (val < 1 or val > 31):
            raise forms.ValidationError("Payment day must be between 1 and 31.")
        return val

    def clean_overtime_multiplier(self):
        val = self.cleaned_data.get('overtime_multiplier')
        if val is not None and val < Decimal('1.00'):
            raise forms.ValidationError("Overtime multiplier must be at least 1.00.")
        return val
