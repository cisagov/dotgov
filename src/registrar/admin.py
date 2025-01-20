from datetime import date
import logging
import copy
from typing import Optional
from django import forms
from django.db.models import (
    Case,
    CharField,
    F,
    Q,
    Value,
    When,
)
from django.db.models.functions import Concat, Coalesce
from django.http import HttpResponseRedirect
from registrar.models.federal_agency import FederalAgency
from registrar.models.portfolio_invitation import PortfolioInvitation
from registrar.utility.admin_helpers import (
    AutocompleteSelectWithPlaceholder,
    get_action_needed_reason_default_email,
    get_rejection_reason_default_email,
    get_field_links_as_list,
)
from django.conf import settings
from django.contrib.messages import get_messages
from django.contrib.admin.helpers import AdminForm
from django.shortcuts import redirect
from django_fsm import get_available_FIELD_transitions, FSMField
from registrar.models import DomainInformation, Portfolio, UserPortfolioPermission, DomainInvitation
from registrar.models.utility.portfolio_helper import UserPortfolioPermissionChoices, UserPortfolioRoleChoices
from registrar.utility.email_invitations import send_domain_invitation_email, send_portfolio_invitation_email
from registrar.views.utility.invitation_helper import (
    get_org_membership,
    get_requested_user,
    handle_invitation_exceptions,
)
from waffle.decorators import flag_is_active
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from epplibwrapper.errors import ErrorCode, RegistryError
from registrar.models.user_domain_role import UserDomainRole
from waffle.admin import FlagAdmin
from waffle.models import Sample, Switch
from registrar.models import Contact, Domain, DomainRequest, DraftDomain, User, Website, SeniorOfficial
from registrar.utility.constants import BranchChoices
from registrar.utility.errors import FSMDomainRequestError, FSMErrorCodes
from registrar.utility.waffle import flag_is_active_for_user
from registrar.views.utility.mixins import OrderableFieldsMixin
from django.contrib.admin.views.main import ORDER_VAR
from registrar.widgets import NoAutocompleteFilteredSelectMultiple
from . import models
from auditlog.models import LogEntry  # type: ignore
from auditlog.admin import LogEntryAdmin  # type: ignore
from django_fsm import TransitionNotAllowed  # type: ignore
from django.utils.safestring import mark_safe
from django.utils.html import escape
from django.contrib.auth.forms import UserChangeForm, UsernameField
from django.contrib.admin.views.main import IGNORED_PARAMS
from django_admin_multiple_choice_list_filter.list_filters import MultipleChoiceListFilter
from import_export import resources
from import_export.admin import ImportExportModelAdmin
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class FsmModelResource(resources.ModelResource):
    """ModelResource is extended to support importing of tables which
    have FSMFields.  ModelResource is extended with the following changes
    to existing behavior:
    When new objects are to be imported, FSMFields are initialized before
    the object is initialized.  This is because FSMFields do not allow
    direct modification.
    When objects, which are to be imported, are updated, the FSMFields
    are skipped."""

    def init_instance(self, row=None):
        """Overrides the init_instance method of ModelResource.  Returns
        an instance of the model, with the FSMFields already initialized
        from data in the row."""

        # Get fields which are fsm fields
        fsm_fields = {}

        for f in self._meta.model._meta.fields:
            if isinstance(f, FSMField):
                if row and f.name in row:
                    fsm_fields[f.name] = row[f.name]

        # Initialize model instance with fsm_fields
        return self._meta.model(**fsm_fields)

    def import_field(self, field, obj, data, is_m2m=False, **kwargs):
        """Overrides the import_field method of ModelResource.  If the
        field being imported is an FSMField, it is not imported."""

        is_fsm = False

        # check each field in the object
        for f in obj._meta.fields:
            # if the field is an instance of FSMField
            if field.attribute == f.name and isinstance(f, FSMField):
                is_fsm = True
        if not is_fsm:
            super().import_field(field, obj, data, is_m2m, **kwargs)


class UserResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.User


class FilteredSelectMultipleArrayWidget(FilteredSelectMultiple):
    """Custom widget to allow for editing an ArrayField in a widget similar to filter_horizontal widget"""

    def __init__(self, verbose_name, is_stacked=False, choices=(), **kwargs):
        super().__init__(verbose_name, is_stacked, **kwargs)
        self.choices = choices

    def value_from_datadict(self, data, files, name):
        values = super().value_from_datadict(data, files, name)
        return values or []

    def get_context(self, name, value, attrs):
        if value is None:
            value = []
        elif isinstance(value, str):
            value = value.split(",")
        # alter self.choices to be a list of selected and unselected choices, based on value;
        # order such that selected choices come before unselected choices
        self.choices = [(choice, label) for choice, label in self.choices if choice in value] + [
            (choice, label) for choice, label in self.choices if choice not in value
        ]
        context = super().get_context(name, value, attrs)
        return context


class MyUserAdminForm(UserChangeForm):
    """This form utilizes the custom widget for its class's ManyToMany UIs.

    It inherits from UserChangeForm which has special handling for the password and username fields."""

    class Meta:
        model = models.User
        fields = "__all__"
        field_classes = {"username": UsernameField}
        widgets = {
            "groups": NoAutocompleteFilteredSelectMultiple("groups", False),
            "user_permissions": NoAutocompleteFilteredSelectMultiple("user_permissions", False),
        }

    def __init__(self, *args, **kwargs):
        """Custom init to modify the user form"""
        super(MyUserAdminForm, self).__init__(*args, **kwargs)
        self._override_base_help_texts()

    def _override_base_help_texts(self):
        """
        Used to override pre-existing help texts in AbstractUser.
        This is done to avoid modifying the base AbstractUser class.
        """
        is_superuser = self.fields.get("is_superuser")
        is_staff = self.fields.get("is_staff")
        password = self.fields.get("password")

        if is_superuser is not None:
            is_superuser.help_text = "For development purposes only; provides superuser access on the database level."

        if is_staff is not None:
            is_staff.help_text = "Designates whether the user can log in to this admin site."

        if password is not None:
            # Link is copied from the base implementation of UserChangeForm.
            link = f"../../{self.instance.pk}/password/"
            password.help_text = (
                "Raw passwords are not stored, so they will not display here. "
                f'You can change the password using <a href="{link}">this form</a>.'
            )


class UserPortfolioPermissionsForm(forms.ModelForm):
    class Meta:
        model = models.UserPortfolioPermission
        fields = "__all__"
        widgets = {
            "roles": FilteredSelectMultipleArrayWidget(
                "roles", is_stacked=False, choices=UserPortfolioRoleChoices.choices
            ),
            "additional_permissions": FilteredSelectMultipleArrayWidget(
                "additional_permissions",
                is_stacked=False,
                choices=UserPortfolioPermissionChoices.choices,
            ),
        }


class PortfolioInvitationAdminForm(UserChangeForm):
    """This form utilizes the custom widget for its class's ManyToMany UIs."""

    class Meta:
        model = models.PortfolioInvitation
        fields = "__all__"
        widgets = {
            "roles": FilteredSelectMultipleArrayWidget(
                "roles", is_stacked=False, choices=UserPortfolioRoleChoices.choices
            ),
            "additional_permissions": FilteredSelectMultipleArrayWidget(
                "additional_permissions",
                is_stacked=False,
                choices=UserPortfolioPermissionChoices.choices,
            ),
        }


class DomainInformationAdminForm(forms.ModelForm):
    """This form utilizes the custom widget for its class's ManyToMany UIs."""

    class Meta:
        model = models.DomainInformation
        fields = "__all__"
        widgets = {
            "other_contacts": NoAutocompleteFilteredSelectMultiple("other_contacts", False),
            "portfolio": AutocompleteSelectWithPlaceholder(
                DomainInformation._meta.get_field("portfolio"), admin.site, attrs={"data-placeholder": "---------"}
            ),
            "sub_organization": AutocompleteSelectWithPlaceholder(
                DomainInformation._meta.get_field("sub_organization"),
                admin.site,
                attrs={"data-placeholder": "---------", "ajax-url": "get-suborganization-list-json"},
            ),
        }


class DomainInformationInlineForm(forms.ModelForm):
    """This form utilizes the custom widget for its class's ManyToMany UIs."""

    class Meta:
        model = models.DomainInformation
        fields = "__all__"
        widgets = {
            "other_contacts": NoAutocompleteFilteredSelectMultiple("other_contacts", False),
            "portfolio": AutocompleteSelectWithPlaceholder(
                DomainInformation._meta.get_field("portfolio"), admin.site, attrs={"data-placeholder": "---------"}
            ),
            "sub_organization": AutocompleteSelectWithPlaceholder(
                DomainInformation._meta.get_field("sub_organization"),
                admin.site,
                attrs={"data-placeholder": "---------", "ajax-url": "get-suborganization-list-json"},
            ),
        }


class DomainRequestAdminForm(forms.ModelForm):
    """Custom form to limit transitions to available transitions.
    This form utilizes the custom widget for its class's ManyToMany UIs."""

    class Meta:
        model = models.DomainRequest
        fields = "__all__"
        widgets = {
            "current_websites": NoAutocompleteFilteredSelectMultiple("current_websites", False),
            "alternative_domains": NoAutocompleteFilteredSelectMultiple("alternative_domains", False),
            "other_contacts": NoAutocompleteFilteredSelectMultiple("other_contacts", False),
            "portfolio": AutocompleteSelectWithPlaceholder(
                DomainRequest._meta.get_field("portfolio"), admin.site, attrs={"data-placeholder": "---------"}
            ),
            "sub_organization": AutocompleteSelectWithPlaceholder(
                DomainRequest._meta.get_field("sub_organization"),
                admin.site,
                attrs={"data-placeholder": "---------", "ajax-url": "get-suborganization-list-json"},
            ),
        }
        labels = {
            "action_needed_reason_email": "Email",
            "rejection_reason_email": "Email",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        domain_request = kwargs.get("instance")
        if domain_request and domain_request.pk:
            current_state = domain_request.status

            # first option in status transitions is current state
            available_transitions = [(current_state, domain_request.get_status_display())]

            if domain_request.investigator is not None:
                transitions = get_available_FIELD_transitions(
                    domain_request, models.DomainRequest._meta.get_field("status")
                )
            else:
                transitions = self.get_custom_field_transitions(
                    domain_request, models.DomainRequest._meta.get_field("status")
                )

            for transition in transitions:
                available_transitions.append((transition.target, transition.target.label))

            # only set the available transitions if the user is not restricted
            # from editing the domain request; otherwise, the form will be
            # readonly and the status field will not have a widget
            if not domain_request.creator.is_restricted():
                self.fields["status"].widget.choices = available_transitions

    def get_custom_field_transitions(self, instance, field):
        """Custom implementation of get_available_FIELD_transitions
        in the FSM. Allows us to still display fields filtered out by a condition."""
        curr_state = field.get_state(instance)
        transitions = field.transitions[instance.__class__]

        for name, transition in transitions.items():
            meta = transition._django_fsm
            if meta.has_transition(curr_state):
                yield meta.get_transition(curr_state)

    def clean(self):
        """
        Override of the default clean on the form.
        This is so we can inject custom form-level error messages.
        """
        # clean is called from clean_forms, which is called from is_valid
        # after clean_fields.  it is used to determine form level errors.
        # is_valid is typically called from view during a post
        cleaned_data = super().clean()
        status = cleaned_data.get("status")
        investigator = cleaned_data.get("investigator")
        rejection_reason = cleaned_data.get("rejection_reason")
        action_needed_reason = cleaned_data.get("action_needed_reason")

        # Get the old status
        initial_status = self.initial.get("status", None)

        # We only care about investigator when in these statuses
        checked_statuses = [
            DomainRequest.DomainRequestStatus.APPROVED,
            DomainRequest.DomainRequestStatus.IN_REVIEW,
            DomainRequest.DomainRequestStatus.ACTION_NEEDED,
            DomainRequest.DomainRequestStatus.REJECTED,
            DomainRequest.DomainRequestStatus.INELIGIBLE,
        ]

        # If a status change occured, check for validity
        if status != initial_status and status in checked_statuses:
            # Checks the "investigators" field for validity.
            # That field must obey certain conditions when an domain request is approved.
            # Will call "add_error" if any issues are found.
            self._check_for_valid_investigator(investigator)

        # If the status is rejected, a rejection reason must exist
        if status == DomainRequest.DomainRequestStatus.REJECTED:
            self._check_for_valid_rejection_reason(rejection_reason)
        elif status == DomainRequest.DomainRequestStatus.ACTION_NEEDED:
            self._check_for_valid_action_needed_reason(action_needed_reason)

        return cleaned_data

    def _check_for_valid_rejection_reason(self, rejection_reason) -> bool:
        """
        Checks if the rejection_reason field is not none.
        Adds form errors on failure.
        """
        is_valid = False

        # Check if a rejection reason exists. Rejection is not possible without one.
        error_message = None
        if rejection_reason is None or rejection_reason == "":
            # Lets grab the error message from a common location
            error_message = FSMDomainRequestError.get_error_message(FSMErrorCodes.NO_REJECTION_REASON)
        else:
            is_valid = True

        if error_message is not None:
            self.add_error("rejection_reason", error_message)

        return is_valid

    def _check_for_valid_action_needed_reason(self, action_needed_reason) -> bool:
        """
        Checks if the action_needed_reason field is not none.
        Adds form errors on failure.
        """
        is_valid = action_needed_reason is not None and action_needed_reason != ""
        if not is_valid:
            error_message = FSMDomainRequestError.get_error_message(FSMErrorCodes.NO_ACTION_NEEDED_REASON)
            self.add_error("action_needed_reason", error_message)

        return is_valid

    def _check_for_valid_investigator(self, investigator) -> bool:
        """
        Checks if the investigator field is not none, and is staff.
        Adds form errors on failure.
        """

        is_valid = False

        # Check if an investigator is assigned. No approval is possible without one.
        error_message = None
        if investigator is None:
            # Lets grab the error message from a common location
            error_message = FSMDomainRequestError.get_error_message(FSMErrorCodes.NO_INVESTIGATOR)
        elif not investigator.is_staff:
            error_message = FSMDomainRequestError.get_error_message(FSMErrorCodes.INVESTIGATOR_NOT_STAFF)
        else:
            is_valid = True

        if error_message is not None:
            self.add_error("investigator", error_message)

        return is_valid


# Based off of this excellent example: https://djangosnippets.org/snippets/10471/
class MultiFieldSortableChangeList(admin.views.main.ChangeList):
    """
    This class overrides the behavior of column sorting in django admin tables in order
    to allow for multi field sorting on admin_order_field.  It also overrides behavior
    of getting the filter params to allow portfolio filters to be executed without
    displaying on the right side of the ChangeList view.


    Usage:

    class MyCustomAdmin(admin.ModelAdmin):

        ...

        def get_changelist(self, request, **kwargs):
            return MultiFieldSortableChangeList

        ...

    """

    def get_ordering(self, request, queryset):
        """
        Returns the list of ordering fields for the change list.

        Mostly identical to the base implementation, except that now it can return
        a list of order_field objects rather than just one.
        """
        params = self.params
        ordering = list(self.model_admin.get_ordering(request) or self._get_default_ordering())

        if ORDER_VAR in params:
            # Clear ordering and used params
            ordering = []

            order_params = params[ORDER_VAR].split(".")
            for p in order_params:
                try:
                    none, pfx, idx = p.rpartition("-")
                    field_name = self.list_display[int(idx)]

                    order_fields = self.get_ordering_field(field_name)

                    if isinstance(order_fields, list):
                        for order_field in order_fields:
                            if order_field:
                                ordering.append(pfx + order_field)
                    else:
                        ordering.append(pfx + order_fields)

                except (IndexError, ValueError):
                    continue  # Invalid ordering specified, skip it.

        # Add the given query's ordering fields, if any.
        ordering.extend(queryset.query.order_by)

        # Ensure that the primary key is systematically present in the list of
        # ordering fields so we can guarantee a deterministic order across all
        # database backends.
        pk_name = self.lookup_opts.pk.name
        if not (set(ordering) & set(["pk", "-pk", pk_name, "-" + pk_name])):
            # The two sets do not intersect, meaning the pk isn't present. So
            # we add it.
            ordering.append("-pk")

        return ordering

    def get_filters_params(self, params=None):
        """
        Add portfolio to ignored params to allow the portfolio filter while not
        listing it as a filter option on the right side of Change List on the
        portfolio list.
        """
        params = params or self.params
        lookup_params = params.copy()  # a dictionary of the query string
        # Remove all the parameters that are globally and systematically
        # ignored.
        # Remove portfolio so that it does not error as an invalid
        # filter parameter.
        ignored_params = list(IGNORED_PARAMS) + ["portfolio"]
        for ignored in ignored_params:
            if ignored in lookup_params:
                del lookup_params[ignored]
        return lookup_params


class CustomLogEntryAdmin(LogEntryAdmin):
    """Overwrite the generated LogEntry admin class"""

    list_display = [
        "created",
        "resource",
        "action",
        "msg_short",
        "user_url",
    ]

    # We name the custom prop 'resource' because linter
    # is not allowing a short_description attr on it
    # This gets around the linter limitation, for now.
    def resource(self, obj):
        # Return the field value without a link
        return f"{obj.content_type} - {obj.object_repr}"

    # We name the custom prop 'created_at' because linter
    # is not allowing a short_description attr on it
    # This gets around the linter limitation, for now.
    @admin.display(description=_("Created at"))
    def created(self, obj):
        return obj.timestamp

    search_help_text = "Search by resource, changes, or user."

    change_form_template = "admin/change_form_no_submit.html"
    add_form_template = "admin/change_form_no_submit.html"

    # Select log entry to change ->  Log entries
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Log entries"
        return super().changelist_view(request, extra_context=extra_context)

    # #786: Skipping on updating audit log tab titles for now
    # def change_view(self, request, object_id, form_url="", extra_context=None):
    #     if extra_context is None:
    #         extra_context = {}

    #     log_entry = self.get_object(request, object_id)

    #     if log_entry:
    #         # Reset title to empty string
    #         extra_context["subtitle"] = ""
    #         extra_context["tabtitle"] = ""

    #         object_repr = log_entry.object_repr  # Hold name of the object
    #         changes = log_entry.changes

    #         # Check if this is a log entry for an addition and related to the contact model
    #         # Created [name] -> Created [name] contact | Change log entry
    #         if (
    #             all(new_value != "None" for field, (old_value, new_value) in changes.items())
    #             and log_entry.content_type.model == "contact"
    #         ):
    #             extra_context["subtitle"] = f"Created {object_repr} contact"
    #             extra_context["tabtitle"] = "Change log entry"

    #     return super().change_view(request, object_id, form_url, extra_context=extra_context)


# TODO #2571 - this should be refactored. This is shared among every class that inherits this,
# and it breaks the senior_official field because it exists both as model "Contact" and "SeniorOfficial".
class AdminSortFields:
    _name_sort = ["first_name", "last_name", "email"]

    # Define a mapping of field names to model querysets and sort expressions.
    # A dictionary is used for specificity, but the downside is some degree of repetition.
    # To eliminate this, this list can be generated dynamically but the readability of that
    # is impacted.
    sort_mapping = {
        # == Contact == #
        "other_contacts": (Contact, _name_sort),
        # == Senior Official == #
        "senior_official": (SeniorOfficial, _name_sort),
        # == User == #
        "creator": (User, _name_sort),
        "user": (User, _name_sort),
        "investigator": (User, _name_sort),
        # == Website == #
        "current_websites": (Website, "website"),
        "alternative_domains": (Website, "website"),
        # == DraftDomain == #
        "requested_domain": (DraftDomain, "name"),
        # == DomainRequest == #
        "domain_request": (DomainRequest, "requested_domain__name"),
        # == Domain == #
        "domain": (Domain, "name"),
        "approved_domain": (Domain, "name"),
    }

    @classmethod
    def get_queryset(cls, db_field):
        """This is a helper function for formfield_for_manytomany and formfield_for_foreignkey"""
        queryset_info = cls.sort_mapping.get(db_field.name, None)
        if queryset_info is None:
            return None

        # Grab the model we want to order, and grab how we want to order it
        model, order_by = queryset_info
        match db_field.name:
            case "investigator":
                # We should only return users who are staff.
                return model.objects.filter(is_staff=True).order_by(*order_by)
            case _:
                if isinstance(order_by, list) or isinstance(order_by, tuple):
                    return model.objects.order_by(*order_by)
                else:
                    return model.objects.order_by(order_by)


class AuditedAdmin(admin.ModelAdmin):
    """Custom admin to make auditing easier."""

    def history_view(self, request, object_id, extra_context=None):
        """On clicking 'History', take admin to the auditlog view for an object."""
        return HttpResponseRedirect(
            "{url}?resource_type={content_type}&object_id={object_id}".format(
                url=reverse("admin:auditlog_logentry_changelist", args=()),
                content_type=ContentType.objects.get_for_model(self.model).pk,
                object_id=object_id,
            )
        )

    def formfield_for_manytomany(self, db_field, request, use_admin_sort_fields=True, **kwargs):
        """customize the behavior of formfields with manytomany relationships.  the customized
        behavior includes sorting of objects in lists as well as customizing helper text"""

        # Define a queryset. Note that in the super of this,
        # a new queryset will only be generated if one does not exist.
        # Thus, the order in which we define queryset matters.

        queryset = AdminSortFields.get_queryset(db_field)
        if queryset and use_admin_sort_fields:
            kwargs["queryset"] = queryset

        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        # customize the help text for all formfields for manytomany
        formfield.help_text = (
            formfield.help_text
            + " If more than one value is selected, the change/delete/view actions will be disabled."
        )
        return formfield

    def formfield_for_foreignkey(self, db_field, request, use_admin_sort_fields=True, **kwargs):
        """Customize the behavior of formfields with foreign key relationships. This will customize
        the behavior of selects. Customized behavior includes sorting of objects in list."""

        # Define a queryset. Note that in the super of this,
        # a new queryset will only be generated if one does not exist.
        # Thus, the order in which we define queryset matters.
        queryset = AdminSortFields.get_queryset(db_field)
        if queryset and use_admin_sort_fields:
            kwargs["queryset"] = queryset

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ListHeaderAdmin(AuditedAdmin, OrderableFieldsMixin):
    """Custom admin to add a descriptive subheader to list views
    and custom table sort behaviour"""

    def get_changelist(self, request, **kwargs):
        """Returns a custom ChangeList class, as opposed to the default.
        This is so we can override the behaviour of the `admin_order_field` field.
        By default, django does not support ordering by multiple fields for this
        particular field (i.e. self.admin_order_field=["first_name", "last_name"] is invalid).

        Reference: https://code.djangoproject.com/ticket/31975
        """
        return MultiFieldSortableChangeList

    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        # Get the filtered values
        filters = self.get_filters(request)
        # Pass the filtered values to the template context
        extra_context["filters"] = filters
        extra_context["search_query"] = request.GET.get("q", "")  # Assuming the search query parameter is 'q'
        return super().changelist_view(request, extra_context=extra_context)

    def get_filters(self, request):
        """Retrieve the current set of parameters being used to filter the table
        Returns:
            dictionary objects in the format {parameter_name: string,
            parameter_value: string}
        TODO: convert investigator id to investigator username
        """
        filters = []
        # Retrieve the filter parameters
        for param in request.GET.keys():
            # Exclude the default search parameter 'q'
            if param != "q" and param != "o":
                parameter_name = param.replace("__exact", "").replace("_type", "").replace("__id", " id")

                if parameter_name == "investigator id":
                    # Retrieves the corresponding contact from Users
                    id_value = request.GET.get(param)
                    try:
                        contact = models.User.objects.get(id=id_value)
                        investigator_name = contact.first_name + " " + contact.last_name

                        filters.append(
                            {
                                "parameter_name": "investigator",
                                "parameter_value": investigator_name,
                            }
                        )
                    except models.User.DoesNotExist:
                        pass
                elif parameter_name == "portfolio":
                    # Retrieves the corresponding portfolio from Portfolio
                    id_value = request.GET.get(param)
                    try:
                        portfolio = models.Portfolio.objects.get(id=id_value)
                        filters.append(
                            {
                                "parameter_name": "portfolio",
                                "parameter_value": portfolio.organization_name,
                            }
                        )
                    except models.Portfolio.DoesNotExist:
                        pass
                else:
                    # For other parameter names, append a dictionary with the original
                    # parameter_name and the corresponding parameter_value
                    filters.append(
                        {
                            "parameter_name": parameter_name,
                            "parameter_value": request.GET.get(param),
                        }
                    )
        return filters


class MyUserAdmin(BaseUserAdmin, ImportExportModelAdmin):
    """Custom user admin class to use our inlines."""

    resource_classes = [UserResource]

    form = MyUserAdminForm
    change_form_template = "django/admin/user_change_form.html"

    class Meta:
        """Contains meta information about this class"""

        model = models.User
        fields = "__all__"

    _meta = Meta()

    list_display = (
        "username",
        "overridden_email_field",
        "first_name",
        "last_name",
        # Group is a custom property defined within this file,
        # rather than in a model like the other properties
        "group",
        "status",
    )

    # Renames inherited AbstractUser label 'email_address to 'email'
    def formfield_for_dbfield(self, dbfield, **kwargs):
        field = super().formfield_for_dbfield(dbfield, **kwargs)
        if dbfield.name == "email":
            field.label = "Email"
        return field

    # Renames inherited AbstractUser column name 'email_address to 'email'
    @admin.display(description=_("Email"))
    def overridden_email_field(self, obj):
        return obj.email

    fieldsets = (
        (
            None,
            {"fields": ("username", "password", "status", "verification_type")},
        ),
        ("User profile", {"fields": ("first_name", "middle_name", "last_name", "title", "email", "phone")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
        ("Associated portfolios", {"fields": ("portfolios",)}),
    )

    readonly_fields = ("verification_type", "portfolios")

    analyst_fieldsets = (
        (
            None,
            {
                "fields": (
                    "status",
                    "verification_type",
                )
            },
        ),
        ("User profile", {"fields": ("first_name", "middle_name", "last_name", "title", "email", "phone")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "groups",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
        ("Associated portfolios", {"fields": ("portfolios",)}),
    )

    # TODO: delete after we merge organization feature
    analyst_fieldsets_no_portfolio = (
        (
            None,
            {
                "fields": (
                    "status",
                    "verification_type",
                )
            },
        ),
        ("User profile", {"fields": ("first_name", "middle_name", "last_name", "title", "email", "phone")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "groups",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    analyst_list_display = [
        "email",
        "first_name",
        "last_name",
        "group",
        "status",
    ]

    # NOT all fields are readonly for admin, otherwise we would have
    # set this at the permissions level. The exception is 'status'
    analyst_readonly_fields = [
        "User profile",
        "first_name",
        "middle_name",
        "last_name",
        "title",
        "email",
        "phone",
        "Permissions",
        "is_active",
        "groups",
        "Important dates",
        "last_login",
        "date_joined",
    ]

    # TODO: delete after we merge organization feature
    analyst_readonly_fields_no_portfolio = [
        "User profile",
        "first_name",
        "middle_name",
        "last_name",
        "title",
        "email",
        "phone",
        "Permissions",
        "is_active",
        "groups",
        "Important dates",
        "last_login",
        "date_joined",
    ]

    list_filter = (
        "is_active",
        "groups",
    )

    # this ordering effects the ordering of results
    # in autocomplete_fields for user
    ordering = ["first_name", "last_name", "email"]
    search_help_text = "Search by first name, last name, or email."

    def portfolios(self, obj: models.User):
        """Returns a list of links for each related suborg"""
        portfolio_ids = obj.get_portfolios().values_list("portfolio", flat=True)
        queryset = models.Portfolio.objects.filter(id__in=portfolio_ids)
        return get_field_links_as_list(queryset, "portfolio", msg_for_none="No portfolios.")

    portfolios.short_description = "Portfolios"  # type: ignore

    def get_search_results(self, request, queryset, search_term):
        """
        Override for get_search_results. This affects any upstream model using autocomplete_fields,
        such as DomainRequest. This is because autocomplete_fields uses an API call to fetch data,
        and this fetch comes from this method.
        """
        # Custom filtering logic
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)

        # If we aren't given a request to modify, we shouldn't try to
        if request is None or not hasattr(request, "GET"):
            return queryset, use_distinct

        # Otherwise, lets modify it!
        request_get = request.GET

        # The request defines model name and field name.
        # For instance, model_name could be "DomainRequest"
        # and field_name could be "investigator".
        model_name = request_get.get("model_name", None)
        field_name = request_get.get("field_name", None)

        # Make sure we're only modifying requests from these models.
        models_to_target = {"domainrequest"}
        if model_name in models_to_target:
            # Define rules per field
            match field_name:
                case "investigator":
                    # We should not display investigators who don't have a staff role
                    queryset = queryset.filter(is_staff=True)
                case _:
                    # In the default case, do nothing
                    pass

        return queryset, use_distinct

    # Let's define First group
    # (which should in theory be the ONLY group)
    def group(self, obj):
        if obj.groups.filter(name="full_access_group").exists():
            return "full_access_group"
        elif obj.groups.filter(name="cisa_analysts_group").exists():
            return "cisa_analysts_group"
        return ""

    def get_list_display(self, request):
        # The full_access_permission perm will load onto the full_access_group
        # which is equivalent to superuser. The other group we use to manage
        # perms is cisa_analysts_group. cisa_analysts_group will never contain
        # full_access_permission
        if request.user.has_perm("registrar.full_access_permission"):
            # Use the default list display for all access users
            return super().get_list_display(request)

        # Customize the list display for analysts
        return self.analyst_list_display

    def get_fieldsets(self, request, obj=None):
        if request.user.has_perm("registrar.full_access_permission"):
            # Show all fields for all access users
            return super().get_fieldsets(request, obj)
        elif request.user.has_perm("registrar.analyst_access_permission"):
            if flag_is_active(request, "organization_feature"):
                # show analyst_fieldsets for analysts
                return self.analyst_fieldsets
            else:
                # TODO: delete after we merge organization feature
                return self.analyst_fieldsets_no_portfolio
        else:
            # any admin user should belong to either full_access_group
            # or cisa_analyst_group
            return []

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)

        if request.user.has_perm("registrar.full_access_permission"):
            return readonly_fields
        else:
            # Return restrictive Read-only fields for analysts and
            # users who might not belong to groups
            if flag_is_active(request, "organization_feature"):
                return self.analyst_readonly_fields
            else:
                # TODO: delete after we merge organization feature
                return self.analyst_readonly_fields_no_portfolio

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Add user's related domains and requests to context"""
        obj = self.get_object(request, object_id)

        domain_requests = DomainRequest.objects.filter(creator=obj).exclude(
            Q(status=DomainRequest.DomainRequestStatus.STARTED) | Q(status=DomainRequest.DomainRequestStatus.WITHDRAWN)
        )
        sort_by = request.GET.get("sort_by", "requested_domain__name")
        domain_requests = domain_requests.order_by(sort_by)

        user_domain_roles = UserDomainRole.objects.filter(user=obj)
        domain_ids = user_domain_roles.values_list("domain_id", flat=True)
        domains = Domain.objects.filter(id__in=domain_ids).exclude(state=Domain.State.DELETED)

        portfolio_ids = obj.get_portfolios().values_list("portfolio", flat=True)
        portfolios = models.Portfolio.objects.filter(id__in=portfolio_ids)
        extra_context = {"domain_requests": domain_requests, "domains": domains, "portfolios": portfolios}
        return super().change_view(request, object_id, form_url, extra_context)


class HostIPInline(admin.StackedInline):
    """Edit an ip address on the host page."""

    model = models.HostIP


class HostResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.Host


class MyHostAdmin(AuditedAdmin, ImportExportModelAdmin):
    """Custom host admin class to use our inlines."""

    resource_classes = [HostResource]

    search_fields = ["name", "domain__name"]
    search_help_text = "Search by domain or host name."
    inlines = [HostIPInline]

    # Select host to change -> Host
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Host"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)


class HostIpResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.HostIP


class HostIpAdmin(AuditedAdmin, ImportExportModelAdmin):
    """Custom host ip admin class"""

    resource_classes = [HostIpResource]
    model = models.HostIP

    # Select host ip to change -> Host ip
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Host IP"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)


class ContactResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.Contact


class ContactAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Custom contact admin class to add search."""

    resource_classes = [ContactResource]

    search_fields = ["email", "first_name", "last_name"]
    search_help_text = "Search by first name, last name or email."
    list_display = [
        "name",
        "email",
    ]
    # this ordering effects the ordering of results
    # in autocomplete_fields
    ordering = ["first_name", "last_name", "email"]

    fieldsets = [
        (
            None,
            {"fields": ["first_name", "middle_name", "last_name", "title", "email", "phone"]},
        )
    ]

    change_form_template = "django/admin/email_clipboard_change_form.html"

    # We name the custom prop 'contact' because linter
    # is not allowing a short_description attr on it
    # This gets around the linter limitation, for now.
    def name(self, obj: models.Contact):
        """Duplicate the contact _str_"""
        if obj.first_name or obj.last_name:
            return obj.get_formatted_name()
        elif obj.email:
            return obj.email
        elif obj.pk:
            return str(obj.pk)
        else:
            return ""

    name.admin_order_field = "first_name"  # type: ignore

    # Read only that we'll leverage for CISA Analysts
    analyst_readonly_fields: list[str] = ["email"]

    def get_readonly_fields(self, request, obj=None):
        """Set the read-only state on form elements.
        We have 1 conditions that determine which fields are read-only:
        admin user permissions.
        """

        readonly_fields = list(self.readonly_fields)

        if request.user.has_perm("registrar.full_access_permission"):
            return readonly_fields
        # Return restrictive Read-only fields for analysts and
        # users who might not belong to groups
        readonly_fields.extend([field for field in self.analyst_readonly_fields])
        return readonly_fields  # Read-only fields for analysts

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Extend the change_view for Contact objects in django admin.
        Customize to display related objects to the Contact. These will be passed
        through the messages construct to the template for display to the user."""

        # Fetch the Contact instance
        contact = models.Contact.objects.get(pk=object_id)

        # initialize related_objects array
        related_objects = []
        # for all defined fields in the model
        for related_field in contact._meta.get_fields():
            # if the field is a relation to another object
            if related_field.is_relation:
                # Check if the related field is not None
                related_manager = getattr(contact, related_field.name)
                if related_manager is not None:
                    # Check if it's a ManyToManyField/reverse ForeignKey or a OneToOneField
                    # Do this by checking for get_queryset method on the related_manager
                    if hasattr(related_manager, "get_queryset"):
                        # Handles ManyToManyRel and ManyToOneRel
                        queryset = related_manager.get_queryset()
                    else:
                        # Handles OneToOne rels, ie. User
                        queryset = [related_manager]

                    for obj in queryset:
                        # for each object, build the edit url in this view and add as tuple
                        # to the related_objects array
                        app_label = obj._meta.app_label
                        model_name = obj._meta.model_name
                        obj_id = obj.id
                        change_url = reverse("admin:%s_%s_change" % (app_label, model_name), args=[obj_id])
                        related_objects.append((change_url, obj))

        if related_objects:
            message = "<ul class='messagelist_content-list--unstyled'>"
            for i, (url, obj) in enumerate(related_objects):
                if i < 5:
                    escaped_obj = escape(obj)
                    message += f"<li>Joined to {obj.__class__.__name__}: <a href='{url}'>{escaped_obj}</a></li>"
            message += "</ul>"
            if len(related_objects) > 5:
                related_objects_over_five = len(related_objects) - 5
                message += f"<p class='font-sans-3xs'>And {related_objects_over_five} more...</p>"

            message_html = mark_safe(message)  # nosec
            messages.warning(
                request,
                message_html,
            )

        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    # Select contact to change -> Contacts
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Contacts"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        # Clear warning messages before saving
        storage = messages.get_messages(request)
        storage.used = False
        for message in storage:
            if message.level == messages.WARNING:
                storage.used = True

        return super().save_model(request, obj, form, change)


class SeniorOfficialAdmin(ListHeaderAdmin):
    """Custom Senior Official Admin class."""

    search_fields = ["first_name", "last_name", "email"]
    search_help_text = "Search by first name, last name or email."
    list_display = ["first_name", "last_name", "email", "federal_agency"]

    # this ordering effects the ordering of results
    # in autocomplete_fields for Senior Official
    ordering = ["first_name", "last_name"]


class WebsiteResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.Website


class WebsiteAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Custom website admin class."""

    resource_classes = [WebsiteResource]

    # Search
    search_fields = [
        "website",
    ]
    search_help_text = "Search by website."

    def get_model_perms(self, request):
        """
        Return empty perms dict thus hiding the model from admin index.
        """
        superuser_perm = request.user.has_perm("registrar.full_access_permission")
        analyst_perm = request.user.has_perm("registrar.analyst_access_permission")
        if analyst_perm and not superuser_perm:
            return {}
        return super().get_model_perms(request)

    def has_change_permission(self, request, obj=None):
        """
        Allow analysts to access the change form directly via URL.
        """
        superuser_perm = request.user.has_perm("registrar.full_access_permission")
        analyst_perm = request.user.has_perm("registrar.analyst_access_permission")
        if analyst_perm and not superuser_perm:
            return True
        return super().has_change_permission(request, obj)

    def response_change(self, request, obj):
        """
        Override to redirect users back to the previous page after saving.
        """
        superuser_perm = request.user.has_perm("registrar.full_access_permission")
        analyst_perm = request.user.has_perm("registrar.analyst_access_permission")
        return_path = request.GET.get("return_path")

        # First, call the super method to perform the standard operations and capture the response
        response = super().response_change(request, obj)

        # Don't redirect to the website page on save if the user is an analyst.
        # Rather, just redirect back to the originating page.
        if (analyst_perm and not superuser_perm) and return_path:
            # Redirect to the return path if it exists
            return HttpResponseRedirect(return_path)

        # If no redirection is needed, return the original response
        return response


class UserDomainRoleResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.UserDomainRole


class UserPortfolioPermissionAdmin(ListHeaderAdmin):
    form = UserPortfolioPermissionsForm

    class Meta:
        """Contains meta information about this class"""

        model = models.UserPortfolioPermission
        fields = "__all__"

    _meta = Meta()

    # Columns
    list_display = [
        "user",
        "portfolio",
        "get_roles",
    ]

    autocomplete_fields = ["user", "portfolio"]
    search_fields = ["user__first_name", "user__last_name", "user__email", "portfolio__organization_name"]
    search_help_text = "Search by first name, last name, email, or portfolio."

    change_form_template = "django/admin/user_portfolio_permission_change_form.html"

    def get_roles(self, obj):
        readable_roles = obj.get_readable_roles()
        return ", ".join(readable_roles)

    get_roles.short_description = "Roles"  # type: ignore


class UserDomainRoleAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Custom user domain role admin class."""

    resource_classes = [UserDomainRoleResource]

    class Meta:
        """Contains meta information about this class"""

        model = models.UserDomainRole
        fields = "__all__"

    _meta = Meta()

    # Columns
    list_display = [
        "user",
        "domain",
        "role",
    ]

    orderable_fk_fields = [
        ("domain", "name"),
        ("user", ["first_name", "last_name", "email"]),
    ]

    # Search
    search_fields = [
        "user__first_name",
        "user__last_name",
        "user__email",
        "domain__name",
        "role",
    ]
    search_help_text = "Search by first name, last name, email, or domain."

    autocomplete_fields = ["user", "domain"]

    change_form_template = "django/admin/user_domain_role_change_form.html"

    # Fixes a bug where non-superusers are redirected to the main page
    def delete_view(self, request, object_id, extra_context=None):
        """Custom delete_view implementation that specifies redirect behaviour"""
        response = super().delete_view(request, object_id, extra_context)

        if isinstance(response, HttpResponseRedirect) and not request.user.has_perm("registrar.full_access_permission"):
            url = reverse("admin:registrar_userdomainrole_changelist")
            return redirect(url)
        else:
            return response

    # User Domain manager [email] is manager on domain [domain name] ->
    # Domain manager [email] on [domain name]
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if extra_context is None:
            extra_context = {}

        if object_id:
            obj = self.get_object(request, object_id)
            if obj:
                email = obj.user.email
                domain_name = obj.domain.name
                extra_context["subtitle"] = f"Domain manager {email} on {domain_name}"

        return super().changeform_view(request, object_id, form_url, extra_context=extra_context)


class BaseInvitationAdmin(ListHeaderAdmin):
    """Base class for admin classes which will customize save_model and send email invitations
    on model adds, and require custom handling of forms and form errors."""

    def response_add(self, request, obj, post_url_continue=None):
        """
        Override response_add to handle rendering when exceptions are raised during add model.

        Normal flow on successful save_model on add is to redirect to changelist_view.
        If there are errors, flow is modified to instead render change form.
        """
        # store current messages from request so that they are preserved throughout the method
        storage = get_messages(request)
        # Check if there are any error or warning messages in the `messages` framework
        has_errors = any(message.level_tag in ["error", "warning"] for message in storage)

        if has_errors:
            # Re-render the change form if there are errors or warnings
            # Prepare context for rendering the change form

            # Get the model form
            ModelForm = self.get_form(request, obj=obj)
            form = ModelForm(instance=obj)

            # Create an AdminForm instance
            admin_form = AdminForm(
                form,
                list(self.get_fieldsets(request, obj)),
                self.get_prepopulated_fields(request, obj),
                self.get_readonly_fields(request, obj),
                model_admin=self,
            )
            media = self.media + form.media

            opts = obj._meta
            change_form_context = {
                **self.admin_site.each_context(request),  # Add admin context
                "title": f"Add {opts.verbose_name}",
                "opts": opts,
                "original": obj,
                "save_as": self.save_as,
                "has_change_permission": self.has_change_permission(request, obj),
                "add": True,  # Indicate this is an "Add" form
                "change": False,  # Indicate this is not a "Change" form
                "is_popup": False,
                "inline_admin_formsets": [],
                "save_on_top": self.save_on_top,
                "show_delete": self.has_delete_permission(request, obj),
                "obj": obj,
                "adminform": admin_form,  # Pass the AdminForm instance
                "media": media,
                "errors": None,
            }
            return self.render_change_form(
                request,
                context=change_form_context,
                add=True,
                change=False,
                obj=obj,
            )

        response = super().response_add(request, obj, post_url_continue)

        # Re-add all messages from storage after `super().response_add`
        # as super().response_add resets the success messages in request
        for message in storage:
            messages.add_message(request, message.level, message.message)

        return response


class DomainInvitationAdmin(BaseInvitationAdmin):
    """Custom domain invitation admin class."""

    class Meta:
        model = models.DomainInvitation
        fields = "__all__"

    _meta = Meta()

    # Columns
    list_display = [
        "email",
        "domain",
        "status",
    ]

    # Search
    search_fields = [
        "email",
        "domain__name",
    ]

    # Filters
    list_filter = ("status",)

    search_help_text = "Search by email or domain."

    # Mark the FSM field 'status' as readonly
    # to allow admin users to create Domain Invitations
    # without triggering the FSM Transition Not Allowed
    # error.
    readonly_fields = ["status"]

    autocomplete_fields = ["domain"]

    change_form_template = "django/admin/domain_invitation_change_form.html"

    # Select domain invitations to change -> Domain invitations
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Domain invitations"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        """
        Override the save_model method.

        On creation of a new domain invitation, attempt to retrieve the invitation,
        which will be successful if a single User exists for that email; otherwise, will
        just continue to create the invitation.
        """
        if not change:
            domain = obj.domain
            domain_org = getattr(domain.domain_info, "portfolio", None)
            requested_email = obj.email
            # Look up a user with that email
            requested_user = get_requested_user(requested_email)
            requestor = request.user

            member_of_a_different_org, member_of_this_org = get_org_membership(
                domain_org, requested_email, requested_user
            )

            try:
                if (
                    flag_is_active(request, "organization_feature")
                    and not flag_is_active(request, "multiple_portfolios")
                    and domain_org is not None
                    and not member_of_this_org
                    and not member_of_a_different_org
                ):
                    send_portfolio_invitation_email(email=requested_email, requestor=requestor, portfolio=domain_org)
                    portfolio_invitation, _ = PortfolioInvitation.objects.get_or_create(
                        email=requested_email,
                        portfolio=domain_org,
                        roles=[UserPortfolioRoleChoices.ORGANIZATION_MEMBER],
                    )
                    # if user exists for email, immediately retrieve portfolio invitation upon creation
                    if requested_user is not None:
                        portfolio_invitation.retrieve()
                        portfolio_invitation.save()
                    messages.success(request, f"{requested_email} has been invited to the organization: {domain_org}")

                send_domain_invitation_email(
                    email=requested_email,
                    requestor=requestor,
                    domains=domain,
                    is_member_of_different_org=member_of_a_different_org,
                    requested_user=requested_user,
                )
                if requested_user is not None:
                    # Domain Invitation creation for an existing User
                    obj.retrieve()
                # Call the parent save method to save the object
                super().save_model(request, obj, form, change)
                messages.success(request, f"{requested_email} has been invited to the domain: {domain}")
            except Exception as e:
                handle_invitation_exceptions(request, e, requested_email)
                return
        else:
            # Call the parent save method to save the object
            super().save_model(request, obj, form, change)


class PortfolioInvitationAdmin(BaseInvitationAdmin):
    """Custom portfolio invitation admin class."""

    form = PortfolioInvitationAdminForm

    class Meta:
        model = models.PortfolioInvitation
        fields = "__all__"

    _meta = Meta()

    # Columns
    list_display = [
        "email",
        "portfolio",
        "roles",
        "additional_permissions",
        "status",
    ]

    # Search
    search_fields = [
        "email",
        "portfolio__organization_name",
    ]

    # Filters
    list_filter = ("status",)

    search_help_text = "Search by email or portfolio."

    # Mark the FSM field 'status' as readonly
    # to allow admin users to create Domain Invitations
    # without triggering the FSM Transition Not Allowed
    # error.
    readonly_fields = ["status"]

    autocomplete_fields = ["portfolio"]

    change_form_template = "django/admin/portfolio_invitation_change_form.html"

    # Select portfolio invitations to change -> Portfolio invitations
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Portfolio invitations"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        """
        Override the save_model method.

        Only send email on creation of the PortfolioInvitation object. Not on updates.
        Emails sent to requested user / email.
        When exceptions are raised, return without saving model.
        """
        if not change:  # Only send email if this is a new PortfolioInvitation (creation)
            portfolio = obj.portfolio
            requested_email = obj.email
            requestor = request.user
            # Look up a user with that email
            requested_user = get_requested_user(requested_email)

            permission_exists = UserPortfolioPermission.objects.filter(
                user__email=requested_email, portfolio=portfolio, user__email__isnull=False
            ).exists()
            try:
                if not permission_exists:
                    # if permission does not exist for a user with requested_email, send email
                    send_portfolio_invitation_email(email=requested_email, requestor=requestor, portfolio=portfolio)
                    # if user exists for email, immediately retrieve portfolio invitation upon creation
                    if requested_user is not None:
                        obj.retrieve()
                    messages.success(request, f"{requested_email} has been invited.")
                else:
                    messages.warning(request, "User is already a member of this portfolio.")
            except Exception as e:
                # when exception is raised, handle and do not save the model
                handle_invitation_exceptions(request, e, requested_email)
                return
        # Call the parent save method to save the object
        super().save_model(request, obj, form, change)


class DomainInformationResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.DomainInformation


class DomainInformationAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Customize domain information admin class."""

    class GenericOrgFilter(admin.SimpleListFilter):
        """Custom Generic Organization filter that accomodates portfolio feature.
        If we have a portfolio, use the portfolio's organization.  If not, use the
        organization in the Domain Information object."""

        title = "generic organization"
        parameter_name = "converted_generic_orgs"

        def lookups(self, request, model_admin):
            converted_generic_orgs = set()

            # Populate the set with tuples of (value, display value)
            for domain_info in DomainInformation.objects.all():
                converted_generic_org = domain_info.converted_generic_org_type  # Actual value
                converted_generic_org_display = domain_info.converted_generic_org_type_display  # Display value

                if converted_generic_org:
                    converted_generic_orgs.add((converted_generic_org, converted_generic_org_display))  # Value, Display

            # Sort the set by display value
            return sorted(converted_generic_orgs, key=lambda x: x[1])  # x[1] is the display value

        # Filter queryset
        def queryset(self, request, queryset):
            if self.value():  # Check if a generic org is selected in the filter
                return queryset.filter(
                    Q(portfolio__organization_type=self.value())
                    | Q(portfolio__isnull=True, generic_org_type=self.value())
                )
            return queryset

    resource_classes = [DomainInformationResource]

    form = DomainInformationAdminForm

    # Customize column header text
    @admin.display(description=_("Generic Org Type"))
    def converted_generic_org_type(self, obj):
        return obj.converted_generic_org_type_display

    # Columns
    list_display = [
        "domain",
        "converted_generic_org_type",
        "created_at",
    ]

    orderable_fk_fields = [("domain", "name")]

    # Define methods to display fields from the related portfolio
    def portfolio_senior_official(self, obj) -> Optional[SeniorOfficial]:
        return obj.portfolio.senior_official if obj.portfolio and obj.portfolio.senior_official else None

    portfolio_senior_official.short_description = "Senior official"  # type: ignore

    def portfolio_organization_type(self, obj):
        return (
            DomainRequest.OrganizationChoices.get_org_label(obj.portfolio.organization_type)
            if obj.portfolio and obj.portfolio.organization_type
            else "-"
        )

    portfolio_organization_type.short_description = "Organization type"  # type: ignore

    def portfolio_federal_type(self, obj):
        return (
            BranchChoices.get_branch_label(obj.portfolio.federal_type)
            if obj.portfolio and obj.portfolio.federal_type
            else "-"
        )

    portfolio_federal_type.short_description = "Federal type"  # type: ignore

    def portfolio_organization_name(self, obj):
        return obj.portfolio.organization_name if obj.portfolio else ""

    portfolio_organization_name.short_description = "Organization name"  # type: ignore

    def portfolio_federal_agency(self, obj):
        return obj.portfolio.federal_agency if obj.portfolio else ""

    portfolio_federal_agency.short_description = "Federal agency"  # type: ignore

    def portfolio_state_territory(self, obj):
        return obj.portfolio.state_territory if obj.portfolio else ""

    portfolio_state_territory.short_description = "State, territory, or military post"  # type: ignore

    def portfolio_address_line1(self, obj):
        return obj.portfolio.address_line1 if obj.portfolio else ""

    portfolio_address_line1.short_description = "Address line 1"  # type: ignore

    def portfolio_address_line2(self, obj):
        return obj.portfolio.address_line2 if obj.portfolio else ""

    portfolio_address_line2.short_description = "Address line 2"  # type: ignore

    def portfolio_city(self, obj):
        return obj.portfolio.city if obj.portfolio else ""

    portfolio_city.short_description = "City"  # type: ignore

    def portfolio_zipcode(self, obj):
        return obj.portfolio.zipcode if obj.portfolio else ""

    portfolio_zipcode.short_description = "Zip code"  # type: ignore

    def portfolio_urbanization(self, obj):
        return obj.portfolio.urbanization if obj.portfolio else ""

    portfolio_urbanization.short_description = "Urbanization"  # type: ignore

    # Filters
    list_filter = [GenericOrgFilter]

    # Search
    search_fields = [
        "domain__name",
    ]
    search_help_text = "Search by domain."

    fieldsets = [
        (
            None,
            {
                "fields": [
                    "domain_request",
                    "notes",
                ]
            },
        ),
        (
            "Requested by",
            {
                "fields": [
                    "portfolio",
                    "sub_organization",
                    "creator",
                ]
            },
        ),
        (".gov domain", {"fields": ["domain"]}),
        (
            "Contacts",
            {
                "fields": [
                    "senior_official",
                    "portfolio_senior_official",
                    "other_contacts",
                    "no_other_contacts_rationale",
                    "cisa_representative_first_name",
                    "cisa_representative_last_name",
                    "cisa_representative_email",
                ]
            },
        ),
        ("Background info", {"fields": ["anything_else"]}),
        (
            "Type of organization",
            {
                "fields": [
                    "is_election_board",
                    "organization_type",
                ]
            },
        ),
        (
            "Show details",
            {
                "classes": ["collapse--dgfieldset"],
                "description": "Extends type of organization",
                "fields": [
                    "federal_type",
                    "federal_agency",
                    "tribe_name",
                    "federally_recognized_tribe",
                    "state_recognized_tribe",
                    "about_your_organization",
                ],
            },
        ),
        (
            "Organization name and mailing address",
            {
                "fields": [
                    "organization_name",
                    "state_territory",
                ]
            },
        ),
        (
            "Show details",
            {
                "classes": ["collapse--dgfieldset"],
                "description": "Extends organization name and mailing address",
                "fields": [
                    "address_line1",
                    "address_line2",
                    "city",
                    "zipcode",
                    "urbanization",
                ],
            },
        ),
        # the below three sections are for portfolio fields
        (
            "Type of organization",
            {
                "fields": [
                    "portfolio_organization_type",
                    "portfolio_federal_type",
                ]
            },
        ),
        (
            "Organization name and mailing address",
            {
                "fields": [
                    "portfolio_organization_name",
                    "portfolio_federal_agency",
                ]
            },
        ),
        (
            "Show details",
            {
                "classes": ["collapse--dgfieldset"],
                "description": "Extends organization name and mailing address",
                "fields": [
                    "portfolio_state_territory",
                    "portfolio_address_line1",
                    "portfolio_address_line2",
                    "portfolio_city",
                    "portfolio_zipcode",
                    "portfolio_urbanization",
                ],
            },
        ),
    ]

    # Readonly fields for analysts and superusers
    readonly_fields = (
        "portfolio_senior_official",
        "portfolio_organization_type",
        "portfolio_federal_type",
        "portfolio_organization_name",
        "portfolio_federal_agency",
        "portfolio_state_territory",
        "portfolio_address_line1",
        "portfolio_address_line2",
        "portfolio_city",
        "portfolio_zipcode",
        "portfolio_urbanization",
        "other_contacts",
        "is_election_board",
    )

    # Read only that we'll leverage for CISA Analysts
    analyst_readonly_fields = [
        "federal_agency",
        "creator",
        "type_of_work",
        "more_organization_information",
        "domain",
        "domain_request",
        "no_other_contacts_rationale",
        "anything_else",
        "is_policy_acknowledged",
    ]

    # For each filter_horizontal, init in admin js initFilterHorizontalWidget
    # to activate the edit/delete/view buttons
    filter_horizontal = ("other_contacts",)

    autocomplete_fields = [
        "creator",
        "domain_request",
        "senior_official",
        "domain",
        "portfolio",
        "sub_organization",
    ]

    # Table ordering
    ordering = ["domain__name"]

    change_form_template = "django/admin/domain_information_change_form.html"

    def get_readonly_fields(self, request, obj=None):
        """Set the read-only state on form elements.
        We have 1 conditions that determine which fields are read-only:
        admin user permissions.
        """

        readonly_fields = list(self.readonly_fields)

        if request.user.has_perm("registrar.full_access_permission"):
            return readonly_fields
        # Return restrictive Read-only fields for analysts and
        # users who might not belong to groups
        readonly_fields.extend([field for field in self.analyst_readonly_fields])
        return readonly_fields  # Read-only fields for analysts

    # Select domain information to change -> Domain information
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Domain information"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Customize the behavior of formfields with foreign key relationships. This will customize
        the behavior of selects. Customized behavior includes sorting of objects in list."""
        # TODO #2571
        # Remove this check on senior_official if this underlying model changes from
        # "Contact" to "SeniorOfficial" or if we refactor AdminSortFields.
        # Removing this will cause the list on django admin to return SeniorOffical
        # objects rather than Contact objects.
        use_sort = db_field.name != "senior_official"
        return super().formfield_for_foreignkey(db_field, request, use_admin_sort_fields=use_sort, **kwargs)


class DomainRequestResource(FsmModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.DomainRequest


class DomainRequestAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Custom domain requests admin class."""

    resource_classes = [DomainRequestResource]

    form = DomainRequestAdminForm
    change_form_template = "django/admin/domain_request_change_form.html"

    # ------ Filters ------
    # Define custom filters
    class StatusListFilter(MultipleChoiceListFilter):
        """Custom status filter which is a multiple choice filter"""

        title = "status"
        parameter_name = "status__in"

        template = "django/admin/multiple_choice_list_filter.html"

        def lookups(self, request, model_admin):
            return DomainRequest.DomainRequestStatus.choices

    class GenericOrgFilter(admin.SimpleListFilter):
        """Custom Generic Organization filter that accomodates portfolio feature.
        If we have a portfolio, use the portfolio's organization.  If not, use the
        organization in the Domain Request object."""

        title = "generic organization"
        parameter_name = "converted_generic_orgs"

        def lookups(self, request, model_admin):
            converted_generic_orgs = set()

            # Populate the set with tuples of (value, display value)
            for domain_request in DomainRequest.objects.all():
                converted_generic_org = domain_request.converted_generic_org_type  # Actual value
                converted_generic_org_display = domain_request.converted_generic_org_type_display  # Display value

                if converted_generic_org:
                    converted_generic_orgs.add((converted_generic_org, converted_generic_org_display))  # Value, Display

            # Sort the set by display value
            return sorted(converted_generic_orgs, key=lambda x: x[1])  # x[1] is the display value

        # Filter queryset
        def queryset(self, request, queryset):
            if self.value():  # Check if a generic org is selected in the filter
                return queryset.filter(
                    Q(portfolio__organization_type=self.value())
                    | Q(portfolio__isnull=True, generic_org_type=self.value())
                )
            return queryset

    class FederalTypeFilter(admin.SimpleListFilter):
        """Custom Federal Type filter that accomodates portfolio feature.
        If we have a portfolio, use the portfolio's federal type.  If not, use the
        organization in the Domain Request object."""

        title = "federal type"
        parameter_name = "converted_federal_types"

        def lookups(self, request, model_admin):
            converted_federal_types = set()

            # Populate the set with tuples of (value, display value)
            for domain_request in DomainRequest.objects.all():
                converted_federal_type = domain_request.converted_federal_type  # Actual value
                converted_federal_type_display = domain_request.converted_federal_type_display  # Display value

                if converted_federal_type:
                    converted_federal_types.add(
                        (converted_federal_type, converted_federal_type_display)  # Value, Display
                    )

            # Sort the set by display value
            return sorted(converted_federal_types, key=lambda x: x[1])  # x[1] is the display value

        # Filter queryset
        def queryset(self, request, queryset):
            if self.value():  # Check if a federal type is selected in the filter
                return queryset.filter(
                    Q(portfolio__federal_agency__federal_type=self.value())
                    | Q(portfolio__isnull=True, federal_type=self.value())
                )
            return queryset

    class InvestigatorFilter(admin.SimpleListFilter):
        """Custom investigator filter that only displays users with the manager role"""

        title = "investigator"
        # Match the old param name to avoid unnecessary refactoring
        parameter_name = "investigator__id__exact"

        def lookups(self, request, model_admin):
            """Lookup reimplementation, gets users of is_staff.
            Returns a list of tuples consisting of (user.id, user)
            """
            # Select all investigators that are staff, then order by name and email
            privileged_users = (
                DomainRequest.objects.select_related("investigator")
                .filter(investigator__is_staff=True)
                .order_by("investigator__first_name", "investigator__last_name", "investigator__email")
            )

            # Annotate the full name and return a values list that lookups can use
            privileged_users_annotated = (
                privileged_users.annotate(
                    full_name=Coalesce(
                        Concat(
                            "investigator__first_name", Value(" "), "investigator__last_name", output_field=CharField()
                        ),
                        "investigator__email",
                        output_field=CharField(),
                    )
                )
                .values_list("investigator__id", "full_name")
                .distinct()
            )

            return privileged_users_annotated

        def queryset(self, request, queryset):
            """Custom queryset implementation, filters by investigator"""
            if self.value() is None:
                return queryset
            else:
                return queryset.filter(investigator__id__exact=self.value())

    class ElectionOfficeFilter(admin.SimpleListFilter):
        """Define a custom filter for is_election_board"""

        title = _("election office")
        parameter_name = "is_election_board"

        def lookups(self, request, model_admin):
            return (
                ("1", _("Yes")),
                ("0", _("No")),
            )

        def queryset(self, request, queryset):
            if self.value() == "1":
                return queryset.filter(is_election_board=True)
            if self.value() == "0":
                return queryset.filter(Q(is_election_board=False) | Q(is_election_board=None))

    class PortfolioFilter(admin.SimpleListFilter):
        """Define a custom filter for portfolio"""

        title = _("portfolio")
        parameter_name = "portfolio__isnull"

        def lookups(self, request, model_admin):
            return (
                ("1", _("Yes")),
                ("0", _("No")),
            )

        def queryset(self, request, queryset):
            if self.value() == "1":
                return queryset.filter(Q(portfolio__isnull=False))
            if self.value() == "0":
                return queryset.filter(Q(portfolio__isnull=True))

    # ------ Custom fields ------
    def custom_election_board(self, obj):
        return "Yes" if obj.is_election_board else "No"

    custom_election_board.admin_order_field = "is_election_board"  # type: ignore
    custom_election_board.short_description = "Election office"  # type: ignore

    @admin.display(description=_("Requested Domain"))
    def custom_requested_domain(self, obj):
        # Example: Show different icons based on `status`
        url = reverse("admin:registrar_domainrequest_changelist") + f"{obj.id}"
        text = obj.requested_domain
        if obj.portfolio:
            return format_html('<a href="{}"><img src="/public/admin/img/icon-yes.svg"> {}</a>', url, text)
        return format_html('<a href="{}">{}</a>', url, text)

    custom_requested_domain.admin_order_field = "requested_domain__name"  # type: ignore

    # ------ Converted fields ------
    # These fields map to @Property methods and
    # require these custom definitions to work properly
    @admin.display(description=_("Generic Org Type"))
    def converted_generic_org_type(self, obj):
        return obj.converted_generic_org_type_display

    @admin.display(description=_("Organization Name"))
    def converted_organization_name(self, obj):
        # Example: Show different icons based on `status`
        if obj.portfolio:
            url = reverse("admin:registrar_portfolio_change", args=[obj.portfolio.id])
            text = obj.converted_organization_name
            return format_html('<a href="{}">{}</a>', url, text)
        else:
            return obj.converted_organization_name

    @admin.display(description=_("Federal Agency"))
    def converted_federal_agency(self, obj):
        return obj.converted_federal_agency

    @admin.display(description=_("Federal Type"))
    def converted_federal_type(self, obj):
        return obj.converted_federal_type_display

    @admin.display(description=_("City"))
    def converted_city(self, obj):
        return obj.converted_city

    @admin.display(description=_("State/Territory"))
    def converted_state_territory(self, obj):
        return obj.converted_state_territory

    # ------ Portfolio fields ------
    # Define methods to display fields from the related portfolio
    def portfolio_senior_official(self, obj) -> Optional[SeniorOfficial]:
        return obj.portfolio.senior_official if obj.portfolio and obj.portfolio.senior_official else None

    portfolio_senior_official.short_description = "Senior official"  # type: ignore

    def portfolio_organization_type(self, obj):
        return (
            DomainRequest.OrganizationChoices.get_org_label(obj.portfolio.organization_type)
            if obj.portfolio and obj.portfolio.organization_type
            else "-"
        )

    portfolio_organization_type.short_description = "Organization type"  # type: ignore

    def portfolio_federal_type(self, obj):
        return (
            BranchChoices.get_branch_label(obj.portfolio.federal_type)
            if obj.portfolio and obj.portfolio.federal_type
            else "-"
        )

    portfolio_federal_type.short_description = "Federal type"  # type: ignore

    def portfolio_organization_name(self, obj):
        return obj.portfolio.organization_name if obj.portfolio else ""

    portfolio_organization_name.short_description = "Organization name"  # type: ignore

    def portfolio_federal_agency(self, obj):
        return obj.portfolio.federal_agency if obj.portfolio else ""

    portfolio_federal_agency.short_description = "Federal agency"  # type: ignore

    def portfolio_state_territory(self, obj):
        return obj.portfolio.state_territory if obj.portfolio else ""

    portfolio_state_territory.short_description = "State, territory, or military post"  # type: ignore

    def portfolio_address_line1(self, obj):
        return obj.portfolio.address_line1 if obj.portfolio else ""

    portfolio_address_line1.short_description = "Address line 1"  # type: ignore

    def portfolio_address_line2(self, obj):
        return obj.portfolio.address_line2 if obj.portfolio else ""

    portfolio_address_line2.short_description = "Address line 2"  # type: ignore

    def portfolio_city(self, obj):
        return obj.portfolio.city if obj.portfolio else ""

    portfolio_city.short_description = "City"  # type: ignore

    def portfolio_zipcode(self, obj):
        return obj.portfolio.zipcode if obj.portfolio else ""

    portfolio_zipcode.short_description = "Zip code"  # type: ignore

    def portfolio_urbanization(self, obj):
        return obj.portfolio.urbanization if obj.portfolio else ""

    portfolio_urbanization.short_description = "Urbanization"  # type: ignore

    # This is just a placeholder. This field will be populated in the detail_table_fieldset view.
    # This is not a field that exists on the model.
    def status_history(self, obj):
        return "No changelog to display."

    status_history.short_description = "Status history"  # type: ignore

    # Columns
    list_display = [
        "custom_requested_domain",
        "first_submitted_date",
        "last_submitted_date",
        "last_status_update",
        "status",
        "custom_election_board",
        "converted_generic_org_type",
        "converted_organization_name",
        "converted_federal_agency",
        "converted_federal_type",
        "converted_city",
        "converted_state_territory",
        "investigator",
    ]

    orderable_fk_fields = [
        ("requested_domain", "name"),
        ("investigator", ["first_name", "last_name"]),
    ]

    # Filters
    list_filter = (
        PortfolioFilter,
        StatusListFilter,
        GenericOrgFilter,
        FederalTypeFilter,
        ElectionOfficeFilter,
        "rejection_reason",
        InvestigatorFilter,
    )

    # Search
    # NOTE: converted fields are included in the override for get_search_results
    search_fields = [
        "requested_domain__name",
        "creator__email",
        "creator__first_name",
        "creator__last_name",
    ]
    search_help_text = "Search by domain, creator, or organization name."

    fieldsets = [
        (
            None,
            {
                "fields": [
                    "status_history",
                    "status",
                    "rejection_reason",
                    "rejection_reason_email",
                    "action_needed_reason",
                    "action_needed_reason_email",
                    "approved_domain",
                    "investigator",
                    "notes",
                ]
            },
        ),
        (
            "Requested by",
            {
                "fields": [
                    "portfolio",
                    "sub_organization",
                    "requested_suborganization",
                    "suborganization_city",
                    "suborganization_state_territory",
                    "creator",
                ]
            },
        ),
        (".gov domain", {"fields": ["requested_domain", "alternative_domains"]}),
        (
            "Contacts",
            {
                "fields": [
                    "senior_official",
                    "portfolio_senior_official",
                    "other_contacts",
                    "no_other_contacts_rationale",
                    "cisa_representative_first_name",
                    "cisa_representative_last_name",
                    "cisa_representative_email",
                ]
            },
        ),
        ("Background info", {"fields": ["purpose", "anything_else", "current_websites"]}),
        (
            "Type of organization",
            {
                "fields": [
                    "is_election_board",
                    "organization_type",
                ]
            },
        ),
        (
            "Show details",
            {
                "classes": ["collapse--dgfieldset"],
                "description": "Extends type of organization",
                "fields": [
                    "federal_type",
                    "federal_agency",
                    "tribe_name",
                    "federally_recognized_tribe",
                    "state_recognized_tribe",
                    "about_your_organization",
                ],
            },
        ),
        (
            "Organization name and mailing address",
            {
                "fields": [
                    "organization_name",
                    "state_territory",
                ]
            },
        ),
        (
            "Show details",
            {
                "classes": ["collapse--dgfieldset"],
                "description": "Extends organization name and mailing address",
                "fields": [
                    "address_line1",
                    "address_line2",
                    "city",
                    "zipcode",
                    "urbanization",
                ],
            },
        ),
        # the below three sections are for portfolio fields
        (
            "Type of organization",
            {
                "fields": [
                    "portfolio_organization_type",
                    "portfolio_federal_type",
                ]
            },
        ),
        (
            "Organization name and mailing address",
            {
                "fields": [
                    "portfolio_organization_name",
                    "portfolio_federal_agency",
                ]
            },
        ),
        (
            "Show details",
            {
                "classes": ["collapse--dgfieldset"],
                "description": "Extends organization name and mailing address",
                "fields": [
                    "portfolio_state_territory",
                    "portfolio_address_line1",
                    "portfolio_address_line2",
                    "portfolio_city",
                    "portfolio_zipcode",
                    "portfolio_urbanization",
                ],
            },
        ),
    ]

    # Readonly fields for analysts and superusers
    readonly_fields = (
        "portfolio_senior_official",
        "portfolio_organization_type",
        "portfolio_federal_type",
        "portfolio_organization_name",
        "portfolio_federal_agency",
        "portfolio_state_territory",
        "portfolio_address_line1",
        "portfolio_address_line2",
        "portfolio_city",
        "portfolio_zipcode",
        "portfolio_urbanization",
        "other_contacts",
        "current_websites",
        "alternative_domains",
        "is_election_board",
        "status_history",
    )

    # Read only that we'll leverage for CISA Analysts
    analyst_readonly_fields = [
        "federal_agency",
        "creator",
        "about_your_organization",
        "requested_domain",
        "approved_domain",
        "alternative_domains",
        "purpose",
        "no_other_contacts_rationale",
        "anything_else",
        "is_policy_acknowledged",
        "cisa_representative_first_name",
        "cisa_representative_last_name",
        "cisa_representative_email",
    ]

    autocomplete_fields = [
        "approved_domain",
        "requested_domain",
        "creator",
        "investigator",
        "portfolio",
        "sub_organization",
    ]

    filter_horizontal = ("current_websites", "alternative_domains", "other_contacts")

    # Table ordering
    # NOTE: This impacts the select2 dropdowns (combobox)
    # Currentl, there's only one for requests on DomainInfo
    ordering = ["-last_submitted_date", "requested_domain__name"]

    change_form_template = "django/admin/domain_request_change_form.html"

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)

        # Hide certain portfolio and suborg fields behind the organization requests flag
        # if it is not enabled
        if not flag_is_active_for_user(request.user, "organization_requests"):
            excluded_fields = [
                "portfolio",
                "sub_organization",
                "requested_suborganization",
                "suborganization_city",
                "suborganization_state_territory",
            ]
            modified_fieldsets = []
            for name, data in fieldsets:
                fields = data.get("fields", [])
                fields = tuple(field for field in fields if field not in excluded_fields)
                modified_fieldsets.append((name, {**data, "fields": fields}))
            return modified_fieldsets
        return fieldsets

    # Trigger action when a fieldset is changed
    def save_model(self, request, obj, form, change):
        """Custom save_model definition that handles edge cases"""

        # == Check that the obj is in a valid state == #

        # If obj is none, something went very wrong.
        # The form should have blocked this, so lets forbid it.
        if not obj:
            logger.error(f"Invalid value for obj ({obj})")
            messages.set_level(request, messages.ERROR)
            messages.error(
                request,
                "Could not save DomainRequest. Something went wrong.",
            )
            return None

        # If the user is restricted or we're saving an invalid model,
        # forbid this action.
        if not obj or obj.creator.status == models.User.RESTRICTED:
            # Clear the success message
            messages.set_level(request, messages.ERROR)

            messages.error(
                request,
                "This action is not permitted for domain requests with a restricted creator.",
            )

            return None

        # == Check if we're making a change or not == #

        # If we're not making a change (adding a record), run save model as we do normally
        if not change:
            return super().save_model(request, obj, form, change)

        # Get the original domain request from the database.
        original_obj = models.DomainRequest.objects.get(pk=obj.pk)

        # == Handle action needed and rejected emails == #
        # Edge case: this logic is handled by javascript, so contexts outside that must be handled
        obj = self._handle_custom_emails(obj)

        # == Handle allowed emails == #
        if obj.status in DomainRequest.get_statuses_that_send_emails() and not settings.IS_PRODUCTION:
            self._check_for_valid_email(request, obj)

        # == Handle status == #
        if obj.status == original_obj.status:
            # If the status hasn't changed, let the base function take care of it
            return super().save_model(request, obj, form, change)
        else:
            # Run some checks on the current object for invalid status changes
            obj, should_save = self._handle_status_change(request, obj, original_obj)

            # We should only save if we don't display any errors in the steps above.
            if should_save:
                return super().save_model(request, obj, form, change)

    def _handle_custom_emails(self, obj):
        if obj.status == DomainRequest.DomainRequestStatus.ACTION_NEEDED:
            if obj.action_needed_reason and not obj.action_needed_reason_email:
                obj.action_needed_reason_email = get_action_needed_reason_default_email(obj, obj.action_needed_reason)
        elif obj.status == DomainRequest.DomainRequestStatus.REJECTED:
            if obj.rejection_reason and not obj.rejection_reason_email:
                obj.rejection_reason_email = get_rejection_reason_default_email(obj, obj.rejection_reason)
        return obj

    def _check_for_valid_email(self, request, obj):
        """Certain emails are whitelisted in non-production environments,
        so we should display that information using this function.

        """
        recipient = obj.creator

        # Displays a warning in admin when an email cannot be sent
        if recipient and recipient.email:
            email = recipient.email
            allowed = models.AllowedEmail.is_allowed_email(email)
            error_message = f"Could not send email. The email '{email}' does not exist within the whitelist."
            if not allowed:
                messages.warning(request, error_message)

    def _handle_status_change(self, request, obj, original_obj):
        """
        Checks for various conditions when a status change is triggered.
        In the event that it is valid, the status will be mapped to
        the appropriate method.

        In the event that we should not status change, an error message
        will be displayed.

        Returns a tuple: (obj: DomainRequest, should_proceed: bool)
        """

        should_proceed = True
        error_message = None

        # Get the method that should be run given the status
        selected_method = self.get_status_method_mapping(obj)
        if selected_method is None:
            logger.warning("Unknown status selected in django admin")

            # If the status is not mapped properly, saving could cause
            # weird issues down the line. Instead, we should block this.
            # NEEDS A UNIT TEST
            should_proceed = False
            return (obj, should_proceed)

        obj_is_not_approved = obj.status != models.DomainRequest.DomainRequestStatus.APPROVED
        if obj_is_not_approved and not obj.domain_is_not_active():
            # REDUNDANT CHECK / ERROR SCREEN AVOIDANCE:
            # This action (moving a request from approved to
            # another status) when the domain is already active (READY),
            # would still not go through even without this check as the rules are
            # duplicated in the model and the error is raised from the model.
            # This avoids an ugly Django error screen.
            error_message = "This action is not permitted. The domain is already active."
        elif (
            original_obj.status != models.DomainRequest.DomainRequestStatus.APPROVED
            and obj.status == models.DomainRequest.DomainRequestStatus.APPROVED
            and original_obj.requested_domain is not None
            and Domain.objects.filter(name=original_obj.requested_domain.name).exists()
        ):
            # REDUNDANT CHECK:
            # This action (approving a request when the domain exists)
            # would still not go through even without this check as the rules are
            # duplicated in the model and the error is raised from the model.
            error_message = FSMDomainRequestError.get_error_message(FSMErrorCodes.APPROVE_DOMAIN_IN_USE)
        elif obj.status == models.DomainRequest.DomainRequestStatus.REJECTED and not obj.rejection_reason:
            # This condition should never be triggered.
            # The opposite of this condition is acceptable (rejected -> other status and rejection_reason)
            # because we clean up the rejection reason in the transition in the model.
            error_message = FSMDomainRequestError.get_error_message(FSMErrorCodes.NO_REJECTION_REASON)
        elif obj.status == models.DomainRequest.DomainRequestStatus.ACTION_NEEDED and not obj.action_needed_reason:
            error_message = FSMDomainRequestError.get_error_message(FSMErrorCodes.NO_ACTION_NEEDED_REASON)
        else:
            # This is an fsm in model which will throw an error if the
            # transition condition is violated, so we roll back the
            # status to what it was before the admin user changed it and
            # let the fsm method set it.
            obj.status = original_obj.status

            # Try to perform the status change.
            # Catch FSMDomainRequestError's and return the message,
            # as these are typically user errors.
            try:
                selected_method()
            except FSMDomainRequestError as err:
                logger.warning(f"An error encountered when trying to change status: {err}")
                error_message = err.message

        if error_message is not None:
            # Clear the success message
            messages.set_level(request, messages.ERROR)
            # Display the error
            messages.error(
                request,
                error_message,
            )

            # If an error message exists, we shouldn't proceed
            should_proceed = False

        return (obj, should_proceed)

    def get_status_method_mapping(self, domain_request):
        """Returns what method should be ran given an domain request object"""
        # Define a per-object mapping
        status_method_mapping = {
            models.DomainRequest.DomainRequestStatus.STARTED: None,
            models.DomainRequest.DomainRequestStatus.SUBMITTED: domain_request.submit,
            models.DomainRequest.DomainRequestStatus.IN_REVIEW: domain_request.in_review,
            models.DomainRequest.DomainRequestStatus.ACTION_NEEDED: domain_request.action_needed,
            models.DomainRequest.DomainRequestStatus.APPROVED: domain_request.approve,
            models.DomainRequest.DomainRequestStatus.WITHDRAWN: domain_request.withdraw,
            models.DomainRequest.DomainRequestStatus.REJECTED: domain_request.reject,
            models.DomainRequest.DomainRequestStatus.INELIGIBLE: (domain_request.reject_with_prejudice),
        }

        # Grab the method
        return status_method_mapping.get(domain_request.status, None)

    def get_readonly_fields(self, request, obj=None):
        """Set the read-only state on form elements.
        We have 2 conditions that determine which fields are read-only:
        admin user permissions and the domain request creator's status, so
        we'll use the baseline readonly_fields and extend it as needed.
        """
        readonly_fields = list(self.readonly_fields)

        # Check if the creator is restricted
        if obj and obj.creator.status == models.User.RESTRICTED:
            # For fields like CharField, IntegerField, etc., the widget used is
            # straightforward and the readonly_fields list can control their behavior
            readonly_fields.extend([field.name for field in self.model._meta.fields])
            # Add the multi-select fields to readonly_fields:
            # Complex fields like ManyToManyField require special handling
            readonly_fields.extend(["alternative_domains"])

        if request.user.has_perm("registrar.full_access_permission"):
            return readonly_fields
        # Return restrictive Read-only fields for analysts and
        # users who might not belong to groups
        readonly_fields.extend([field for field in self.analyst_readonly_fields])
        return readonly_fields

    def display_restricted_warning(self, request, obj):
        if obj and obj.creator.status == models.User.RESTRICTED:
            messages.warning(
                request,
                "Cannot edit a domain request with a restricted creator.",
            )

    def changelist_view(self, request, extra_context=None):
        """
        Override changelist_view to set the selected value of status filter.
        """
        # there are two conditions which should set the default selected filter:
        # 1 - there are no query parameters in the request and the request is the
        #     initial request for this view
        # 2 - there are no query parameters in the request and the referring url is
        #     the change view for a domain request
        should_apply_default_filter = False
        # use http_referer in order to distinguish between request as a link from another page
        # and request as a removal of all filters
        http_referer = request.META.get("HTTP_REFERER", "")
        # if there are no query parameters in the request
        if not bool(request.GET):
            # if the request is the initial request for this view
            if request.path not in http_referer:
                should_apply_default_filter = True
            # elif the request is a referral from changelist view or from
            # domain request change view
            elif request.path in http_referer:
                # find the index to determine the referring url after the path
                index = http_referer.find(request.path)
                # Check if there is a character following the path in http_referer
                next_char_index = index + len(request.path)
                if index + next_char_index < len(http_referer):
                    next_char = http_referer[next_char_index]

                    # Check if the next character is a digit, if so, this indicates
                    # a change view for domain request
                    if next_char.isdigit():
                        should_apply_default_filter = True

        # Select domain request to change -> Domain requests
        if extra_context is None:
            extra_context = {}
            extra_context["tabtitle"] = "Domain requests"

        if should_apply_default_filter:
            # modify the GET of the request to set the selected filter
            modified_get = copy.deepcopy(request.GET)
            modified_get["status__in"] = "submitted,in review,action needed"
            request.GET = modified_get

        response = super().changelist_view(request, extra_context=extra_context)
        return response

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Display restricted warning, setup the auditlog trail and pass it in extra context,
        display warning that status cannot be changed from 'Approved' if domain is in Ready state"""

        # Fetch the domain request instance
        domain_request: models.DomainRequest = models.DomainRequest.objects.get(pk=object_id)
        if domain_request.approved_domain and domain_request.approved_domain.state == models.Domain.State.READY:
            domain = domain_request.approved_domain
            # get change url for domain
            app_label = domain_request.approved_domain._meta.app_label
            model_name = domain._meta.model_name
            obj_id = domain.id
            change_url = reverse("admin:%s_%s_change" % (app_label, model_name), args=[obj_id])

            message = format_html(
                "The status of this domain request cannot be changed because it has been joined to a domain in Ready status: "  # noqa: E501
                "<a href='{}'>{}</a>",
                mark_safe(change_url),  # nosec
                escape(str(domain)),
            )
            messages.warning(
                request,
                message,
            )

        obj = self.get_object(request, object_id)
        self.display_restricted_warning(request, obj)

        # Initialize variables for tracking status changes and filtered entries
        filtered_audit_log_entries = []

        try:
            # Retrieve and order audit log entries by timestamp in descending order
            audit_log_entries = LogEntry.objects.filter(
                object_id=object_id, content_type__model="domainrequest"
            ).order_by("-timestamp")

            # Process each log entry to filter based on the change criteria
            for log_entry in audit_log_entries:
                entry = self.process_log_entry(log_entry)
                if entry:
                    filtered_audit_log_entries.append(entry)

        except ObjectDoesNotExist as e:
            logger.error(f"Object with object_id {object_id} does not exist: {e}")
        except Exception as e:
            logger.error(f"An error occurred during change_view: {e}")

        # Initialize extra_context and add filtered entries
        extra_context = extra_context or {}
        extra_context["filtered_audit_log_entries"] = filtered_audit_log_entries

        # Denote if an action needed email was sent or not
        email_sent = request.session.get("action_needed_email_sent", False)
        extra_context["action_needed_email_sent"] = email_sent
        if email_sent:
            request.session["action_needed_email_sent"] = False

        # Call the superclass method with updated extra_context
        return super().change_view(request, object_id, form_url, extra_context)

    def process_log_entry(self, log_entry):
        """Process a log entry and return filtered entry dictionary if applicable."""
        changes = log_entry.changes
        status_changed = "status" in changes
        rejection_reason_changed = "rejection_reason" in changes
        action_needed_reason_changed = "action_needed_reason" in changes

        # Check if the log entry meets the filtering criteria
        if status_changed or (not status_changed and (rejection_reason_changed or action_needed_reason_changed)):
            entry = {}

            # Handle status change
            if status_changed:
                _, status_value = changes.get("status")
                if status_value:
                    entry["status"] = DomainRequest.DomainRequestStatus.get_status_label(status_value)

            # Handle rejection reason change
            if rejection_reason_changed:
                _, rejection_reason_value = changes.get("rejection_reason")
                if rejection_reason_value:
                    entry["rejection_reason"] = (
                        ""
                        if rejection_reason_value == "None"
                        else DomainRequest.RejectionReasons.get_rejection_reason_label(rejection_reason_value)
                    )
                    # Handle case where rejection reason changed but not status
                    if not status_changed:
                        entry["status"] = DomainRequest.DomainRequestStatus.get_status_label(
                            DomainRequest.DomainRequestStatus.REJECTED
                        )

            # Handle action needed reason change
            if action_needed_reason_changed:
                _, action_needed_reason_value = changes.get("action_needed_reason")
                if action_needed_reason_value:
                    entry["action_needed_reason"] = (
                        ""
                        if action_needed_reason_value == "None"
                        else DomainRequest.ActionNeededReasons.get_action_needed_reason_label(
                            action_needed_reason_value
                        )
                    )
                    # Handle case where action needed reason changed but not status
                    if not status_changed:
                        entry["status"] = DomainRequest.DomainRequestStatus.get_status_label(
                            DomainRequest.DomainRequestStatus.ACTION_NEEDED
                        )

            # Add actor and timestamp information
            entry["actor"] = log_entry.actor
            entry["timestamp"] = log_entry.timestamp

            return entry

        return None

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Customize the behavior of formfields with foreign key relationships. This will customize
        the behavior of selects. Customized behavior includes sorting of objects in list."""
        # TODO #2571
        # Remove this check on senior_official if this underlying model changes from
        # "Contact" to "SeniorOfficial" or if we refactor AdminSortFields.
        # Removing this will cause the list on django admin to return SeniorOffical
        # objects rather than Contact objects.
        use_sort = db_field.name != "senior_official"
        return super().formfield_for_foreignkey(db_field, request, use_admin_sort_fields=use_sort, **kwargs)

    def get_queryset(self, request):
        """Custom get_queryset to filter by portfolio if portfolio is in the
        request params."""
        qs = super().get_queryset(request)
        # Check if a 'portfolio' parameter is passed in the request
        portfolio_id = request.GET.get("portfolio")
        if portfolio_id:
            # Further filter the queryset by the portfolio
            qs = qs.filter(portfolio=portfolio_id)
        return qs

    def get_search_results(self, request, queryset, search_term):
        # Call the parent's method to apply default search logic
        base_queryset, use_distinct = super().get_search_results(request, queryset, search_term)

        # Add custom search logic for the annotated field
        if search_term:
            annotated_queryset = queryset.filter(
                # converted_organization_name
                Q(portfolio__organization_name__icontains=search_term)
                | Q(portfolio__isnull=True, organization_name__icontains=search_term)
            )

            # Combine the two querysets using union
            combined_queryset = base_queryset | annotated_queryset
        else:
            combined_queryset = base_queryset

        return combined_queryset, use_distinct


class TransitionDomainAdmin(ListHeaderAdmin):
    """Custom transition domain admin class."""

    # Columns
    list_display = [
        "username",
        "domain_name",
        "status",
        "email_sent",
        "processed",
    ]

    search_fields = ["username", "domain_name"]
    search_help_text = "Search by user or domain name."

    change_form_template = "django/admin/email_clipboard_change_form.html"


class DomainInformationInline(admin.StackedInline):
    """Edit a domain information on the domain page.
    We had issues inheriting from both StackedInline
    and the source DomainInformationAdmin since these
    classes conflict, so we'll just pull what we need
    from DomainInformationAdmin
    """

    form = DomainInformationInlineForm
    template = "django/admin/includes/domain_info_inline_stacked.html"
    model = models.DomainInformation

    # Define methods to display fields from the related portfolio
    def portfolio_senior_official(self, obj) -> Optional[SeniorOfficial]:
        return obj.portfolio.senior_official if obj.portfolio and obj.portfolio.senior_official else None

    portfolio_senior_official.short_description = "Senior official"  # type: ignore

    def portfolio_organization_type(self, obj):
        return (
            DomainRequest.OrganizationChoices.get_org_label(obj.portfolio.organization_type)
            if obj.portfolio and obj.portfolio.organization_type
            else "-"
        )

    portfolio_organization_type.short_description = "Organization type"  # type: ignore

    def portfolio_federal_type(self, obj):
        return (
            BranchChoices.get_branch_label(obj.portfolio.federal_type)
            if obj.portfolio and obj.portfolio.federal_type
            else "-"
        )

    portfolio_federal_type.short_description = "Federal type"  # type: ignore

    def portfolio_organization_name(self, obj):
        return obj.portfolio.organization_name if obj.portfolio else ""

    portfolio_organization_name.short_description = "Organization name"  # type: ignore

    def portfolio_federal_agency(self, obj):
        return obj.portfolio.federal_agency if obj.portfolio else ""

    portfolio_federal_agency.short_description = "Federal agency"  # type: ignore

    def portfolio_state_territory(self, obj):
        return obj.portfolio.state_territory if obj.portfolio else ""

    portfolio_state_territory.short_description = "State, territory, or military post"  # type: ignore

    def portfolio_address_line1(self, obj):
        return obj.portfolio.address_line1 if obj.portfolio else ""

    portfolio_address_line1.short_description = "Address line 1"  # type: ignore

    def portfolio_address_line2(self, obj):
        return obj.portfolio.address_line2 if obj.portfolio else ""

    portfolio_address_line2.short_description = "Address line 2"  # type: ignore

    def portfolio_city(self, obj):
        return obj.portfolio.city if obj.portfolio else ""

    portfolio_city.short_description = "City"  # type: ignore

    def portfolio_zipcode(self, obj):
        return obj.portfolio.zipcode if obj.portfolio else ""

    portfolio_zipcode.short_description = "Zip code"  # type: ignore

    def portfolio_urbanization(self, obj):
        return obj.portfolio.urbanization if obj.portfolio else ""

    portfolio_urbanization.short_description = "Urbanization"  # type: ignore

    fieldsets = copy.deepcopy(list(DomainInformationAdmin.fieldsets))
    readonly_fields = copy.deepcopy(DomainInformationAdmin.readonly_fields)
    analyst_readonly_fields = copy.deepcopy(DomainInformationAdmin.analyst_readonly_fields)
    autocomplete_fields = copy.deepcopy(DomainInformationAdmin.autocomplete_fields)

    def get_domain_managers(self, obj):
        user_domain_roles = UserDomainRole.objects.filter(domain=obj.domain)
        user_ids = user_domain_roles.values_list("user_id", flat=True)
        domain_managers = User.objects.filter(id__in=user_ids)
        return domain_managers

    def get_domain_invitations(self, obj):
        domain_invitations = DomainInvitation.objects.filter(
            domain=obj.domain, status=DomainInvitation.DomainInvitationStatus.INVITED
        )
        return domain_invitations

    def domain_managers(self, obj):
        """Get domain managers for the domain, unpack and return an HTML block."""
        domain_managers = self.get_domain_managers(obj)
        if not domain_managers:
            return "No domain managers found."

        domain_manager_details = "<table><thead><tr><th>UID</th><th>Name</th><th>Email</th></tr></thead><tbody>"
        for domain_manager in domain_managers:
            full_name = domain_manager.get_formatted_name()
            change_url = reverse("admin:registrar_user_change", args=[domain_manager.pk])
            domain_manager_details += "<tr>"
            domain_manager_details += f'<td><a href="{change_url}">{escape(domain_manager.username)}</a>'
            domain_manager_details += f"<td>{escape(full_name)}</td>"
            domain_manager_details += f"<td>{escape(domain_manager.email)}</td>"
            domain_manager_details += "</tr>"
        domain_manager_details += "</tbody></table>"
        return format_html(domain_manager_details)

    domain_managers.short_description = "Domain managers"  # type: ignore

    def invited_domain_managers(self, obj):
        """Get emails which have been invited to the domain, unpack and return an HTML block."""
        domain_invitations = self.get_domain_invitations(obj)
        if not domain_invitations:
            return "No invited domain managers found."

        domain_invitation_details = "<table><thead><tr><th>Email</th><th>Status</th>" + "</tr></thead><tbody>"
        for domain_invitation in domain_invitations:
            domain_invitation_details += "<tr>"
            domain_invitation_details += f"<td>{escape(domain_invitation.email)}</td>"
            domain_invitation_details += f"<td>{escape(domain_invitation.status.capitalize())}</td>"
            domain_invitation_details += "</tr>"
        domain_invitation_details += "</tbody></table>"
        return format_html(domain_invitation_details)

    invited_domain_managers.short_description = "Invited domain managers"  # type: ignore

    def has_change_permission(self, request, obj=None):
        """Custom has_change_permission override so that we can specify that
        analysts can edit this through this inline, but not through the model normally"""

        superuser_perm = request.user.has_perm("registrar.full_access_permission")
        analyst_perm = request.user.has_perm("registrar.analyst_access_permission")
        if analyst_perm and not superuser_perm:
            return True
        return super().has_change_permission(request, obj)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        """customize the behavior of formfields with manytomany relationships.  the customized
        behavior includes sorting of objects in lists as well as customizing helper text"""

        queryset = AdminSortFields.get_queryset(db_field)
        if queryset:
            kwargs["queryset"] = queryset
        formfield = super().formfield_for_manytomany(db_field, request, **kwargs)
        # customize the help text for all formfields for manytomany
        formfield.help_text = (
            formfield.help_text
            + " If more than one value is selected, the change/delete/view actions will be disabled."
        )
        return formfield

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Customize the behavior of formfields with foreign key relationships. This will customize
        the behavior of selects. Customized behavior includes sorting of objects in list."""
        # Remove this check on senior_official if this underlying model changes from
        # "Contact" to "SeniorOfficial" or if we refactor AdminSortFields.
        # Removing this will cause the list on django admin to return SeniorOffical
        # objects rather than Contact objects.
        queryset = AdminSortFields.get_queryset(db_field)
        if queryset and db_field.name != "senior_official":
            kwargs["queryset"] = queryset
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = copy.deepcopy(DomainInformationAdmin.get_readonly_fields(self, request, obj=None))
        readonly_fields.extend(["domain_managers", "invited_domain_managers"])  # type: ignore
        return readonly_fields

    # Re-route the get_fieldsets method to utilize DomainInformationAdmin.get_fieldsets
    # since that has all the logic for excluding certain fields according to user permissions.
    # Then modify the remaining fields to further trim out any we don't want for this inline
    # form
    def get_fieldsets(self, request, obj=None):
        # Grab fieldsets from DomainInformationAdmin so that it handles all logic
        # for permission-based field visibility.
        modified_fieldsets = copy.deepcopy(DomainInformationAdmin.get_fieldsets(self, request, obj=None))

        # Modify fieldset sections in place
        for index, (title, options) in enumerate(modified_fieldsets):
            if title is None:
                options["fields"] = [
                    field for field in options["fields"] if field not in ["creator", "domain_request", "notes"]
                ]
            elif title == "Contacts":
                options["fields"] = [
                    field
                    for field in options["fields"]
                    if field not in ["other_contacts", "no_other_contacts_rationale"]
                ]
                options["fields"].extend(["domain_managers", "invited_domain_managers"])  # type: ignore
            elif title == "Background info":
                # move domain request and notes to background
                options["fields"].extend(["domain_request", "notes"])  # type: ignore

        # Remove or remove fieldset sections
        for index, (title, f) in enumerate(modified_fieldsets):
            if title == ".gov domain":
                # remove .gov domain from fieldset
                modified_fieldsets.pop(index)
            elif title == "Background info":
                # move Background info to the bottom of the list
                fieldsets_to_move = modified_fieldsets.pop(index)
                modified_fieldsets.append(fieldsets_to_move)

        return modified_fieldsets


class DomainResource(FsmModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.Domain


class DomainAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Custom domain admin class to add extra buttons."""

    resource_classes = [DomainResource]

    # ------- FILTERS
    class ElectionOfficeFilter(admin.SimpleListFilter):
        """Define a custom filter for is_election_board"""

        title = _("election office")
        parameter_name = "is_election_board"

        def lookups(self, request, model_admin):
            return (
                ("1", _("Yes")),
                ("0", _("No")),
            )

        def queryset(self, request, queryset):
            if self.value() == "1":
                return queryset.filter(domain_info__is_election_board=True)
            if self.value() == "0":
                return queryset.filter(Q(domain_info__is_election_board=False) | Q(domain_info__is_election_board=None))

    class GenericOrgFilter(admin.SimpleListFilter):
        """Custom Generic Organization filter that accomodates portfolio feature.
        If we have a portfolio, use the portfolio's organization.  If not, use the
        organization in the Domain Information object."""

        title = "generic organization"
        parameter_name = "converted_generic_orgs"

        def lookups(self, request, model_admin):
            converted_generic_orgs = set()

            # Populate the set with tuples of (value, display value)
            for domain_info in DomainInformation.objects.all():
                converted_generic_org = domain_info.converted_generic_org_type  # Actual value
                converted_generic_org_display = domain_info.converted_generic_org_type_display  # Display value

                if converted_generic_org:
                    converted_generic_orgs.add((converted_generic_org, converted_generic_org_display))  # Value, Display

            # Sort the set by display value
            return sorted(converted_generic_orgs, key=lambda x: x[1])  # x[1] is the display value

        # Filter queryset
        def queryset(self, request, queryset):
            if self.value():  # Check if a generic org is selected in the filter
                return queryset.filter(
                    Q(domain_info__portfolio__organization_type=self.value())
                    | Q(domain_info__portfolio__isnull=True, domain_info__generic_org_type=self.value())
                )

            return queryset

    class FederalTypeFilter(admin.SimpleListFilter):
        """Custom Federal Type filter that accomodates portfolio feature.
        If we have a portfolio, use the portfolio's federal type.  If not, use the
        federal type in the Domain Information object."""

        title = "federal type"
        parameter_name = "converted_federal_types"

        def lookups(self, request, model_admin):
            converted_federal_types = set()

            # Populate the set with tuples of (value, display value)
            for domain_info in DomainInformation.objects.all():
                converted_federal_type = domain_info.converted_federal_type  # Actual value
                converted_federal_type_display = domain_info.converted_federal_type_display  # Display value

                if converted_federal_type:
                    converted_federal_types.add(
                        (converted_federal_type, converted_federal_type_display)  # Value, Display
                    )

            # Sort the set by display value
            return sorted(converted_federal_types, key=lambda x: x[1])  # x[1] is the display value

        # Filter queryset
        def queryset(self, request, queryset):
            if self.value():  # Check if a federal type is selected in the filter
                return queryset.filter(
                    Q(domain_info__portfolio__federal_agency__federal_type=self.value())
                    | Q(domain_info__portfolio__isnull=True, domain_info__federal_agency__federal_type=self.value())
                )
            return queryset

    def get_annotated_queryset(self, queryset):
        return queryset.annotate(
            converted_generic_org_type=Case(
                # When portfolio is present, use its value instead
                When(domain_info__portfolio__isnull=False, then=F("domain_info__portfolio__organization_type")),
                # Otherwise, return the natively assigned value
                default=F("domain_info__generic_org_type"),
            ),
            converted_federal_agency=Case(
                # When portfolio is present, use its value instead
                When(
                    Q(domain_info__portfolio__isnull=False) & Q(domain_info__portfolio__federal_agency__isnull=False),
                    then=F("domain_info__portfolio__federal_agency__agency"),
                ),
                # Otherwise, return the natively assigned value
                default=F("domain_info__federal_agency__agency"),
            ),
            converted_federal_type=Case(
                # When portfolio is present, use its value instead
                When(
                    Q(domain_info__portfolio__isnull=False) & Q(domain_info__portfolio__federal_agency__isnull=False),
                    then=F("domain_info__portfolio__federal_agency__federal_type"),
                ),
                # Otherwise, return the natively assigned value
                default=F("domain_info__federal_agency__federal_type"),
            ),
            converted_organization_name=Case(
                # When portfolio is present, use its value instead
                When(domain_info__portfolio__isnull=False, then=F("domain_info__portfolio__organization_name")),
                # Otherwise, return the natively assigned value
                default=F("domain_info__organization_name"),
            ),
            converted_city=Case(
                # When portfolio is present, use its value instead
                When(domain_info__portfolio__isnull=False, then=F("domain_info__portfolio__city")),
                # Otherwise, return the natively assigned value
                default=F("domain_info__city"),
            ),
            converted_state_territory=Case(
                # When portfolio is present, use its value instead
                When(domain_info__portfolio__isnull=False, then=F("domain_info__portfolio__state_territory")),
                # Otherwise, return the natively assigned value
                default=F("domain_info__state_territory"),
            ),
        )

    # Filters
    list_filter = [GenericOrgFilter, FederalTypeFilter, ElectionOfficeFilter, "state"]

    # ------- END FILTERS

    # Inlines
    inlines = [DomainInformationInline]

    # Columns
    list_display = [
        "name",
        "converted_generic_org_type",
        "converted_federal_type",
        "converted_federal_agency",
        "converted_organization_name",
        "custom_election_board",
        "converted_city",
        "converted_state_territory",
        "state",
        "expiration_date",
        "created_at",
        "first_ready",
        "deleted",
    ]

    fieldsets = (
        (
            None,
            {"fields": ["state", "expiration_date", "first_ready", "deleted", "dnssecdata", "nameservers"]},
        ),
    )

    # ------- Domain Information Fields

    # --- Generic Org Type
    # Use converted value in the table
    @admin.display(description=_("Generic Org Type"))
    def converted_generic_org_type(self, obj):
        return obj.domain_info.converted_generic_org_type_display

    converted_generic_org_type.admin_order_field = "converted_generic_org_type"  # type: ignore

    # Use native value for the change form
    def generic_org_type(self, obj):
        return obj.domain_info.get_generic_org_type_display()

    # --- Federal Agency
    @admin.display(description=_("Federal Agency"))
    def converted_federal_agency(self, obj):
        return obj.domain_info.converted_federal_agency

    converted_federal_agency.admin_order_field = "converted_federal_agency"  # type: ignore

    # Use native value for the change form
    def federal_agency(self, obj):
        if obj.domain_info:
            return obj.domain_info.federal_agency
        else:
            return None

    # --- Federal Type
    # Use converted value in the table
    @admin.display(description=_("Federal Type"))
    def converted_federal_type(self, obj):
        return obj.domain_info.converted_federal_type_display

    converted_federal_type.admin_order_field = "converted_federal_type"  # type: ignore

    # Use native value for the change form
    def federal_type(self, obj):
        return obj.domain_info.federal_type if obj.domain_info else None

    # --- Organization Name
    # Use converted value in the table
    @admin.display(description=_("Organization Name"))
    def converted_organization_name(self, obj):
        return obj.domain_info.converted_organization_name

    converted_organization_name.admin_order_field = "converted_organization_name"  # type: ignore

    # Use native value for the change form
    def organization_name(self, obj):
        return obj.domain_info.organization_name if obj.domain_info else None

    # --- City
    # Use converted value in the table
    @admin.display(description=_("City"))
    def converted_city(self, obj):
        return obj.domain_info.converted_city

    converted_city.admin_order_field = "converted_city"  # type: ignore

    # Use native value for the change form
    def city(self, obj):
        return obj.domain_info.city if obj.domain_info else None

    # --- State
    # Use converted value in the table
    @admin.display(description=_("State / territory"))
    def converted_state_territory(self, obj):
        return obj.domain_info.converted_state_territory

    converted_state_territory.admin_order_field = "converted_state_territory"  # type: ignore

    # Use native value for the change form
    def state_territory(self, obj):
        return obj.domain_info.state_territory if obj.domain_info else None

    def dnssecdata(self, obj):
        return "Yes" if obj.dnssecdata else "No"

    dnssecdata.short_description = "DNSSEC enabled"  # type: ignore

    # Custom method to display formatted nameservers
    def nameservers(self, obj):
        if not obj.nameservers:
            return "No nameservers"

        formatted_nameservers = []
        for server, ip_list in obj.nameservers:
            server_display = str(server)
            if ip_list:
                server_display += f" [{', '.join(ip_list)}]"
            formatted_nameservers.append(server_display)

        # Join the formatted strings with line breaks
        return "\n".join(formatted_nameservers)

    nameservers.short_description = "Name servers"  # type: ignore

    def custom_election_board(self, obj):
        domain_info = getattr(obj, "domain_info", None)
        if domain_info:
            return "Yes" if domain_info.is_election_board else "No"
        return "No"

    custom_election_board.admin_order_field = "domain_info__is_election_board"  # type: ignore
    custom_election_board.short_description = "Election office"  # type: ignore

    # Search
    search_fields = ["name"]
    search_help_text = "Search by domain name."

    # Change Form
    change_form_template = "django/admin/domain_change_form.html"

    # Readonly Fields
    readonly_fields = (
        "state",
        "expiration_date",
        "first_ready",
        "deleted",
        "federal_agency",
        "dnssecdata",
        "nameservers",
    )

    # Table ordering
    ordering = ["name"]

    # Override for the delete confirmation page on the domain table (bulk delete action)
    delete_selected_confirmation_template = "django/admin/domain_delete_selected_confirmation.html"

    def delete_view(self, request, object_id, extra_context=None):
        """
        Custom delete_view to perform additional actions or customize the template.
        """

        # Set the delete template to a custom one
        self.delete_confirmation_template = "django/admin/domain_delete_confirmation.html"
        response = super().delete_view(request, object_id, extra_context=extra_context)

        return response

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """Custom changeform implementation to pass in context information"""
        if extra_context is None:
            extra_context = {}

        if object_id is not None:
            domain = Domain.objects.get(pk=object_id)

            # Used in the custom contact view
            if domain is not None and hasattr(domain, "domain_info"):
                extra_context["original_object"] = domain.domain_info

            extra_context["state_help_message"] = Domain.State.get_admin_help_text(domain.state)
            extra_context["domain_state"] = domain.get_state_display()
            extra_context["curr_exp_date"] = (
                domain.expiration_date if domain.expiration_date is not None else self._get_current_date()
            )

        return super().changeform_view(request, object_id, form_url, extra_context)

    def response_change(self, request, obj):
        # Create dictionary of action functions
        ACTION_FUNCTIONS = {
            "_place_client_hold": self.do_place_client_hold,
            "_remove_client_hold": self.do_remove_client_hold,
            "_edit_domain": self.do_edit_domain,
            "_delete_domain": self.do_delete_domain,
            "_get_status": self.do_get_status,
            "_extend_expiration_date": self.do_extend_expiration_date,
        }

        # Check which action button was pressed and call the corresponding function
        for action, function in ACTION_FUNCTIONS.items():
            if action in request.POST:
                return function(request, obj)

        # If no matching action button is found, return the super method
        return super().response_change(request, obj)

    def do_extend_expiration_date(self, request, obj):
        """Extends a domains expiration date by one year from the current date"""

        # Make sure we're dealing with a Domain
        if not isinstance(obj, Domain):
            self.message_user(request, "Object is not of type Domain.", messages.ERROR)
            return None

        # Renew the domain.
        try:
            obj.renew_domain()
            self.message_user(
                request,
                "Successfully extended the expiration date.",
            )
        except RegistryError as err:
            if err.is_connection_error():
                error_message = "Error connecting to the registry."
            else:
                error_message = f"Error extending this domain: {err}."
            self.message_user(request, error_message, messages.ERROR)
        except KeyError:
            # In normal code flow, a keyerror can only occur when
            # fresh data can't be pulled from the registry, and thus there is no cache.
            self.message_user(
                request,
                "Error connecting to the registry. No expiration date was found.",
                messages.ERROR,
            )
        except Exception as err:
            logger.error(err, stack_info=True)
            self.message_user(request, "Could not delete: An unspecified error occured", messages.ERROR)

        return HttpResponseRedirect(".")

    # Workaround for unit tests, as we cannot mock date directly.
    # it is immutable. Rather than dealing with a convoluted workaround,
    # lets wrap this in a function.
    def _get_current_date(self):
        """Gets the current date"""
        return date.today()

    def do_delete_domain(self, request, obj):
        if not isinstance(obj, Domain):
            # Could be problematic if the type is similar,
            # but not the same (same field/func names).
            # We do not want to accidentally delete records.
            self.message_user(request, "Object is not of type Domain", messages.ERROR)
            return

        try:
            obj.deletedInEpp()
            obj.save()
        except RegistryError as err:
            # Using variables to get past the linter
            message1 = f"Cannot delete Domain when in state {obj.state}"
            message2 = f"This subdomain is being used as a hostname on another domain: {err.note}"
            # Human-readable mappings of ErrorCodes. Can be expanded.
            error_messages = {
                # noqa on these items as black wants to reformat to an invalid length
                ErrorCode.OBJECT_STATUS_PROHIBITS_OPERATION: message1,
                ErrorCode.OBJECT_ASSOCIATION_PROHIBITS_OPERATION: message2,
            }

            message = "Cannot connect to the registry"
            if not err.is_connection_error():
                # If nothing is found, will default to returned err
                message = error_messages.get(err.code, err)
            self.message_user(request, f"Error deleting this Domain: {message}", messages.ERROR)
        except TransitionNotAllowed:
            if obj.state == Domain.State.DELETED:
                self.message_user(
                    request,
                    "This domain is already deleted",
                    messages.INFO,
                )
            else:
                self.message_user(
                    request,
                    (
                        "Error deleting this Domain: "
                        f"Can't switch from state '{obj.state}' to 'deleted'"
                        ", must be either 'dns_needed' or 'on_hold'"
                    ),
                    messages.ERROR,
                )
        except Exception:
            self.message_user(
                request,
                "Could not delete: An unspecified error occured",
                messages.ERROR,
            )
        else:
            self.message_user(
                request,
                "Domain %s has been deleted. Thanks!" % obj.name,
            )

        return HttpResponseRedirect(".")

    def do_get_status(self, request, obj):
        try:
            statuses = obj.statuses
        except Exception as err:
            self.message_user(request, err, messages.ERROR)
        else:
            self.message_user(
                request,
                f"The registry statuses are {statuses}. These statuses are from the provider of the .gov registry.",
            )
        return HttpResponseRedirect(".")

    def do_place_client_hold(self, request, obj):
        try:
            obj.place_client_hold()
            obj.save()
        except Exception as err:
            # if error is an error from the registry, display useful
            # and readable error
            if err.code:
                self.message_user(
                    request,
                    f"Error placing the hold with the registry: {err}",
                    messages.ERROR,
                )
            elif err.is_connection_error():
                self.message_user(
                    request,
                    "Error connecting to the registry",
                    messages.ERROR,
                )
            else:
                # all other type error messages, display the error
                self.message_user(request, err, messages.ERROR)
        else:
            self.message_user(
                request,
                "%s is in client hold. This domain is no longer accessible on the public internet." % obj.name,
            )
        return HttpResponseRedirect(".")

    def do_remove_client_hold(self, request, obj):
        try:
            obj.revert_client_hold()
            obj.save()
        except Exception as err:
            # if error is an error from the registry, display useful
            # and readable error
            if err.code:
                self.message_user(
                    request,
                    f"Error removing the hold in the registry: {err}",
                    messages.ERROR,
                )
            elif err.is_connection_error():
                self.message_user(
                    request,
                    "Error connecting to the registry",
                    messages.ERROR,
                )
            else:
                # all other type error messages, display the error
                self.message_user(request, err, messages.ERROR)
        else:
            self.message_user(
                request,
                "%s is ready. This domain is accessible on the public internet." % obj.name,
            )
        return HttpResponseRedirect(".")

    def do_edit_domain(self, request, obj):
        # We want to know, globally, when an edit action occurs
        request.session["analyst_action"] = "edit"
        # Restricts this action to this domain (pk) only
        request.session["analyst_action_location"] = obj.id
        return HttpResponseRedirect(reverse("domain", args=(obj.id,)))

    def change_view(self, request, object_id):
        # If the analyst was recently editing a domain page,
        # delete any associated session values
        if "analyst_action" in request.session:
            del request.session["analyst_action"]
            del request.session["analyst_action_location"]
        return super().change_view(request, object_id)

    def has_change_permission(self, request, obj=None):
        # Fixes a bug wherein users which are only is_staff
        # can access 'change' when GET,
        # but cannot access this page when it is a request of type POST.
        if request.user.has_perm("registrar.full_access_permission") or request.user.has_perm(
            "registrar.analyst_access_permission"
        ):
            return True
        return super().has_change_permission(request, obj)

    def get_queryset(self, request):
        """Custom get_queryset to filter by portfolio if portfolio is in the
        request params."""
        initial_qs = super().get_queryset(request)
        qs = self.get_annotated_queryset(initial_qs)
        # Check if a 'portfolio' parameter is passed in the request
        portfolio_id = request.GET.get("portfolio")
        if portfolio_id:
            # Further filter the queryset by the portfolio
            qs = qs.filter(domain_info__portfolio=portfolio_id)
        return qs


class DraftDomainResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.DraftDomain


class DraftDomainAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Custom draft domain admin class."""

    resource_classes = [DraftDomainResource]

    search_fields = ["name"]
    search_help_text = "Search by draft domain name."

    # this ordering effects the ordering of results
    # in autocomplete_fields for user
    ordering = ["name"]
    list_display = ["name"]

    @admin.display(description=_("Requested domain"))
    def name(self, obj):
        return obj.name

    def get_model_perms(self, request):
        """
        Return empty perms dict thus hiding the model from admin index.
        """
        superuser_perm = request.user.has_perm("registrar.full_access_permission")
        analyst_perm = request.user.has_perm("registrar.analyst_access_permission")
        if analyst_perm and not superuser_perm:
            return {}
        return super().get_model_perms(request)

    def has_change_permission(self, request, obj=None):
        """
        Allow analysts to access the change form directly via URL.
        """
        superuser_perm = request.user.has_perm("registrar.full_access_permission")
        analyst_perm = request.user.has_perm("registrar.analyst_access_permission")
        if analyst_perm and not superuser_perm:
            return True
        return super().has_change_permission(request, obj)

    def response_change(self, request, obj):
        """
        Override to redirect users back to the previous page after saving.
        """
        superuser_perm = request.user.has_perm("registrar.full_access_permission")
        analyst_perm = request.user.has_perm("registrar.analyst_access_permission")
        return_path = request.GET.get("return_path")

        # First, call the super method to perform the standard operations and capture the response
        response = super().response_change(request, obj)

        # Don't redirect to the website page on save if the user is an analyst.
        # Rather, just redirect back to the originating page.
        if (analyst_perm and not superuser_perm) and return_path:
            # Redirect to the return path if it exists
            return HttpResponseRedirect(return_path)

        # If no redirection is needed, return the original response
        return response

    # Select draft domain to change -> Draft domains
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "Draft domains"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)


class PublicContactResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.PublicContact
        # may want to consider these bulk options in future, so left in as comments
        # use_bulk = True
        # batch_size = 1000
        # force_init_instance = True

    def __init__(self):
        """Sets global variables for code tidyness"""
        super().__init__()
        self.skip_epp_save = False

    def import_data(
        self,
        dataset,
        dry_run=False,
        raise_errors=False,
        use_transactions=None,
        collect_failed_rows=False,
        rollback_on_validation_errors=False,
        **kwargs,
    ):
        """Override import_data to set self.skip_epp_save if in kwargs"""
        self.skip_epp_save = kwargs.get("skip_epp_save", False)
        return super().import_data(
            dataset,
            dry_run,
            raise_errors,
            use_transactions,
            collect_failed_rows,
            rollback_on_validation_errors,
            **kwargs,
        )

    def save_instance(self, instance, is_create, using_transactions=True, dry_run=False):
        """Override save_instance setting skip_epp_save to True"""
        self.before_save_instance(instance, using_transactions, dry_run)
        if self._meta.use_bulk:
            if is_create:
                self.create_instances.append(instance)
            else:
                self.update_instances.append(instance)
        elif not using_transactions and dry_run:
            # we don't have transactions and we want to do a dry_run
            pass
        else:
            instance.save(skip_epp_save=self.skip_epp_save)
        self.after_save_instance(instance, using_transactions, dry_run)


class PublicContactAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    """Custom PublicContact admin class."""

    resource_classes = [PublicContactResource]

    change_form_template = "django/admin/email_clipboard_change_form.html"
    autocomplete_fields = ["domain"]

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if extra_context is None:
            extra_context = {}

        if object_id:
            obj = self.get_object(request, object_id)
            if obj:
                name = obj.name
                email = obj.email
                registry_id = obj.registry_id
                extra_context["subtitle"] = f"{name} <{email}> id: {registry_id}"

        return super().changeform_view(request, object_id, form_url, extra_context=extra_context)


class VerifiedByStaffAdmin(ListHeaderAdmin):
    list_display = ("email", "requestor", "truncated_notes", "created_at")
    search_fields = ["email"]
    search_help_text = "Search by email."
    readonly_fields = [
        "requestor",
    ]

    change_form_template = "django/admin/email_clipboard_change_form.html"

    def truncated_notes(self, obj):
        # Truncate the 'notes' field to 50 characters
        return str(obj.notes)[:50]

    truncated_notes.short_description = "Notes (Truncated)"  # type: ignore

    def save_model(self, request, obj, form, change):
        # Set the user field to the current admin user
        obj.requestor = request.user if request.user.is_authenticated else None
        super().save_model(request, obj, form, change)


class PortfolioAdmin(ListHeaderAdmin):

    class Meta:
        """Contains meta information about this class"""

        model = models.Portfolio
        fields = "__all__"

    _meta = Meta()

    change_form_template = "django/admin/portfolio_change_form.html"
    fieldsets = [
        # created_on is the created_at field
        (None, {"fields": ["creator", "created_on", "notes"]}),
        ("Type of organization", {"fields": ["organization_type", "federal_type"]}),
        (
            "Organization name and mailing address",
            {
                "fields": [
                    "organization_name",
                    "federal_agency",
                ]
            },
        ),
        (
            "Show details",
            {
                "classes": ["collapse--dgfieldset"],
                "description": "Extends organization name and mailing address",
                "fields": [
                    "state_territory",
                    "address_line1",
                    "address_line2",
                    "city",
                    "zipcode",
                    "urbanization",
                ],
            },
        ),
        ("Portfolio members", {"fields": ["display_admins", "display_members"]}),
        ("Domains and requests", {"fields": ["domains", "domain_requests"]}),
        ("Suborganizations", {"fields": ["suborganizations"]}),
        ("Senior official", {"fields": ["senior_official"]}),
    ]

    # This is the fieldset display when adding a new model
    add_fieldsets = [
        (None, {"fields": ["creator", "notes"]}),
        ("Type of organization", {"fields": ["organization_type"]}),
        (
            "Organization name and mailing address",
            {
                "fields": [
                    "organization_name",
                    "federal_agency",
                    "state_territory",
                    "address_line1",
                    "address_line2",
                    "city",
                    "zipcode",
                    "urbanization",
                ]
            },
        ),
        ("Senior official", {"fields": ["senior_official"]}),
    ]

    list_display = ("organization_name", "organization_type", "federal_type", "creator")
    search_fields = ["organization_name"]
    search_help_text = "Search by organization name."
    readonly_fields = [
        # This is the created_at field
        "created_on",
        # Django admin doesn't allow methods to be directly listed in fieldsets. We can
        # display the custom methods display_admins amd display_members in the admin form if
        # they are readonly.
        "federal_type",
        "domains",
        "domain_requests",
        "suborganizations",
        "display_admins",
        "display_members",
        "creator",
        # As of now this means that only federal agency can update this, but this will change.
        "senior_official",
    ]

    # Even though this is empty, I will leave it as a stub for easy changes in the future
    # rather than strip it out of our logic.
    analyst_readonly_fields = []  # type: ignore

    def get_admin_users(self, obj):
        # Filter UserPortfolioPermission objects related to the portfolio
        admin_permissions = self.get_user_portfolio_permission_admins(obj)

        # Get the user objects associated with these permissions
        admin_users = User.objects.filter(portfolio_permissions__in=admin_permissions)

        return admin_users

    def get_user_portfolio_permission_admins(self, obj):
        """Returns each admin on UserPortfolioPermission for a given portfolio."""
        if obj:
            return obj.portfolio_users.filter(
                portfolio=obj, roles__contains=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
            )
        else:
            return []

    def get_non_admin_users(self, obj):
        # Filter UserPortfolioPermission objects related to the portfolio that do NOT have the "Admin" role
        non_admin_permissions = UserPortfolioPermission.objects.filter(portfolio=obj).exclude(
            roles__contains=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN]
        )

        # Get the user objects associated with these permissions
        non_admin_users = User.objects.filter(portfolio_permissions__in=non_admin_permissions)

        return non_admin_users

    def get_user_portfolio_permission_non_admins(self, obj):
        """Returns each admin on UserPortfolioPermission for a given portfolio."""
        if obj:
            return obj.portfolio_users.exclude(roles__contains=[UserPortfolioRoleChoices.ORGANIZATION_ADMIN])
        else:
            return []

    def federal_type(self, obj: models.Portfolio):
        """Returns the federal_type field"""
        return BranchChoices.get_branch_label(obj.federal_type) if obj.federal_type else "-"

    federal_type.short_description = "Federal type"  # type: ignore

    def created_on(self, obj: models.Portfolio):
        """Returns the created_at field, with a different short description"""
        # Format: Dec 12, 2024
        return obj.created_at.strftime("%b %d, %Y") if obj.created_at else "-"

    created_on.short_description = "Created on"  # type: ignore

    def suborganizations(self, obj: models.Portfolio):
        """Returns a list of links for each related suborg"""
        queryset = obj.get_suborganizations()
        return get_field_links_as_list(queryset, "suborganization")

    suborganizations.short_description = "Suborganizations"  # type: ignore

    def domains(self, obj: models.Portfolio):
        """Returns the count of domains with a link to view them in the admin."""
        domain_count = obj.get_domains().count()  # Count the related domains
        if domain_count > 0:
            # Construct the URL to the admin page, filtered by portfolio
            url = reverse("admin:registrar_domain_changelist") + f"?portfolio={obj.id}"
            label = "domain" if domain_count == 1 else "domains"
            # Create a clickable link with the domain count
            return format_html('<a href="{}">{} {}</a>', url, domain_count, label)
        return "No domains"

    domains.short_description = "Domains"  # type: ignore

    def domain_requests(self, obj: models.Portfolio):
        """Returns the count of domain requests with a link to view them in the admin."""
        domain_request_count = obj.get_domain_requests().count()  # Count the related domain requests
        if domain_request_count > 0:
            # Construct the URL to the admin page, filtered by portfolio
            url = reverse("admin:registrar_domainrequest_changelist") + f"?portfolio={obj.id}"
            # Create a clickable link with the domain request count
            return format_html('<a href="{}">{} domain requests</a>', url, domain_request_count)
        return "No domain requests"

    domain_requests.short_description = "Domain requests"  # type: ignore

    def display_admins(self, obj):
        """Returns the number of administrators for this portfolio"""
        admin_count = len(self.get_user_portfolio_permission_admins(obj))
        if admin_count > 0:
            url = reverse("admin:registrar_userportfoliopermission_changelist") + f"?portfolio={obj.id}"
            # Create a clickable link with the count
            return format_html(f'<a href="{url}">{admin_count} administrators</a>')
        return "No administrators found."

    display_admins.short_description = "Administrators"  # type: ignore

    def display_members(self, obj):
        """Returns the number of members for this portfolio"""
        member_count = len(self.get_user_portfolio_permission_non_admins(obj))
        if member_count > 0:
            url = reverse("admin:registrar_userportfoliopermission_changelist") + f"?portfolio={obj.id}"
            # Create a clickable link with the count
            return format_html(f'<a href="{url}">{member_count} members</a>')
        return "No additional members found."

    display_members.short_description = "Members"  # type: ignore

    # Creates select2 fields (with search bars)
    autocomplete_fields = [
        "creator",
        "federal_agency",
        "senior_official",
    ]

    def get_fieldsets(self, request, obj=None):
        """Override of the default get_fieldsets definition to add an add_fieldsets view"""
        # This is the add view if no obj exists
        if not obj:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

    def get_readonly_fields(self, request, obj=None):
        """Set the read-only state on form elements.
        We have 2 conditions that determine which fields are read-only:
        admin user permissions and the creator's status, so
        we'll use the baseline readonly_fields and extend it as needed.
        """
        readonly_fields = list(self.readonly_fields)

        # Check if the creator is restricted
        if obj and obj.creator.status == models.User.RESTRICTED:
            # For fields like CharField, IntegerField, etc., the widget used is
            # straightforward and the readonly_fields list can control their behavior
            readonly_fields.extend([field.name for field in self.model._meta.fields])

        # Make senior_official readonly for federal organizations
        if obj and obj.organization_type == obj.OrganizationChoices.FEDERAL:
            if "senior_official" not in readonly_fields:
                readonly_fields.append("senior_official")
        elif "senior_official" in readonly_fields:
            # Remove senior_official from readonly_fields if org is non-federal
            readonly_fields.remove("senior_official")

        if request.user.has_perm("registrar.full_access_permission"):
            return readonly_fields

        # Return restrictive Read-only fields for analysts and
        # users who might not belong to groups
        readonly_fields.extend([field for field in self.analyst_readonly_fields])
        return readonly_fields

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Add related suborganizations and domain groups.
        Add the summary for the portfolio members field (list of members that link to change_forms)."""
        obj: Portfolio = self.get_object(request, object_id)
        extra_context = extra_context or {}
        extra_context["skip_additional_contact_info"] = True

        if obj:
            extra_context["members"] = self.get_user_portfolio_permission_non_admins(obj)
            extra_context["admins"] = self.get_user_portfolio_permission_admins(obj)
            extra_context["domains"] = obj.get_domains(order_by=["domain__name"])
            extra_context["domain_requests"] = obj.get_domain_requests(order_by=["requested_domain__name"])
        return super().change_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj: Portfolio, form, change):
        if hasattr(obj, "creator") is False:
            # ---- update creator ----
            # Set the creator field to the current admin user
            obj.creator = request.user if request.user.is_authenticated else None  # type: ignore
        # ---- update organization name ----
        # org name will be the same as federal agency, if it is federal,
        # otherwise it will be the actual org name. If nothing is entered for
        # org name and it is a federal organization, have this field fill with
        # the federal agency text name.
        is_federal = obj.organization_type == DomainRequest.OrganizationChoices.FEDERAL
        if is_federal and obj.organization_name is None:
            obj.organization_name = obj.federal_agency.agency

        # Set the senior official field to the senior official on the federal agency
        # when federal - otherwise, clear the field.
        if obj.organization_type == obj.OrganizationChoices.FEDERAL:
            if obj.federal_agency:
                if obj.federal_agency.so_federal_agency.exists():
                    obj.senior_official = obj.federal_agency.so_federal_agency.first()
                else:
                    obj.senior_official = None
        else:
            if obj.federal_agency and obj.federal_agency.agency != "Non-Federal Agency":
                if obj.federal_agency.so_federal_agency.first() == obj.senior_official:
                    obj.senior_official = None
                obj.federal_agency = FederalAgency.objects.filter(agency="Non-Federal Agency").first()  # type: ignore

        super().save_model(request, obj, form, change)


class FederalAgencyResource(resources.ModelResource):
    """defines how each field in the referenced model should be mapped to the corresponding fields in the
    import/export file"""

    class Meta:
        model = models.FederalAgency


class FederalAgencyAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    list_display = ["agency"]
    search_fields = ["agency"]
    search_help_text = "Search by federal agency."
    ordering = ["agency"]
    resource_classes = [FederalAgencyResource]


class UserGroupAdmin(AuditedAdmin):
    """Overwrite the generated UserGroup admin class"""

    list_display = ["user_group"]

    fieldsets = ((None, {"fields": ("name", "permissions")}),)

    def formfield_for_dbfield(self, dbfield, **kwargs):
        field = super().formfield_for_dbfield(dbfield, **kwargs)
        if dbfield.name == "name":
            field.label = "Group name"
        if dbfield.name == "permissions":
            field.label = "User permissions"
        return field

    # We name the custom prop 'Group' because linter
    # is not allowing a short_description attr on it
    # This gets around the linter limitation, for now.
    @admin.display(description=_("Group"))
    def user_group(self, obj):
        return obj.name

    # Select user groups to change -> User groups
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["tabtitle"] = "User groups"
        # Get the filtered values
        return super().changelist_view(request, extra_context=extra_context)


class WaffleFlagAdmin(FlagAdmin):
    """Custom admin implementation of django-waffle's Flag class"""

    class Meta:
        """Contains meta information about this class"""

        model = models.WaffleFlag
        fields = "__all__"

    # Hack to get the dns_prototype_flag to auto populate when you navigate to
    # the waffle flag page.
    def changelist_view(self, request, extra_context=None):
        if extra_context is None:
            extra_context = {}
        extra_context["dns_prototype_flag"] = flag_is_active_for_user(request.user, "dns_prototype_flag")
        return super().changelist_view(request, extra_context=extra_context)


class DomainGroupAdmin(ListHeaderAdmin, ImportExportModelAdmin):
    list_display = ["name", "portfolio"]


class SuborganizationAdmin(ListHeaderAdmin, ImportExportModelAdmin):

    list_display = ["name", "portfolio"]
    autocomplete_fields = [
        "portfolio",
    ]
    search_fields = ["name"]
    search_help_text = "Search by suborganization."

    change_form_template = "django/admin/suborg_change_form.html"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Add suborg's related domains and requests to context"""
        obj = self.get_object(request, object_id)

        # ---- Domain Requests
        domain_requests = DomainRequest.objects.filter(sub_organization=obj)
        sort_by = request.GET.get("sort_by", "requested_domain__name")
        domain_requests = domain_requests.order_by(sort_by)

        # ---- Domains
        domain_infos = DomainInformation.objects.filter(sub_organization=obj)
        domain_ids = domain_infos.values_list("domain", flat=True)
        domains = Domain.objects.filter(id__in=domain_ids).exclude(state=Domain.State.DELETED)

        extra_context = {"domain_requests": domain_requests, "domains": domains}
        return super().change_view(request, object_id, form_url, extra_context)


class AllowedEmailAdmin(ListHeaderAdmin):
    class Meta:
        model = models.AllowedEmail

    list_display = ["email"]
    search_fields = ["email"]
    search_help_text = "Search by email."
    ordering = ["email"]


admin.site.unregister(LogEntry)  # Unregister the default registration

admin.site.register(LogEntry, CustomLogEntryAdmin)
admin.site.register(models.User, MyUserAdmin)
# Unregister the built-in Group model
admin.site.unregister(Group)
# Register UserGroup
admin.site.register(models.UserGroup, UserGroupAdmin)
admin.site.register(models.UserDomainRole, UserDomainRoleAdmin)
admin.site.register(models.Contact, ContactAdmin)
admin.site.register(models.DomainInvitation, DomainInvitationAdmin)
admin.site.register(models.DomainInformation, DomainInformationAdmin)
admin.site.register(models.Domain, DomainAdmin)
admin.site.register(models.DraftDomain, DraftDomainAdmin)
admin.site.register(models.FederalAgency, FederalAgencyAdmin)
admin.site.register(models.Host, MyHostAdmin)
admin.site.register(models.HostIP, HostIpAdmin)
admin.site.register(models.Website, WebsiteAdmin)
admin.site.register(models.PublicContact, PublicContactAdmin)
admin.site.register(models.DomainRequest, DomainRequestAdmin)
admin.site.register(models.TransitionDomain, TransitionDomainAdmin)
admin.site.register(models.VerifiedByStaff, VerifiedByStaffAdmin)
admin.site.register(models.PortfolioInvitation, PortfolioInvitationAdmin)
admin.site.register(models.Portfolio, PortfolioAdmin)
admin.site.register(models.DomainGroup, DomainGroupAdmin)
admin.site.register(models.Suborganization, SuborganizationAdmin)
admin.site.register(models.SeniorOfficial, SeniorOfficialAdmin)
admin.site.register(models.UserPortfolioPermission, UserPortfolioPermissionAdmin)
admin.site.register(models.AllowedEmail, AllowedEmailAdmin)

# Register our custom waffle implementations
admin.site.register(models.WaffleFlag, WaffleFlagAdmin)

# Unregister Switch and Sample from the waffle library
admin.site.unregister(Switch)
admin.site.unregister(Sample)
