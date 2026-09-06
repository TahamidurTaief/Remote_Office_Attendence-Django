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


from apps.employees.models import Bank, BankBranch, Employee
from apps.employees.bank_registry import BANGLADESH_BANK_CHOICES, BANGLADESH_BANK_GROUPS, resolve_canonical_bank_name
from apps.payroll.models import PayrollPaymentDestination, PaymentType, MFSProvider
from apps.payroll.payment_destination_service import PayrollPaymentDestinationService


class PayrollPaymentDestinationForm(forms.Form):
    """
    Form for configuring and updating tenant-scoped employee payment destination.
    """
    payment_type = forms.ChoiceField(
        choices=PaymentType.choices,
        widget=forms.Select()
    )
    bank_name = forms.ChoiceField(
        choices=BANGLADESH_BANK_CHOICES,
        required=False,
        widget=forms.Select()
    )
    branch_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Branch name'})
    )
    account_holder_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Account Holder Name'})
    )
    account_number = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Bank Account Number'})
    )
    routing_number = forms.CharField(
        max_length=9,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': '9-digit routing number (optional)'})
    )

    mfs_provider = forms.ChoiceField(
        choices=[('', '-- Choose Provider --')] + MFSProvider.choices,
        required=False,
        widget=forms.Select()
    )
    wallet_number = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. 017XXXXXXXX'})
    )

    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional destination notes...'})
    )

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)
        if employee:
            dest = getattr(employee, 'payment_destination', None)
            if dest:
                self.fields['payment_type'].initial = dest.payment_type
                if dest.payment_type == PaymentType.BANK:
                    self.fields['bank_name'].initial = resolve_canonical_bank_name(dest.bank_name)
                    self.fields['branch_name'].initial = dest.branch_name
                    self.fields['account_holder_name'].initial = dest.account_holder_name
                    self.fields['account_number'].initial = dest.get_account_number()
                    self.fields['routing_number'].initial = dest.routing_number
                elif dest.payment_type == PaymentType.MFS:
                    self.fields['mfs_provider'].initial = dest.mfs_provider
                    self.fields['wallet_number'].initial = dest.get_wallet_number()
                self.fields['notes'].initial = dest.notes
            else:
                pm = getattr(employee, 'payment_method', 'bank') or 'bank'
                if pm == 'bank':
                    self.fields['payment_type'].initial = PaymentType.BANK
                    self.fields['bank_name'].initial = resolve_canonical_bank_name(employee.bank_name)
                    self.fields['account_number'].initial = employee.bank_account
                    self.fields['account_holder_name'].initial = employee.get_full_name()
                elif pm in ('mfs', 'mobile'):
                    self.fields['payment_type'].initial = PaymentType.MFS
                    self.fields['wallet_number'].initial = employee.bank_account
                else:
                    self.fields['payment_type'].initial = PaymentType.CASH

    def clean(self):
        cleaned_data = super().clean()
        ptype = cleaned_data.get('payment_type')
        if ptype == PaymentType.BANK:
            b_name = cleaned_data.get('bank_name')
            acc_num = cleaned_data.get('account_number')
            holder = cleaned_data.get('account_holder_name')
            branch = cleaned_data.get('branch_name')
            if not b_name:
                self.add_error('bank_name', 'Bank selection is required for bank transfer.')
            if not acc_num:
                self.add_error('account_number', 'Account number is required for bank transfer.')
            if not holder:
                self.add_error('account_holder_name', 'Account holder name is required.')
            if not branch:
                self.add_error('branch_name', 'Branch name is required.')
        elif ptype == PaymentType.MFS:
            mfs_prov = cleaned_data.get('mfs_provider')
            wallet = cleaned_data.get('wallet_number')
            if not mfs_prov:
                self.add_error('mfs_provider', 'MFS Provider is required.')
            if not wallet:
                self.add_error('wallet_number', 'Wallet number is required.')
            else:
                try:
                    cleaned_data['wallet_number'] = PayrollPaymentDestinationService.validate_wallet_number(wallet)
                except forms.ValidationError as e:
                    if hasattr(e, 'message_dict') and 'wallet_number' in e.message_dict:
                        self.add_error('wallet_number', e.message_dict['wallet_number'][0])
                    elif hasattr(e, 'messages'):
                        self.add_error('wallet_number', e.messages[0])
                    else:
                        self.add_error('wallet_number', str(e))
        return cleaned_data
