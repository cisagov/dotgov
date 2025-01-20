"""Forms for domain management."""

import logging
from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator, MaxLengthValidator
from django.forms import formset_factory
from registrar.forms.utility.combobox import ComboboxWidget
from registrar.models import DomainRequest, FederalAgency
from phonenumber_field.widgets import RegionalPhoneNumberWidget
from registrar.models.suborganization import Suborganization
from registrar.models.utility.domain_helper import DomainHelper
from registrar.utility.errors import (
    NameserverError,
    NameserverErrorCodes as nsErrorCodes,
    DsDataError,
    DsDataErrorCodes,
    SecurityEmailError,
    SecurityEmailErrorCodes,
)

from ..models import Contact, DomainInformation, Domain, User
from .common import (
    ALGORITHM_CHOICES,
    DIGEST_TYPE_CHOICES,
)

import re


logger = logging.getLogger(__name__)


class DomainAddUserForm(forms.Form):
    """Form for adding a user to a domain."""

    email = forms.EmailField(
        label="Email",
        max_length=None,
        error_messages={
            "invalid": ("Enter an email address in the required format, like name@example.com."),
            "required": ("Enter an email address in the required format, like name@example.com."),
        },
        validators=[
            MaxLengthValidator(
                320,
                message="Response must be less than 320 characters.",
            )
        ],
    )

    def clean(self):
        """clean form data by lowercasing email"""
        cleaned_data = super().clean()

        # Lowercase the value of the 'email' field
        email_value = cleaned_data.get("email")
        if email_value:
            cleaned_data["email"] = email_value.lower()

        return cleaned_data


class DomainNameserverForm(forms.Form):
    """Form for changing nameservers."""

    domain = forms.CharField(widget=forms.HiddenInput, required=False)

    server = forms.CharField(label="Name server", strip=True)

    ip = forms.CharField(
        label="IP address (IPv4 or IPv6)",
        strip=True,
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super(DomainNameserverForm, self).__init__(*args, **kwargs)

        # add custom error messages
        self.fields["server"].error_messages.update(
            {
                "required": "At least two name servers are required.",
            }
        )

    def clean(self):
        # clean is called from clean_forms, which is called from is_valid
        # after clean_fields.  it is used to determine form level errors.
        # is_valid is typically called from view during a post
        cleaned_data = super().clean()

        self.clean_empty_strings(cleaned_data)

        server = cleaned_data.get("server", "")
        server = server.replace(" ", "").lower()
        cleaned_data["server"] = server

        ip = cleaned_data.get("ip", "")
        ip = ip.replace(" ", "")
        cleaned_data["ip"] = ip

        domain = cleaned_data.get("domain", "")

        ip_list = self.extract_ip_list(ip)

        # Capture the server_value
        server_value = self.cleaned_data.get("server")

        # Validate if the form has a server or an ip
        if (ip and ip_list) or server:
            self.validate_nameserver_ip_combo(domain, server, ip_list)

        # Re-set the server value:
        # add_error which is called on validate_nameserver_ip_combo will clean-up (delete) any invalid data.
        # We need that data because we need to know the total server entries (even if invalid) in the formset
        # clean method where we determine whether a blank first and/or second entry should throw a required error.
        self.cleaned_data["server"] = server_value

        return cleaned_data

    def clean_empty_strings(self, cleaned_data):
        ip = cleaned_data.get("ip", "")
        if ip and len(ip.strip()) == 0:
            cleaned_data["ip"] = None

    def extract_ip_list(self, ip):
        return [ip.strip() for ip in ip.split(",")] if ip else []

    def validate_nameserver_ip_combo(self, domain, server, ip_list):
        try:
            Domain.checkHostIPCombo(domain, server, ip_list)
        except NameserverError as e:
            if e.code == nsErrorCodes.GLUE_RECORD_NOT_ALLOWED:
                self.add_error(
                    "server",
                    NameserverError(
                        code=nsErrorCodes.GLUE_RECORD_NOT_ALLOWED,
                        nameserver=domain,
                        ip=ip_list,
                    ),
                )
            elif e.code == nsErrorCodes.MISSING_IP:
                self.add_error(
                    "ip",
                    NameserverError(code=nsErrorCodes.MISSING_IP, nameserver=domain, ip=ip_list),
                )
            elif e.code == nsErrorCodes.MISSING_HOST:
                self.add_error(
                    "server",
                    NameserverError(code=nsErrorCodes.MISSING_HOST, nameserver=domain, ip=ip_list),
                )
            elif e.code == nsErrorCodes.INVALID_HOST:
                self.add_error(
                    "server",
                    NameserverError(code=nsErrorCodes.INVALID_HOST, nameserver=server, ip=ip_list),
                )
            else:
                self.add_error("ip", str(e))


class DomainSuborganizationForm(forms.ModelForm):
    """Form for updating the suborganization"""

    sub_organization = forms.ModelChoiceField(
        label="Suborganization name",
        queryset=Suborganization.objects.none(),
        required=False,
        widget=ComboboxWidget,
    )

    class Meta:
        model = DomainInformation
        fields = [
            "sub_organization",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        portfolio = self.instance.portfolio if self.instance else None
        self.fields["sub_organization"].queryset = Suborganization.objects.filter(portfolio=portfolio)


class BaseNameserverFormset(forms.BaseFormSet):
    def clean(self):
        """
        Check for duplicate entries in the formset.
        """

        # Check if there are at least two valid servers
        valid_servers_count = sum(
            1 for form in self.forms if form.cleaned_data.get("server") and form.cleaned_data.get("server").strip()
        )
        if valid_servers_count >= 2:
            # If there are, remove the "At least two name servers are required" error from each form
            # This will allow for successful submissions when the first or second entries are blanked
            # but there are enough entries total
            for form in self.forms:
                if form.errors.get("server") == ["At least two name servers are required."]:
                    form.errors.pop("server")

        if any(self.errors):
            # Don't bother validating the formset unless each form is valid on its own
            return

        data = []
        duplicates = []

        for index, form in enumerate(self.forms):
            if form.cleaned_data:
                value = form.cleaned_data["server"]
                # We need to make sure not to trigger the duplicate error in case the first and second nameservers
                # are empty. If there are enough records in the formset, that error is an unecessary blocker.
                # If there aren't, the required error will block the submit.
                if value in data and not (form.cleaned_data.get("server", "").strip() == "" and index == 1):
                    form.add_error(
                        "server",
                        NameserverError(code=nsErrorCodes.DUPLICATE_HOST, nameserver=value),
                    )
                    duplicates.append(value)
                else:
                    data.append(value)


NameserverFormset = formset_factory(
    DomainNameserverForm,
    formset=BaseNameserverFormset,
    extra=1,
    max_num=13,
    validate_max=True,
)


class UserForm(forms.ModelForm):
    """Form for updating users."""

    email = forms.EmailField(max_length=None)

    class Meta:
        model = User
        fields = ["first_name", "middle_name", "last_name", "title", "email", "phone"]
        widgets = {
            "first_name": forms.TextInput,
            "middle_name": forms.TextInput,
            "last_name": forms.TextInput,
            "title": forms.TextInput,
            "email": forms.EmailInput,
            "phone": RegionalPhoneNumberWidget,
        }

    # the database fields have blank=True so ModelForm doesn't create
    # required fields by default. Use this list in __init__ to mark each
    # of these fields as required
    required = ["first_name", "last_name", "title", "email", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # take off maxlength attribute for the phone number field
        # which interferes with out input_with_errors template tag
        self.fields["phone"].widget.attrs.pop("maxlength", None)

        # Define a custom validator for the email field with a custom error message
        email_max_length_validator = MaxLengthValidator(320, message="Response must be less than 320 characters.")
        self.fields["email"].validators.append(email_max_length_validator)

        for field_name in self.required:
            self.fields[field_name].required = True

        # Set custom form label
        self.fields["middle_name"].label = "Middle name (optional)"

        # Set custom error messages
        self.fields["first_name"].error_messages = {"required": "Enter your first name / given name."}
        self.fields["last_name"].error_messages = {"required": "Enter your last name / family name."}
        self.fields["title"].error_messages = {
            "required": "Enter your title or role in your organization (e.g., Chief Information Officer)"
        }
        self.fields["email"].error_messages = {
            "required": "Enter an email address in the required format, like name@example.com."
        }
        self.fields["phone"].error_messages["required"] = "Enter your phone number."
        self.domainInfo = None

    def set_domain_info(self, domainInfo):
        """Set the domain information for the form.
        The form instance is associated with the contact itself. In order to access the associated
        domain information object, this needs to be set in the form by the view."""
        self.domainInfo = domainInfo


class ContactForm(forms.ModelForm):
    """Form for updating contacts."""

    email = forms.EmailField(max_length=None)

    class Meta:
        model = Contact
        fields = ["first_name", "middle_name", "last_name", "title", "email", "phone"]
        widgets = {
            "first_name": forms.TextInput,
            "middle_name": forms.TextInput,
            "last_name": forms.TextInput,
            "title": forms.TextInput,
            "email": forms.EmailInput,
            "phone": RegionalPhoneNumberWidget,
        }

    # the database fields have blank=True so ModelForm doesn't create
    # required fields by default. Use this list in __init__ to mark each
    # of these fields as required
    required = ["first_name", "last_name", "title", "email", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # take off maxlength attribute for the phone number field
        # which interferes with out input_with_errors template tag
        self.fields["phone"].widget.attrs.pop("maxlength", None)

        # Define a custom validator for the email field with a custom error message
        email_max_length_validator = MaxLengthValidator(320, message="Response must be less than 320 characters.")
        self.fields["email"].validators.append(email_max_length_validator)

        for field_name in self.required:
            self.fields[field_name].required = True

        # Set custom form label
        self.fields["middle_name"].label = "Middle name (optional)"

        # Set custom error messages
        self.fields["first_name"].error_messages = {"required": "Enter your first name / given name."}
        self.fields["last_name"].error_messages = {"required": "Enter your last name / family name."}
        self.fields["title"].error_messages = {
            "required": "Enter your title or role in your organization (e.g., Chief Information Officer)"
        }
        self.fields["email"].error_messages = {
            "required": "Enter an email address in the required format, like name@example.com."
        }
        self.fields["phone"].error_messages["required"] = "Enter your phone number."
        self.domainInfo = None

    def set_domain_info(self, domainInfo):
        """Set the domain information for the form.
        The form instance is associated with the contact itself. In order to access the associated
        domain information object, this needs to be set in the form by the view."""
        self.domainInfo = domainInfo


class SeniorOfficialContactForm(ContactForm):
    """Form for updating senior official contacts."""

    JOIN = "senior_official"
    full_name = forms.CharField(label="Full name", required=False)

    def __init__(self, disable_fields=False, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.id:
            self.fields["full_name"].initial = self.instance.get_formatted_name()

        # Overriding bc phone not required in this form
        self.fields["phone"] = forms.IntegerField(required=False)

        # Set custom error messages
        self.fields["first_name"].error_messages = {
            "required": "Enter the first name / given name of your senior official."
        }
        self.fields["last_name"].error_messages = {
            "required": "Enter the last name / family name of your senior official."
        }
        self.fields["title"].error_messages = {
            "required": "Enter the title or role your senior official has in your \
            organization (e.g., Chief Information Officer)."
        }
        self.fields["email"].error_messages = {
            "required": "Enter an email address in the required format, like name@example.com."
        }

        # All fields should be disabled if the domain is federal or tribal
        if disable_fields:
            DomainHelper.mass_disable_fields(fields=self.fields, disable_required=True, disable_maxlength=True)

    def clean(self):
        """Clean override to remove unused fields"""
        cleaned_data = super().clean()
        cleaned_data.pop("full_name", None)
        return cleaned_data

    def save(self, commit=True):
        """
        Override the save() method of the BaseModelForm.
        Used to perform checks on the underlying domain_information object.
        If this doesn't exist, we just save as normal.
        """

        # If the underlying Domain doesn't have a domainInfo object,
        # just let the default super handle it.
        if not self.domainInfo:
            return super().save()

        # Determine if the domain is federal or tribal
        is_federal = self.domainInfo.generic_org_type == DomainRequest.OrganizationChoices.FEDERAL
        is_tribal = self.domainInfo.generic_org_type == DomainRequest.OrganizationChoices.TRIBAL

        # Get the Contact object from the db for the Senior Official
        db_so = Contact.objects.get(id=self.instance.id)

        if (is_federal or is_tribal) and self.has_changed():
            # This action should be blocked by the UI, as the text fields are readonly.
            # If they get past this point, we forbid it this way.
            # This could be malicious, so lets reserve information for the backend only.
            raise ValueError("Senior official cannot be modified for federal or tribal domains.")
        elif db_so.has_more_than_one_join("information_senior_official"):
            # Handle the case where the domain information object is available and the SO Contact
            # has more than one joined object.
            # In this case, create a new Contact, and update the new Contact with form data.
            # Then associate with domain information object as the senior_official
            data = dict(self.cleaned_data.items())
            self.domainInfo.senior_official = Contact.objects.create(**data)
            self.domainInfo.save()
        else:
            # If all checks pass, just save normally
            super().save()


class DomainSecurityEmailForm(forms.Form):
    """Form for adding or editing a security email to a domain."""

    security_email = forms.EmailField(
        label="Security email (optional)",
        max_length=None,
        required=False,
        error_messages={
            "invalid": str(SecurityEmailError(code=SecurityEmailErrorCodes.BAD_DATA)),
        },
        validators=[
            MaxLengthValidator(
                320,
                message="Response must be less than 320 characters.",
            )
        ],
    )


class DomainOrgNameAddressForm(forms.ModelForm):
    """Form for updating the organization name and mailing address."""

    # for federal agencies we also want to know the top-level agency.
    federal_agency = forms.ModelChoiceField(
        label="Federal agency",
        required=False,
        queryset=FederalAgency.objects.all(),
        widget=ComboboxWidget,
    )
    zipcode = forms.CharField(
        label="Zip code",
        validators=[
            RegexValidator(
                "^[0-9]{5}(?:-[0-9]{4})?$|^$",
                message="Enter a 5-digit or 9-digit zip code, like 12345 or 12345-6789.",
            )
        ],
        error_messages={
            "required": "Enter a 5-digit or 9-digit zip code, like 12345 or 12345-6789.",
        },
    )

    state_territory = forms.ChoiceField(
        label="State, territory, or military post",
        required=True,
        choices=DomainInformation.StateTerritoryChoices.choices,
        error_messages={
            "required": ("Select the state, territory, or military post where your organization is located.")
        },
        widget=ComboboxWidget(),
    )

    class Meta:
        model = DomainInformation
        fields = [
            "federal_agency",
            "organization_name",
            "address_line1",
            "address_line2",
            "city",
            "state_territory",
            "zipcode",
            "urbanization",
        ]
        error_messages = {
            "federal_agency": {"required": "Select the federal agency for your organization."},
            "organization_name": {"required": "Enter the name of your organization."},
            "address_line1": {"required": "Enter the street address of your organization."},
            "city": {"required": "Enter the city where your organization is located."},
        }
        widgets = {
            "organization_name": forms.TextInput,
            "address_line1": forms.TextInput,
            "address_line2": forms.TextInput,
            "city": forms.TextInput,
            "urbanization": forms.TextInput,
        }

    # the database fields have blank=True so ModelForm doesn't create
    # required fields by default. Use this list in __init__ to mark each
    # of these fields as required
    required = ["organization_name", "address_line1", "city", "state_territory", "zipcode"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.required:
            self.fields[field_name].required = True
        self.fields["state_territory"].widget.attrs.pop("maxlength", None)
        self.fields["zipcode"].widget.attrs.pop("maxlength", None)

        self.is_federal = self.instance.generic_org_type == DomainRequest.OrganizationChoices.FEDERAL
        self.is_tribal = self.instance.generic_org_type == DomainRequest.OrganizationChoices.TRIBAL

        field_to_disable = None
        if self.is_federal:
            field_to_disable = "federal_agency"
        elif self.is_tribal:
            field_to_disable = "organization_name"

        # Disable any field that should be disabled, if applicable
        if field_to_disable is not None:
            DomainHelper.disable_field(self.fields[field_to_disable], disable_required=True)

    def save(self, commit=True):
        """Override the save() method of the BaseModelForm."""

        if self.has_changed():

            # This action should be blocked by the UI, as the text fields are readonly.
            # If they get past this point, we forbid it this way.
            # This could be malicious, so lets reserve information for the backend only.

            if self.is_federal:
                if not self._field_unchanged("federal_agency"):
                    raise ValueError("federal_agency cannot be modified when the generic_org_type is federal")

            elif self.is_tribal and not self._field_unchanged("organization_name"):
                raise ValueError("organization_name cannot be modified when the generic_org_type is tribal")

            else:  # If this error that means Non-Federal Agency is missing
                non_federal_agency_instance = FederalAgency.get_non_federal_agency()
                self.instance.federal_agency = non_federal_agency_instance

        return super().save(commit=commit)

    def _field_unchanged(self, field_name) -> bool:
        """
        Checks if a specified field has not changed between the old value
        and the new value.

        The old value is grabbed from self.initial.
        The new value is grabbed from self.cleaned_data.
        """
        old_value = self.initial.get(field_name, None)
        new_value = self.cleaned_data.get(field_name, None)

        field = self.fields[field_name]

        # Check if the field has a queryset attribute before accessing it
        if hasattr(field, "queryset") and isinstance(new_value, str):
            try:
                # Convert the string to the corresponding ID
                new_value = field.queryset.get(name=new_value).id
            except field.queryset.model.DoesNotExist:
                pass  # Handle the case where the object does not exist

        elif hasattr(new_value, "id"):
            # If new_value is a model instance, compare by ID.
            new_value = new_value.id

        return old_value == new_value


class DomainDnssecForm(forms.Form):
    """Form for enabling and disabling dnssec"""


class DomainDsdataForm(forms.Form):
    """Form for adding or editing DNSSEC DS data to a domain."""

    def validate_hexadecimal(value):
        """
        Tests that string matches all hexadecimal values.

        Raise validation error to display error in form
        if invalid characters entered
        """
        if not re.match(r"^[0-9a-fA-F]+$", value):
            raise forms.ValidationError(str(DsDataError(code=DsDataErrorCodes.INVALID_DIGEST_CHARS)))

    key_tag = forms.IntegerField(
        required=True,
        label="Key tag",
        validators=[
            MinValueValidator(0, message=str(DsDataError(code=DsDataErrorCodes.INVALID_KEYTAG_SIZE))),
            MaxValueValidator(65535, message=str(DsDataError(code=DsDataErrorCodes.INVALID_KEYTAG_SIZE))),
        ],
        error_messages={"required": ("Key tag is required.")},
    )

    algorithm = forms.TypedChoiceField(
        required=True,
        label="Algorithm",
        coerce=int,  # need to coerce into int so dsData objects can be compared
        choices=[(None, "--Select--")] + ALGORITHM_CHOICES,  # type: ignore
        error_messages={"required": ("Algorithm is required.")},
    )

    digest_type = forms.TypedChoiceField(
        required=True,
        label="Digest type",
        coerce=int,  # need to coerce into int so dsData objects can be compared
        choices=[(None, "--Select--")] + DIGEST_TYPE_CHOICES,  # type: ignore
        error_messages={"required": ("Digest type is required.")},
    )

    digest = forms.CharField(
        required=True,
        label="Digest",
        validators=[validate_hexadecimal],
        error_messages={
            "required": "Digest is required.",
        },
    )

    def clean(self):
        # clean is called from clean_forms, which is called from is_valid
        # after clean_fields.  it is used to determine form level errors.
        # is_valid is typically called from view during a post
        cleaned_data = super().clean()
        digest_type = cleaned_data.get("digest_type", 0)
        digest = cleaned_data.get("digest", "")
        # validate length of digest depending on digest_type
        if digest_type == 1 and len(digest) != 40:
            self.add_error(
                "digest",
                DsDataError(code=DsDataErrorCodes.INVALID_DIGEST_SHA1),
            )
        elif digest_type == 2 and len(digest) != 64:
            self.add_error(
                "digest",
                DsDataError(code=DsDataErrorCodes.INVALID_DIGEST_SHA256),
            )
        return cleaned_data


DomainDsdataFormset = formset_factory(
    DomainDsdataForm,
    extra=0,
    can_delete=True,
)


class DomainRenewalForm(forms.Form):
    """Form making sure domain renewal ack is checked"""

    is_policy_acknowledged = forms.BooleanField(
        required=True,
        label="I have read and agree to the requirements for operating a .gov domain.",
        error_messages={
            "required": "Check the box if you read and agree to the requirements for operating a .gov domain."
        },
    )
