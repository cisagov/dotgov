"""Forms for portfolio."""

import logging
from django import forms
from django.core.validators import RegexValidator
from django.core.validators import MaxLengthValidator
from django.utils.safestring import mark_safe

from registrar.forms.utility.combobox import ComboboxWidget
from registrar.models import (
    PortfolioInvitation,
    UserPortfolioPermission,
    DomainInformation,
    Portfolio,
    SeniorOfficial,
)
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices

logger = logging.getLogger(__name__)


class PortfolioOrgAddressForm(forms.ModelForm):
    """Form for updating the portfolio org mailing address."""

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
        widget=ComboboxWidget,
    )

    class Meta:
        model = Portfolio
        fields = [
            "address_line1",
            "address_line2",
            "city",
            "state_territory",
            "zipcode",
            # "urbanization",
        ]
        error_messages = {
            "address_line1": {"required": "Enter the street address of your organization."},
            "city": {"required": "Enter the city where your organization is located."},
            "zipcode": {"required": "Enter a 5-digit or 9-digit zip code, like 12345 or 12345-6789."},
        }
        widgets = {
            "address_line1": forms.TextInput,
            "address_line2": forms.TextInput,
            "city": forms.TextInput,
            # "urbanization": forms.TextInput,
        }

    # the database fields have blank=True so ModelForm doesn't create
    # required fields by default. Use this list in __init__ to mark each
    # of these fields as required
    required = ["address_line1", "city", "state_territory", "zipcode"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.required:
            self.fields[field_name].required = True
        self.fields["state_territory"].widget.attrs.pop("maxlength", None)
        self.fields["zipcode"].widget.attrs.pop("maxlength", None)


class PortfolioSeniorOfficialForm(forms.ModelForm):
    """
    Form for updating the portfolio senior official.
    This form is readonly for now.
    """

    JOIN = "senior_official"
    full_name = forms.CharField(label="Full name", required=False)

    class Meta:
        model = SeniorOfficial
        fields = [
            "title",
            "email",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.id:
            self.fields["full_name"].initial = self.instance.get_formatted_name()

    def clean(self):
        """Clean override to remove unused fields"""
        cleaned_data = super().clean()
        cleaned_data.pop("full_name", None)
        return cleaned_data


class BasePortfolioMemberForm(forms.ModelForm):
    """Base form for the PortfolioMemberForm and PortfolioInvitedMemberForm"""

    # The label for each of these has a red "required" star. We can just embed that here for simplicity.
    required_star = '<abbr class="usa-hint usa-hint--required" title="required">*</abbr>'
    role = forms.ChoiceField(
        choices=[
            # Uses .value because the choice has a different label (on /admin)
            (UserPortfolioRoleChoices.ORGANIZATION_ADMIN.value, "Admin access"),
            (UserPortfolioRoleChoices.ORGANIZATION_MEMBER.value, "Basic access"),
        ],
        widget=forms.RadioSelect,
        required=True,
        error_messages={
            "required": "Member access level is required",
        },
    )

    domain_request_permission_admin = forms.ChoiceField(
        label=mark_safe(f"Select permission {required_star}"),  # nosec
        choices=[
            (UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value, "View all requests"),
            (UserPortfolioPermissionChoices.EDIT_REQUESTS.value, "View all requests plus create requests"),
        ],
        widget=forms.RadioSelect,
        required=False,
        error_messages={
            "required": "Admin domain request permission is required",
        },
    )

    member_permission_admin = forms.ChoiceField(
        label=mark_safe(f"Select permission {required_star}"),  # nosec
        choices=[
            (UserPortfolioPermissionChoices.VIEW_MEMBERS.value, "View all members"),
            (UserPortfolioPermissionChoices.EDIT_MEMBERS.value, "View all members plus manage members"),
        ],
        widget=forms.RadioSelect,
        required=False,
        error_messages={
            "required": "Admin member permission is required",
        },
    )

    domain_request_permission_member = forms.ChoiceField(
        label=mark_safe(f"Select permission {required_star}"),  # nosec
        choices=[
            (UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS.value, "View all requests"),
            (UserPortfolioPermissionChoices.EDIT_REQUESTS.value, "View all requests plus create requests"),
            ("no_access", "No access"),
        ],
        widget=forms.RadioSelect,
        required=False,
        error_messages={
            "required": "Basic member permission is required",
        },
    )

    # Tracks what form elements are required for a given role choice.
    # All of the fields included here have "required=False" by default as they are conditionally required.
    # see def clean() for more details.
    ROLE_REQUIRED_FIELDS = {
        UserPortfolioRoleChoices.ORGANIZATION_ADMIN: [
            "domain_request_permission_admin",
            "member_permission_admin",
        ],
        UserPortfolioRoleChoices.ORGANIZATION_MEMBER: [
            "domain_request_permission_member",
        ],
    }

    class Meta:
        model = None
        fields = ["roles", "additional_permissions"]

    def __init__(self, *args, **kwargs):
        """
        Override the form's initialization.

        Map existing model values to custom form fields.
        Update field descriptions.
        """
        super().__init__(*args, **kwargs)
        # Adds a <p> description beneath each role option
        self.fields["role"].descriptions = {
            "organization_admin": UserPortfolioRoleChoices.get_role_description(
                UserPortfolioRoleChoices.ORGANIZATION_ADMIN
            ),
            "organization_member": UserPortfolioRoleChoices.get_role_description(
                UserPortfolioRoleChoices.ORGANIZATION_MEMBER
            ),
        }
        # Map model instance values to custom form fields
        if self.instance:
            self.map_instance_to_initial()

    def clean(self):
        """Validates form data based on selected role and its required fields.
        Updates roles and additional_permissions in cleaned_data so they can be properly
        mapped to the model.
        """
        cleaned_data = super().clean()
        role = cleaned_data.get("role")

        # Get required fields for the selected role. Then validate all required fields for the role.
        required_fields = self.ROLE_REQUIRED_FIELDS.get(role, [])
        for field_name in required_fields:
            # Helpful error for if this breaks
            if field_name not in self.fields:
                raise ValueError(f"ROLE_REQUIRED_FIELDS referenced a non-existent field: {field_name}.")

            if not cleaned_data.get(field_name):
                self.add_error(field_name, self.fields.get(field_name).error_messages.get("required"))

        # Edgecase: Member uses a special form value for None called "no_access".
        if cleaned_data.get("domain_request_permission_member") == "no_access":
            cleaned_data["domain_request_permission_member"] = None

        # Handle roles
        cleaned_data["roles"] = [role]

        # Handle additional_permissions
        valid_fields = self.ROLE_REQUIRED_FIELDS.get(role, [])
        additional_permissions = {cleaned_data.get(field) for field in valid_fields if cleaned_data.get(field)}

        # Handle EDIT permissions (should be accompanied with a view permission)
        if UserPortfolioPermissionChoices.EDIT_MEMBERS in additional_permissions:
            additional_permissions.add(UserPortfolioPermissionChoices.VIEW_MEMBERS)

        if UserPortfolioPermissionChoices.EDIT_REQUESTS in additional_permissions:
            additional_permissions.add(UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS)

        # Only set unique permissions not already defined in the base role
        role_permissions = UserPortfolioPermission.get_portfolio_permissions(cleaned_data["roles"], [], get_list=False)
        cleaned_data["additional_permissions"] = list(additional_permissions - role_permissions)

        return cleaned_data

    def map_instance_to_initial(self):
        """
        Maps self.instance to self.initial, handling roles and permissions.
        Updates self.initial dictionary with appropriate permission levels based on user role:
        {
            "role": "organization_admin" or "organization_member",
            "member_permission_admin": permission level if admin,
            "domain_request_permission_admin": permission level if admin,
            "domain_request_permission_member": permission level if member
        }
        """
        if self.initial is None:
            self.initial = {}
        # Function variables
        perms = UserPortfolioPermission.get_portfolio_permissions(
            self.instance.roles, self.instance.additional_permissions, get_list=False
        )
        # Get the available options for roles, domains, and member.
        roles = [
            UserPortfolioRoleChoices.ORGANIZATION_ADMIN,
            UserPortfolioRoleChoices.ORGANIZATION_MEMBER,
        ]
        domain_perms = [
            UserPortfolioPermissionChoices.EDIT_REQUESTS,
            UserPortfolioPermissionChoices.VIEW_ALL_REQUESTS,
        ]
        member_perms = [
            UserPortfolioPermissionChoices.EDIT_MEMBERS,
            UserPortfolioPermissionChoices.VIEW_MEMBERS,
        ]

        # Build form data based on role (which options are available).
        # Get which one should be "selected" by assuming that EDIT takes precedence over view,
        # and ADMIN takes precedence over MEMBER.
        roles = self.instance.roles or []
        selected_role = next((role for role in roles if role in roles), None)
        self.initial["role"] = selected_role
        is_admin = selected_role == UserPortfolioRoleChoices.ORGANIZATION_ADMIN
        if is_admin:
            selected_domain_permission = next((perm for perm in domain_perms if perm in perms), None)
            selected_member_permission = next((perm for perm in member_perms if perm in perms), None)
            self.initial["domain_request_permission_admin"] = selected_domain_permission
            self.initial["member_permission_admin"] = selected_member_permission
        else:
            # Edgecase: Member uses a special form value for None called "no_access". This ensures a form selection.
            selected_domain_permission = next((perm for perm in domain_perms if perm in perms), "no_access")
            self.initial["domain_request_permission_member"] = selected_domain_permission


class PortfolioMemberForm(BasePortfolioMemberForm):
    """
    Form for updating a portfolio member.
    """

    class Meta:
        model = UserPortfolioPermission
        fields = ["roles", "additional_permissions"]


class PortfolioInvitedMemberForm(BasePortfolioMemberForm):
    """
    Form for updating a portfolio invited member.
    """

    class Meta:
        model = PortfolioInvitation
        fields = ["roles", "additional_permissions"]


class PortfolioNewMemberForm(BasePortfolioMemberForm):
    """
    Form for adding a portfolio invited member.
    """

    email = forms.EmailField(
        label="Enter the email of the member you'd like to invite",
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
        required=True,
    )

    class Meta:
        model = PortfolioInvitation
        fields = ["portfolio", "email", "roles", "additional_permissions"]
